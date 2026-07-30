# inventory/signals.py
"""
Stock synchronisation. Warehouse quantities move ONLY through these handlers —
never by a view editing `Item.current_stock` directly.

TWO RULES THAT LOOK LIKE BUGS AND ARE NOT
-----------------------------------------
1. Only `source=INVENTORY` rows move stock, and they move it through the `item`
   FK — never by matching `spare_part_name` against `Item.name`. The old name
   match had no idea where a part came from, so a spare *bought from a shop*
   whose name happened to equal a stock product silently deducted the warehouse
   as well, while the analysis engine (correctly) also billed it as a shop
   purchase. One part, paid once, counted twice, and the shelf count drifted
   down until a restock bill papered over it.

2. Stock is NOT clamped at zero — it is allowed to go negative. A job card
   records a part the mechanic has *already physically taken*; refusing or
   truncating that record does not put the part back on the shelf. The old
   `Greatest(…, ZERO)` did not prevent an overdraw, it destroyed the evidence of
   one: drawing 5 from a shelf of 2 stored 0 instead of −3, so when the missing
   supplier bill finally arrived (+10) the count landed on 10 instead of 7 and
   three units were invented, permanently and silently. A negative balance is
   self-healing (−3 + 10 = 7) and is the signal that a Supplies Shop bill is
   missing or a count is wrong. Do not reinstate the clamp.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.db.models import F
from workshop.models import JobCardSpareItem, JobCard
from .costing import recompute_average_cost
from .models import Item, SupplierRestockBill, SupplierRestockItem

INVENTORY = JobCardSpareItem.SOURCE_INVENTORY


def _as_decimal(value):
    """Normalize a possibly-None quantity to an exact Decimal for stock math."""
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _apply(deltas):
    """Apply netted {item_id: delta} stock movements. No clamping — see module docstring."""
    for item_id, delta in deltas.items():
        if delta:
            Item.objects.filter(pk=item_id).update(current_stock=F('current_stock') + delta)


def _recost(item_ids):
    """Replay the weighted-average cost for each product id given."""
    for item in Item.objects.filter(pk__in={i for i in item_ids if i}):
        recompute_average_cost(item)


# -----------------------------------------------------------------------------
# Workshop consumption — warehouse draws on job cards
# -----------------------------------------------------------------------------
@receiver(pre_save, sender=JobCardSpareItem)
def track_old_draw(sender, instance, **kwargs):
    """
    Snapshot the pre-save draw so the post_save handler can compute a delta.

    Captures `source` and `item` as well as quantity: a row can be corrected
    from one product to another (the mechanic picked the wrong oil), which has
    to return the first product's stock and take the second's.
    """
    old = None
    if instance.pk:
        old = JobCardSpareItem.objects.filter(pk=instance.pk).values(
            'source', 'item_id', 'quantity').first()

    if old:
        instance._old_source = old['source']
        instance._old_item_id = old['item_id']
        instance._old_quantity = _as_decimal(old['quantity'])
    else:
        instance._old_source = None
        instance._old_item_id = None
        instance._old_quantity = Decimal('0')


@receiver(post_save, sender=JobCardSpareItem)
def sync_stock_on_save(sender, instance, created, **kwargs):
    """
    Return whatever the row previously drew, then take what it now draws. Netted
    per product so the common case (same product, quantity edited) is one query.
    """
    deltas = defaultdict(Decimal)

    if getattr(instance, '_old_source', None) == INVENTORY and instance._old_item_id:
        deltas[instance._old_item_id] += instance._old_quantity

    if instance.source == INVENTORY and instance.item_id:
        deltas[instance.item_id] -= _as_decimal(instance.quantity)

    _apply(deltas)


@receiver(post_delete, sender=JobCardSpareItem)
def restore_stock_on_delete(sender, instance, **kwargs):
    """Removing a warehouse draw returns the stock. A shop purchase never touched it."""
    if instance.source == INVENTORY and instance.item_id:
        _apply({instance.item_id: _as_decimal(instance.quantity)})


# -----------------------------------------------------------------------------
# JobCard soft-delete reversal — DORMANT
# -----------------------------------------------------------------------------
# Job cards are hard-deleted now, and the delete guard forbids deleting a card
# that still holds spares, so `is_deleted` never flips and these never fire.
# Kept for safety and updated to be source-aware alongside the handlers above,
# so they cannot reintroduce name matching if ever reactivated.
@receiver(pre_save, sender=JobCard)
def track_jobcard_deleted_state(sender, instance, **kwargs):
    if instance.pk:
        old = JobCard.objects.filter(pk=instance.pk).values('is_deleted').first()
        instance._old_is_deleted = old['is_deleted'] if old else False
    else:
        instance._old_is_deleted = False


@receiver(post_save, sender=JobCard)
def update_stock_on_jobcard_delete(sender, instance, created, **kwargs):
    old_deleted = getattr(instance, '_old_is_deleted', False)
    if old_deleted == instance.is_deleted:
        return

    # Soft-deleting returns stock to the shelf; restoring takes it again.
    direction = 1 if instance.is_deleted else -1

    deltas = defaultdict(Decimal)
    draws = instance.spares.filter(source=INVENTORY, item__isnull=False)
    for spare in draws:
        deltas[spare.item_id] += direction * _as_decimal(spare.quantity)

    _apply(deltas)


# -----------------------------------------------------------------------------
# Supplier restocking — receipts into the warehouse
# -----------------------------------------------------------------------------
@receiver(pre_save, sender=SupplierRestockItem)
def track_old_restock_quantity(sender, instance, **kwargs):
    old = None
    if instance.pk:
        old = SupplierRestockItem.objects.filter(pk=instance.pk).values(
            'item_id', 'quantity').first()

    if old:
        instance._old_item_id = old['item_id']
        instance._old_quantity = _as_decimal(old['quantity'])
    else:
        instance._old_item_id = None
        instance._old_quantity = Decimal('0')


@receiver(post_save, sender=SupplierRestockItem)
def update_stock_on_restock_save(sender, instance, created, **kwargs):
    deltas = defaultdict(Decimal)

    old_item_id = getattr(instance, '_old_item_id', None)
    if old_item_id:
        deltas[old_item_id] -= _as_decimal(instance._old_quantity)

    if instance.item_id:
        deltas[instance.item_id] += _as_decimal(instance.quantity)

    _apply(deltas)

    # A receipt is the only thing that moves the average — and its price may have
    # changed, not just its quantity, so recompute on every save.
    touched = set()
    if instance.item_id:
        touched.add(instance.item_id)
    if old_item_id:
        touched.add(old_item_id)

    # On a DISCOUNTED bill the sibling lines move too: the discount is shared out
    # pro-rata by value, so changing one line's price changes the bill total and
    # therefore every other line's share of it. Skipped when there is no discount,
    # where the apportionment is a no-op and only this line's product is affected.
    if instance.bill_id and (instance.bill.discount_amount or Decimal("0")) > 0:
        touched.update(
            SupplierRestockItem.objects
            .filter(bill_id=instance.bill_id)
            .values_list("item_id", flat=True)
        )

    _recost(touched)


@receiver(pre_save, sender=SupplierRestockBill)
def track_old_bill_terms(sender, instance, **kwargs):
    """Snapshot the two bill-level fields that change what its stock cost."""
    old = None
    if instance.pk:
        old = SupplierRestockBill.objects.filter(pk=instance.pk).values(
            'bill_date', 'discount_amount').first()
    instance._old_bill_date = old['bill_date'] if old else None
    instance._old_discount = old['discount_amount'] if old else None


@receiver(post_save, sender=SupplierRestockBill)
def recost_on_bill_terms_change(sender, instance, created, **kwargs):
    """
    A bill's DATE and DISCOUNT both change the average, even though neither lives
    on a line:

      • the costing replay is date-ordered, so moving a bill across an existing
        draw changes which receipts the draw was averaged against;
      • the discount is apportioned into each line's effective unit price.

    Neither re-saves the lines, so without this the stored average silently went
    stale — measured at ₹2,818.18 stored against ₹2,000.00 true after a date-only
    edit. Recompute is a full replay, so firing it once per changed bill is safe
    and idempotent.
    """
    if created:
        return
    date_changed = getattr(instance, '_old_bill_date', None) != instance.bill_date
    discount_changed = getattr(instance, '_old_discount', None) != instance.discount_amount
    if not (date_changed or discount_changed):
        return

    _recost(SupplierRestockItem.objects.filter(bill=instance)
            .values_list('item_id', flat=True))


@receiver(post_delete, sender=SupplierRestockItem)
def restore_stock_on_restock_delete(sender, instance, **kwargs):
    """Deleting a receipt removes the stock it brought in — unclamped, so a
    deletion that overdraws the shelf shows as negative rather than being
    swallowed the way the old zero-clamp swallowed it."""
    if instance.item_id:
        _apply({instance.item_id: -_as_decimal(instance.quantity)})
        # Removing a line changes the bill total, so a discounted bill re-shares
        # its discount across whatever lines remain.
        touched = {instance.item_id}
        if instance.bill_id:
            bill = SupplierRestockBill.objects.filter(pk=instance.bill_id).first()
            if bill and (bill.discount_amount or Decimal('0')) > 0:
                touched.update(SupplierRestockItem.objects.filter(bill_id=bill.pk)
                               .values_list('item_id', flat=True))
        _recost(touched)
