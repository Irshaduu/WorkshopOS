"""
workshop/analysis_views.py
==========================
Owner → Analysis & Reports.

Two pages, and the split between them is the whole point of the design:

  /analysis/            PROFIT — the protected page.
                        Turnover − Expenses = Profit, for one date window.
                        This is what the owners open to decide profit
                        distribution, so it stays deliberately plain: no
                        drill-downs, no cleverness, nothing that needs
                        explaining. Every rupee on it traces to one of four
                        expense streams.

  /analysis/insights/   INSIGHTS — everything else. Mechanics, spares,
                        vehicles, fleet accounts, shops, operations. Loads one
                        section at a time over AJAX so the heavy Top-N queries
                        only run for the section actually being looked at.

All money math lives in analysis_engine.py, not here. These views resolve the
date window, call the engine, and render — so a bug in a chart can never
become a bug in the profit figure.

Owner-only throughout (@owner_required); Office and Floor never see this.
"""

from decimal import Decimal

from django.db.models import Sum, Count, Q, F, Value, DecimalField
from django.db.models.functions import Coalesce, Lower, TruncMonth
from django.http import Http404
from django.shortcuts import render

from .decorators import owner_required
from .models import (
    JobCard, JobCardSpareItem, BulkPayer, SpareShop,
)
from . import analysis_engine as engine
from .analysis_engine import MONEY, ZERO, SPARE_COST, SUPPLIER_BILL_COST, live_jobcards, _sum


# =============================================================================
# PAGE 1 — PROFIT
# =============================================================================

@owner_required
def analysis_dashboard(request):
    """
    The Profit page.

    Computed eagerly (unlike Insights): indexed aggregates whose cost is a
    function of how many months are in range, not how many rows exist, and an
    owner checking profit should get the whole picture in one load rather than
    watching cards populate one by one.

    Measured 2026-08-25 against 1,479 job cards over two years: **59 queries**
    for an unfinished period, 48 for a finished one, 47 for All Time. (The
    docstring said "~14" for a month, which had not been true for a long time.)
    The spread is the comparison: an unfinished period builds the report three
    times — the window, the comparison period, and the window trimmed to today —
    and the last two pass `disclosures=False`, which drops six footnote-only
    queries each. All Time builds it once, because it has no previous period.

    ⚠ Re-measure rather than trusting that line; it has gone stale once already:
        with CaptureQueriesContext(connection) as ctx: analysis_dashboard(req)
    """
    start, end, range_key, label = engine.resolve_period(
        request.GET.get('range'),
        request.GET.get('start'),
        request.GET.get('end'),
    )

    report = engine.build_profit_report(start, end)
    series = engine.monthly_series(start, end)
    position = engine.financial_position()

    # WHAT TO COMPARE AGAINST, and how far into THIS window to read. Both come
    # from one place (`engine.comparison_window`) because they are one decision:
    # an unfinished period is measured only as far as it has data, and against
    # the same days of the period before — see that function for why the old
    # whole-against-part comparison reported a decline on a workshop that was
    # growing.
    prev_start, prev_end, read_to, comparison_label, _partial = engine.comparison_window(start, end)
    # WHEN THERE IS NOTHING HONEST TO COMPARE AGAINST, DON'T.
    #
    # Two cases, one rule — the comparison period has to be one the system has
    # a WHOLE history for, or the percentage measures how much data exists
    # rather than how the workshop did:
    #
    #   • All Time already starts at the first record, so "the period before" is
    #     empty by definition.
    #   • A previous window reaching back past the first record is only PARTLY
    #     covered. Last Year read "7.1× vs previous" against a 2024 the system
    #     only holds five months of — true arithmetic, and not a fact about the
    #     workshop. This clears itself as history accumulates.
    #
    # A window with NO overlap at all is already handled: `pct_change` drops a
    # chip whose baseline is zero.
    first_record = engine.first_record_date()
    compare = range_key != 'all_time' and not (
        first_record is not None and prev_start < first_record <= prev_end
    )
    prev = engine.build_profit_report(prev_start, prev_end, disclosures=False) if compare else None
    # On a finished period `read_to` IS `end`, so this is the report already
    # built above; only a part-period pays for the second one.
    current = report if read_to == end else engine.build_profit_report(start, read_to, disclosures=False)

    def pct_change(now, before):
        """Percentage movement, or None when there is nothing to move from.

        A zero baseline has no percentage — "up from ₹0" is not 100%, it is
        undefined — so the chip is dropped rather than printing a made-up
        figure. `abs()` on the denominator keeps the SIGN meaningful when the
        previous period was a loss: recovering from −₹1,000 to +₹500 reads as
        an increase, which it is.
        """
        if not before:
            return None
        return float((now - before) / abs(before) * 100)

    def pct_text(pct):
        """How a movement is WRITTEN, which is not the same as how big it is.

        A small baseline makes an honest percentage enormous — July 1–25 made
        ₹25,301 and August 1–25 made ₹4,90,577, which is a true 1,838.9%. But a
        four-digit percentage carried to one decimal reads as a broken figure
        rather than a good month, and the tenth of a percent is noise at that
        size. Past 300% it is said as a multiple, which is how anybody would say
        it out loud; under 10% the decimal is kept, because there the difference
        between 2% and 2.4% is real.
        """
        if pct is None:
            return None
        size = abs(pct)
        if size >= 300:
            return f"{1 + size / 100:.1f}×".replace('.0×', '×')
        if size >= 10:
            return f"{pct:.0f}%"
        return f"{pct:.1f}%"

    # Computed once. `delta` decides the up/down arrow and its colour; `text`
    # is the wording beside it. Both come off the same number so they can never
    # point one way and read the other.
    delta = pct_change(current['profit'], prev['profit']) if compare else None

    return render(request, 'workshop/analysis/profit.html', {
        'report': report,
        'position': position,
        'range_key': range_key,
        'range_label': label,
        'period_choices': engine.PERIOD_CHOICES,
        'start': start,
        'end': end,
        'custom_start': request.GET.get('start', ''),
        'custom_end': request.GET.get('end', ''),
        # Only what the template reads. `prev`, `prev_label` and a turnover
        # delta were all passed and none was ever rendered — dead context on a
        # page whose whole point is that every figure on it is arguable.
        'comparison_label': comparison_label,
        'delta_profit': delta,
        'delta_profit_text': pct_text(delta),
        # Charts — handed to JS via json_script in the template, never |safe.
        'chart_labels': [m['label'] for m in series],
        'chart_turnover': [float(m['turnover']) for m in series],
        'chart_expenses': [float(m['expenses']) for m in series],
        'chart_profit': [float(m['profit']) for m in series],
        'chart_expense_labels': [l['label'] for l in report['expense_lines']],
        'chart_expense_values': [float(l['amount']) for l in report['expense_lines']],
        'has_data': report['turnover'] != ZERO or report['expense_total'] != ZERO,
        'multi_month': len(series) > 1,
    })


# =============================================================================
# PAGE 2 — INSIGHTS
# =============================================================================

INSIGHT_SECTIONS = [
    ('mechanics',  'Mechanics',  'bi-person-gear',  'Who generates the work, and the profit on it'),
    ('spares',     'Spares',     'bi-tools',        'Which parts move, and which actually earn'),
    ('vehicles',   'Vehicles',   'bi-car-front',    'Repeat cars, brands, and how often they return'),
    ('fleet',      'Fleet',      'bi-buildings',    'Fleet account volume, settlement and balances'),
    ('shops',      'Shops',      'bi-shop',         'Spare shops and supplies shops, by spend'),
    ('operations', 'Operations', 'bi-speedometer2', 'Workload, completion and how customers pay'),
]


@owner_required
def analysis_insights(request):
    """Shell page. Each section's data arrives via analysis_insight_section."""
    start, end, range_key, label = engine.resolve_period(
        request.GET.get('range'), request.GET.get('start'), request.GET.get('end'),
    )
    return render(request, 'workshop/analysis/insights.html', {
        'sections': INSIGHT_SECTIONS,
        'range_key': range_key,
        'range_label': label,
        'period_choices': engine.PERIOD_CHOICES,
        'custom_start': request.GET.get('start', ''),
        'custom_end': request.GET.get('end', ''),
        'start': start,
        'end': end,
    })


@owner_required
def analysis_insight_section(request, section):
    """
    AJAX endpoint — renders ONE insight section.

    Lazy by section so opening Insights never fires six sets of Top-N queries
    at once. Every handler filters by date first, aggregates in SQL, and slices
    Top-N in the query (SQL LIMIT) rather than in Python.
    """
    handlers = {
        'mechanics': _insight_mechanics,
        'spares': _insight_spares,
        'vehicles': _insight_vehicles,
        'fleet': _insight_fleet,
        'shops': _insight_shops,
        'operations': _insight_operations,
    }
    if section not in handlers:
        raise Http404("Unknown insight section")

    start, end, range_key, label = engine.resolve_period(
        request.GET.get('range'), request.GET.get('start'), request.GET.get('end'),
    )
    context = handlers[section](start, end)
    context.update({'start': start, 'end': end, 'range_label': label})
    return render(request, f'workshop/analysis/sections/{section}.html', context)


def _cards_in(start, end):
    return live_jobcards().filter(admitted_date__range=(start, end))


def _net_revenue():
    """Net revenue expression — bills minus discounts, the earned figure."""
    return F('total_bill_amount') - F('discount_amount')


# ---------------------------------------------------------------- mechanics --
def _insight_mechanics(start, end):
    """
    Per-mechanic revenue and the gross profit on it.

    Profit = the jobs' net revenue − the spare cost on those jobs. Labour has
    no direct cost so it flows through whole. Salary is deliberately NOT
    deducted per mechanic: splitting a workshop-wide wage bill across job cards
    would be an allocation *choice*, not a measurement, and this page should
    only show measured numbers. The wage bill lives on the Profit page.
    """
    cards = _cards_in(start, end).filter(lead_mechanic__isnull=False)

    rows = list(
        cards.values('lead_mechanic', 'lead_mechanic__name', 'lead_mechanic__role')
             .annotate(
                 jobs=Count('id', distinct=True),
                 revenue=Coalesce(Sum(_net_revenue(), output_field=MONEY),
                                  Value(ZERO, output_field=MONEY), output_field=MONEY),
             )
             .order_by('-revenue')
    )

    # Spare cost is fetched separately and merged by id. Annotating it onto the
    # same queryset would fan the revenue Sum out across the spare join rows —
    # the classic Django multi-join inflation bug — and silently multiply
    # revenue by the number of spares on each card.
    costs = {
        r['job_card__lead_mechanic']: r['c']
        for r in JobCardSpareItem.objects.filter(
            job_card__in=cards, job_card__lead_mechanic__isnull=False
        ).values('job_card__lead_mechanic')
         .annotate(c=Coalesce(Sum(SPARE_COST, output_field=MONEY),
                              Value(ZERO, output_field=MONEY), output_field=MONEY))
    }

    for r in rows:
        r['cost'] = costs.get(r['lead_mechanic'], ZERO)
        r['profit'] = r['revenue'] - r['cost']
        r['avg_job'] = (r['revenue'] / r['jobs']) if r['jobs'] else ZERO
        r['margin'] = float(r['profit'] / r['revenue'] * 100) if r['revenue'] else 0.0

    top = max((r['profit'] for r in rows), default=ZERO)
    for r in rows:
        r['bar'] = float(r['profit'] / top * 100) if top and r['profit'] > 0 else 0.0

    return {
        'rows': rows,
        'chart_labels': [r['lead_mechanic__name'] for r in rows[:10]],
        'chart_profit': [float(r['profit']) for r in rows[:10]],
        'chart_revenue': [float(r['revenue']) for r in rows[:10]],
        'total_jobs': sum(r['jobs'] for r in rows),
        'unassigned': _cards_in(start, end).filter(lead_mechanic__isnull=True).count(),
    }


# ------------------------------------------------------------------- spares --
def _insight_spares(start, end):
    """Most-used parts by frequency, and the parts that actually earn."""
    base = JobCardSpareItem.objects.filter(
        job_card__isnull=False, job_card__is_deleted=False,
        job_card__admitted_date__range=(start, end),
    )

    most_used = list(
        base.annotate(n=Lower('spare_part_name')).values('n')
            .annotate(qty=Coalesce(Sum('quantity'), Value(ZERO, output_field=MONEY), output_field=MONEY),
                      times=Count('id'),
                      revenue=Coalesce(Sum('total_price', output_field=MONEY),
                                       Value(ZERO, output_field=MONEY), output_field=MONEY))
            .order_by('-times')[:15]
    )

    most_profitable = list(
        base.annotate(n=Lower('spare_part_name')).values('n')
            .annotate(revenue=Coalesce(Sum('total_price', output_field=MONEY),
                                       Value(ZERO, output_field=MONEY), output_field=MONEY),
                      cost=Coalesce(Sum(SPARE_COST, output_field=MONEY),
                                    Value(ZERO, output_field=MONEY), output_field=MONEY),
                      times=Count('id'))
            .annotate(profit=F('revenue') - F('cost'))
            .order_by('-profit')[:15]
    )
    for r in most_profitable:
        r['margin'] = float(r['profit'] / r['revenue'] * 100) if r['revenue'] else 0.0

    totals = base.aggregate(
        revenue=Coalesce(Sum('total_price', output_field=MONEY), Value(ZERO, output_field=MONEY), output_field=MONEY),
        cost=Coalesce(Sum(SPARE_COST, output_field=MONEY), Value(ZERO, output_field=MONEY), output_field=MONEY),
        lines=Count('id'),
    )
    totals['profit'] = totals['revenue'] - totals['cost']
    totals['margin'] = float(totals['profit'] / totals['revenue'] * 100) if totals['revenue'] else 0.0

    # Off the job card, not off its job lines: the labour charge moved onto
    # `JobCard.labour_amount` when the workshop's real practice — one price for
    # the whole job — replaced the per-line column. Summing the lines here would
    # now report every card created since as ₹0 of labour.
    labour = _sum(JobCard.objects.filter(
        is_deleted=False, admitted_date__range=(start, end)), F('labour_amount'))

    return {
        'most_used': most_used,
        'most_profitable': most_profitable,
        'totals': totals,
        'labour_total': labour,
        'chart_labels': [(r['n'] or '—').title() for r in most_used[:10]],
        'chart_qty': [r['times'] for r in most_used[:10]],
    }


# ----------------------------------------------------------------- vehicles --
def _insight_vehicles(start, end):
    """
    Repeat vehicles and brand mix, plus a deliberately small customer note.

    Customer name/contact are optional on a normal job card and usually left
    blank on the floor, so there is no customer-level analysis here — only a
    coverage line, so the owner can see how thin that data is instead of being
    shown a confident chart built on a fraction of the rows.
    """
    cards = _cards_in(start, end)

    top_vehicles = list(
        cards.values('registration_number')
             .annotate(visits=Count('id'),
                       revenue=Coalesce(Sum(_net_revenue(), output_field=MONEY),
                                        Value(ZERO, output_field=MONEY), output_field=MONEY))
             .order_by('-visits', '-revenue')[:15]
    )
    # Most recent brand/model for each of those plates — display only.
    plates = [r['registration_number'] for r in top_vehicles]
    info = {}
    if plates:
        for c in (cards.filter(registration_number__in=plates)
                       .order_by('registration_number', '-admitted_date')
                       .values('registration_number', 'brand_name', 'model_name')):
            info.setdefault(c['registration_number'], c)
    for r in top_vehicles:
        d = info.get(r['registration_number'], {})
        r['brand'] = d.get('brand_name', '')
        r['model'] = d.get('model_name', '')

    brands = list(
        cards.exclude(brand_name='')
             .values('brand_name')
             .annotate(jobs=Count('id'),
                       revenue=Coalesce(Sum(_net_revenue(), output_field=MONEY),
                                        Value(ZERO, output_field=MONEY), output_field=MONEY))
             .order_by('-jobs')[:12]
    )

    total_cards = cards.count()
    distinct = cards.values('registration_number').distinct().count()
    repeat_plates = cards.values('registration_number').annotate(n=Count('id')).filter(n__gt=1).count()
    named = cards.exclude(Q(customer_name__isnull=True) | Q(customer_name='')).count()

    return {
        'top_vehicles': top_vehicles,
        'brands': brands,
        'total_cards': total_cards,
        'distinct_vehicles': distinct,
        'repeat_vehicles': repeat_plates,
        'repeat_pct': round(repeat_plates / distinct * 100, 1) if distinct else 0,
        'avg_visits': round(total_cards / distinct, 2) if distinct else 0,
        'named_pct': round(named / total_cards * 100, 1) if total_cards else 0,
        'named_count': named,
        'chart_labels': [r['brand_name'] for r in brands[:8]],
        'chart_jobs': [r['jobs'] for r in brands[:8]],
    }


# -------------------------------------------------------------------- fleet --
def _insight_fleet(start, end):
    """Fleet (BulkPayer) accounts: volume in the window, balance as of now."""
    cards = _cards_in(start, end).filter(bulk_payer__isnull=False)

    rows = list(
        cards.values('bulk_payer', 'bulk_payer__customer_name')
             .annotate(jobs=Count('id'),
                       billed=Coalesce(Sum(_net_revenue(), output_field=MONEY),
                                       Value(ZERO, output_field=MONEY), output_field=MONEY),
                       received=Coalesce(Sum('received_amount', output_field=MONEY),
                                         Value(ZERO, output_field=MONEY), output_field=MONEY))
             .order_by('-billed')
    )
    # Live running balance per account. Not window-scoped on purpose: a balance
    # is a running total, so slicing it by date would produce a meaningless
    # number.
    balances = {
        b['id']: (b['total_billed_amount'] - b['total_paid_amount'], b['advance_balance'])
        for b in BulkPayer.objects.values('id', 'total_billed_amount', 'total_paid_amount', 'advance_balance')
    }
    for r in rows:
        bal, adv = balances.get(r['bulk_payer'], (ZERO, ZERO))
        r['balance'] = bal
        r['advance'] = adv
        r['collected_pct'] = float(r['received'] / r['billed'] * 100) if r['billed'] else 0.0

    walkin = _cards_in(start, end).filter(bulk_payer__isnull=True)
    return {
        'rows': rows,
        'fleet_jobs': cards.count(),
        'fleet_revenue': _sum(cards, _net_revenue()),
        'walkin_jobs': walkin.count(),
        'walkin_revenue': _sum(walkin, _net_revenue()),
        'accounts': BulkPayer.objects.filter(is_trashed=False).count(),
    }


# -------------------------------------------------------------------- shops --
def _insight_shops(start, end):
    """Where the parts money goes — spare shops (per job) and supplies shops."""
    from inventory.models import SupplierShop, SupplierRestockBill

    spare_rows = list(
        JobCardSpareItem.objects.filter(
            shop__isnull=False, job_card__isnull=False, job_card__is_deleted=False,
            job_card__admitted_date__range=(start, end),
        ).values('shop', 'shop__name')
         .annotate(spend=Coalesce(Sum(SPARE_COST, output_field=MONEY),
                                  Value(ZERO, output_field=MONEY), output_field=MONEY),
                   revenue=Coalesce(Sum('total_price', output_field=MONEY),
                                    Value(ZERO, output_field=MONEY), output_field=MONEY),
                   parts=Count('id'))
         .order_by('-spend')
    )
    for r in spare_rows:
        r['profit'] = r['revenue'] - r['spend']
        r['margin'] = float(r['profit'] / r['revenue'] * 100) if r['revenue'] else 0.0

    supplier_rows = list(
        SupplierRestockBill.objects.filter(bill_date__range=(start, end))
        .values('supplier', 'supplier__name')
        .annotate(spend=Coalesce(Sum(SUPPLIER_BILL_COST, output_field=MONEY),
                                 Value(ZERO, output_field=MONEY), output_field=MONEY),
                  bills=Count('id'))
        .order_by('-spend')
    )

    dues = {s['id']: s['total_purchased_amount'] - s['total_paid_amount']
            for s in SpareShop.objects.values('id', 'total_purchased_amount', 'total_paid_amount')}
    for r in spare_rows:
        r['due'] = dues.get(r['shop'], ZERO)
    sdues = {s['id']: s['total_billed_amount'] - s['total_paid_amount']
             for s in SupplierShop.objects.values('id', 'total_billed_amount', 'total_paid_amount')}
    for r in supplier_rows:
        r['due'] = sdues.get(r['supplier'], ZERO)

    return {
        'spare_rows': spare_rows,
        'supplier_rows': supplier_rows,
        'spare_total': sum((r['spend'] for r in spare_rows), ZERO),
        'supplier_total': sum((r['spend'] for r in supplier_rows), ZERO),
    }


# --------------------------------------------------------------- operations --
def _insight_operations(start, end):
    """Workload, completion and payment behaviour."""
    cards = _cards_in(start, end)

    monthly = list(
        cards.annotate(m=TruncMonth('admitted_date')).values('m')
             .annotate(jobs=Count('id'),
                       revenue=Coalesce(Sum(_net_revenue(), output_field=MONEY),
                                        Value(ZERO, output_field=MONEY), output_field=MONEY))
             .order_by('m')
    )

    status_map = dict(JobCard.PAYMENT_STATUS_CHOICES)
    status = list(cards.values('payment_status').annotate(n=Count('id')).order_by('-n'))
    for s in status:
        s['label'] = status_map.get(s['payment_status'], s['payment_status'])

    method_map = dict(JobCard.PAYMENT_METHOD_CHOICES)
    methods = list(
        cards.exclude(Q(payment_method__isnull=True) | Q(payment_method=''))
             .values('payment_method')
             .annotate(n=Count('id'),
                       total=Coalesce(Sum('received_amount', output_field=MONEY),
                                      Value(ZERO, output_field=MONEY), output_field=MONEY))
             .order_by('-n')
    )
    for m in methods:
        m['label'] = method_map.get(m['payment_method'], m['payment_method'])

    total = cards.count()
    completed = cards.filter(completed=True).count()

    return {
        'monthly': monthly,
        'status': status,
        'methods': methods,
        'total_cards': total,
        'completed': completed,
        'open_jobs': total - completed,
        'completed_pct': round(completed / total * 100, 1) if total else 0,
        'on_hold': cards.filter(on_hold=True).count(),
        'chart_labels': [m['m'].strftime('%b %y') for m in monthly if m['m']],
        'chart_jobs': [m['jobs'] for m in monthly if m['m']],
        'chart_status_labels': [s['label'] for s in status],
        'chart_status_values': [s['n'] for s in status],
    }
