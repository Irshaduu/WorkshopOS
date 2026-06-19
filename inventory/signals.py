# inventory/signals.py
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.db.models import F, Value
from django.db.models.functions import Greatest
from workshop.models import JobCardSpareItem, JobCard
from .models import Item, SupplierRestockItem

@receiver(pre_save, sender=JobCardSpareItem)
def track_old_quantity(sender, instance, **kwargs):
    """
    Snapshots the original part data before a database commit.
    This is critical for calculating stock deltas during updates.
    """
    if instance.pk:
        try:
            old_instance = JobCardSpareItem.objects.get(pk=instance.pk)
            instance._old_quantity = old_instance.quantity or 0
            instance._old_name = old_instance.spare_part_name
        except JobCardSpareItem.DoesNotExist:
            instance._old_quantity = 0
            instance._old_name = None
    else:
        instance._old_quantity = 0
        instance._old_name = None

@receiver(post_save, sender=JobCardSpareItem)
def update_stock_on_save(sender, instance, created, **kwargs):
    """
    Orchestrates the Workshop-to-Warehouse stock synchronization.
    
    Algorithm Breakdown (Delta Logic):
    ---------------------------------
    Scenario A: Part Replacement (Name Change)
        If 'Oil Filter' is changed to 'Air Filter':
        1. Restore stock for 'Oil Filter' (+OldQty).
        2. Deduct stock for 'Air Filter' (-NewQty).
        
    Scenario B: Quantity Adjustment (Same Name)
        If 'Oil Filter' remains but quantity goes from 2 to 5:
        1. Calculate Delta: 5 - 2 = 3.
        2. Deduct only the difference (-3) from Warehouse.
        
    Scenario C: New Entry
        1. Deduct full quantity from Warehouse.
    """
    new_qty = float(instance.quantity or 0)
    new_name = instance.spare_part_name
    
    old_qty = float(getattr(instance, '_old_quantity', 0))
    old_name = getattr(instance, '_old_name', None)

    # Logic Implementation...
    if old_name and old_name != new_name:
        # Scenario A: Rename
        old_inv_item = Item.objects.filter(name__iexact=old_name).first()
        if old_inv_item:
            Item.objects.filter(pk=old_inv_item.pk).update(
                current_stock=F('current_stock') + old_qty
            )
            
        if new_name:
            new_inv_item = Item.objects.filter(name__iexact=new_name).first()
            if new_inv_item:
                Item.objects.filter(pk=new_inv_item.pk).update(
                    current_stock=Greatest(F('current_stock') - new_qty, Value(0.0))
                )
                
    elif new_name:
        # Scenario B/C: Quantity Change
        diff = new_qty - old_qty
        if diff != 0:
            inv_item = Item.objects.filter(name__iexact=new_name).first()
            if inv_item:
                Item.objects.filter(pk=inv_item.pk).update(
                    current_stock=Greatest(F('current_stock') - diff, Value(0.0))
                )

@receiver(post_delete, sender=JobCardSpareItem)
def restore_stock_on_delete(sender, instance, **kwargs):
    """
    Reverses stock deduction when a part is removed from a job card.
    Ensure 100% warehouse integrity on cancellations.
    """
    if instance.spare_part_name and instance.quantity:
        inv_item = Item.objects.filter(name__iexact=instance.spare_part_name).first()
        if inv_item:
            Item.objects.filter(pk=inv_item.pk).update(
                current_stock=F('current_stock') + float(instance.quantity)
            )

# -----------------------------------------------------------------------------
# JobCard Soft-Delete Reversal
# -----------------------------------------------------------------------------
@receiver(pre_save, sender=JobCard)
def track_jobcard_deleted_state(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = JobCard.objects.get(pk=instance.pk)
            instance._old_is_deleted = old_instance.is_deleted
        except JobCard.DoesNotExist:
            instance._old_is_deleted = False
    else:
        instance._old_is_deleted = False

@receiver(post_save, sender=JobCard)
def update_stock_on_jobcard_delete(sender, instance, created, **kwargs):
    old_deleted = getattr(instance, '_old_is_deleted', False)
    new_deleted = instance.is_deleted

    if old_deleted == new_deleted:
        return

    direction = 1 if (old_deleted == False and new_deleted == True) else -1

    spare_items = instance.spares.filter(spare_part_name__isnull=False).exclude(spare_part_name='')
    
    qty_map = {}
    for spare in spare_items:
        name_lower = spare.spare_part_name.strip().lower()
        qty_map[name_lower] = qty_map.get(name_lower, 0) + float(spare.quantity or 0)

    for name_lower, total_qty in qty_map.items():
        Item.objects.filter(name__iexact=name_lower).update(
            current_stock=Greatest(F('current_stock') + (direction * total_qty), Value(0.0))
        )

# -----------------------------------------------------------------------------
# Supplier Restock Signals
# -----------------------------------------------------------------------------
@receiver(pre_save, sender=SupplierRestockItem)
def track_old_restock_quantity(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = SupplierRestockItem.objects.get(pk=instance.pk)
            instance._old_quantity = old_instance.quantity or 0
        except SupplierRestockItem.DoesNotExist:
            instance._old_quantity = 0
    else:
        instance._old_quantity = 0

@receiver(post_save, sender=SupplierRestockItem)
def update_stock_on_restock_save(sender, instance, created, **kwargs):
    new_qty = float(instance.quantity or 0)
    old_qty = float(getattr(instance, '_old_quantity', 0))
    diff = new_qty - old_qty
    
    if diff != 0 and instance.item:
        Item.objects.filter(pk=instance.item.pk).update(
            current_stock=Greatest(F('current_stock') + diff, Value(0.0))
        )

@receiver(post_delete, sender=SupplierRestockItem)
def restore_stock_on_restock_delete(sender, instance, **kwargs):
    if instance.item and instance.quantity:
        Item.objects.filter(pk=instance.item.pk).update(
            current_stock=Greatest(F('current_stock') - float(instance.quantity), Value(0.0))
        )
