""" 
workshop/analysis_views.py 
========================== 
Admin Data Analysis & Reports — Owner-Only Dashboard 
===================================================== 
 
Architecture: 
  - analysis_dashboard(): Main page. Computes 4 hero KPIs on page load. 
  - analysis_zone():       AJAX endpoint. Returns ONE zone partial at a time. 
                           This lazy-loading pattern prevents 30+ queries on 
                           page load when data is at 1M+ records. 
 
SQLite Compatibility: 
  All queries use .distinct() with NO field arguments (SQLite-safe). 
  For unique counts, uses .values('field').distinct().count() pattern. 
  Fully forward-compatible with PostgreSQL migration. 
 
Performance Rules (enforced in every zone handler): 
  1. Filter by date range BEFORE any aggregation (smallest result set first) 
  2. Pure SQL aggregates only — no Python loops over querysets 
  3. Top-N results always use ORM slicing [:N] (SQL LIMIT) 
  4. Coalesce() on every Sum/Avg to handle NULLs gracefully 
  5. No N+1 queries — all data from annotate/aggregate, never per-object calls 
"""

from datetime import date, timedelta
from django.utils import timezone
from django.shortcuts import render
from django.http import Http404
from django.db.models import Sum, Count, Q, DecimalField, Avg, FloatField, F, ExpressionWrapper, IntegerField, DurationField
from django.db.models.functions import Coalesce, TruncMonth, TruncDay
from decimal import Decimal
import json

from .decorators import owner_required
from .models import JobCard, Mechanic


# =============================================================================
# ZONE REGISTRY
# Maps URL zone_name → template path
# Each handler is a private function defined below, filled in per phase.
# =============================================================================
ZONE_REGISTRY = {
    'revenue':   '_zone_revenue',
    'mechanic':  '_zone_mechanic',
    'spares':    '_zone_spares',
    'customer':  '_zone_customer',
    'inventory': '_zone_inventory',
    'cashbook':  '_zone_cashbook',
    'workshop':  '_zone_workshop',
}


# =============================================================================
# DATE RANGE UTILITY
# =============================================================================

def get_date_range(range_key, start_str=None, end_str=None):
    """
    Returns (start_date, end_date, label) for a given range_key.

    Supported keys:
        today | this_week | this_month | this_year
        last_week | last_month | last_year | custom | all_time

    Financial year: Calendar year (Jan 1 – Dec 31) as agreed.
    Week: Monday-based.
    """
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'

    if range_key == 'today':
        return today, today, 'Today'

    elif range_key == 'this_week':
        # Monday of this week
        start = today - timedelta(days=today.weekday())
        end   = start + timedelta(days=6)
        return start, end, 'This Week'

    elif range_key == 'this_month':
        start = today.replace(day=1)
        # Last day of current month
        if today.month == 12:
            end = date(today.year, 12, 31)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        return start, end, f'{today.strftime("%B %Y")}'

    elif range_key == 'this_year':
        start = date(today.year, 1, 1)
        end   = date(today.year, 12, 31)
        return start, end, f'Year {today.year}'

    elif range_key == 'last_week':
        start_of_this_week = today - timedelta(days=today.weekday())
        end   = start_of_this_week - timedelta(days=1)
        start = end - timedelta(days=6)
        return start, end, 'Last Week'

    elif range_key == 'last_month':
        first_of_this_month = today.replace(day=1)
        end   = first_of_this_month - timedelta(days=1)
        start = end.replace(day=1)
        return start, end, f'{end.strftime("%B %Y")}'

    elif range_key == 'last_year':
        y = today.year - 1
        return date(y, 1, 1), date(y, 12, 31), f'Year {y}'

    elif range_key == 'custom' and start_str and end_str:
        try:
            start = date.fromisoformat(start_str)
            end   = date.fromisoformat(end_str)
            if start > end:
                start, end = end, start
            return start, end, f'{start.strftime("%d %b")} – {end.strftime("%d %b %Y")}'
        except (ValueError, TypeError):
            pass

    # Default fallback: this month
    start = today.replace(day=1)
    if today.month == 12:
        end = date(today.year, 12, 31)
    else:
        end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    return start, end, f'{today.strftime("%B %Y")}'


# =============================================================================
# CURRENCY FORMATTER — Indian Lakh format: ₹1,23,456
# =============================================================================

def format_inr(amount):
    """
    Formats a Decimal/float/int as Indian Rupee Lakh notation.
    Examples:
        10033854  → ₹1,00,33,854
        45000     → ₹45,000
        0         → ₹0
    """
    if amount is None:
        return '₹0'
    amount = int(amount)
    if amount < 0:
        return f'-{format_inr(-amount)}'
    s = str(amount)
    if len(s) <= 3:
        return f'₹{s}'
    # Last 3 digits
    result = s[-3:]
    s = s[:-3]
    while len(s) > 2:
        result = s[-2:] + ',' + result
        s = s[:-2]
    if s:
        result = s + ',' + result
    return f'₹{result}'


def format_inr_short(amount):
    """
    Short format for trend/comparison: 45.2L, 1.2Cr, etc.
    """
    if amount is None:
        return '₹0'
    amount = int(amount)
    if amount >= 10_000_000:  # 1 Crore
        return f'₹{amount / 10_000_000:.1f}Cr'
    elif amount >= 100_000:   # 1 Lakh
        return f'₹{amount / 100_000:.1f}L'
    elif amount >= 1000:
        return f'₹{amount / 1000:.1f}K'
    return f'₹{amount}'


# =============================================================================
# HERO KPI QUERIES — Phase 2
# 4 metrics computed on page load (small queries, all indexed)
# =============================================================================

def _get_hero_kpis(start_date, end_date):
    """
    Returns the 4 hero KPIs for the given date range.
    All queries filter by admitted_date range — the primary indexed field.
    
    Returns a dict with:
      total_revenue     — Sum of total_bill_amount for DELIVERED jobs
      total_collected   — Sum of received_amount for DELIVERED jobs  
      cars_completed    — Count of DELIVERED jobs
      outstanding       — Sum of (total_bill_amount - received_amount) for
                          PENDING + PARTIAL (all non-deleted jobs, not just delivered)
    """
    base_qs = JobCard.objects.filter(
        is_deleted=False,
        admitted_date__gte=start_date,
        admitted_date__lte=end_date,
    )

    # Delivered jobs: revenue and collected
    delivered_qs = base_qs.filter(delivered=True)
    delivered_agg = delivered_qs.aggregate(
        revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
        collected=Coalesce(Sum('received_amount'), Decimal('0'), output_field=DecimalField()),
        count=Count('id'),
    )

    # Outstanding = unpaid amount across PENDING and PARTIAL jobs (all, not just delivered)
    # Formula: total_bill_amount - received_amount for status in [PENDING, PARTIAL]
    outstanding_agg = base_qs.filter(
        payment_status__in=['PENDING', 'PARTIAL']
    ).aggregate(
        outstanding=Coalesce(
            Sum('total_bill_amount') - Sum('received_amount'),
            Decimal('0'),
            output_field=DecimalField()
        )
    )

    # Fallback: compute manually if the subtraction returns None
    if outstanding_agg['outstanding'] is None:
        out_data = base_qs.filter(
            payment_status__in=['PENDING', 'PARTIAL']
        ).aggregate(
            total=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
            paid=Coalesce(Sum('received_amount'), Decimal('0'), output_field=DecimalField()),
        )
        outstanding = out_data['total'] - out_data['paid']
    else:
        outstanding = outstanding_agg['outstanding']

    revenue   = delivered_agg['revenue']
    collected = delivered_agg['collected']
    count     = delivered_agg['count']

    return {
        'total_revenue':   format_inr(revenue),
        'total_collected': format_inr(collected),
        'cars_completed':  count,
        'outstanding':     format_inr(outstanding),
        # Raw values for trends (Phase 2+)
        'revenue_raw':     int(revenue),
        'collected_raw':   int(collected),
        'outstanding_raw': int(outstanding),
    }


# =============================================================================
# MAIN VIEW — Hero KPIs loaded on page load. Zones load via AJAX.
# =============================================================================

@owner_required
def analysis_dashboard(request):
    """
    Main analysis page.
    Phase 2: Date range filter + 4 hero KPI cards with real data.
    Zones still load via AJAX (lazy) — only hero KPIs on page load.
    """
    range_key  = request.GET.get('range', 'this_month')
    start_str  = request.GET.get('start', '')
    end_str    = request.GET.get('end', '')

    start_date, end_date, date_label = get_date_range(range_key, start_str, end_str)
    hero_kpis = _get_hero_kpis(start_date, end_date)

    context = {
        'range_key':   range_key,
        'start_date':  start_date.isoformat(),
        'end_date':    end_date.isoformat(),
        'date_label':  date_label,
        **hero_kpis,
    }
    return render(request, 'workshop/analysis/analysis_dashboard.html', context)


# =============================================================================
# AJAX ZONE ENDPOINT — Computes ONE zone per request.
# =============================================================================

@owner_required
def analysis_zone(request, zone_name):
    """
    AJAX endpoint called when owner expands a zone on the dashboard.
    Returns a partial HTML fragment for that specific zone only.

    URL: GET /analysis/zone/<zone_name>/?range=this_month
    zone_name options: revenue | mechanic | spares | customer |
                       inventory | cashbook | workshop
    """
    if zone_name not in ZONE_REGISTRY:
        raise Http404(f"Zone '{zone_name}' does not exist.")

    range_key = request.GET.get('range', 'this_month')
    start_str = request.GET.get('start', '')
    end_str   = request.GET.get('end', '')

    start_date, end_date, date_label = get_date_range(range_key, start_str, end_str)

    # Dispatch to the correct zone handler to get zone-specific data
    handlers = {
        'revenue':   _zone_revenue,
        'mechanic':  _zone_mechanic,
        'spares':    _zone_spares,
        'customer':  _zone_customer,
        'inventory': _zone_inventory,
        'cashbook':  _zone_cashbook,
        'workshop':  _zone_workshop,
    }
    zone_data = handlers[zone_name](start_date, end_date)

    context = {
        'range_key':  range_key,
        'start_date': start_date,
        'end_date':   end_date,
        'date_label': date_label,
        **zone_data,
    }

    template = f'workshop/analysis/zones/zone_{zone_name}.html'
    return render(request, template, context)


# =============================================================================
# ZONE HANDLERS (Private) — One per zone, filled in Phases 3–9
# =============================================================================

def _zone_revenue(start_date, end_date):
    """
    Zone 1: Revenue & Profit Analytics
    Phase 3 implementation — 8 KPIs + 4 chart datasets.
    
    Queries (6 total):
      1. Spare revenue + cost aggregate (SpareItem JOIN JobCard)
      2. Labour revenue aggregate (LabourItem JOIN JobCard)
      3. JobCard aggregate: discount, avg bill, payment methods
      4. Top 5 discount recipients
      5. Monthly trend (12 data points max)
      6. Daily revenue (31 data points max)
    """
    from .models import JobCardSpareItem, JobCardLabourItem

    # Base queryset — delivered, non-deleted, in date range
    base_jc = JobCard.objects.filter(
        is_deleted=False,
        delivered=True,
        admitted_date__gte=start_date,
        admitted_date__lte=end_date,
    )

    # -------------------------------------------------------------------------
    # QUERY 1: Spare parts revenue and cost
    # -------------------------------------------------------------------------
    spare_agg = JobCardSpareItem.objects.filter(
        job_card__is_deleted=False,
        job_card__delivered=True,
        job_card__admitted_date__gte=start_date,
        job_card__admitted_date__lte=end_date,
    ).aggregate(
        spare_revenue=Coalesce(Sum('total_price'),   Decimal('0'), output_field=DecimalField()),
        spare_cost=Coalesce(Sum('unit_price'),       Decimal('0'), output_field=DecimalField()),
        spare_qty=Coalesce(Sum('quantity'),          Decimal('0'), output_field=DecimalField()),
    )
    spare_revenue = spare_agg['spare_revenue']
    spare_cost    = spare_agg['spare_cost']
    spare_profit  = spare_revenue - spare_cost

    # -------------------------------------------------------------------------
    # QUERY 2: Labour revenue
    # -------------------------------------------------------------------------
    labour_agg = JobCardLabourItem.objects.filter(
        job_card__is_deleted=False,
        job_card__delivered=True,
        job_card__admitted_date__gte=start_date,
        job_card__admitted_date__lte=end_date,
    ).aggregate(
        labour_revenue=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField()),
    )
    labour_revenue = labour_agg['labour_revenue']

    # -------------------------------------------------------------------------
    # QUERY 3: JobCard-level stats (discount, avg bill, payment methods, totals)
    # -------------------------------------------------------------------------
    jc_agg = base_jc.aggregate(
        total_revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
        total_collected=Coalesce(Sum('received_amount'),  Decimal('0'), output_field=DecimalField()),
        total_discount=Coalesce(Sum('discount_amount'),   Decimal('0'), output_field=DecimalField()),
        avg_bill=Coalesce(Avg('total_bill_amount'),       Decimal('0'), output_field=DecimalField()),
        total_jobs=Count('id'),
        jobs_with_discount=Count('id', filter=Q(discount_amount__gt=0)),
    )
    total_revenue   = jc_agg['total_revenue']
    total_collected = jc_agg['total_collected']
    total_discount  = jc_agg['total_discount']
    avg_bill        = jc_agg['avg_bill']
    total_jobs      = jc_agg['total_jobs']

    # Collection efficiency %
    collection_pct = (
        round((int(total_collected) / int(total_revenue)) * 100, 1)
        if total_revenue and int(total_revenue) > 0 else 0
    )

    # -------------------------------------------------------------------------
    # QUERY 4: Payment method distribution (for pie chart)
    # -------------------------------------------------------------------------
    payment_method_qs = base_jc.values('payment_method').annotate(
        count=Count('id'),
        revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
    ).order_by('-count')

    payment_methods = [
        {
            'label': (pm['payment_method'] or 'Unknown').replace('_', ' ').title(),
            'count': pm['count'],
            'revenue': int(pm['revenue']),
        }
        for pm in payment_method_qs
    ]

    # -------------------------------------------------------------------------
    # QUERY 5: Monthly revenue trend (up to 12 months)
    # -------------------------------------------------------------------------
    monthly_qs = base_jc.annotate(
        month=TruncMonth('admitted_date')
    ).values('month').annotate(
        revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
        count=Count('id'),
    ).order_by('month')

    monthly_labels  = [m['month'].strftime('%b %Y') for m in monthly_qs]
    monthly_revenue = [int(m['revenue']) for m in monthly_qs]
    monthly_counts  = [m['count'] for m in monthly_qs]

    # -------------------------------------------------------------------------
    # QUERY 6: Daily OR Monthly revenue (auto-scales to range)
    # >< 45 days  → daily bars (max ~45 points)
    # ≥ 45 days  → monthly bars (12 points max)
    # -------------------------------------------------------------------------
    range_days = (end_date - start_date).days
    use_monthly = range_days >= 45

    if use_monthly:
        period_qs = base_jc.annotate(
            period=TruncMonth('admitted_date')
        ).values('period').annotate(
            revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
            count=Count('id'),
        ).order_by('period')
        daily_labels  = [d['period'].strftime('%b %Y') for d in period_qs]
        chart_title_suffix = '(Monthly)'
    else:
        period_qs = base_jc.annotate(
            period=TruncDay('admitted_date')
        ).values('period').annotate(
            revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
            count=Count('id'),
        ).order_by('period')
        daily_labels  = [d['period'].strftime('%d %b') for d in period_qs]
        chart_title_suffix = '(Daily)'

    daily_revenue = [int(d['revenue']) for d in period_qs]
    # Build a human-friendly period label from dates (date_label is in the view,
    # not passed to this handler — so derive it here)
    period_label = f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"
    daily_chart_title = f'{period_label} {chart_title_suffix}'

    # -------------------------------------------------------------------------
    # QUERY 7: Top 5 customers by revenue (in this period)
    # -------------------------------------------------------------------------
    top_customers = list(
        base_jc.values('customer_name').annotate(
            revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
            count=Count('id'),
        ).order_by('-revenue')[:5]
    )

    return {
        # --- KPI Cards ---
        'spare_revenue':      format_inr(spare_revenue),
        'spare_cost':         format_inr(spare_cost),
        'spare_profit':       format_inr(spare_profit),
        'labour_revenue':     format_inr(labour_revenue),
        'total_discount':     format_inr(total_discount),
        'collection_pct':     collection_pct,
        'avg_bill':           format_inr(avg_bill),
        'total_jobs':         total_jobs,
        'total_revenue_raw':  int(total_revenue),
        'total_collected_raw':int(total_collected),
        # --- Chart: Revenue split (Spares vs Labour) ---
        'split_labels':       json.dumps(['Spare Parts', 'Labour']),
        'split_data':         json.dumps([int(spare_revenue), int(labour_revenue)]),
        # --- Chart: Payment methods ---
        'payment_labels':     json.dumps([pm['label'] for pm in payment_methods]),
        'payment_counts':     json.dumps([pm['count'] for pm in payment_methods]),
        'payment_revenue':    json.dumps([pm['revenue'] for pm in payment_methods]),
        # --- Chart: Monthly trend ---
        'monthly_labels':     json.dumps(monthly_labels),
        'monthly_revenue':    json.dumps(monthly_revenue),
        'monthly_counts':     json.dumps(monthly_counts),
        # --- Chart: Daily/Monthly bar (auto-scaled) ---
        'daily_labels':       json.dumps(daily_labels),
        'daily_revenue':      json.dumps(daily_revenue),
        'daily_chart_title':  daily_chart_title,
        # --- Top 5 customers table ---
        'top_customers':      top_customers,
    }


def _zone_mechanic(start_date, end_date):
    """
    Zone 2: Mechanic Performance Rankings.
    Queries (all SQL-side, no Python loops):
      1. Per-mechanic aggregates: jobs, revenue, avg turnaround days
      2. Active (in-progress) job count per mechanic
      3. Hero KPIs: top earner, top job-count, fastest turnaround
      4. Monthly trend for top-5 mechanics (jobs per month)
    Performance score (SQLite-safe, computed in Python after the aggregate):
      score = 0.60 * rev_pct + 0.25 * jobs_pct + 0.15 * speed_pct
      (all normalised 0-100 against the max in the current cohort)
    """
    # Base queryset — only non-deleted, date-filtered job cards
    base_jc = JobCard.objects.filter(
        is_deleted=False,
        admitted_date__gte=start_date,
        admitted_date__lte=end_date,
    )

    # -------------------------------------------------------------------------
    # QUERY 1: Per-mechanic delivered stats
    # -------------------------------------------------------------------------
    mechanic_qs = (
        base_jc
        .filter(lead_mechanic__isnull=False)
        .values('lead_mechanic__id', 'lead_mechanic__name')
        .annotate(
            jobs_done=Count('id'),
            total_revenue=Coalesce(
                Sum('total_bill_amount'), Decimal('0'),
                output_field=DecimalField()
            ),
            avg_turnaround=Coalesce(
                # SQLite: DateField - DateField → timedelta in microseconds.
                # Divide by 86400000000 (µs per day) to get days.
                Avg(
                    ExpressionWrapper(
                        F('discharged_date') - F('admitted_date'),
                        output_field=FloatField()
                    )
                ),
                0.0,
                output_field=FloatField()
            ),
        )
        .order_by('-total_revenue')
    )
    mechanics = list(mechanic_qs)

    # -------------------------------------------------------------------------
    # QUERY 2: Active (in-progress) jobs per mechanic (not yet delivered)
    # -------------------------------------------------------------------------
    active_qs = (
        JobCard.objects.filter(
            is_deleted=False,
            delivered=False,
            lead_mechanic__isnull=False,
        )
        .values('lead_mechanic__id')
        .annotate(active_count=Count('id'))
    )
    active_map = {row['lead_mechanic__id']: row['active_count'] for row in active_qs}

    # -------------------------------------------------------------------------
    # QUERY 3: Compute weighted performance scores (Python side – tiny list)
    # -------------------------------------------------------------------------
    if mechanics:
        MICROSECONDS_PER_DAY = 86_400_000_000.0
        max_rev   = max((m['total_revenue'] or 0) for m in mechanics) or 1
        max_jobs  = max((m['jobs_done']     or 0) for m in mechanics) or 1
        # For speed: lower turnaround = better. Convert raw µs → days first.
        valid_ta_raw = [m['avg_turnaround'] for m in mechanics if (m['avg_turnaround'] or 0) > 0]
        if valid_ta_raw:
            max_ta_raw = max(valid_ta_raw)
            max_ta = max_ta_raw / MICROSECONDS_PER_DAY if max_ta_raw > 1000 else max_ta_raw
        else:
            max_ta = 1.0

        for m in mechanics:
            rev_pct   = float(m['total_revenue'] or 0) / float(max_rev)   * 100
            jobs_pct  = float(m['jobs_done']     or 0) / float(max_jobs)  * 100
            ta_raw    = float(m['avg_turnaround'] or 0)
            ta        = ta_raw / MICROSECONDS_PER_DAY if ta_raw > 1000 else ta_raw
            speed_pct = (1 - ta / max_ta) * 100 if max_ta > 0 and ta > 0 else 100

            m['score']           = round(0.60 * rev_pct + 0.25 * jobs_pct + 0.15 * speed_pct, 1)
            m['active_jobs']     = active_map.get(m['lead_mechanic__id'], 0)
            m['avg_days']        = round(ta, 1)
            m['revenue_display'] = int(m['total_revenue'] or 0)

        mechanics.sort(key=lambda m: m['score'], reverse=True)

    # -------------------------------------------------------------------------
    # QUERY 4: Hero KPIs
    # -------------------------------------------------------------------------
    top_earner   = mechanics[0]  if mechanics else None
    top_jobs     = max(mechanics, key=lambda m: m['jobs_done'],     default=None) if mechanics else None
    fastest      = min(
        [m for m in mechanics if m['avg_days'] > 0],
        key=lambda m: m['avg_days'],
        default=None
    )
    total_mechanics = Mechanic.objects.filter(is_active=True).count()

    # -------------------------------------------------------------------------
    # QUERY 5: Monthly jobs trend for ALL mechanics (chart)
    # -------------------------------------------------------------------------
    monthly_trend_qs = (
        base_jc
        .filter(lead_mechanic__isnull=False)
        .annotate(month=TruncMonth('admitted_date'))
        .values('month', 'lead_mechanic__name')
        .annotate(jobs=Count('id'))
        .order_by('month')
    )

    # Pivot into {mechanic_name: [jobs per month]}
    months_set = sorted({row['month'] for row in monthly_trend_qs})
    month_labels = [m.strftime('%b %Y') for m in months_set]
    month_index  = {m: i for i, m in enumerate(months_set)}

    mech_trend_map = {}
    for row in monthly_trend_qs:
        name = row['lead_mechanic__name']
        if name not in mech_trend_map:
            mech_trend_map[name] = [0] * len(months_set)
        mech_trend_map[name][month_index[row['month']]] = row['jobs']

    # Keep only top-5 mechanics by total jobs (chart legibility)
    top5_names  = sorted(mech_trend_map, key=lambda n: sum(mech_trend_map[n]), reverse=True)[:5]
    trend_datasets = [
        {'name': n, 'data': mech_trend_map[n]} for n in top5_names
    ]

    period_label = f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"

    return {
        'mechanics':          mechanics,
        'top_earner':         top_earner,
        'top_jobs':           top_jobs,
        'fastest':            fastest,
        'total_mechanics':    total_mechanics,
        'period_label':       period_label,
        # Charts
        'month_labels':       json.dumps(month_labels),
        'trend_datasets':     json.dumps(trend_datasets),
        'chart_names':        json.dumps([m['lead_mechanic__name'] for m in mechanics[:10]]),
        'chart_jobs':         json.dumps([m['jobs_done']      for m in mechanics[:10]]),
        'chart_revenue':      json.dumps([m['revenue_display'] for m in mechanics[:10]]),
        'chart_scores':       json.dumps([m['score']           for m in mechanics[:10]]),
    }


def _zone_spares(start_date, end_date):
    """
    Zone 3: Spare Parts Intelligence — Phase 5.
    Queries (6 total, all SQL-side):
      1. Hero KPIs: total revenue, cost, profit, margin%, total parts used
      2. Top 20 spare parts by usage quantity
      3. Top 10 most profitable parts (total_price - unit_price) * qty
      4. Top 5 spare shops by revenue
      5. Monthly spare revenue trend (line chart)
      6. Parts status distribution (PENDING/ORDERED/RECEIVED)
    """
    from .models import JobCardSpareItem

    # Base queryset: spare items linked to non-deleted job cards in date range
    base_qs = JobCardSpareItem.objects.filter(
        job_card__isnull=False,
        job_card__is_deleted=False,
        job_card__admitted_date__gte=start_date,
        job_card__admitted_date__lte=end_date,
    )

    # -------------------------------------------------------------------------
    # QUERY 1: Hero KPIs
    # -------------------------------------------------------------------------
    agg = base_qs.aggregate(
        total_revenue=Coalesce(Sum('total_price'), Decimal('0'), output_field=DecimalField()),
        total_cost=Coalesce(Sum('unit_price'), Decimal('0'), output_field=DecimalField()),
        total_qty=Coalesce(Sum('quantity'), Decimal('0'), output_field=DecimalField()),
        total_items=Count('id'),
    )
    total_revenue = agg['total_revenue'] or Decimal('0')
    total_cost    = agg['total_cost']    or Decimal('0')
    total_qty     = agg['total_qty']     or Decimal('0')
    total_profit  = total_revenue - total_cost
    margin_pct    = round(float(total_profit) / float(total_revenue) * 100, 1) if total_revenue else 0

    # -------------------------------------------------------------------------
    # QUERY 2: Top 20 spare parts by quantity used
    # -------------------------------------------------------------------------
    top_by_qty = (
        base_qs
        .filter(spare_part_name__isnull=False)
        .values('spare_part_name')
        .annotate(
            qty=Coalesce(Sum('quantity'), Decimal('0'), output_field=DecimalField()),
            revenue=Coalesce(Sum('total_price'), Decimal('0'), output_field=DecimalField()),
            jobs=Count('job_card', distinct=True),
        )
        .order_by('-qty')[:20]
    )

    # -------------------------------------------------------------------------
    # QUERY 3: Top 10 most profitable spare parts
    # -------------------------------------------------------------------------
    top_by_profit = (
        base_qs
        .filter(spare_part_name__isnull=False, total_price__isnull=False, unit_price__isnull=False)
        .values('spare_part_name')
        .annotate(
            profit=Coalesce(
                Sum(
                    ExpressionWrapper(
                        (F('total_price') - F('unit_price')),
                        output_field=DecimalField()
                    )
                ),
                Decimal('0'),
                output_field=DecimalField()
            ),
            revenue=Coalesce(Sum('total_price'), Decimal('0'), output_field=DecimalField()),
        )
        .order_by('-profit')[:10]
    )

    # -------------------------------------------------------------------------
    # QUERY 4: Top 5 shops by spare revenue
    # -------------------------------------------------------------------------
    top_shops = (
        base_qs
        .filter(shop_name__isnull=False)
        .values('shop_name')
        .annotate(
            revenue=Coalesce(Sum('total_price'), Decimal('0'), output_field=DecimalField()),
            parts=Count('id'),
        )
        .order_by('-revenue')[:5]
    )

    # -------------------------------------------------------------------------
    # QUERY 5: Monthly spare revenue trend
    # -------------------------------------------------------------------------
    monthly_trend = (
        base_qs
        .annotate(month=TruncMonth('job_card__admitted_date'))
        .values('month')
        .annotate(revenue=Coalesce(Sum('total_price'), Decimal('0'), output_field=DecimalField()))
        .order_by('month')
    )
    trend_labels  = [r['month'].strftime('%b %Y') for r in monthly_trend if r['month']]
    trend_revenue = [int(r['revenue'] or 0) for r in monthly_trend if r['month']]

    # -------------------------------------------------------------------------
    # QUERY 6: Parts status breakdown (PENDING / ORDERED / RECEIVED)
    # -------------------------------------------------------------------------
    status_qs = (
        base_qs
        .values('status')
        .annotate(cnt=Count('id'))
    )
    status_map = {row['status']: row['cnt'] for row in status_qs}
    status_pending  = status_map.get('PENDING',  0)
    status_ordered  = status_map.get('ORDERED',  0)
    status_received = status_map.get('RECEIVED', 0)

    period_label = f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"

    return {
        # Hero KPIs
        'spare_revenue':    int(total_revenue),
        'spare_cost':       int(total_cost),
        'spare_profit':     int(total_profit),
        'spare_margin':     margin_pct,
        'total_qty':        int(total_qty),
        'total_items':      agg['total_items'],
        # Tables
        'top_by_qty':       list(top_by_qty),
        'top_by_profit':    list(top_by_profit),
        'top_shops':        list(top_shops),
        # Status
        'status_pending':   status_pending,
        'status_ordered':   status_ordered,
        'status_received':  status_received,
        'period_label':     period_label,
        # Chart data
        'trend_labels':     json.dumps(trend_labels),
        'trend_revenue':    json.dumps(trend_revenue),
        'chart_part_names': json.dumps([r['spare_part_name'] for r in top_by_qty][:15]),
        'chart_part_qty':   json.dumps([float(r['qty'] or 0) for r in top_by_qty][:15]),
        'chart_shop_names': json.dumps([r['shop_name'] for r in top_shops]),
        'chart_shop_rev':   json.dumps([int(r['revenue'] or 0) for r in top_shops]),
        'status_labels':    json.dumps(['Pending', 'Ordered', 'Received']),
        'status_data':      json.dumps([status_pending, status_ordered, status_received]),
    }


def _zone_customer(start_date, end_date):
    """
    Zone 4: Customer & Vehicle Intelligence — Phase 6.
    Queries (all SQL-side):
      1. Hero KPIs: unique customers, unique vehicles, avg bill per customer
      2. Top 10 customers by revenue
      3. Top 10 customers by job count
      4. Top 10 car brands by job count
      5. Top 10 car models by job count
      6. Monthly unique customers trend
      7. Returning vs new customer breakdown (customers with > 1 job vs == 1)
    """
    base_jc = JobCard.objects.filter(
        is_deleted=False,
        admitted_date__gte=start_date,
        admitted_date__lte=end_date,
    )

    # -------------------------------------------------------------------------
    # QUERY 1: Hero KPIs
    # -------------------------------------------------------------------------
    agg = base_jc.aggregate(
        total_jobs=Count('id'),
        total_revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
    )
    # Unique customers in period (distinct by customer_name)
    unique_customers = (
        base_jc.filter(customer_name__isnull=False)
        .values('customer_name').distinct().count()
    )
    unique_vehicles = (
        base_jc.filter(registration_number__isnull=False)
        .values('registration_number').distinct().count()
    )
    total_revenue = agg['total_revenue'] or Decimal('0')
    avg_bill = round(float(total_revenue) / unique_customers, 0) if unique_customers else 0

    # -------------------------------------------------------------------------
    # QUERY 2: Top 10 customers by total revenue
    # -------------------------------------------------------------------------
    top_customers_rev = (
        base_jc.filter(customer_name__isnull=False)
        .values('customer_name')
        .annotate(
            revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
            jobs=Count('id'),
        )
        .order_by('-revenue')[:10]
    )

    # -------------------------------------------------------------------------
    # QUERY 3: Top 10 customers by job count
    # -------------------------------------------------------------------------
    top_customers_jobs = (
        base_jc.filter(customer_name__isnull=False)
        .values('customer_name')
        .annotate(
            jobs=Count('id'),
            revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
        )
        .order_by('-jobs')[:10]
    )

    # -------------------------------------------------------------------------
    # QUERY 4: Top car brands by job count
    # -------------------------------------------------------------------------
    top_brands = (
        base_jc.filter(brand_name__isnull=False)
        .values('brand_name')
        .annotate(jobs=Count('id'))
        .order_by('-jobs')[:10]
    )

    # -------------------------------------------------------------------------
    # QUERY 5: Top car models by job count
    # -------------------------------------------------------------------------
    top_models = (
        base_jc.filter(model_name__isnull=False)
        .values('brand_name', 'model_name')
        .annotate(jobs=Count('id'))
        .order_by('-jobs')[:10]
    )

    # -------------------------------------------------------------------------
    # QUERY 6: Monthly unique customers trend
    # -------------------------------------------------------------------------
    monthly_customers = (
        base_jc.filter(customer_name__isnull=False)
        .annotate(month=TruncMonth('admitted_date'))
        .values('month')
        .annotate(unique_cust=Count('customer_name', distinct=True))
        .order_by('month')
    )
    cust_trend_labels = [r['month'].strftime('%b %Y') for r in monthly_customers if r['month']]
    cust_trend_data   = [r['unique_cust'] for r in monthly_customers if r['month']]

    # -------------------------------------------------------------------------
    # QUERY 7: Returning (>1 job) vs New (1 job) customers
    # -------------------------------------------------------------------------
    cust_job_counts = (
        base_jc.filter(customer_name__isnull=False)
        .values('customer_name')
        .annotate(job_count=Count('id'))
    )
    returning = sum(1 for c in cust_job_counts if c['job_count'] > 1)
    new_cust  = sum(1 for c in cust_job_counts if c['job_count'] == 1)

    period_label = f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"

    top_brands_list  = list(top_brands)
    top_models_list  = list(top_models)
    top_cust_rev     = list(top_customers_rev)
    top_cust_jobs    = list(top_customers_jobs)

    return {
        # Hero KPIs
        'unique_customers':     unique_customers,
        'unique_vehicles':      unique_vehicles,
        'avg_bill':             int(avg_bill),
        'total_jobs':           agg['total_jobs'],
        'period_label':         period_label,
        # Tables
        'top_customers_rev':    top_cust_rev,
        'top_customers_jobs':   top_cust_jobs,
        'top_brands':           top_brands_list,
        'top_models':           top_models_list,
        # Loyalty
        'returning_customers':  returning,
        'new_customers':        new_cust,
        # Chart data
        'cust_trend_labels':    json.dumps(cust_trend_labels),
        'cust_trend_data':      json.dumps(cust_trend_data),
        'brand_labels':         json.dumps([r['brand_name'] for r in top_brands_list]),
        'brand_data':           json.dumps([r['jobs'] for r in top_brands_list]),
        'model_labels':         json.dumps([f"{r['brand_name']} {r['model_name']}" for r in top_models_list]),
        'model_data':           json.dumps([r['jobs'] for r in top_models_list]),
        'loyalty_labels':       json.dumps(['Returning', 'New']),
        'loyalty_data':         json.dumps([returning, new_cust]),
    }


def _zone_inventory(start_date, end_date):
    """
    Zone 5: Inventory & Supplier Health — Phase 7.
    Two separate supplier systems:
      A) SpareShop (workshop.models) — spare parts shops, tracked via JobCardSpareItem
      B) SupplierShop (inventory.models) — inventory restock suppliers

    Queries:
      1. Spare shop summary: total purchased, total paid, top 10 by purchase amount
      2. Monthly spare purchases trend (by ordered_date on spare items)
      3. Inventory supplier summary: total billed, total paid, top 10
      4. Monthly inventory purchases trend (by bill_date on SupplierRestockBill)
      5. Pending balance totals (what workshop still owes)
    """
    from .models import SpareShop, JobCardSpareItem, SpareShopPayment
    from inventory.models import SupplierShop, SupplierRestockBill, SupplierPayment

    period_label = f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"

    # =========================================================================
    # SECTION A: Spare Shops (workshop spare parts suppliers)
    # =========================================================================

    # QUERY 1a: All active spare shops with denormalised totals
    spare_shops = list(
        SpareShop.objects.filter(is_trashed=False)
        .values('id', 'name', 'total_purchased_amount', 'total_paid_amount')
        .order_by('-total_purchased_amount')[:10]
    )
    for s in spare_shops:
        s['balance'] = int((s['total_purchased_amount'] or 0) - (s['total_paid_amount'] or 0))
        s['purchased'] = int(s['total_purchased_amount'] or 0)
        s['paid'] = int(s['total_paid_amount'] or 0)

    total_spare_purchased = sum(s['purchased'] for s in spare_shops)
    total_spare_paid      = sum(s['paid']      for s in spare_shops)
    total_spare_balance   = sum(s['balance']   for s in spare_shops)

    # QUERY 1b: Monthly spare parts purchases (ordered_date in range)
    spare_monthly = (
        JobCardSpareItem.objects.filter(
            ordered_date__gte=start_date,
            ordered_date__lte=end_date,
            shop__isnull=False,
        )
        .annotate(month=TruncMonth('ordered_date'))
        .values('month')
        .annotate(
            cost=Coalesce(Sum('unit_price'), Decimal('0'), output_field=DecimalField()),
            items=Count('id'),
        )
        .order_by('month')
    )
    spare_trend_labels = [r['month'].strftime('%b %Y') for r in spare_monthly if r['month']]
    spare_trend_cost   = [int(r['cost'] or 0) for r in spare_monthly if r['month']]

    # =========================================================================
    # SECTION B: Inventory Supplier Shops
    # =========================================================================

    # QUERY 2a: All active inventory suppliers with denormalised totals
    inv_suppliers = list(
        SupplierShop.objects.filter(is_active=True)
        .values('id', 'name', 'total_billed_amount', 'total_paid_amount')
        .order_by('-total_billed_amount')[:10]
    )
    for s in inv_suppliers:
        s['balance']  = int((s['total_billed_amount'] or 0) - (s['total_paid_amount'] or 0))
        s['billed']   = int(s['total_billed_amount'] or 0)
        s['paid']     = int(s['total_paid_amount'] or 0)

    total_inv_billed  = sum(s['billed']  for s in inv_suppliers)
    total_inv_paid    = sum(s['paid']    for s in inv_suppliers)
    total_inv_balance = sum(s['balance'] for s in inv_suppliers)

    # QUERY 2b: Monthly inventory restock bills in date range
    inv_monthly = (
        SupplierRestockBill.objects.filter(
            bill_date__gte=start_date,
            bill_date__lte=end_date,
        )
        .annotate(month=TruncMonth('bill_date'))
        .values('month')
        .annotate(
            billed=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F('total_amount') - F('discount_amount'),
                        output_field=DecimalField()
                    )
                ),
                Decimal('0'),
                output_field=DecimalField()
            ),
        )
        .order_by('month')
    )
    inv_trend_labels = [r['month'].strftime('%b %Y') for r in inv_monthly if r['month']]
    inv_trend_billed = [int(r['billed'] or 0) for r in inv_monthly if r['month']]

    return {
        # Spare shops section
        'spare_shops':            spare_shops,
        'total_spare_purchased':  total_spare_purchased,
        'total_spare_paid':       total_spare_paid,
        'total_spare_balance':    total_spare_balance,
        # Inventory suppliers section
        'inv_suppliers':          inv_suppliers,
        'total_inv_billed':       total_inv_billed,
        'total_inv_paid':         total_inv_paid,
        'total_inv_balance':      total_inv_balance,
        'period_label':           period_label,
        # Chart data
        'spare_trend_labels':     json.dumps(spare_trend_labels),
        'spare_trend_cost':       json.dumps(spare_trend_cost),
        'inv_trend_labels':       json.dumps(inv_trend_labels),
        'inv_trend_billed':       json.dumps(inv_trend_billed),
        'spare_shop_names':       json.dumps([s['name'] for s in spare_shops]),
        'spare_shop_purchased':   json.dumps([s['purchased'] for s in spare_shops]),
        'spare_shop_balance':     json.dumps([s['balance'] for s in spare_shops]),
        'inv_supplier_names':     json.dumps([s['name'] for s in inv_suppliers]),
        'inv_supplier_billed':    json.dumps([s['billed'] for s in inv_suppliers]),
    }


def _zone_cashbook(start_date, end_date):
    """
    Zone 6: Cashbook & P/L Summary — Phase 8.
    Queries (all SQL-side):
      1. Total income, total expenses, net profit from CashbookEntry
      2. Top 10 expense categories
      3. Top 5 income categories
      4. Monthly income vs expense trend
      5. Payment method breakdown from JobCard collections
      6. JobCard payment status breakdown (PENDING/PARTIAL/PAID/BULK_PAID)
    """
    from .models import CashbookEntry

    period_label = f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"

    base_cb = CashbookEntry.objects.filter(
        date__gte=start_date,
        date__lte=end_date,
    )

    # ── QUERY 1: Total income and expenses ──
    agg = base_cb.aggregate(
        total_income=Coalesce(
            Sum('amount', filter=Q(entry_type='INCOME')),
            Decimal('0'), output_field=DecimalField()
        ),
        total_expense=Coalesce(
            Sum('amount', filter=Q(entry_type='EXPENSE')),
            Decimal('0'), output_field=DecimalField()
        ),
    )
    total_income  = agg['total_income']  or Decimal('0')
    total_expense = agg['total_expense'] or Decimal('0')
    net_profit    = total_income - total_expense

    # ── QUERY 2: Top 10 expense categories ──
    top_expenses = list(
        base_cb.filter(entry_type='EXPENSE')
        .values('category')
        .annotate(total=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField()))
        .order_by('-total')[:10]
    )

    # ── QUERY 3: Top 5 income categories ──
    top_income_cats = list(
        base_cb.filter(entry_type='INCOME')
        .values('category')
        .annotate(total=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField()))
        .order_by('-total')[:5]
    )

    # ── QUERY 4: Monthly income vs expense trend ──
    monthly = (
        base_cb
        .annotate(month=TruncMonth('date'))
        .values('month', 'entry_type')
        .annotate(total=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField()))
        .order_by('month')
    )
    month_map = {}
    for row in monthly:
        if not row['month']:
            continue
        lbl = row['month'].strftime('%b %Y')
        if lbl not in month_map:
            month_map[lbl] = {'INCOME': 0, 'EXPENSE': 0}
        month_map[lbl][row['entry_type']] = int(row['total'] or 0)
    cb_trend_labels   = list(month_map.keys())
    cb_trend_income   = [month_map[l]['INCOME']  for l in cb_trend_labels]
    cb_trend_expense  = [month_map[l]['EXPENSE'] for l in cb_trend_labels]
    cb_trend_profit   = [month_map[l]['INCOME'] - month_map[l]['EXPENSE'] for l in cb_trend_labels]

    # ── QUERY 5: Payment method breakdown from delivered job cards ──
    base_jc = JobCard.objects.filter(
        is_deleted=False, delivered=True,
        discharged_date__gte=start_date,
        discharged_date__lte=end_date,
    )
    pay_method_qs = (
        base_jc.filter(payment_method__isnull=False)
        .values('payment_method')
        .annotate(cnt=Count('id'), revenue=Coalesce(Sum('received_amount'), Decimal('0'), output_field=DecimalField()))
        .order_by('-revenue')
    )
    pay_methods = list(pay_method_qs)
    pm_labels   = [r['payment_method'] for r in pay_methods]
    pm_revenue  = [int(r['revenue'] or 0) for r in pay_methods]

    # ── QUERY 6: Payment status breakdown across all active jobs in period ──
    status_qs = (
        JobCard.objects.filter(
            is_deleted=False,
            admitted_date__gte=start_date,
            admitted_date__lte=end_date,
        )
        .values('payment_status')
        .annotate(cnt=Count('id'))
    )
    ps_map = {r['payment_status']: r['cnt'] for r in status_qs}

    return {
        # KPIs
        'cb_income':          int(total_income),
        'cb_expense':         int(total_expense),
        'cb_net':             int(net_profit),
        'period_label':       period_label,
        # Tables
        'top_expenses':       top_expenses,
        'top_income_cats':    top_income_cats,
        # Payment method
        'pay_methods':        pay_methods,
        'ps_pending':         ps_map.get('PENDING', 0),
        'ps_partial':         ps_map.get('PARTIAL', 0),
        'ps_paid':            ps_map.get('PAID', 0),
        'ps_bulk':            ps_map.get('BULK_PAID', 0),
        # Charts
        'cb_trend_labels':    json.dumps(cb_trend_labels),
        'cb_trend_income':    json.dumps(cb_trend_income),
        'cb_trend_expense':   json.dumps(cb_trend_expense),
        'cb_trend_profit':    json.dumps(cb_trend_profit),
        'pm_labels':          json.dumps(pm_labels),
        'pm_revenue':         json.dumps(pm_revenue),
        'expense_cat_labels': json.dumps([r['category'] for r in top_expenses]),
        'expense_cat_vals':   json.dumps([int(r['total'] or 0) for r in top_expenses]),
        'ps_labels':          json.dumps(['Pending', 'Partial', 'Paid', 'Bulk Paid']),
        'ps_data':            json.dumps([ps_map.get('PENDING', 0), ps_map.get('PARTIAL', 0),
                                          ps_map.get('PAID', 0), ps_map.get('BULK_PAID', 0)]),
    }


def _zone_workshop(start_date, end_date):
    """
    Zone 7: Workshop Operational KPIs — Phase 9.
    Queries:
      1. Job counts by status (active, delivered, on-hold)
      2. Avg turnaround time (delivered jobs only)
      3. Delivery rate %
      4. Monthly jobs completed trend
      5. Jobs by brand (top 10)
      6. On-hold reasons / count
      7. Revenue per job (avg)
    """
    period_label = f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"

    base_jc = JobCard.objects.filter(
        is_deleted=False,
        admitted_date__gte=start_date,
        admitted_date__lte=end_date,
    )

    # ── QUERY 1: Hero KPIs — job counts ──
    agg = base_jc.aggregate(
        total_jobs=Count('id'),
        delivered_jobs=Count('id', filter=Q(delivered=True)),
        active_jobs=Count('id', filter=Q(delivered=False)),
        on_hold_jobs=Count('id', filter=Q(on_hold=True)),
        total_revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()),
        total_collected=Coalesce(Sum('received_amount'), Decimal('0'), output_field=DecimalField()),
    )
    total_jobs     = agg['total_jobs']     or 0
    delivered_jobs = agg['delivered_jobs'] or 0
    active_jobs    = agg['active_jobs']    or 0
    on_hold_jobs   = agg['on_hold_jobs']   or 0
    delivery_rate  = round(delivered_jobs / total_jobs * 100, 1) if total_jobs else 0
    avg_revenue    = round(float(agg['total_revenue'] or 0) / total_jobs, 0) if total_jobs else 0

    # ── QUERY 2: Avg turnaround (delivered + has discharged_date) ──
    # SQLite: DateField diff = microseconds → divide by 86_400_000_000
    delivered_qs = base_jc.filter(
        delivered=True,
        discharged_date__isnull=False,
    )
    avg_days_raw = delivered_qs.aggregate(
        avg=Avg(
            ExpressionWrapper(
                F('discharged_date') - F('admitted_date'),
                output_field=DurationField()
            )
        )
    )['avg']
    if avg_days_raw is not None:
        try:
            avg_turnaround = round(avg_days_raw.total_seconds() / 86400, 1)
        except AttributeError:
            avg_turnaround = round(avg_days_raw / 86_400_000_000.0, 1)
    else:
        avg_turnaround = 0

    # ── QUERY 3: Monthly jobs completed trend ──
    monthly_jobs = (
        base_jc
        .annotate(month=TruncMonth('admitted_date'))
        .values('month')
        .annotate(
            total=Count('id'),
            completed=Count('id', filter=Q(delivered=True)),
        )
        .order_by('month')
    )
    ws_trend_labels    = [r['month'].strftime('%b %Y') for r in monthly_jobs if r['month']]
    ws_trend_total     = [r['total']     for r in monthly_jobs if r['month']]
    ws_trend_completed = [r['completed'] for r in monthly_jobs if r['month']]

    # ── QUERY 4: Top 10 brands by job count ──
    top_brands_ws = list(
        base_jc.filter(brand_name__isnull=False)
        .values('brand_name')
        .annotate(jobs=Count('id'), revenue=Coalesce(Sum('total_bill_amount'), Decimal('0'), output_field=DecimalField()))
        .order_by('-jobs')[:10]
    )

    # ── QUERY 5: Payment status (how well collected) ──
    ps_qs = (
        base_jc
        .values('payment_status')
        .annotate(cnt=Count('id'))
    )
    ps_map = {r['payment_status']: r['cnt'] for r in ps_qs}

    return {
        # KPIs
        'total_jobs':        total_jobs,
        'delivered_jobs':    delivered_jobs,
        'active_jobs':       active_jobs,
        'on_hold_jobs':      on_hold_jobs,
        'delivery_rate':     delivery_rate,
        'avg_turnaround':    avg_turnaround,
        'avg_revenue':       int(avg_revenue),
        'total_revenue':     int(agg['total_revenue'] or 0),
        'total_collected':   int(agg['total_collected'] or 0),
        'period_label':      period_label,
        # Tables
        'top_brands_ws':     top_brands_ws,
        # Charts
        'ws_trend_labels':   json.dumps(ws_trend_labels),
        'ws_trend_total':    json.dumps(ws_trend_total),
        'ws_trend_completed':json.dumps(ws_trend_completed),
        'ws_brand_labels':   json.dumps([r['brand_name'] for r in top_brands_ws]),
        'ws_brand_jobs':     json.dumps([r['jobs'] for r in top_brands_ws]),
        'ws_ps_labels':      json.dumps(['Pending', 'Partial', 'Paid', 'Bulk Paid']),
        'ws_ps_data':        json.dumps([ps_map.get('PENDING',0), ps_map.get('PARTIAL',0),
                                         ps_map.get('PAID',0), ps_map.get('BULK_PAID',0)]),
        'ws_status_labels':  json.dumps(['Delivered', 'Active', 'On Hold']),
        'ws_status_data':    json.dumps([delivered_jobs, active_jobs - on_hold_jobs, on_hold_jobs]),
    }
