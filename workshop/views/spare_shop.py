import json
from decimal import Decimal
from datetime import date, datetime, timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum, Count, F, Value, ExpressionWrapper, DecimalField
from django.db.models.functions import Coalesce
from django.db import transaction
from django.core.paginator import Paginator

from ..models import JobCardSpareItem, SpareShop, SpareShopPayment
from ..decorators import office_required, owner_required


@office_required
def spare_shop_list(request):
    """
    Lists all registered spare shops with annotated financial totals.
    Calculates total purchased (unit_price sum), total paid, and balance owed
    entirely in SQL — zero Python loops.
    """
    shops = (
        SpareShop.objects.filter(is_trashed=False)
        .annotate(
            item_count=Count('spare_items', distinct=True),
            total_balance=ExpressionWrapper(
                F('total_purchased_amount') - F('total_paid_amount'),
                output_field=DecimalField()
            )
        )
        .order_by('name')
    )

    return render(request, 'workshop/spare_shops/shop_list.html', {
        'shops': shops,
    })


@office_required
def spare_shop_create(request):
    """POST: Create a new SpareShop entry."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()

        if not name:
            messages.error(request, "Shop name cannot be empty.")
            return redirect('spare_shop_list')

        if SpareShop.objects.filter(name__iexact=name).exists():
            messages.error(request, f"Shop '{name}' already exists.")
            return redirect('spare_shop_list')

        shop = SpareShop.objects.create(
            name=name,
            phone=phone or None,
            address=address or None,
        )
        messages.success(request, f"Shop '{shop.name}' created successfully.")
        return redirect('spare_shop_detail', pk=shop.pk)

    return redirect('spare_shop_list')


@office_required
def spare_shop_edit(request, pk):
    """POST: Edit an existing SpareShop (name, phone, address)."""
    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()

        if not name:
            messages.error(request, "Shop name cannot be empty.")
            return redirect('spare_shop_detail', pk=pk)

        if SpareShop.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, f"Another shop named '{name}' already exists.")
            return redirect('spare_shop_detail', pk=pk)

        shop.name = name
        shop.phone = phone or None
        shop.address = address or None
        shop.save()
        messages.success(request, f"Shop '{shop.name}' updated.")
    return redirect('spare_shop_detail', pk=pk)


@office_required
def spare_shop_detail(request, pk):
    """
    Full page: All spare items purchased from this shop across all job cards.
    Shows per-item financials and payment history.
    """
    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)

    # Sort/Group logic
    sort_by = request.GET.get('sort_by', 'received')
    group_field = 'ordered_date' if sort_by == 'ordered' else 'received_date'

    # All spare items from this shop, ordered newest first for history display
    items_qs = (
        JobCardSpareItem.objects
        .filter(shop=shop)
        .select_related('job_card')
        .annotate(
            group_date=Coalesce(group_field, 'job_card__admitted_date')
        )
        .order_by('-group_date', '-pk')
    )

    payment_qs = shop.payments.filter(is_trashed=False).order_by('-created_at')

    # Date Filtering
    filter_type = request.GET.get('filter', 'all')
    start_date_str = ''
    end_date_str = ''
    today = date.today()

    if filter_type == 'month':
        sd = today - timedelta(days=30)
        items_qs = items_qs.filter(ordered_date__gte=sd)
        payment_qs = payment_qs.filter(created_at__date__gte=sd)
    elif filter_type == 'year':
        sd = today - timedelta(days=365)
        items_qs = items_qs.filter(ordered_date__gte=sd)
        payment_qs = payment_qs.filter(created_at__date__gte=sd)
    elif filter_type == 'custom':
        start_date_str = request.GET.get('start_date', '')
        end_date_str = request.GET.get('end_date', '')
        if start_date_str and end_date_str:
            items_qs = items_qs.filter(
                ordered_date__gte=start_date_str,
                ordered_date__lte=end_date_str
            )
            payment_qs = payment_qs.filter(
                created_at__date__gte=start_date_str,
                created_at__date__lte=end_date_str
            )

    from django.db.models import OuterRef, Subquery, Q
    older_items_sum_sq = JobCardSpareItem.objects.filter(
        shop=OuterRef('shop')
    ).filter(
        Q(job_card__admitted_date__lt=OuterRef('job_card__admitted_date')) | 
        Q(job_card__admitted_date=OuterRef('job_card__admitted_date'), pk__lte=OuterRef('pk'))
    ).values('shop').annotate(
        total=Sum(
            ExpressionWrapper(
                Coalesce(F('unit_price'), Value(Decimal('0'), output_field=DecimalField())) * 
                Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField())),
                output_field=DecimalField()
            )
        )
    ).values('total')

    items_qs = items_qs.annotate(
        absolute_running_sum=Coalesce(Subquery(older_items_sum_sq), Decimal('0'), output_field=DecimalField()),
        item_cost=ExpressionWrapper(
            Coalesce(F('unit_price'), Value(Decimal('0'), output_field=DecimalField())) * 
            Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField())),
            output_field=DecimalField()
        )
    )

    total_purchases = shop.total_purchased_amount
    total_paid = shop.total_paid_amount
    total_balance = total_purchases - total_paid
    item_count = items_qs.count()

    paginator = Paginator(items_qs, 45)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    # ── Absolute Ledger Waterfall Calculation ──
    page_items = list(page_obj)
    for item in page_items:
        item_cost = item.item_cost
        older_sum = item.absolute_running_sum - item_cost
        bulk_pool = total_paid - older_sum
        
        if bulk_pool >= item_cost:
            item.covered_status = 'COVERED'
            item.pending_amount = Decimal('0')
        elif bulk_pool <= Decimal('0'):
            item.covered_status = 'UNPAID'
            item.pending_amount = item_cost
        else:
            item.covered_status = 'PARTIAL'
            item.pending_amount = item_cost - bulk_pool
            item.covered_amount = bulk_pool

    pay_paginator = Paginator(payment_qs, 15)
    pay_page_obj = pay_paginator.get_page(request.GET.get('pay_page'))

    return render(request, 'workshop/spare_shops/shop_detail.html', {
        'shop': shop,
        'items': page_items,
        'page_obj': page_obj,
        'total_purchases': total_purchases,
        'total_paid': total_paid,
        'total_balance': total_balance,
        'item_count': item_count,
        'pay_page_obj': pay_page_obj,
        'pay_count': payment_qs.count(),
        'filter_type': filter_type,
        'sort_by': sort_by,
        'start_date': start_date_str if filter_type == 'custom' else '',
        'end_date': end_date_str if filter_type == 'custom' else '',
    })


@office_required
@transaction.atomic
def spare_shop_pay(request, pk):
    """
    POST: Process a lump-sum payment to a shop.
    Creates a SpareShopPayment audit record and updates shop totals.
    """
    if request.method != 'POST':
        return redirect('spare_shop_detail', pk=pk)

    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
    payment_method = request.POST.get('payment_method', 'CASH')
    note = request.POST.get('note', '').strip()

    try:
        lump_sum = Decimal(str(request.POST.get('lump_sum', '0')))
    except Exception:
        lump_sum = Decimal('0')

    if lump_sum <= 0:
        messages.error(request, "Invalid payment amount.")
        return redirect('spare_shop_detail', pk=pk)

    SpareShopPayment.objects.create(
        shop=shop,
        amount=lump_sum,
        payment_method=payment_method,
        note=note or None,
    )

    messages.success(request, f"₹{lump_sum:,.0f} payment recorded for {shop.name}.")
    return redirect('spare_shop_detail', pk=pk)


@owner_required
@transaction.atomic
def spare_shop_payment_reverse(request, shop_pk, payment_pk):
    """
    POST: Reverse a SpareShopPayment (Soft Delete). Owner only.
    """
    if request.method != 'POST':
        return redirect('spare_shop_detail', pk=shop_pk)

    shop = get_object_or_404(SpareShop, pk=shop_pk)
    payment = get_object_or_404(SpareShopPayment, pk=payment_pk, shop=shop, is_trashed=False)

    payment.is_trashed = True
    payment.save()

    messages.success(request, f"Payment of ₹{payment.amount:,.0f} reversed and moved to Trash.")
    return redirect('spare_shop_detail', pk=shop_pk)


@owner_required
def spare_shop_delete(request, pk):
    """POST: Soft-delete a spare shop (move to trash). Owner only."""
    if request.method == 'POST':
        shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
        shop.is_trashed = True
        shop.save()
        messages.success(request, f"Shop '{shop.name}' moved to trash.")
    return redirect('spare_shop_list')


@owner_required
def spare_shop_restore(request, pk):
    """POST: Restore a trashed spare shop. Owner only."""
    if request.method == 'POST':
        shop = get_object_or_404(SpareShop, pk=pk, is_trashed=True)
        shop.is_trashed = False
        shop.save()
        messages.success(request, f"Shop '{shop.name}' restored.")
    return redirect('/trash/?tab=spare_shops')


@owner_required
def spare_shop_permanent_delete(request, pk):
    """POST: Permanently delete a trashed spare shop. Owner only."""
    if request.method == 'POST':
        shop = get_object_or_404(SpareShop, pk=pk, is_trashed=True)
        name = shop.name
        shop.delete()
        messages.success(request, f"Shop '{name}' permanently deleted.")
    return redirect('/trash/?tab=spare_shops')


@owner_required
def spare_shop_payment_permanent_delete(request, payment_pk):
    """POST: Permanently delete a trashed shop payment record. Owner only."""
    if request.method == 'POST':
        payment = get_object_or_404(SpareShopPayment, pk=payment_pk, is_trashed=True)
        amount = payment.amount
        payment.delete()
        messages.success(request, f"Shop payment of ₹{amount:,.0f} permanently deleted.")
    return redirect('/trash/?tab=shop_payments')


@office_required
def spare_shop_print(request, pk):
    """
    Print/PDF View: Displays a printer-friendly layout of a spare shop's purchases.
    Applies the exact same 'Ordered Date' filtering logic as the main detail view.
    """
    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)

    # Sort logic dynamically matching the main view
    sort_by = request.GET.get('sort_by', 'received')
    group_field = 'ordered_date' if sort_by == 'ordered' else 'received_date'

    items_qs = (
        JobCardSpareItem.objects
        .filter(shop=shop)
        .select_related('job_card')
        .annotate(group_date=Coalesce(group_field, 'job_card__admitted_date'))
        .order_by('-group_date', '-pk')
    )

    payment_qs = shop.payments.filter(is_trashed=False)

    # Date Filtering
    filter_type = request.GET.get('filter', 'all')
    start_date_str = ''
    end_date_str = ''
    today = date.today()

    if filter_type == 'month':
        sd = today - timedelta(days=30)
        items_qs = items_qs.filter(ordered_date__gte=sd)
        payment_qs = payment_qs.filter(created_at__date__gte=sd)
    elif filter_type == 'year':
        sd = today - timedelta(days=365)
        items_qs = items_qs.filter(ordered_date__gte=sd)
        payment_qs = payment_qs.filter(created_at__date__gte=sd)
    elif filter_type == 'custom':
        start_date_str = request.GET.get('start_date', '')
        end_date_str = request.GET.get('end_date', '')
        if start_date_str and end_date_str:
            items_qs = items_qs.filter(
                ordered_date__gte=start_date_str,
                ordered_date__lte=end_date_str
            )
            payment_qs = payment_qs.filter(
                created_at__date__gte=start_date_str,
                created_at__date__lte=end_date_str
            )

    # Grand totals (pure SQL)
    total_purchases = items_qs.aggregate(
        total_purchases=Coalesce(Sum(ExpressionWrapper(F('unit_price') * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField())), output_field=DecimalField())), Value(Decimal('0'), output_field=DecimalField()), output_field=DecimalField())
    )['total_purchases']
    
    total_paid = payment_qs.aggregate(
        total_paid=Coalesce(Sum('amount'), Value(Decimal('0')), output_field=DecimalField())
    )['total_paid']
    
    total_balance = total_purchases - total_paid

    start_date_obj = None
    end_date_obj = None
    if filter_type == 'custom' and start_date_str and end_date_str:
        try:
            start_date_obj = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    return render(request, 'workshop/spare_shops/shop_print.html', {
        'shop': shop,
        'items': items_qs,
        'payments': payment_qs.order_by('-created_at'),
        'filter_type': filter_type,
        'sort_by': sort_by,
        'start_date_obj': start_date_obj,
        'end_date_obj': end_date_obj,
        'total_purchases': total_purchases,
        'total_paid': total_paid,
        'total_balance': total_balance,
        'item_count': items_qs.count()
    })

# -----------------------------------------------------------------------------
# Unassigned Spares / Legacy Balances
# -----------------------------------------------------------------------------

@office_required
def spare_shop_add_unassigned(request, pk):
    """POST: Add a legacy balance or stock item directly to a shop (job_card=None)."""
    shop = get_object_or_404(SpareShop, pk=pk)
    if request.method == 'POST':
        spare_part_name = request.POST.get('spare_part_name', '').strip()
        unit_price = request.POST.get('unit_price', '0')
        quantity = request.POST.get('quantity', '1')
        
        if spare_part_name:
            try:
                from decimal import Decimal, InvalidOperation
                safe_price = Decimal(unit_price) if unit_price else Decimal('0')
                safe_qty = Decimal(quantity) if quantity else Decimal('1')
                
                JobCardSpareItem.objects.create(
                    job_card=None,
                    shop=shop,
                    spare_part_name=spare_part_name,
                    unit_price=safe_price,
                    quantity=safe_qty,
                    status='RECEIVED',
                    ordered_date=date.today(),
                    received_date=date.today()
                )
                messages.success(request, f"Added '{spare_part_name}' to shop ledger.")
            except (InvalidOperation, ValueError, TypeError):
                messages.error(request, "Invalid number format for price or quantity.")
        else:
            messages.error(request, "Item name cannot be empty.")
    return redirect('spare_shop_detail', pk=pk)


@office_required
def spare_shop_unassign_item(request, item_pk):
    """POST: Detach an item from a Job Card but keep it in the shop ledger."""
    item = get_object_or_404(JobCardSpareItem, pk=item_pk)
    shop_id = item.shop_id
    if request.method == 'POST':
        if not shop_id:
            messages.error(request, "Cannot unassign an item that isn't linked to a Spare Shop.")
            if item.job_card:
                return redirect('jobcard_detail', pk=item.job_card.pk)
            return redirect('home')
        
        # Valid shop and request method
        old_jc = item.job_card
        if old_jc:
            brand = old_jc.brand_name or ""
            model = old_jc.model_name or ""
            reg = old_jc.registration_number or ""
            info = f"{brand} {model}".strip()
            if reg:
                info += f" ({reg})"
            item.original_vehicle_info = info.strip()
            
        item.job_card = None
        item.save()
        # The model's save() won't update old_jc since job_card is now None,
        # so we must manually refresh the old job card's totals.
        if old_jc:
            old_jc.update_totals()
        messages.success(request, f"'{item.spare_part_name}' moved to unassigned stock.")
        if old_jc:
            return redirect('jobcard_detail', pk=old_jc.pk)
        return redirect('spare_shop_detail', pk=shop_id)
    return redirect('home')


@office_required
def spare_shop_update_item_price(request, item_pk):
    """POST: Update unit_price and quantity of an item directly from the shop ledger."""
    item = get_object_or_404(JobCardSpareItem, pk=item_pk)
    shop_id = item.shop_id
    if request.method == 'POST':
        price = request.POST.get('unit_price')
        qty = request.POST.get('quantity')
        
        updated = False
        try:
            from decimal import Decimal, InvalidOperation
            if price is not None and price != '':
                item.unit_price = Decimal(price)
                updated = True
            if qty is not None and qty != '':
                item.quantity = Decimal(qty)
                updated = True
                
            if updated:
                item.save()
                messages.success(request, f"Updated pricing for '{item.spare_part_name}'.")
        except (InvalidOperation, ValueError, TypeError):
            messages.error(request, "Invalid number format for price or quantity.")
            
    if shop_id:
        return redirect('spare_shop_detail', pk=shop_id)
    return redirect('home')





@office_required
def unassigned_spares_hub(request):
    """
    Dedicated hub for viewing and managing all Unassigned Spares.
    """
    unassigned_items = JobCardSpareItem.objects.filter(job_card__isnull=True, shop__isnull=False).select_related('shop').order_by('shop__name', '-ordered_date')
    
    from ..models import JobCard
    active_jobcards = JobCard.objects.filter(is_deleted=False).order_by('-pk')[:200]
    
    context = {
        'unassigned_items': unassigned_items,
        'active_jobcards': active_jobcards,
    }
    return render(request, 'workshop/spare_shops/unassigned_hub.html', context)
