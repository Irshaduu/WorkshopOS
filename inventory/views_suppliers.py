from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Count, Prefetch, Sum, F, OuterRef, Subquery, Q, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal
from datetime import timedelta, date
from .models import Item, Category, SupplierShop, ShopCatalogItem, SupplierRestockBill, SupplierRestockItem, SupplierPayment
from workshop.decorators import staff_required
from django.db import transaction, IntegrityError

@staff_required
def supplier_shop_list(request):
    # Annotate catalog_count in SQL — avoids N+1 per-card `.count()` call in template
    shops = (
        SupplierShop.objects
        .filter(is_active=True)
        .annotate(catalog_count=Count('catalog_items', distinct=True))
        .order_by('name')
    )
    return render(request, 'inventory/suppliers/shop_list.html', {'shops': shops, 'is_active_list': True})

@staff_required
def deactivated_supplier_shop_list(request):
    shops = (
        SupplierShop.objects
        .filter(is_active=False)
        .annotate(catalog_count=Count('catalog_items', distinct=True))
        .order_by('name')
    )
    return render(request, 'inventory/suppliers/shop_list.html', {'shops': shops, 'is_active_list': False})

@staff_required
def add_supplier_shop(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone')
        address = request.POST.get('address')
        if name:
            try:
                SupplierShop.objects.create(name=name, phone=phone, address=address)
                messages.success(request, f"Shop '{name}' added successfully.")
                return redirect('supplier_shop_list')
            except IntegrityError:
                messages.error(request, f"A shop with the name '{name}' already exists.")
    return render(request, 'inventory/suppliers/add_shop.html')

@staff_required
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


@staff_required
def edit_supplier_shop(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    if request.method == 'POST':
        shop.name = request.POST.get('name')
        shop.phone = request.POST.get('phone')
        shop.address = request.POST.get('address')
        try:
            shop.save()
            messages.success(request, f"Shop '{shop.name}' updated.")
            return redirect('supplier_shop_detail', shop_id=shop.id)
        except IntegrityError:
            messages.error(request, f"A shop with the name '{shop.name}' already exists.")
    return render(request, 'inventory/suppliers/edit_shop.html', {'shop': shop})

@staff_required
def deactivate_supplier_shop(request, shop_id):
    if request.method == 'POST':
        shop = get_object_or_404(SupplierShop, pk=shop_id)
        shop.is_active = False
        shop.save()
        messages.success(request, f"Shop '{shop.name}' deactivated.")
    return redirect('supplier_shop_list')

@staff_required
def activate_supplier_shop(request, shop_id):
    if request.method == 'POST':
        shop = get_object_or_404(SupplierShop, pk=shop_id)
        shop.is_active = True
        shop.save()
        messages.success(request, f"Shop '{shop.name}' activated.")
    return redirect('deactivated_supplier_shop_list')

@staff_required
def add_shop_catalog_item(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    
    if request.method == 'POST':
        item_name = request.POST.get('item_name', '').strip()
        category_name = request.POST.get('category_name', '').strip()
        confirm_existing = request.POST.get('confirm_existing') == '1'
        
        if item_name and category_name:
            # Check if item exists globally (case-insensitive)
            existing_item = Item.objects.filter(name__iexact=item_name).first()
            
            if existing_item:
                # Check if it's already in THIS shop
                if ShopCatalogItem.objects.filter(shop=shop, item=existing_item).exists():
                    messages.error(request, f"'{existing_item.name}' is already in this shop's catalog.")
                    return redirect('add_shop_catalog_item', shop_id=shop.id)
                
                # If it exists globally but NOT in this shop, ask for confirmation
                if not confirm_existing:
                    categories = Category.objects.all().order_by('name')
                    return render(request, 'inventory/suppliers/add_catalog_item.html', {
                        'shop': shop,
                        'categories': categories,
                        'item_name': item_name,
                        'category_name': existing_item.category.name,
                        'requires_confirmation': True,
                        'existing_item': existing_item
                    })
                else:
                    ShopCatalogItem.objects.create(shop=shop, item=existing_item)
                    messages.success(request, f"'{existing_item.name}' added to catalog.")
                    return redirect('supplier_shop_detail', shop_id=shop.id)
            else:
                # Item doesn't exist at all. Create category if needed, then item.
                category, _ = Category.objects.get_or_create(name__iexact=category_name, defaults={'name': category_name})
                new_item = Item.objects.create(category=category, name=item_name)
                ShopCatalogItem.objects.create(shop=shop, item=new_item)
                messages.success(request, f"New item '{new_item.name}' created and added to catalog.")
                return redirect('supplier_shop_detail', shop_id=shop.id)
                
    categories = Category.objects.all().order_by('name')
    return render(request, 'inventory/suppliers/add_catalog_item.html', {
        'shop': shop,
        'categories': categories
    })

@staff_required
def remove_shop_catalog_item(request, shop_id, catalog_item_id):
    if request.method == 'POST':
        catalog_item = get_object_or_404(ShopCatalogItem, pk=catalog_item_id, shop_id=shop_id)
        name = catalog_item.item.name
        catalog_item.delete()
        messages.success(request, f"'{name}' removed from catalog.")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@staff_required
def edit_catalog_item(request, shop_id, catalog_item_id):
    """Rename the item linked to this catalog entry."""
    catalog_item = get_object_or_404(ShopCatalogItem, pk=catalog_item_id, shop_id=shop_id)
    if request.method == 'POST':
        new_name = request.POST.get('item_name', '').strip()
        if new_name:
            catalog_item.item.name = new_name
            catalog_item.item.save()
            messages.success(request, f"Item renamed to '{new_name}'.")
    return redirect('supplier_shop_detail', shop_id=shop_id)



@staff_required
@transaction.atomic
def update_bill_discount(request, shop_id, bill_id):
    """Update the discount amount on an existing bill."""
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    bill = get_object_or_404(SupplierRestockBill, pk=bill_id, supplier=shop)
    if request.method == 'POST':
        discount = request.POST.get('discount_amount', '0')
        try:
            discount = float(discount)
            if discount >= 0:
                SupplierRestockBill.objects.filter(pk=bill.pk).update(discount_amount=discount)
                shop.update_totals()
                messages.success(request, f"Discount updated for Bill #{bill.id}.")
        except ValueError:
            messages.error(request, "Invalid discount amount.")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@staff_required
def shop_restock_select(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    catalog = shop.catalog_items.select_related('item', 'item__category').all()
    
    if request.method == 'POST':
        selected_item_ids = request.POST.getlist('selected_items')
        if not selected_item_ids:
            messages.error(request, "Please select at least one item to restock.")
            return redirect('shop_restock_select', shop_id=shop.id)
            
        # Store in session and redirect to bill entry
        request.session['restock_items'] = selected_item_ids
        return redirect('shop_restock_bill', shop_id=shop.id)
        
    return render(request, 'inventory/suppliers/restock_select.html', {'shop': shop, 'catalog': catalog})

@staff_required
@transaction.atomic
def shop_restock_bill(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    selected_item_ids = request.session.get('restock_items', [])
    
    if not selected_item_ids:
        messages.error(request, "No items selected.")
        return redirect('shop_restock_select', shop_id=shop.id)
        
    items = Item.objects.filter(id__in=selected_item_ids)
    
    if request.method == 'POST':
        try:
            discount = float(request.POST.get('discount_amount') or 0)
            
            bill = SupplierRestockBill.objects.create(
                supplier=shop,
                discount_amount=discount
            )
            
            for item in items:
                qty = float(request.POST.get(f'qty_{item.id}') or 0)
                price = float(request.POST.get(f'price_{item.id}') or 0)
                if qty > 0:
                    SupplierRestockItem.objects.create(
                        bill=bill,
                        item=item,
                        quantity=qty,
                        total_price=price
                    )
                    
            # Update bill totals which will trigger shop total update
            bill.update_totals()
            
            # Clear session
            if 'restock_items' in request.session:
                del request.session['restock_items']
                
            messages.success(request, "Restock bill created successfully.")
            return redirect('supplier_shop_detail', shop_id=shop.id)
        except ValueError:
            messages.error(request, "Invalid number entered for quantity, price, or discount.")
            return redirect('shop_restock_bill', shop_id=shop.id)
        
    return render(request, 'inventory/suppliers/restock_bill.html', {'shop': shop, 'items': items})

@staff_required
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
                
            discount = float(request.POST.get('discount_amount') or 0)
            bill.discount_amount = discount
            bill.save()
            
            # 2. Update existing items
            for restock_item in bill.items.all():
                qty_str = request.POST.get(f'qty_{restock_item.id}')
                price_str = request.POST.get(f'price_{restock_item.id}')
                
                if qty_str is not None:
                    qty = float(qty_str)
                    price = float(price_str or 0)
                    if qty <= 0:
                        restock_item.delete()
                    else:
                        restock_item.quantity = qty
                        restock_item.total_price = price
                        restock_item.save()
                        
            # 3. Add any new items selected from the catalog
            new_item_ids = request.POST.getlist('new_items')
            if new_item_ids:
                items_to_add = Item.objects.filter(id__in=new_item_ids)
                for new_item in items_to_add:
                    qty_str = request.POST.get(f'new_qty_{new_item.id}')
                    price_str = request.POST.get(f'new_price_{new_item.id}')
                    if qty_str:
                        qty = float(qty_str)
                        price = float(price_str or 0)
                        if qty > 0:
                            SupplierRestockItem.objects.create(
                                bill=bill,
                                item=new_item,
                                quantity=qty,
                                total_price=price
                            )

            # Trigger total update
            bill.update_totals()
            messages.success(request, f"Bill #{bill.id} updated successfully.")
            return redirect('supplier_shop_detail', shop_id=shop.id)
        except ValueError:
            messages.error(request, "Invalid number entered for quantity, price, or discount.")
            return redirect('edit_restock_bill', shop_id=shop.id, bill_id=bill.id)
        
    # Get items currently in the bill
    existing_items = bill.items.select_related('item', 'item__category').all()
    existing_item_ids = [ei.item.id for ei in existing_items]
    
    # Get catalog items NOT in the bill for the "Add new items" section
    available_catalog_items = shop.catalog_items.exclude(item_id__in=existing_item_ids).select_related('item', 'item__category').all()
    
    return render(request, 'inventory/suppliers/restock_bill_edit.html', {
        'shop': shop,
        'bill': bill,
        'existing_items': existing_items,
        'available_catalog_items': available_catalog_items
    })

@staff_required
@transaction.atomic
def delete_restock_bill(request, shop_id, bill_id):
    if request.method == 'POST':
        bill = get_object_or_404(SupplierRestockBill, pk=bill_id, supplier_id=shop_id)
        bill.delete()
        messages.success(request, "Bill deleted and stock reversed.")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@staff_required
@transaction.atomic
def add_shop_payment(request, shop_id):
    shop = get_object_or_404(SupplierShop, pk=shop_id)
    
    if request.method == 'POST':
        try:
            amount = float(request.POST.get('amount') or 0)
            method = request.POST.get('payment_method')
            note = request.POST.get('note')
            
            if amount > 0:
                SupplierPayment.objects.create(
                    supplier=shop,
                    amount=amount,
                    payment_method=method,
                    note=note
                )
                messages.success(request, "Payment recorded successfully.")
                return redirect('supplier_shop_detail', shop_id=shop.id)
        except ValueError:
            messages.error(request, "Invalid payment amount.")
            
    return render(request, 'inventory/suppliers/add_payment.html', {'shop': shop})

@staff_required
@transaction.atomic
def delete_shop_payment(request, shop_id, payment_id):
    if request.method == 'POST':
        payment = get_object_or_404(SupplierPayment, pk=payment_id, supplier_id=shop_id)
        payment.is_trashed = True
        payment.save()
        messages.success(request, "Payment reversed.")
    return redirect('supplier_shop_detail', shop_id=shop_id)

@staff_required
def inventory_item_suppliers(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    catalogs = item.shop_catalogs.select_related('shop').filter(shop__is_active=True)
    
    return render(request, 'inventory/suppliers/item_suppliers.html', {
        'item': item,
        'catalogs': catalogs
    })

@staff_required
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

@staff_required
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
