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
            total_purchases=Coalesce(
                Sum(ExpressionWrapper(F('spare_items__unit_price') * Coalesce(F('spare_items__quantity'), Value(1, output_field=DecimalField())), output_field=DecimalField())),
                Value(0, output_field=DecimalField())
            ),
            total_paid=Coalesce(
                Sum('spare_items__shop_paid_amount'),
                Value(0, output_field=DecimalField())
            ),
            item_count=Count('spare_items', distinct=True),
        )
        .annotate(
            total_balance=ExpressionWrapper(
                F('total_purchases') - F('total_paid'),
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

    # Grand totals (pure SQL)
    totals = items_qs.aggregate(
        total_purchases=Coalesce(Sum(ExpressionWrapper(F('unit_price') * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField())), output_field=DecimalField())), Value(Decimal('0'), output_field=DecimalField()), output_field=DecimalField()),
        total_paid=Coalesce(Sum('shop_paid_amount'), Value(Decimal('0')), output_field=DecimalField()),
    )
    total_purchases = totals['total_purchases']
    total_paid = totals['total_paid']
    total_balance = max(Decimal('0'), total_purchases - total_paid)
    item_count = items_qs.count()

    # Annotate per-item balance for the template
    items_qs = items_qs.annotate(
        item_balance=ExpressionWrapper(
            (Coalesce(F('unit_price'), Value(Decimal('0'), output_field=DecimalField())) * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField()))) - F('shop_paid_amount'),
            output_field=DecimalField()
        )
    )

    paginator = Paginator(items_qs, 45)
    page_obj = paginator.get_page(request.GET.get('page'))

    pay_paginator = Paginator(payment_qs, 15)
    pay_page_obj = pay_paginator.get_page(request.GET.get('pay_page'))

    return render(request, 'workshop/spare_shops/shop_detail.html', {
        'shop': shop,
        'items': page_obj,
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
def spare_shop_pay(request, pk):
    """
    POST: Process a lump-sum payment to a shop using the Cascade Algorithm.
    Distributes the amount across unpaid items oldest-first.
    Thread-safe via select_for_update. Creates a SpareShopPayment audit record.
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

    with transaction.atomic():
        pending_items = (
            JobCardSpareItem.objects
            .select_for_update()
            .filter(shop=shop)
            .exclude(unit_price__isnull=True)
            .annotate(
                item_balance=ExpressionWrapper(
                    (F('unit_price') * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField()))) - F('shop_paid_amount'),
                    output_field=DecimalField()
                )
            )
            .filter(item_balance__gt=0)
            .order_by('job_card__admitted_date', 'pk')
        )

        total_outstanding = pending_items.aggregate(
            total=Coalesce(
                Sum(ExpressionWrapper((F('unit_price') * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField()))) - F('shop_paid_amount'), output_field=DecimalField())),
                Value(Decimal('0'), output_field=DecimalField())
            )
        )['total']

        if lump_sum > total_outstanding:
            messages.error(request, f"Amount (₹{lump_sum:,.0f}) exceeds total balance of ₹{total_outstanding:,.0f}.")
            return redirect('spare_shop_detail', pk=pk)

        remaining = lump_sum
        items_updated = 0
        history_details = []

        for item in pending_items:
            if remaining <= 0:
                break
            balance = (item.unit_price * (item.quantity or Decimal('1'))) - item.shop_paid_amount
            if balance <= 0:
                continue

            if remaining >= balance:
                paid_amount = balance
                item.shop_paid_amount += balance
                remaining -= balance
            else:
                paid_amount = remaining
                item.shop_paid_amount += remaining
                remaining = Decimal('0')

            item.save(update_fields=['shop_paid_amount'])
            items_updated += 1
            history_details.append({
                'item_id': item.pk,
                'job_id': item.job_card_id,
                'part': item.spare_part_name or '—',
                'paid': str(paid_amount),
            })

        SpareShopPayment.objects.create(
            shop=shop,
            amount=lump_sum,
            payment_method=payment_method,
            note=note or None,
            items_affected=items_updated,
            details=json.dumps(history_details),
        )

    messages.success(request, f"₹{lump_sum:,.0f} distributed across {items_updated} item(s) for {shop.name}.")
    return redirect('spare_shop_detail', pk=pk)


@office_required
def spare_shop_pay_item(request, pk, item_pk):
    """
    POST: Pay a single spare item immediately (Pay Now button).
    Pays the full remaining balance for that specific item.
    Creates a SpareShopPayment audit record.
    """
    if request.method != 'POST':
        return redirect('spare_shop_detail', pk=pk)

    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
    item = get_object_or_404(JobCardSpareItem, pk=item_pk, shop=shop)
    payment_method = request.POST.get('payment_method', 'CASH')
    note = request.POST.get('note', '').strip()

    unit_price = item.unit_price or Decimal('0')
    qty = item.quantity or Decimal('1')
    total_cost = unit_price * qty
    already_paid = item.shop_paid_amount or Decimal('0')
    pay_now = total_cost - already_paid

    if pay_now <= 0:
        messages.info(request, "This item is already fully paid.")
        return redirect('spare_shop_detail', pk=pk)

    with transaction.atomic():
        item.shop_paid_amount = total_cost
        item.save(update_fields=['shop_paid_amount'])

        SpareShopPayment.objects.create(
            shop=shop,
            amount=pay_now,
            payment_method=payment_method,
            note=note or None,
            items_affected=1,
            details=json.dumps([{
                'item_id': item.pk,
                'job_id': item.job_card_id,
                'part': item.spare_part_name or '—',
                'paid': str(pay_now),
            }]),
        )

    messages.success(request, f"₹{pay_now:,.0f} paid for '{item.spare_part_name or 'item'}' to {shop.name}.")
    return redirect('spare_shop_detail', pk=pk)


@owner_required
def spare_shop_payment_reverse(request, shop_pk, payment_pk):
    """
    POST: Reverse a SpareShopPayment and subtract amounts from affected items.
    Uses the stored JSON snapshot to roll back precisely. Owner only.
    """
    if request.method != 'POST':
        return redirect('spare_shop_detail', pk=shop_pk)

    shop = get_object_or_404(SpareShop, pk=shop_pk)
    payment = get_object_or_404(SpareShopPayment, pk=payment_pk, shop=shop, is_trashed=False)

    with transaction.atomic():
        try:
            details = json.loads(payment.details)
        except (json.JSONDecodeError, TypeError):
            details = []

        for entry in details:
            try:
                item = JobCardSpareItem.objects.select_for_update().get(pk=entry['item_id'])
                reversed_amount = Decimal(str(entry['paid']))
                item.shop_paid_amount = max(Decimal('0'), item.shop_paid_amount - reversed_amount)
                item.save(update_fields=['shop_paid_amount'])
            except (JobCardSpareItem.DoesNotExist, KeyError):
                continue

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

    # Grand totals (pure SQL) — uses shop_paid_amount to match detail view exactly
    totals = items_qs.aggregate(
        total_purchases=Coalesce(Sum(ExpressionWrapper(F('unit_price') * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField())), output_field=DecimalField())), Value(Decimal('0'), output_field=DecimalField()), output_field=DecimalField()),
        total_paid=Coalesce(Sum('shop_paid_amount'), Value(Decimal('0')), output_field=DecimalField()),
    )
    total_purchases = totals['total_purchases']
    total_paid = totals['total_paid']
    total_balance = max(Decimal('0'), total_purchases - total_paid)

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
