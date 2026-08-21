from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Count, Max, Prefetch, Sum, F, OuterRef, Subquery, Q, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal, InvalidOperation
from datetime import timedelta, date
from .models import Item, Category, SupplierShop, ShopCatalogItem, SupplierRestockBill, SupplierRestockItem, SupplierPayment
from workshop.decorators import office_required
from workshop.models import DeletionLog, JobCardSpareItem
from workshop.notifications import notify
from workshop.money import parse_money, fit_text
from django.urls import reverse
from workshop.templatetags.custom_filters import clean_qty
from django.db import transaction, IntegrityError


def _dec(raw, default='0'):
    """Parse a supplier-form numeric field into an exact Decimal.
    Falls back to `default` on blank/invalid input instead of raising."""
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return Decimal(default)


def _active_catalog_items(shop, item_ids):
    """The subset of `item_ids` that this shop actually stocks and has not deactivated.

    Restock bills add warehouse stock, so which items may appear on one is a real
    invariant — it must be enforced here, in the view that writes the rows, not only
    in the picker template that offers them.
    """
    if not item_ids:
        return Item.objects.none()
    return (
        Item.objects
        .filter(id__in=item_ids, shop_catalogs__shop=shop, shop_catalogs__is_active=True)
        .select_related('category')
        .distinct()
    )

@office_required
def supplier_shop_list(request):
    # Annotate catalog_count in SQL — avoids N+1 per-card `.count()` call in template.
    # Sorted by most recent restock bill first, so shops the workshop actually deals
    # with day to day surface at the top instead of behind alphabetically-earlier,
    # rarely-used ones. Shops with no bills yet sort to the bottom, then by name.
    shops = (
        SupplierShop.objects
        .filter(is_active=True)
        .annotate(
            catalog_count=Count('catalog_items', distinct=True),
            last_activity=Max('bills__bill_date'),
        )
        .order_by(F('last_activity').desc(nulls_last=True), 'name')
    )
    return render(request, 'inventory/suppliers/shop_list.html', {'shops': shops, 'is_active_list': True})

@office_required
def deactivated_supplier_shop_list(request):
    shops = (
        SupplierShop.objects
        .filter(is_active=False)
        .annotate(catalog_count=Count('catalog_items', distinct=True))
        .order_by('name')
    )
    return render(request, 'inventory/suppliers/shop_list.html', {'shops': shops, 'is_active_list': False})

def _supplier_name_clash(name, exclude_pk=None):
    """The shop already using `name`, ignoring case — or None.

    `SupplierShop.name` is `unique=True`, which in both PostgreSQL and SQLite is
    CASE-SENSITIVE, so the column happily holds "Kochi Auto" and "kochi auto" as
    two shops. That is the same defect the master lists carried until they were
    given `__iexact` checks (see CLAUDE.md), and it matters more here than a
    duplicate brand name would: two shops mean two ledgers, so half the bills
    for one supplier land against a name nobody is watching, and the balance the
    workshop reads as owed is wrong by whatever went to the other spelling.

    Archived shops are included on purpose. An archived shop still holds its
    bills and payments and can be reactivated, so letting a second shop take its
    name would produce exactly the split ledger this exists to prevent — and the
    unique constraint would refuse the insert anyway, which is how this whole
    view used to 500.
    """
    match = SupplierShop.objects.filter(name__iexact=name)
    if exclude_pk is not None:
        match = match.exclude(pk=exclude_pk)
    return match.first()


def _duplicate_message(existing):
    """Why the name was refused — including whether the holder is archived."""
    if existing.is_active:
        return f"A shop named '{existing.name}' already exists."
    # Without this the message names a shop that is on no page the reader can
    # see, since supplier_shop_list filters is_active=True.
    return (
        f"'{existing.name}' already exists but is archived. Reactivate it from "
        f"Supplies Shops → Deactivated rather than creating a second one — the "
        f"bills and payments already recorded against it belong to that shop."
    )


@office_required
def add_supplier_shop(request):
    """
    Create a Supplies Shop.

    AUD-0089. This used to `create()` inside `try/except IntegrityError`, which
    is the documented Django trap: a failed statement leaves the transaction
    unusable, so catching the error is not enough — the very next query raises
    `TransactionManagementError`. The `except` branch here ran `messages.error`
    and then `render()`, both of which query, so a duplicate shop name produced
    a 500 *after* being caught, and the tidy "already exists" message was never
    seen by anyone. It happened 40 times.

    Checking first is the fix, and it is what the equivalent spare-shop view
    (`workshop.views.spare_shop.spare_shop_create`) has always done. The
    `IntegrityError` backstop is kept for the race between the check and the
    insert, but it is now wrapped in `transaction.atomic()` so what breaks is a
    savepoint and not the request.
    """
    name = (request.POST.get('name') or '').strip()
    phone = (request.POST.get('phone') or '').strip()
    address = (request.POST.get('address') or '').strip()

    if request.method == 'POST':
        if not name:
            messages.error(request, "Shop name cannot be empty.")
        else:
            existing = _supplier_name_clash(name)
            if existing:
                messages.error(request, _duplicate_message(existing))
            else:
                try:
                    with transaction.atomic():
                        SupplierShop.objects.create(
                            name=name, phone=phone or None, address=address or None
                        )
                except IntegrityError:
                    messages.error(request, f"A shop named '{name}' already exists.")
                else:
                    messages.success(request, f"Shop '{name}' added successfully.")
                    return redirect('supplier_shop_list')

    # Hand back what was typed. The form used to re-render empty, so a rejected
    # name cost the phone number and address as well.
    return render(request, 'inventory/suppliers/add_shop.html', {
        'name': name, 'phone': phone, 'address': address,
    })

@office_required
def supplier_shop_detail(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    # select_related on catalog items avoids per-item queries into Item and Category
    catalog = shop.catalog_items.select_related('item', 'item__category').all()

    # ── Time-range filter — calendar-aligned, consistent with other sections ──
    filter_type = request.GET.get('filter', 'this_year')
    start_date_str = request.GET.get('start_date', '')
    end_date_str = request.GET.get('end_date', '')
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'

    older_bills_sum_sq = SupplierRestockBill.objects.filter(
        supplier=OuterRef('supplier')
    ).filter(
        Q(bill_date__lt=OuterRef('bill_date')) |
        Q(bill_date=OuterRef('bill_date'), id__lte=OuterRef('id'))
    ).values('supplier').annotate(
        total=Sum(F('total_amount') - F('discount_amount'))
    ).values('total')

    bills_qs = (
        shop.bills
        .prefetch_related('items__item')
        .annotate(
            absolute_running_sum=Coalesce(Subquery(older_bills_sum_sq), Decimal('0'), output_field=DecimalField())
        )
        .order_by('-bill_date', '-id')
    )
    payments_qs = shop.payments.filter(is_trashed=False).order_by('-date', '-id')

    if filter_type == 'today':
        bills_qs    = bills_qs.filter(bill_date=today)
        payments_qs = payments_qs.filter(date=today)

    elif filter_type == 'this_week':
        start = today - timedelta(days=today.weekday())
        bills_qs    = bills_qs.filter(bill_date__gte=start)
        payments_qs = payments_qs.filter(date__gte=start)

    elif filter_type == 'this_month':
        start = today.replace(day=1)
        bills_qs    = bills_qs.filter(bill_date__gte=start)
        payments_qs = payments_qs.filter(date__gte=start)

    elif filter_type == 'this_year':
        start = today.replace(month=1, day=1)
        bills_qs    = bills_qs.filter(bill_date__gte=start)
        payments_qs = payments_qs.filter(date__gte=start)

    elif filter_type == 'last_week':
        start = today - timedelta(days=today.weekday() + 7)
        end   = start + timedelta(days=6)
        bills_qs    = bills_qs.filter(bill_date__gte=start, bill_date__lte=end)
        payments_qs = payments_qs.filter(date__gte=start, date__lte=end)

    elif filter_type == 'last_month':
        first_of_this = today.replace(day=1)
        last_of_last  = first_of_this - timedelta(days=1)
        first_of_last = last_of_last.replace(day=1)
        bills_qs    = bills_qs.filter(bill_date__gte=first_of_last, bill_date__lte=last_of_last)
        payments_qs = payments_qs.filter(date__gte=first_of_last, date__lte=last_of_last)

    elif filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1,  day=1)
        end   = today.replace(year=today.year - 1, month=12, day=31)
        bills_qs    = bills_qs.filter(bill_date__gte=start, bill_date__lte=end)
        payments_qs = payments_qs.filter(date__gte=start, date__lte=end)

    elif filter_type == 'custom' and start_date_str and end_date_str:
        try:
            start_dt = date.fromisoformat(start_date_str)
            end_dt   = date.fromisoformat(end_date_str)
            bills_qs    = bills_qs.filter(bill_date__range=(start_dt, end_dt))
            payments_qs = payments_qs.filter(date__range=(start_dt, end_dt))
        except ValueError:
            pass
    # any other value (incl. legacy 'all') → no date filter applied

    bills_count = bills_qs.count()
    payments_count = payments_qs.count()

    # Slice directly via SQL, avoiding O(N) memory leak
    bills_list = list(bills_qs[:50])
    payments_list = list(payments_qs[:30])

    # ── Absolute Ledger Waterfall Calculation ──
    total_paid = shop.total_paid_amount
    for bill in bills_list:
        effective_amt = bill.get_effective_amount
        older_sum = bill.absolute_running_sum - effective_amt
        bulk_pool = total_paid - older_sum
        
        if bulk_pool >= effective_amt:
            bill.covered_status = 'COVERED'
            bill.pending_amount = Decimal('0')
        elif bulk_pool <= Decimal('0'):
            bill.covered_status = 'UNPAID'
            bill.pending_amount = effective_amt
        else:
            bill.covered_status = 'PARTIAL'
            bill.pending_amount = effective_amt - bulk_pool
            bill.covered_amount = bulk_pool

    return render(request, 'inventory/suppliers/shop_detail.html', {
        'shop': shop,
        'catalog': catalog,
        'bills': bills_list,
        'bills_count': bills_count,
        'recent_payments': payments_list,
        'payments_count': payments_count,
        'filter_type': filter_type,
        'start_date': start_date_str,
        'end_date': end_date_str,
    })


@office_required
def edit_supplier_shop(request, shop_id):
    """
    Rename a Supplies Shop, or correct its phone / address.

    Same AUD-0089 shape as `add_supplier_shop` above — renaming onto an existing
    name broke the transaction and 500'd the page — with two more holes closed
    while here: a POST without a `name` field wrote `None` into a NOT NULL
    column, and an untrimmed name meant " Kochi Auto " and "Kochi Auto" were two
    shops that looked identical on screen.

    A rename is deliberately REFUSED on a collision rather than merged. The
    master lists merge, because two spellings of "Oil Filter" are one part; two
    Supplies Shops are two accounts with two balances and two payment histories,
    and combining them would move money between suppliers with no way back.
    """
    shop = get_object_or_404(SupplierShop, pk=shop_id)

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        phone = (request.POST.get('phone') or '').strip()
        address = (request.POST.get('address') or '').strip()

        if not name:
            messages.error(request, "Shop name cannot be empty.")
        else:
            existing = _supplier_name_clash(name, exclude_pk=shop.pk)
            if existing:
                messages.error(request, _duplicate_message(existing))
            else:
                shop.name = name
                shop.phone = phone or None
                shop.address = address or None
                try:
                    with transaction.atomic():
                        shop.save()
                except IntegrityError:
                    messages.error(request, f"A shop named '{name}' already exists.")
                else:
                    messages.success(request, f"Shop '{shop.name}' updated.")
                    return redirect('supplier_shop_detail', shop_id=shop.id)

        # Falling through re-renders the form. `shop` still carries what was
        # typed, which is what the reader wants to correct — and it was never
        # saved, so nothing is persisted by showing it.
        shop.name = name or shop.name

    return render(request, 'inventory/suppliers/edit_shop.html', {'shop': shop})

@office_required
def deactivate_supplier_shop(request, shop_id):
    if request.method == 'POST':
        shop = get_object_or_404(SupplierShop, pk=shop_id)
        shop.is_active = False
        shop.save()
        notify(
            'ACCOUNT_ARCHIVED',
            f"Supplies Shop '{shop.name}' was archived. Reactivate it from "
            f"Supplies Shops → Deactivated.",
            actor=request.user,
            # The DEACTIVATED list, not `supplier_shop_list` — that one filters
            # `is_active=True`, so it is the one page guaranteed NOT to contain
            # the shop this notification is about. An owner tapped "Shop X was
            # archived" and landed on a list without X in it, with nothing to
            # press. Exactly the ACCOUNT_LOCKED defect recorded in CLAUDE.md,
            # committed a second time. The spare-shop and fleet versions of this
            # same event already point at their archived lists.
            url=reverse('deactivated_supplier_shop_list'),
            object_type='SUPPLIER_SHOP', object_id=shop.pk,
        )
        messages.success(request, f"Shop '{shop.name}' deactivated.")
    return redirect('supplier_shop_list')

@office_required
def activate_supplier_shop(request, shop_id):
    if request.method == 'POST':
        shop = get_object_or_404(SupplierShop, pk=shop_id)
        shop.is_active = True
        shop.save()
        messages.success(request, f"Shop '{shop.name}' activated.")
    return redirect('deactivated_supplier_shop_list')

@office_required
def add_shop_catalog_item(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    
    if request.method == 'POST':
        item_name = request.POST.get('item_name', '').strip()
        category_name = request.POST.get('category_name', '').strip()
        avg_stock = _dec(request.POST.get('average_stock'))
        confirm_existing = request.POST.get('confirm_existing') == '1'

        if item_name and category_name:
            # Check if item exists globally (case-insensitive)
            existing_item = Item.objects.filter(name__iexact=item_name).first()
            
            if existing_item:
                # Check if it's already in THIS shop
                if ShopCatalogItem.objects.filter(shop=shop, item=existing_item).exists():
                    messages.error(request, f"'{existing_item.name}' is already in this shop's catalog.")
                    return redirect('add_shop_catalog_item', shop_id=shop.id)
                
                # If it exists globally but NOT in this shop, ask for confirmation.
                # Linking is deliberate: one product = one Item, stocked by many shops.
                if not confirm_existing:
                    categories = Category.objects.all().order_by('name')
                    return render(request, 'inventory/suppliers/add_catalog_item.html', {
                        'shop': shop,
                        'categories': categories,
                        'item_name': item_name,
                        'category_name': existing_item.category.name,
                        'average_stock': avg_stock,
                        'requires_confirmation': True,
                        'existing_item': existing_item
                    })
                else:
                    ShopCatalogItem.objects.create(shop=shop, item=existing_item)
                    # The Average Stock typed on the form is otherwise thrown away here.
                    # Only apply it when the shared product has none yet (legacy rows
                    # created before the threshold was required) — never silently
                    # overwrite a threshold another shop's screen already set.
                    if avg_stock > 0 and existing_item.average_stock <= 0:
                        existing_item.average_stock = avg_stock
                        existing_item.save(update_fields=['average_stock'])
                    messages.success(request, f"'{existing_item.name}' added to catalog.")
                    return redirect('supplier_shop_detail', shop_id=shop.id)
            else:
                # New product — Average Stock (how many are normally kept in stock) is
                # REQUIRED because Low Stock is computed as a fraction of it; at 0 the
                # product is filtered out of the alert list entirely and never warns.
                if avg_stock <= 0:
                    messages.error(request, "Average Stock is required and must be greater than 0.")
                    categories = Category.objects.all().order_by('name')
                    return render(request, 'inventory/suppliers/add_catalog_item.html', {
                        'shop': shop,
                        'categories': categories,
                        'item_name': item_name,
                        'category_name': category_name,
                        'average_stock': avg_stock,
                    })
                # Item doesn't exist at all. Create category if needed, then item.
                # Atomic so a product can never be left with no catalog link — an
                # Item with no shop has no edit/remove path anywhere in the UI.
                with transaction.atomic():
                    category, _ = Category.objects.get_or_create(name__iexact=category_name, defaults={'name': category_name})
                    new_item = Item.objects.create(category=category, name=item_name, average_stock=avg_stock)
                    ShopCatalogItem.objects.create(shop=shop, item=new_item)
                messages.success(request, f"New item '{new_item.name}' created and added to catalog.")
                return redirect('supplier_shop_detail', shop_id=shop.id)
                
    categories = Category.objects.all().order_by('name')
    return render(request, 'inventory/suppliers/add_catalog_item.html', {
        'shop': shop,
        'categories': categories
    })

@office_required
@transaction.atomic
def remove_shop_catalog_item(request, shop_id, catalog_item_id):
    """
    Remove a product from this shop's catalog.

    Two safety guards, both of which DEACTIVATE instead of removing:
      1. Purchase history — this shop has restock bills for the product, so
         unlinking it would alter historical bill records.
      2. Stock on hand — the product still holds warehouse stock (in either
         direction; a negative balance is an overdraw awaiting a supplier bill,
         not an empty shelf). Stock can only be changed by signals, so deleting
         the Item here would silently destroy a real, countable quantity.

    Only when neither applies is the catalog link removed. If that leaves the Item
    orphaned — no shop carries it, no bill history, and no job card records having
    used it — the Item itself is permanently deleted, and like every other
    permanent delete a snapshot is written to the Owner-only Deletion History
    first.
    """
    if request.method == 'POST':
        catalog_item = get_object_or_404(ShopCatalogItem, pk=catalog_item_id, shop_id=shop_id)
        item = catalog_item.item
        name = item.name

        shop_has_history = SupplierRestockItem.objects.filter(
            item=item, bill__supplier_id=shop_id
        ).exists()
        if shop_has_history:
            catalog_item.is_active = False
            catalog_item.save(update_fields=['is_active'])
            messages.info(request, f"'{name}' has purchase history from this shop, so it was deactivated instead of removed. Reactivate it any time.")
            return redirect('supplier_shop_detail', shop_id=shop_id)

        if item.current_stock != 0:
            catalog_item.is_active = False
            catalog_item.save(update_fields=['is_active'])
            messages.info(
                request,
                f"'{name}' still has {clean_qty(item.current_stock)} in stock, so it was deactivated "
                f"instead of removed. It can be removed once the stock reaches zero."
            )
            return redirect('supplier_shop_detail', shop_id=shop_id)

        shop_name = catalog_item.shop.name
        catalog_item.delete()
        # A product named on a job card must outlive its catalog links. The
        # `JobCardSpareItem.item` FK is PROTECT, so deleting the Item here would
        # raise ProtectedError — and that history is the whole reason the FK is
        # PROTECT. The stock guard above catches most of this (a drawn product is
        # rarely at exactly zero), but not all of it: edit a draw's quantity back
        # down to zero and the stock returns to 0 while the row still points here.
        if not JobCardSpareItem.objects.filter(item=item).exists() and \
           not ShopCatalogItem.objects.filter(item=item).exists() and \
           not SupplierRestockItem.objects.filter(item=item).exists():
            DeletionLog.record(
                DeletionLog.ENTITY_INVENTORY_ITEM, item,
                user=request.user,
                label=f"{item.category.name} · {name}",
                extra={'removed_from_shop': shop_name},
            )
            item.delete()
            messages.success(request, f"'{name}' removed and deleted from inventory (logged to Deletion History).")
        else:
            messages.success(request, f"'{name}' removed from this shop's catalog.")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@office_required
def deactivate_catalog_item(request, shop_id, catalog_item_id):
    """Deactivate a catalog entry: stays listed (greyed) but excluded from restock bills."""
    if request.method == 'POST':
        ci = get_object_or_404(ShopCatalogItem, pk=catalog_item_id, shop_id=shop_id)
        ci.is_active = False
        ci.save(update_fields=['is_active'])
        messages.success(request, f"'{ci.item.name}' deactivated in this shop's catalog.")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@office_required
def reactivate_catalog_item(request, shop_id, catalog_item_id):
    """Reactivate a deactivated catalog entry."""
    if request.method == 'POST':
        ci = get_object_or_404(ShopCatalogItem, pk=catalog_item_id, shop_id=shop_id)
        ci.is_active = True
        ci.save(update_fields=['is_active'])
        messages.success(request, f"'{ci.item.name}' reactivated.")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@office_required
def edit_catalog_item(request, shop_id, catalog_item_id):
    """Edit the shared product's name + Average Stock (usual quantity kept in stock).

    `Item` is unique on (category, name), so a rename can collide with another
    product in the same category. Since this is now the only way to correct a
    product's name, that collision is reported as a message — never as a 500.
    """
    catalog_item = get_object_or_404(ShopCatalogItem, pk=catalog_item_id, shop_id=shop_id)
    if request.method == 'POST':
        new_name = request.POST.get('item_name', '').strip()
        avg_stock = _dec(request.POST.get('average_stock'))
        item = catalog_item.item

        if new_name and new_name.lower() != item.name.lower():
            clash = Item.objects.filter(
                category=item.category, name__iexact=new_name
            ).exclude(pk=item.pk).exists()
            if clash:
                messages.error(
                    request,
                    f"'{new_name}' already exists in {item.category.name}. "
                    f"Pick a different name, or add that existing product to this shop instead."
                )
                return redirect('supplier_shop_detail', shop_id=shop_id)

        if new_name:
            item.name = new_name
        if avg_stock > 0:
            item.average_stock = avg_stock
        try:
            item.save()
        except IntegrityError:
            # Backstop for a race between the check above and the save.
            messages.error(request, f"Couldn't rename to '{new_name}' — that name is already taken.")
            return redirect('supplier_shop_detail', shop_id=shop_id)
        messages.success(request, f"'{item.name}' updated.")
    return redirect('supplier_shop_detail', shop_id=shop_id)


@office_required
def shop_catalog_item_detail(request, shop_id, catalog_item_id):
    """
    Read-only AJAX fragment for a catalog card's click-to-expand modal.

    Shows what this shop specifically has sold the workshop of this product
    (`bill__supplier=shop` scoped — `Item` is shared across shops, so an
    unscoped total would double-count purchases made from other shops),
    plus which other active shops also carry it and the combined total
    across all of them (that one is deliberately unscoped — money already
    spent doesn't un-spend itself just because a shop later goes inactive).
    """
    catalog_item = get_object_or_404(ShopCatalogItem, pk=catalog_item_id, shop_id=shop_id)
    item = catalog_item.item

    this_shop = SupplierRestockItem.objects.filter(
        bill__supplier_id=shop_id, item=item
    ).aggregate(
        qty=Coalesce(Sum('quantity'), Decimal('0'), output_field=DecimalField()),
        spent=Coalesce(Sum('total_price'), Decimal('0'), output_field=DecimalField()),
    )

    combined = SupplierRestockItem.objects.filter(item=item).aggregate(
        qty=Coalesce(Sum('quantity'), Decimal('0'), output_field=DecimalField()),
        spent=Coalesce(Sum('total_price'), Decimal('0'), output_field=DecimalField()),
    )

    other_shops = (
        SupplierShop.objects
        .filter(catalog_items__item=item, is_active=True)
        .exclude(pk=shop_id)
        .values_list('name', flat=True)
        .distinct()
        .order_by('name')
    )

    return render(request, 'inventory/suppliers/partials/catalog_item_detail.html', {
        'item': item,
        'this_shop_qty': this_shop['qty'],
        'this_shop_spent': this_shop['spent'],
        'combined_qty': combined['qty'],
        'combined_spent': combined['spent'],
        'other_shops': other_shops,
    })


@office_required
@transaction.atomic
def update_bill_discount(request, shop_id, bill_id):
    """Update the discount amount on an existing bill."""
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    bill = get_object_or_404(SupplierRestockBill, pk=bill_id, supplier=shop)
    if request.method == 'POST':
        discount = request.POST.get('discount_amount', '0')
        try:
            discount = Decimal(str(discount).strip())
            if discount >= 0:
                SupplierRestockBill.objects.filter(pk=bill.pk).update(discount_amount=discount)
                shop.update_totals()
                messages.success(request, f"Discount updated for Bill #{bill.id}.")
        except (ValueError, InvalidOperation):
            messages.error(request, "Invalid discount amount.")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@office_required
def shop_restock_select(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    catalog = shop.catalog_items.filter(is_active=True).select_related('item', 'item__category').all()
    
    if request.method == 'POST':
        selected_item_ids = request.POST.getlist('selected_items')
        if not selected_item_ids:
            messages.error(request, "Please select at least one item to restock.")
            return redirect('shop_restock_select', shop_id=shop.id)
            
        # Store in session and redirect to bill entry
        request.session['restock_items'] = selected_item_ids
        return redirect('shop_restock_bill', shop_id=shop.id)
        
    return render(request, 'inventory/suppliers/restock_select.html', {'shop': shop, 'catalog': catalog})

def _reject_impossible_discount(request, bill):
    """A discount larger than the bill it sits on is always a typo.

    Left alone it produced a NEGATIVE bill: the supplier appeared to owe the
    workshop money, and the Supplies Shops expense went negative, *raising*
    reported profit. One mistyped extra zero was enough. The discount is dropped
    rather than clamped, and said out loud, so nobody has to guess which number
    the system decided to keep. Returns True if it rejected something.

    Runs only after `bill.update_totals()` — at creation the bill exists before
    its lines do, so there is no total to compare against until then.
    """
    if bill.discount_amount > bill.total_amount:
        attempted = bill.discount_amount
        bill.discount_amount = Decimal('0')
        bill.save(update_fields=['discount_amount'])
        bill.supplier.update_totals()
        messages.error(
            request,
            f"Discount ₹{attempted:,.2f} is more than the bill total "
            f"₹{bill.total_amount:,.2f}, so it was NOT applied. "
            f"Edit the bill and enter the correct discount."
        )
        return True
    return False


@office_required
@transaction.atomic
def shop_restock_bill(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    selected_item_ids = request.session.get('restock_items', [])
    
    if not selected_item_ids:
        messages.error(request, "No items selected.")
        return redirect('shop_restock_select', shop_id=shop.id)

    # Re-validate against the shop's ACTIVE catalog on every request. The picker
    # already filters, but the ids arrive via the session and can be stale (e.g. the
    # product was deactivated in another tab between selecting and billing).
    items = _active_catalog_items(shop, selected_item_ids)
    if not items:
        messages.error(request, "Those products are no longer in this shop's active catalog.")
        request.session.pop('restock_items', None)
        return redirect('shop_restock_select', shop_id=shop.id)

    dropped = len(set(selected_item_ids)) - len(items)
    if dropped > 0:
        # Never let a product disappear off the bill silently.
        messages.warning(
            request,
            f"{dropped} selected product(s) are no longer in this shop's active catalog "
            f"and have been left off this bill."
        )

    if request.method == 'POST':
        try:
            discount = _dec(request.POST.get('discount_amount'))

            bill = SupplierRestockBill.objects.create(
                supplier=shop,
                discount_amount=discount
            )

            for item in items:
                qty = _dec(request.POST.get(f'qty_{item.id}'))
                price = _dec(request.POST.get(f'price_{item.id}'))
                if qty > 0:
                    SupplierRestockItem.objects.create(
                        bill=bill,
                        item=item,
                        quantity=qty,
                        total_price=price
                    )
                    
            # Update bill totals which will trigger shop total update
            bill.update_totals()
            bill.refresh_from_db()
            _reject_impossible_discount(request, bill)

            # Clear session
            if 'restock_items' in request.session:
                del request.session['restock_items']
                
            messages.success(request, "Restock bill created successfully.")
            return redirect('supplier_shop_detail', shop_id=shop.id)
        except ValueError:
            messages.error(request, "Invalid number entered for quantity, price, or discount.")
            return redirect('shop_restock_bill', shop_id=shop.id)
        
    return render(request, 'inventory/suppliers/restock_bill.html', {'shop': shop, 'items': items})

@office_required
@transaction.atomic
def edit_restock_bill(request, shop_id, bill_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    bill = get_object_or_404(SupplierRestockBill, pk=bill_id, supplier=shop)
    
    if request.method == 'POST':
        try:
            # 1. Update bill-level info
            bill_date_str = request.POST.get('bill_date')
            if bill_date_str:
                bill.bill_date = bill_date_str
                
            discount = _dec(request.POST.get('discount_amount'))
            bill.discount_amount = discount
            bill.save()
            
            # 2. Update existing items
            for restock_item in bill.items.all():
                qty_str = request.POST.get(f'qty_{restock_item.id}')
                price_str = request.POST.get(f'price_{restock_item.id}')
                
                if qty_str is not None:
                    qty = _dec(qty_str)
                    price = _dec(price_str)
                    if qty <= 0:
                        restock_item.delete()
                    else:
                        restock_item.quantity = qty
                        restock_item.total_price = price
                        restock_item.save()
                        
            # 3. Add any new items selected from the catalog.
            # Same server-side guard as shop_restock_bill: only products this shop
            # actively stocks may be added. (Lines already on the bill are edited
            # above and are deliberately left alone even if since deactivated.)
            new_item_ids = request.POST.getlist('new_items')
            if new_item_ids:
                items_to_add = _active_catalog_items(shop, new_item_ids)
                for new_item in items_to_add:
                    qty_str = request.POST.get(f'new_qty_{new_item.id}')
                    price_str = request.POST.get(f'new_price_{new_item.id}')
                    if qty_str:
                        qty = _dec(qty_str)
                        price = _dec(price_str)
                        if qty > 0:
                            SupplierRestockItem.objects.create(
                                bill=bill,
                                item=new_item,
                                quantity=qty,
                                total_price=price
                            )

            # Trigger total update
            bill.update_totals()
            bill.refresh_from_db()
            if _reject_impossible_discount(request, bill):
                return redirect('edit_restock_bill', shop_id=shop.id, bill_id=bill.id)
            messages.success(request, f"Bill #{bill.id} updated successfully.")
            return redirect('supplier_shop_detail', shop_id=shop.id)
        except ValueError:
            messages.error(request, "Invalid number entered for quantity, price, or discount.")
            return redirect('edit_restock_bill', shop_id=shop.id, bill_id=bill.id)
        
    # Get items currently in the bill
    existing_items = bill.items.select_related('item', 'item__category').all()
    existing_item_ids = [ei.item.id for ei in existing_items]
    
    # Get ACTIVE catalog items NOT in the bill for the "Add new items" section
    available_catalog_items = shop.catalog_items.filter(is_active=True).exclude(item_id__in=existing_item_ids).select_related('item', 'item__category').all()
    
    return render(request, 'inventory/suppliers/restock_bill_edit.html', {
        'shop': shop,
        'bill': bill,
        'existing_items': existing_items,
        'available_catalog_items': available_catalog_items
    })

@office_required
@transaction.atomic
def delete_restock_bill(request, shop_id, bill_id):
    if request.method == 'POST':
        bill = get_object_or_404(SupplierRestockBill, pk=bill_id, supplier_id=shop_id)
        reason = request.POST.get('reason', '').strip()
        DeletionLog.record(
            DeletionLog.ENTITY_RESTOCK_BILL, bill,
            user=request.user, reason=reason, amount=bill.total_amount,
            label=f"Restock Bill #{bill.id} · {bill.supplier.name} · ₹{bill.total_amount:,.0f}",
            extra={'items': [
                {'item': i.item.name, 'qty': str(i.quantity), 'total': str(i.total_price)}
                for i in bill.items.select_related('item').all()
            ]},
        )
        bill.delete()
        messages.success(request, "Bill permanently deleted and stock reversed (logged to Deletion History).")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@office_required
@transaction.atomic
def add_shop_payment(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    
    if request.method == 'POST':
        # workshop/money.py, as everywhere else a rupee amount is typed. The old
        # `amount > 0` guard let 'Infinity' straight through into the ledger,
        # raised decimal.InvalidOperation on 'NaN' (an ordered comparison
        # against NaN raises, and InvalidOperation is not in the except clause),
        # and passed an 11-digit fat finger to a numeric(12,2) column, which
        # Postgres refuses with an overflow — a 500 instead of a message.
        amount = parse_money(request.POST.get('amount') or '0', SupplierPayment, 'amount')
        if amount is None:
            messages.error(request, "Invalid payment amount.")
        else:
            SupplierPayment.objects.create(
                supplier=shop,
                amount=amount,
                payment_method=request.POST.get('payment_method'),
                note=fit_text(request.POST.get('note'), SupplierPayment, 'note'),
            )
            messages.success(request, "Payment recorded successfully.")
            return redirect('supplier_shop_detail', shop_id=shop.id)
            
    return render(request, 'inventory/suppliers/add_payment.html', {'shop': shop})

@office_required
@transaction.atomic
def delete_shop_payment(request, shop_id, payment_id):
    if request.method == 'POST':
        payment = get_object_or_404(SupplierPayment, pk=payment_id, supplier_id=shop_id)
        reason = request.POST.get('reason', '').strip()
        amount = payment.amount
        DeletionLog.record(
            DeletionLog.ENTITY_SUPPLIER_PAYMENT, payment,
            user=request.user, reason=reason, amount=amount,
            label=f"₹{amount:,.0f} → {payment.supplier.name}",
        )
        payment.delete()  # SupplierPayment.delete() recomputes supplier.update_totals()
        messages.success(request, f"Payment of ₹{amount:,.0f} permanently deleted (logged to Deletion History).")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@office_required
def inventory_item_suppliers(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    catalogs = item.shop_catalogs.select_related('shop').filter(shop__is_active=True)
    
    return render(request, 'inventory/suppliers/item_suppliers.html', {
        'item': item,
        'catalogs': catalogs
    })

@office_required
def ajax_supplier_bills(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    page = int(request.GET.get('page', 1))
    filter_type = request.GET.get('filter', 'all')
    
    older_bills_sum_sq = SupplierRestockBill.objects.filter(
        supplier=OuterRef('supplier')
    ).filter(
        Q(bill_date__lt=OuterRef('bill_date')) | 
        Q(bill_date=OuterRef('bill_date'), id__lte=OuterRef('id'))
    ).values('supplier').annotate(
        total=Sum(F('total_amount') - F('discount_amount'))
    ).values('total')

    bills_qs = shop.bills.prefetch_related('items__item').annotate(
        absolute_running_sum=Coalesce(Subquery(older_bills_sum_sq), Decimal('0'), output_field=DecimalField())
    ).order_by('-bill_date', '-id')

    # Quick filters — calendar-aligned
    filter_today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
    if filter_type == 'today':
        bills_qs = bills_qs.filter(bill_date=filter_today)
    elif filter_type == 'this_week':
        start = filter_today - timedelta(days=filter_today.weekday())
        bills_qs = bills_qs.filter(bill_date__gte=start)
    elif filter_type == 'this_month':
        bills_qs = bills_qs.filter(bill_date__gte=filter_today.replace(day=1))
    elif filter_type == 'this_year':
        bills_qs = bills_qs.filter(bill_date__gte=filter_today.replace(month=1, day=1))
    elif filter_type == 'last_week':
        start = filter_today - timedelta(days=filter_today.weekday() + 7)
        bills_qs = bills_qs.filter(bill_date__gte=start, bill_date__lte=start + timedelta(days=6))
    elif filter_type == 'last_month':
        first_of_this = filter_today.replace(day=1)
        last_of_last  = first_of_this - timedelta(days=1)
        bills_qs = bills_qs.filter(bill_date__gte=last_of_last.replace(day=1), bill_date__lte=last_of_last)
    elif filter_type == 'last_year':
        start = filter_today.replace(year=filter_today.year - 1, month=1,  day=1)
        end   = filter_today.replace(year=filter_today.year - 1, month=12, day=31)
        bills_qs = bills_qs.filter(bill_date__gte=start, bill_date__lte=end)

    # Slice directly via SQL for the requested page (50 per page)
    start = (page - 1) * 50
    end = page * 50
    page_bills = list(bills_qs[start:end])

    # ── Absolute Ledger Waterfall Calculation ──
    total_paid = shop.total_paid_amount
    for bill in page_bills:
        effective_amt = bill.get_effective_amount
        older_sum = bill.absolute_running_sum - effective_amt
        bulk_pool = total_paid - older_sum
        
        if bulk_pool >= effective_amt:
            bill.covered_status = 'COVERED'
            bill.pending_amount = Decimal('0')
        elif bulk_pool <= Decimal('0'):
            bill.covered_status = 'UNPAID'
            bill.pending_amount = effective_amt
        else:
            bill.covered_status = 'PARTIAL'
            bill.pending_amount = effective_amt - bulk_pool
            bill.covered_amount = bulk_pool

    return render(request, 'inventory/suppliers/partials/bill_list_chunk.html', {
        'shop': shop,
        'bills': page_bills
    })

@office_required
def ajax_supplier_payments(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    page = int(request.GET.get('page', 1))
    
    payments = shop.payments.filter(is_trashed=False).order_by('-date', '-id')
    
    # Slice for requested page (30 per page)
    start = (page - 1) * 30
    end = page * 30
    page_payments = payments[start:end]

    return render(request, 'inventory/suppliers/partials/payment_list_chunk.html', {
        'shop': shop,
        'recent_payments': page_payments
    })
