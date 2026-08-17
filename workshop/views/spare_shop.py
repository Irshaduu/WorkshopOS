import json
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.db.models import Sum, Count, Max, F, Value, ExpressionWrapper, DecimalField
from django.db.models.functions import Coalesce
from django.db import transaction
from django.core.paginator import Paginator
from django.urls import reverse

from ..models import JobCardSpareItem, SpareShop, SpareShopPayment, DeletionLog
from ..decorators import office_required, owner_required, staff_required, is_office_or_owner
from ..notifications import notify
from ..spare_dates import pair_problem
# What a shop-bought line cost, in the one place it is defined. This page's
# running balance, its grand total and `SpareShop.total_purchased_amount` are
# three views of the same money, and they used to be three hand-written copies
# of the expression — so a change to one would have shown a different debt on
# the shop's own page than on the Profit page.
from ..analysis_engine import SHOP_LINE_COST


@office_required
def spare_shop_list(request):
    """
    Lists all registered spare shops with annotated financial totals.
    Calculates total purchased (unit_price sum), total paid, and balance owed
    entirely in SQL — zero Python loops.

    Sorted by most recent job-card usage first, so shops the workshop actually
    deals with day to day surface at the top instead of behind alphabetically-
    earlier, rarely-used ones. Shops never used on a job sort to the bottom,
    then by name.
    """
    shops = (
        SpareShop.objects.filter(is_trashed=False)
        .annotate(
            item_count=Count('spare_items', distinct=True),
            total_balance=ExpressionWrapper(
                F('total_purchased_amount') - F('total_paid_amount'),
                output_field=DecimalField()
            ),
            last_activity=Max('spare_items__job_card__admitted_date'),
        )
        .order_by(F('last_activity').desc(nulls_last=True), 'name')
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
    # NOTE: No Coalesce fallback — items with no date get group_date=None
    # and are correctly shown under "No Date Recorded" in the template.
    items_qs = (
        JobCardSpareItem.objects
        .filter(shop=shop)
        .select_related('job_card')
        .annotate(
            group_date=F(group_field)
        )
        .order_by(F('group_date').desc(nulls_first=True), '-pk')
    )

    payment_qs = shop.payments.filter(is_trashed=False).order_by('-created_at')

    # Date Filtering — calendar-aligned, consistent with Paid Bills & Completed sections
    # Filter applies to group_field so "Today" in Received mode = received today,
    # and "Today" in Ordered mode = ordered today.
    filter_type = request.GET.get('filter', 'this_year')
    start_date_str = ''
    end_date_str = ''
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
    null_key = f'{group_field}__isnull'  # e.g. 'received_date__isnull'

    from django.db.models import Q as _Q

    def _date_q(exact=None, gte=None, lte=None):
        """Build a Q that matches group_field date range + always includes NULL-date items."""
        if exact is not None:
            return _Q(**{group_field: exact}) | _Q(**{null_key: True})
        kwargs = {}
        if gte is not None:
            kwargs[f'{group_field}__gte'] = gte
        if lte is not None:
            kwargs[f'{group_field}__lte'] = lte
        return _Q(**kwargs) | _Q(**{null_key: True})

    if filter_type == 'today':
        items_qs   = items_qs.filter(_date_q(exact=today))
        payment_qs = payment_qs.filter(created_at__date=today)

    elif filter_type == 'this_week':
        start = today - timedelta(days=today.weekday())  # Monday of current week
        items_qs   = items_qs.filter(_date_q(gte=start))
        payment_qs = payment_qs.filter(created_at__date__gte=start)

    elif filter_type == 'this_month':
        start = today.replace(day=1)
        items_qs   = items_qs.filter(_date_q(gte=start))
        payment_qs = payment_qs.filter(created_at__date__gte=start)

    elif filter_type == 'this_year':
        start = today.replace(month=1, day=1)
        items_qs   = items_qs.filter(_date_q(gte=start))
        payment_qs = payment_qs.filter(created_at__date__gte=start)

    elif filter_type == 'last_week':
        start = today - timedelta(days=today.weekday() + 7)  # Previous Mon
        end   = start + timedelta(days=6)                     # Previous Sun
        items_qs   = items_qs.filter(_date_q(gte=start, lte=end))
        payment_qs = payment_qs.filter(created_at__date__gte=start, created_at__date__lte=end)

    elif filter_type == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_of_last_month  = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_of_last_month.replace(day=1)
        items_qs   = items_qs.filter(_date_q(gte=first_of_last_month, lte=last_of_last_month))
        payment_qs = payment_qs.filter(created_at__date__gte=first_of_last_month, created_at__date__lte=last_of_last_month)

    elif filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1,  day=1)
        end   = today.replace(year=today.year - 1, month=12, day=31)
        items_qs   = items_qs.filter(_date_q(gte=start, lte=end))
        payment_qs = payment_qs.filter(created_at__date__gte=start, created_at__date__lte=end)

    elif filter_type == 'custom':
        start_date_str = request.GET.get('start_date', '')
        end_date_str   = request.GET.get('end_date', '')
        # Parsed, not handed to the ORM as text — an unparseable string raises
        # in `get_prep_value`, i.e. a 500 from a hand-edited URL.
        if start_date_str and end_date_str:
            try:
                sd, ed = date.fromisoformat(start_date_str), date.fromisoformat(end_date_str)
            except ValueError:
                sd = ed = None
            if sd and ed:
                items_qs   = items_qs.filter(_date_q(gte=sd, lte=ed))
                payment_qs = payment_qs.filter(created_at__date__gte=sd, created_at__date__lte=ed)
    # filter_type == 'all' → no date filter applied


    from django.db.models import OuterRef, Subquery, Q
    older_items_sum_sq = JobCardSpareItem.objects.filter(
        shop=OuterRef('shop')
    ).filter(
        Q(job_card__admitted_date__lt=OuterRef('job_card__admitted_date')) | 
        Q(job_card__admitted_date=OuterRef('job_card__admitted_date'), pk__lte=OuterRef('pk'))
    ).values('shop').annotate(
        total=Sum(SHOP_LINE_COST, output_field=DecimalField())
    ).values('total')

    items_qs = items_qs.annotate(
        absolute_running_sum=Coalesce(Subquery(older_items_sum_sq), Decimal('0'), output_field=DecimalField()),
        item_cost=SHOP_LINE_COST,
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


@office_required
@transaction.atomic
def spare_shop_payment_reverse(request, shop_pk, payment_pk):
    """
    POST: Permanently delete a spare-shop payment.

    Logs a full snapshot to the Owner-only Deletion History, then removes the
    record and recomputes the shop balance. Owner + Office. No restore.
    """
    if request.method != 'POST':
        return redirect('spare_shop_detail', pk=shop_pk)

    shop = get_object_or_404(SpareShop, pk=shop_pk)
    payment = get_object_or_404(SpareShopPayment, pk=payment_pk, shop=shop)
    reason = request.POST.get('reason', '').strip()
    amount = payment.amount

    DeletionLog.record(
        DeletionLog.ENTITY_SHOP_PAYMENT, payment,
        user=request.user, reason=reason, amount=amount,
        label=f"₹{amount:,.0f} → {shop.name}",
    )
    payment.delete()  # SpareShopPayment.delete() recomputes shop.update_totals()

    messages.success(request, f"Payment of ₹{amount:,.0f} permanently deleted (logged to Deletion History).")
    return redirect('spare_shop_detail', pk=shop_pk)


@office_required
def spare_shop_delete(request, pk):
    """POST: Deactivate (archive) a spare shop — reversible, keeps all history."""
    if request.method == 'POST':
        shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
        shop.is_trashed = True
        shop.save(update_fields=['is_trashed'])
        notify(
            'ACCOUNT_ARCHIVED',
            f"Spare Shop '{shop.name}' was archived.",
            actor=request.user,
            url=reverse('spare_shop_archived'),
            object_type='SPARE_SHOP', object_id=shop.pk,
        )
        messages.success(request, f"Shop '{shop.name}' deactivated (archived).")
    return redirect('spare_shop_list')


@office_required
def spare_shop_archived(request):
    """List archived (deactivated) spare shops, each with a Reactivate action."""
    shops = SpareShop.objects.filter(is_trashed=True).order_by('name')
    page_obj = Paginator(shops, 45).get_page(request.GET.get('page'))
    return render(request, 'workshop/spare_shops/shop_archived.html', {
        'page_obj': page_obj,
    })


@office_required
def spare_shop_restore(request, pk):
    """POST: Reactivate an archived spare shop."""
    if request.method == 'POST':
        shop = get_object_or_404(SpareShop, pk=pk, is_trashed=True)
        shop.is_trashed = False
        shop.save(update_fields=['is_trashed'])
        messages.success(request, f"Shop '{shop.name}' reactivated.")
    return redirect('spare_shop_archived')


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
        .annotate(group_date=F(group_field))
        .order_by(F('group_date').desc(nulls_first=True), '-pk')
    )

    payment_qs = shop.payments.filter(is_trashed=False)
    
    # Date Filtering — mirrors detail view exactly (calendar-aligned)
    # Filters on group_field so sort mode and filter mode always agree.
    filter_type = request.GET.get('filter', 'all')
    start_date_str = ''
    end_date_str = ''
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
    null_key = f'{group_field}__isnull'

    from django.db.models import Q as _Q

    def _date_q(exact=None, gte=None, lte=None):
        if exact is not None:
            return _Q(**{group_field: exact}) | _Q(**{null_key: True})
        kwargs = {}
        if gte is not None:
            kwargs[f'{group_field}__gte'] = gte
        if lte is not None:
            kwargs[f'{group_field}__lte'] = lte
        return _Q(**kwargs) | _Q(**{null_key: True})

    if filter_type == 'today':
        items_qs   = items_qs.filter(_date_q(exact=today))
        payment_qs = payment_qs.filter(created_at__date=today)

    elif filter_type == 'this_week':
        start = today - timedelta(days=today.weekday())
        items_qs   = items_qs.filter(_date_q(gte=start))
        payment_qs = payment_qs.filter(created_at__date__gte=start)

    elif filter_type == 'this_month':
        start = today.replace(day=1)
        items_qs   = items_qs.filter(_date_q(gte=start))
        payment_qs = payment_qs.filter(created_at__date__gte=start)

    elif filter_type == 'this_year':
        start = today.replace(month=1, day=1)
        items_qs   = items_qs.filter(_date_q(gte=start))
        payment_qs = payment_qs.filter(created_at__date__gte=start)

    elif filter_type == 'last_week':
        start = today - timedelta(days=today.weekday() + 7)
        end   = start + timedelta(days=6)
        items_qs   = items_qs.filter(_date_q(gte=start, lte=end))
        payment_qs = payment_qs.filter(created_at__date__gte=start, created_at__date__lte=end)

    elif filter_type == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_of_last_month  = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_of_last_month.replace(day=1)
        items_qs   = items_qs.filter(_date_q(gte=first_of_last_month, lte=last_of_last_month))
        payment_qs = payment_qs.filter(created_at__date__gte=first_of_last_month, created_at__date__lte=last_of_last_month)

    elif filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1,  day=1)
        end   = today.replace(year=today.year - 1, month=12, day=31)
        items_qs   = items_qs.filter(_date_q(gte=start, lte=end))
        payment_qs = payment_qs.filter(created_at__date__gte=start, created_at__date__lte=end)

    elif filter_type == 'custom':
        start_date_str = request.GET.get('start_date', '')
        end_date_str   = request.GET.get('end_date', '')
        # Parsed, not handed to the ORM as text — an unparseable string raises
        # in `get_prep_value`, i.e. a 500 from a hand-edited URL.
        if start_date_str and end_date_str:
            try:
                sd, ed = date.fromisoformat(start_date_str), date.fromisoformat(end_date_str)
            except ValueError:
                sd = ed = None
            if sd and ed:
                items_qs   = items_qs.filter(_date_q(gte=sd, lte=ed))
                payment_qs = payment_qs.filter(
                    created_at__date__gte=sd,
                    created_at__date__lte=ed,
                )
    # Legacy aliases for any old bookmarked print URLs
    elif filter_type == 'month':
        sd = today - timedelta(days=30)
        items_qs   = items_qs.filter(_date_q(gte=sd))
        payment_qs = payment_qs.filter(created_at__date__gte=sd)
    elif filter_type == 'year':
        sd = today - timedelta(days=365)
        items_qs   = items_qs.filter(_date_q(gte=sd))
        payment_qs = payment_qs.filter(created_at__date__gte=sd)
    # filter_type == 'all' → no date filter applied

    # Grand totals (pure SQL)
    total_purchases = items_qs.aggregate(
        total_purchases=Coalesce(
            Sum(SHOP_LINE_COST, output_field=DecimalField()),
            Value(Decimal('0'), output_field=DecimalField()),
            output_field=DecimalField(),
        )
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

# Bounds come from the columns these values land in:
#   JobCardSpareItem.unit_price  max_digits=10, decimal_places=2
#   JobCardSpareItem.quantity    max_digits=8,  decimal_places=2
# A value past either does not fail cleanly — it is written, and then every later
# read of that shop's ledger raises InvalidOperation while aggregating it. One
# oversized typo made a shop's page permanently un-openable.
MAX_UNIT_PRICE = Decimal('99999999.99')
MAX_QUANTITY = Decimal('999999.99')


#: Sentinel for "this caller has no price to give" — distinct from a blank box.
#: Floor never sees a price field, so its adds arrive with this and store NULL.
PRICE_NOT_SUPPLIED = object()


def _clean_spare_dates(raw_ordered, raw_received, blank_is_today):
    """
    Resolve the ordered/received pair for an unassigned spare.

    Returns `(ordered, received, error_message)` — the error is set on exactly
    the inputs a person cannot have meant, and nothing is quietly substituted
    for them. Three rules, matching the price and quantity checks beside it:

    * unparseable is REFUSED, never turned into today. Both boxes are
      `<input type="date">`, which posts either an ISO date or nothing, so
      anything else here is a crafted POST — and silently stamping today onto
      one writes a date nobody chose onto a supplier's ledger.
    * a date in the FUTURE is refused. These rows are created `RECEIVED`; a
      part cannot have arrived on a day that has not come. Same reasoning as
      `_parse_money`'s future-advance refusal.
    * received before ordered is refused — that is the pair the wrong way round,
      and it is the one mistake the two boxes together can express.

    `blank_is_today` is what separates creating from editing. On create both
    boxes arrive pre-filled with today and an empty one means "the usual", so
    today is the honest answer. On edit an empty box means the person cleared
    it, and clearing has to be allowed to stick.
    """
    fallback = timezone.localdate() if blank_is_today else None

    def one(raw, label):
        if raw is None:
            return fallback, None
        if isinstance(raw, datetime):
            return raw.date(), None
        if isinstance(raw, date):
            return raw, None
        text = str(raw).strip()
        if not text:
            return fallback, None
        try:
            return date.fromisoformat(text), None
        except ValueError:
            return None, f"{label} is not a valid date."

    ordered, err = one(raw_ordered, "Ordered date")
    if err:
        return None, None, err
    received, err = one(raw_received, "Received date")
    if err:
        return None, None, err

    # The pair rule itself lives in `workshop/spare_dates.py`, shared with the
    # job card's own spare rows — those are the same two boxes, and the same
    # mistake, on the screen where most spares are actually entered. Parsing
    # stays here because only this caller receives raw POST text.
    problem = pair_problem(ordered, received)
    if problem:
        return None, None, problem

    return ordered, received, None


def _build_unassigned_spare(shop, name, raw_price, raw_qty,
                            ordered_date=None, received_date=None,
                            vehicle_info=None):
    """
    Validate and create one unassigned spare on a shop's ledger.

    Returns `(item, error_message)` — exactly one of the two is set.

    Single entry point on purpose. This row is money owed to a supplier, and it
    used to be created by a view that accepted a NEGATIVE price (making the shop
    appear to owe the workshop), a negative or zero quantity, and an oversized
    price that corrupted the ledger. Any second screen that offered "add" would
    have inherited all of it, so the rules live here rather than in a view.

    `raw_price=PRICE_NOT_SUPPLIED` stores NULL rather than zero, and the
    difference is the documented one: zero means the part was free, NULL means
    nobody has priced it yet. That is the Floor case — a mechanic records the
    part that arrived, and Office fills the figure in when the shop's bill is
    keyed. `SpareShop.update_totals()` coalesces NULL to 0, so an unpriced row
    adds nothing to what the shop is owed until it is priced.

    `vehicle_info` is the "Ordered For" note — free text, with no picker and no
    FK, because at the moment somebody records a purchase the car very often has
    no job card to point at yet. It moves no money and joins no table. It is
    TRIMMED to the column rather than allowed to fail, the same rule the name
    follows and for the same reason: an oversized value is stored by SQLite and
    rejected by PostgreSQL, so the only consistent answer is to trim.
    """
    if shop is None:
        return None, "Choose which shop this was bought from."
    if shop.is_trashed:
        return None, f"'{shop.name}' is archived. Restore it before adding purchases."

    name = (name or '').strip()
    if not name:
        return None, "Item name cannot be empty."
    name = name[:100]          # matches the column; silently truncating beats a 500

    # A price nobody supplied and a price box left empty are the same fact —
    # this part has not been priced yet — and both store NULL. Zero is reserved
    # for a part genuinely given away, which is a different thing to record.
    # `SpareShop.update_totals()` coalesces NULL to 0, so an unpriced row adds
    # nothing to the shop's balance until somebody prices it.
    price_unknown = raw_price is PRICE_NOT_SUPPLIED or not str(raw_price or '').strip()
    try:
        price = None if price_unknown else Decimal(str(raw_price).strip())
        qty = Decimal(str(raw_qty).strip()) if str(raw_qty).strip() else Decimal('1')
    except (InvalidOperation, ValueError, TypeError):
        return None, "Price and quantity must be numbers."

    if price is not None:
        if price < 0:
            return None, "Price cannot be negative — that would show the shop owing the workshop."
        if price > MAX_UNIT_PRICE:
            return None, f"Price is too large (limit ₹{MAX_UNIT_PRICE:,})."
    if qty <= 0:
        return None, "Quantity must be more than zero."
    if qty > MAX_QUANTITY:
        return None, f"Quantity is too large (limit {MAX_QUANTITY:,})."

    ord_date, rec_date, date_error = _clean_spare_dates(
        ordered_date, received_date, blank_is_today=True
    )
    if date_error:
        return None, date_error

    item = JobCardSpareItem.objects.create(
        job_card=None,
        shop=shop,
        source=JobCardSpareItem.SOURCE_SHOP,
        spare_part_name=name,
        unit_price=None if price is None else price.quantize(Decimal('0.01')),
        quantity=qty.quantize(Decimal('0.01')),
        status='RECEIVED',
        ordered_date=ord_date,
        received_date=rec_date,
        original_vehicle_info=(vehicle_info or '').strip()[:255] or None,
    )
    return item, None

@office_required
def spare_shop_add_unassigned(request, pk):
    """POST: Add a legacy balance or stock item directly to a shop (job_card=None)."""
    # `is_trashed=False`, matching spare_shop_pay: an archived shop takes no new
    # activity. The detail page this redirects back to refuses archived shops too,
    # so accepting the POST here would only redirect the user into a 404.
    # `_build_unassigned_spare` re-checks, for any caller that resolves the shop
    # from form input rather than the URL.
    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
    if request.method == 'POST':
        item, error = _build_unassigned_spare(
            shop,
            request.POST.get('spare_part_name'),
            request.POST.get('unit_price', '0'),
            request.POST.get('quantity', '1'),
            ordered_date=request.POST.get('ordered_date'),
            received_date=request.POST.get('received_date'),
        )
        if error:
            messages.error(request, error)
        else:
            messages.success(request, f"Added '{item.spare_part_name}' to shop ledger.")
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
    """
    POST: correct one row's cost and quantity from the shop ledger.

    Bounded by the same limits `_build_unassigned_spare` and
    `unassigned_spare_edit` apply, and for the same reason: a value past
    `max_digits` is written and then every later read of that shop's ledger
    raises `InvalidOperation` while aggregating it, which leaves the shop's page
    permanently un-openable. This was the last door into these rows that did not
    go through those rules — it took a negative price (making the shop appear to
    owe the workshop), a zero quantity and an oversized figure alike.

    Unlike the Hub's own edit this one may touch a row already fitted to a car,
    because the shop ledger lists both. It still only moves what the workshop
    PAID (`unit_price`); the customer's figure is `total_price` and is not
    reachable from here.
    """
    item = get_object_or_404(JobCardSpareItem, pk=item_pk)
    shop_id = item.shop_id

    def done(message=None, error=False):
        if message:
            (messages.error if error else messages.success)(request, message)
        if shop_id:
            return redirect('spare_shop_detail', pk=shop_id)
        return redirect('home')

    if request.method != 'POST':
        return done()

    raw_price = request.POST.get('unit_price')
    raw_qty = request.POST.get('quantity')

    try:
        # Blank means "leave it alone" here, not "clear it" — this form posts
        # only the field being corrected.
        price = Decimal(raw_price.strip()) if (raw_price or '').strip() else None
        qty = Decimal(raw_qty.strip()) if (raw_qty or '').strip() else None
    except (InvalidOperation, ValueError, TypeError):
        return done("Price and quantity must be numbers.", error=True)

    if price is None and qty is None:
        return done()

    if price is not None:
        if price < 0:
            return done("Price cannot be negative — that would show the shop "
                        "owing the workshop.", error=True)
        if price > MAX_UNIT_PRICE:
            return done(f"Price is too large (limit ₹{MAX_UNIT_PRICE:,}).", error=True)
        item.unit_price = price.quantize(Decimal('0.01'))

    if qty is not None:
        if qty <= 0:
            return done("Quantity must be more than zero.", error=True)
        if qty > MAX_QUANTITY:
            return done(f"Quantity is too large (limit {MAX_QUANTITY:,}).", error=True)
        item.quantity = qty.quantize(Decimal('0.01'))

    item.save()
    return done(f"Updated pricing for '{item.spare_part_name}'.")





@office_required
@transaction.atomic
def spare_shop_delete_unassigned(request, item_pk):
    """
    POST: Permanently delete an UNASSIGNED spare from a shop's ledger.

    Scoped to rows with no job card on purpose. A spare already fitted to a car is
    removed from that car's Spare Parts section instead, so every row has exactly
    one screen that owns deleting it.

    Until 2026-07-31 there was no way to delete one at all — no route, no button,
    and `/admin/` unreachable by design. A mistyped entry on a shop ledger
    therefore inflated what the workshop owed that shop permanently, with nothing
    anywhere able to remove it. Logged to Deletion History like every other
    permanent delete of a financial record, and there is no restore.
    """
    item = get_object_or_404(
        JobCardSpareItem.objects.select_related('shop'),
        pk=item_pk, job_card__isnull=True,
    )
    if request.method != 'POST':
        return redirect('unassigned_spares_hub')

    shop = item.shop
    name = item.spare_part_name or 'Unnamed spare'
    # The shop's line total, as typed — the same figure the ledger carried for
    # this row, so the Deletion History records what was actually removed from
    # the balance rather than a recomputation of it.
    cost = item.unit_price or Decimal('0')

    DeletionLog.record(
        DeletionLog.ENTITY_UNASSIGNED_SPARE, item,
        user=request.user,
        reason=request.POST.get('reason', '').strip(),
        amount=cost,
        label=f"{name} × {item.quantity or 1} · {shop.name if shop else 'no shop'}",
    )
    item.delete()   # JobCardSpareItem.delete() recomputes the shop's totals
    messages.success(
        request,
        f"'{name}' removed from the ledger (logged to Deletion History)."
    )
    return redirect('unassigned_spares_hub')


@staff_required
def unassigned_spare_add(request):
    """
    POST from the Unassigned Hub: record a shop purchase without opening that
    shop's page first.

    Same row as `spare_shop_add_unassigned` creates — the shop simply arrives as
    a form field instead of a URL segment — and it goes through the same
    `_build_unassigned_spare` rules, so this door cannot drift from that one.

    The shop is REQUIRED. A row with no job card *and* no shop would be filtered
    out of this Hub (which lists `shop__isnull=False`), absent from every shop
    ledger, and unreachable by the only delete there is — invisible money.

    FLOOR MAY ADD, AND THE PRICE IS STRIPPED HERE, not merely hidden in the
    template. The mechanic is who receives the part, so recording it at that
    moment is the only way the ledger is not a day behind; but Floor is shown no
    cost anywhere in this app, and a hidden input is one crafted POST away from
    writing one. `PRICE_NOT_SUPPLIED` stores NULL — unpriced, not free — which
    Office fills in from the shop's bill later. This is the same server-side
    half the job card's `_floor_locked_data` exists for (AUD-0081).
    """
    if request.method != 'POST':
        return redirect('unassigned_spares_hub')

    raw_shop = (request.POST.get('shop') or '').strip()
    shop = None
    if raw_shop.isdigit():
        shop = SpareShop.objects.filter(pk=int(raw_shop), is_trashed=False).first()

    if is_office_or_owner(request.user):
        raw_price = request.POST.get('unit_price', '0')
    else:
        raw_price = PRICE_NOT_SUPPLIED

    item, error = _build_unassigned_spare(
        shop,
        request.POST.get('spare_part_name'),
        raw_price,
        request.POST.get('quantity', '1'),
        ordered_date=request.POST.get('ordered_date'),
        received_date=request.POST.get('received_date'),
        vehicle_info=request.POST.get('original_vehicle_info'),
    )
    if error:
        messages.error(request, error)
    else:
        messages.success(request, f"Added '{item.spare_part_name}' to {shop.name}'s ledger.")
    return redirect('unassigned_spares_hub')


@office_required
@transaction.atomic
def unassigned_spare_edit(request, item_pk):
    """
    POST: correct an UNASSIGNED spare — shop, name, quantity, price and the two
    dates. Office and Owner only: this rewrites what a supplier is owed.

    Every rule `_build_unassigned_spare` applies on create is applied again
    here, because an edit can reach exactly the same bad states a create can and
    this row is money. The price bounds are the column's (an oversized value is
    written and then breaks every later read of that shop's ledger), a negative
    price would show the shop owing the workshop, and the dates go through the
    same `_clean_spare_dates` pair check — with `blank_is_today=False`, because
    clearing a date here is a deliberate act rather than "the usual".

    AN ARCHIVED SHOP THIS ROW ALREADY POINTS AT STAYS RESOLVABLE. Only active
    shops may be moved TO, but the row's own shop is accepted whatever its
    state — the same rule as `_resolvable_shops()` on the job card, and for the
    same reason: an archived shop must keep the purchases already booked
    against it, so correcting a typo in the part name cannot be the thing that
    silently moves that debt to whichever shop happened to be first in the list.
    """
    item = get_object_or_404(
        JobCardSpareItem.objects.select_related('shop'),
        pk=item_pk, job_card__isnull=True,
    )
    if request.method != 'POST':
        return redirect('unassigned_spares_hub')

    def refuse(message):
        messages.error(request, message)
        return redirect('unassigned_spares_hub')

    raw_shop = (request.POST.get('shop') or '').strip()
    shop = None
    if raw_shop.isdigit():
        shop_pk = int(raw_shop)
        shop = SpareShop.objects.filter(pk=shop_pk, is_trashed=False).first()
        if shop is None and item.shop_id == shop_pk:
            shop = item.shop          # its own archived shop — keeps its debt
    if shop is None:
        return refuse("Choose which shop this was bought from.")

    name = (request.POST.get('spare_part_name') or '').strip()
    if not name:
        return refuse("Item name cannot be empty.")
    name = name[:100]

    try:
        raw_price = request.POST.get('unit_price', '')
        raw_qty = request.POST.get('quantity', '1')
        # Blank clears the price back to "not yet known" rather than asserting
        # the part was free — the same distinction a Floor-recorded row starts
        # life in, and the one `SpareShop.update_totals()` coalesces to zero.
        price = Decimal(str(raw_price).strip()) if str(raw_price).strip() else None
        qty = Decimal(str(raw_qty).strip()) if str(raw_qty).strip() else Decimal('1')
    except (InvalidOperation, ValueError, TypeError):
        return refuse("Price and quantity must be numbers.")

    if price is not None:
        if price < 0:
            return refuse("Price cannot be negative — that would show the shop owing the workshop.")
        if price > MAX_UNIT_PRICE:
            return refuse(f"Price is too large (limit ₹{MAX_UNIT_PRICE:,}).")
    if qty <= 0:
        return refuse("Quantity must be more than zero.")
    if qty > MAX_QUANTITY:
        return refuse(f"Quantity is too large (limit {MAX_QUANTITY:,}).")

    ord_date, rec_date, date_error = _clean_spare_dates(
        request.POST.get('ordered_date'),
        request.POST.get('received_date'),
        blank_is_today=False,
    )
    if date_error:
        return refuse(date_error)

    previous_shop = item.shop
    item.shop = shop
    item.spare_part_name = name
    item.unit_price = None if price is None else price.quantize(Decimal('0.01'))
    item.quantity = qty.quantize(Decimal('0.01'))
    item.ordered_date = ord_date
    item.received_date = rec_date
    # The "Ordered For" note. Correctable like every other field on the row, and
    # trimmed rather than refused — see `_build_unassigned_spare`. Clearing it is
    # a deliberate act and stores NULL, the same way clearing a date does here.
    item.original_vehicle_info = (
        (request.POST.get('original_vehicle_info') or '').strip()[:255] or None
    )
    # JobCardSpareItem.save() snapshots the previous shop_id and refreshes both
    # ledgers itself (AUD-0080), so moving a row between shops is already
    # accounted for on both sides — nothing further is needed here.
    item.save()

    messages.success(request, f"Updated '{item.spare_part_name}'.")
    return redirect('unassigned_spares_hub')


@staff_required
def unassigned_spares_hub(request):
    """
    Every shop purchase not yet fitted to a car, grouped by shop.

    OPEN TO FLOOR, add-only. A mechanic takes delivery of a part, so letting
    them record it is what keeps the ledger same-day; but Floor is shown no cost
    anywhere in this app, so `can_see_prices` drops the price column, the price
    box and the ledger figures, and `can_manage` drops Edit and Delete. Both are
    resolved here and only read in the template — the server halves are the
    decorators on `unassigned_spare_edit` / `spare_shop_delete_unassigned` and
    the price strip in `unassigned_spare_add`, so hiding a control here is
    presentation, never the control itself.

    ROWS ON ARCHIVED SHOPS ARE STILL LISTED. Archiving hides a shop from the
    pickers; it must not hide what is owed to it, or that debt is reachable from
    no screen at all. The group carries an "Archived" badge and takes no new
    purchases, while its existing rows stay editable — see
    `unassigned_spare_edit`.

    No job-card list here: a spare is put ON a car from the car's own Spare
    Parts section ("Import from Unassigned"), which is the one place that also
    sets the price and quantity the customer is billed.
    """
    can_manage = is_office_or_owner(request.user)

    unassigned_items = (
        JobCardSpareItem.objects
        .filter(job_card__isnull=True, shop__isnull=False)
        .select_related('shop')
        .order_by('shop__name', '-ordered_date', '-pk')
    )

    return render(request, 'workshop/spare_shops/unassigned_hub.html', {
        'unassigned_items': unassigned_items,
        'item_count': unassigned_items.count(),
        # Active shops only: a purchase cannot be booked against an archived one.
        # The edit modal re-adds a row's own archived shop client-side so it
        # round-trips; this list is what may be chosen fresh.
        'shops': SpareShop.objects.filter(is_trashed=False).order_by('name'),
        'can_manage': can_manage,
        'can_see_prices': can_manage,
    })
