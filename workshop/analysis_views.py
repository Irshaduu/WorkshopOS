"""
workshop/analysis_views.py
==========================
Owner → Analysis & Reports.

Two pages, and the split between them is the whole point of the design:

  /analysis/            PROFIT — the protected page.
                        Turnover − Expenses = Profit, for one date window, and
                        then THE SAME PROFIT decomposed by what earned it.
                        This is what the owners open to decide profit
                        distribution, so it stays deliberately plain: no
                        drill-downs, no cleverness, nothing that needs
                        explaining. Every rupee on it traces to one of four
                        expense streams.

  /analysis/insights/   INSIGHTS — everything else. Mechanics, spare parts,
                        inventory, vehicles, fleet accounts, shops, cashbook,
                        operations. Loads one section at a time over AJAX so
                        the heavy Top-N queries only run for the section
                        actually being looked at.

All money math lives in analysis_engine.py, not here. These views resolve the
date window, call the engine, and render — so a bug in a chart can never
become a bug in the profit figure.

--------------------------------------------------------------------------
ONE WORD, ONE MEANING — across both pages
--------------------------------------------------------------------------
Four different figures were all called "Profit" across these two screens, an
owner reading both in one sitting. The vocabulary is now fixed:

  Profit          the bottom line, after every expense. The Profit page's
                  word, and nothing else in Analysis may use it bare.
  Gross profit    revenue − parts cost, no overhead taken off. Car profiles
                  and the Mechanics section — the same calculation, so the
                  same words.
  Margin          parts sold − parts cost. Spare Parts, Inventory, Shops.
                  Thinner than gross profit: no labour in it either.

Owner-only throughout (@owner_required); Office and Floor never see this.
"""

from decimal import Decimal

from django.db.models import Sum, Count, Min, Q, F, Value, DecimalField
from django.db.models.functions import Coalesce, Lower, TruncMonth
from django.http import Http404
from django.shortcuts import render

from .decorators import owner_required
from .models import (
    JobCard, JobCardSpareItem, BulkPayer, SpareShop, SpareShopPayment,
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
        # ONE chart on this page now. The "Where It Went" donut plotted
        # `expense_lines`, which the Expenses card already prints with a share
        # percentage and a proportional bar per line — the same four numbers
        # drawn twice. Its two context keys went with it rather than being left
        # behind as payload nothing reads.
        'chart_labels': [m['label'] for m in series],
        'chart_turnover': [float(m['turnover']) for m in series],
        'chart_expenses': [float(m['expenses']) for m in series],
        'chart_profit': [float(m['profit']) for m in series],
        'has_data': report['turnover'] != ZERO or report['expense_total'] != ZERO,
        'multi_month': len(series) > 1,
        # The Deep Analysis link's subtitle, off the one list that defines
        # them. It used to be a hand-typed string in the template and went
        # stale the moment the sections changed.
        'insight_section_names': [label for _key, label, _icon, _blurb in INSIGHT_SECTIONS],
    })


# =============================================================================
# PAGE 2 — INSIGHTS
# =============================================================================

# THE TWO PARTS ROUTES ARE TWO SECTIONS, not two tables in one.
#
# They were one "Spares" section carrying both, and before 2026-08-25 one
# merged TABLE. Splitting the tables fixed the worst of it and left the thing
# above them still merged: a combined headline reading "Parts revenue / Parts
# cost / Parts profit / Margin" across both routes. That blended margin is a
# number with no business behind it — it averages a per-job trading margin
# against a shelf margin that depends on `avg_cost` being right — on a section
# whose own reasoning says the two are different businesses.
#
# The deciding argument is that THE OWNER ALREADY SPLITS THEM: asked what the
# workshop earns from, the answer was "labour, inventory commission, spare
# parts commission, cashbook income" — four streams, of which these are two.
# The Profit page's earnings card now names them separately too, so the two
# pages describe the business the same way.
#
# ⚠ The Spares glyph was `bi-tools`, which is the JOB PERFORMED icon — the
# section that BUYS parts wearing the icon of the section that FITS them, the
# exact mistake CLAUDE.md records being fixed on the Spare Shops pages. The
# app-wide Spare Parts glyph is `bi-gear-wide-connected`. It survived because
# `SparePartsWearsOneGlyphTests` scans templates and this list is Python.
INSIGHT_SECTIONS = [
    ('mechanics',   'Mechanics',   'bi-person-gear',         'Who generates the work, and the gross profit on it'),
    ('spare_parts', 'Spare Parts', 'bi-gear-wide-connected', 'Parts bought per job — what they cost and what they earn'),
    ('inventory',   'Inventory',   'bi-box-seam',            'Parts off the warehouse shelf — what they earn'),
    ('vehicles',    'Vehicles',    'bi-car-front',           'Repeat cars, brands, and how often they return'),
    ('fleet',       'Fleet',       'bi-buildings',           'Fleet account volume, settlement and balances'),
    ('shops',       'Shops',       'bi-shop',                'Spare shops and supplies shops, by spend'),
    ('cashbook',    'Cashbook',    'bi-journal-text',        'General running costs by category, and scrap income'),
    ('operations',  'Operations',  'bi-speedometer2',        'Workload, completion and how customers pay'),
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
        'spare_parts': _insight_spare_parts,
        'inventory': _insight_inventory,
        'vehicles': _insight_vehicles,
        'fleet': _insight_fleet,
        'shops': _insight_shops,
        'cashbook': _insight_cashbook,
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


# -------------------------------------------------------------- spare parts --
# THE TWO ROUTES ARE TWO SECTIONS. What is shared between them is the money
# rule, and that lives in `engine.parts_trading` — one aggregate pair read by
# both sections AND by the Profit page's earnings card, so a margin quoted on
# one screen cannot disagree with the same margin one tap away.
#
# What is NOT shared is the shape of the table, and that is the whole reason
# they are separate:
#
#   • A shop row has a shop, an ordering state and a payable behind it. A draw
#     came off a shelf already paid for by a restock bill. Only the first is
#     chaseable.
#   • The COST columns are not the same kind of number. A shop line's cost is
#     the LINE TOTAL as typed; a draw's is a derived weighted average x
#     quantity. `SPARE_COST` gets each right, and printing them in one column
#     invites dividing one by a quantity that does not price it.
#   • QUANTITY means different things. A draw's quantity is what left the
#     shelf; a shop row's moves no money at all. Shown for stock, and left off
#     the shop table for exactly that reason.
#
# ⚠ THE MONEY COLUMN IS CALLED "MARGIN", NEVER "PROFIT". Four different things
# were called Profit across these two pages — the bottom line, a car's gross
# profit, a mechanic's, and this. This one is the thinnest of them: parts sold
# less parts cost, with no labour in it and no overhead taken off. `Profit` is
# now the Profit page's word alone, `Gross profit` means revenue less parts
# cost (car profiles and mechanics), and `Margin` means this.

#: Rows in a parts table. Long enough to see the tail of what matters, short
#: enough to stay one screen on a phone.
PARTS_ROW_CAP = 15
#: Bars in the "most used" chart.
PARTS_CHART_CAP = 10


def _parts_base(start, end):
    """Spare rows on real job cards admitted in the window, either route."""
    return JobCardSpareItem.objects.filter(
        job_card__isnull=False, job_card__is_deleted=False,
        job_card__admitted_date__range=(start, end),
    )


def _money(expr):
    return Coalesce(Sum(expr, output_field=MONEY),
                    Value(ZERO, output_field=MONEY), output_field=MONEY)


def _with_margin(rows):
    for r in rows:
        r['margin'] = float(r['profit'] / r['revenue'] * 100) if r['revenue'] else 0.0
    return rows


def _insight_spare_parts(start, end):
    """
    The SHOP route — parts ordered from a spare shop for one car.

    Grouped by the free-text name, lowered so 'brake pad' and 'Brake Pad' are
    one row. `Min` then hands back a REAL stored spelling: displaying the
    lowered key re-title-cased is what turned 'DOT 4' into 'Dot 4'.
    """
    base = _parts_base(start, end).filter(source=JobCardSpareItem.SOURCE_SHOP)

    rows = _with_margin(list(
        base.annotate(key=Lower('spare_part_name'))
            .values('key')
            .annotate(name=Min('spare_part_name'),
                      times=Count('id'),
                      revenue=_money('total_price'),
                      cost=_money(SPARE_COST))
            .annotate(profit=F('revenue') - F('cost'))
            .order_by('-profit')[:PARTS_ROW_CAP]
    ))

    return {
        'rows': rows,
        'totals': engine.parts_trading(start, end)['shop'],
        'movers': _parts_movers(base, Lower('spare_part_name'), 'spare_part_name'),
        'shops_used': base.filter(shop__isnull=False).values('shop').distinct().count(),
        # Real money with no payee. It is inside this section's cost — every
        # SOURCE_SHOP row is — but it is NOT inside the Profit page's Spare
        # Shops line, which is where an owner would go looking for it.
        'no_shop': base.filter(shop__isnull=True).count(),
        # A row with no `unit_price` costs ₹0, so it reads as a FREE part and
        # pushes the margin UP. Exactly what `uncosted_draws` does for the
        # warehouse route one section over — this side had no equivalent, so
        # the identical defect was disclosed on one route and silent on the
        # other.
        'uncosted_shop': engine.uncosted_shop_count(start, end),
    }


def _insight_inventory(start, end):
    """
    The WAREHOUSE route — parts drawn off the shelf.

    Grouped by the `item` FK, never by the name. `spare_part_name` on a draw is
    a SNAPSHOT taken when the part was drawn and is not rewritten when the
    product is renamed, so grouping by it would split one product's history
    into two rows the day somebody corrects a spelling.

    NOT filtered by `item__isnull=False`. A draw with no product FK is
    malformed and should not exist — `InventoryDrawForm` requires one and
    `source` is not editable — but filtering it out here while the subtotal
    still counted it would drop a row with nothing saying so and leave the two
    disagreeing. `name` is the fallback label for that case.
    """
    base = _parts_base(start, end).filter(source=JobCardSpareItem.SOURCE_INVENTORY)

    rows = _with_margin(list(
        base.values('item', 'item__name', 'item__category__name')
            .annotate(name=Min('spare_part_name'),
                      times=Count('id'),
                      qty=Coalesce(Sum('quantity'), Value(ZERO, output_field=MONEY),
                                   output_field=MONEY),
                      revenue=_money('total_price'),
                      cost=_money(SPARE_COST))
            .annotate(profit=F('revenue') - F('cost'))
            .order_by('-profit')[:PARTS_ROW_CAP]
    ))

    return {
        'rows': rows,
        'totals': engine.parts_trading(start, end)['stock'],
        'movers': _parts_movers(base, F('item'), 'item__name'),
        # A draw with no `unit_price` costs ₹0, so it reads as a FREE part and
        # pushes the margin UP — the one way this table can be wrong without
        # looking wrong. Counted so the section can say so, exactly as the
        # Profit page does.
        'uncosted_draws': base.filter(unit_price__isnull=True).count(),
        # What the shelf is worth right now, so the section that reports what
        # LEFT the shelf also reports what is still on it. Not window-scoped —
        # a stock level is a position, not a flow.
        # From the engine, so this section and the Profit page's position tile
        # can never quote different figures for one shelf.
        'stock_value': engine.warehouse_stock_value(),
    }


def _parts_movers(base, key_expr, label_field):
    """
    The most-USED parts, counted over the whole route.

    ⚠ NOT taken from the table above it. The old merged section built its
    "Parts That Move" chart by re-sorting the fifteen rows it had already cut
    by PROFIT — so a cheap part fitted to every car in the workshop could not
    appear in the chart of what moves unless it also happened to be one of the
    fifteen most profitable. The chart was answering "which of the top earners
    is used most", under a heading that says something else. It is its own
    query, and its own ordering.
    """
    return list(
        base.annotate(mkey=key_expr)
            .values('mkey')
            .annotate(times=Count('id'), label=Min(label_field))
            .order_by('-times')[:PARTS_CHART_CAP]
    )


# ------------------------------------------------------------------ cashbook --
def _insight_cashbook(start, end):
    """
    General running costs by category, and the small income side.

    MOVED HERE FROM THE PROFIT PAGE. Categories are free text with no master
    list, so the list has no ceiling — All Time on a real workshop's books runs
    to dozens, and it sat on a page whose rule is that it carries no
    drill-downs, behind a "Show all" button, between the owner and the position
    tiles. Which of forty categories rent fell into is not a question anybody
    asks while settling the month's profit.

    Nothing was lost by moving it: the Cashbook page lists ENTRIES and has
    never totalled them by category, so this is the only place that view
    exists. It gets a whole accordion section here instead of a truncated card,
    so there is no cap and no reveal button — the total always adds up from the
    rows under it.

    ⚠ THE WAGE WARNING DID NOT MOVE. `cashbook_expense()` still flags a
    wage-looking category and the Profit page still prints that warning, because
    it says the profit figure may be double-counting the wage bill — and a
    warning that changes what the headline means belongs beside the headline.
    The flag is repeated on the row here so the two screens agree about which
    category is the suspect one.
    """
    expense = engine.cashbook_expense(start, end)
    income_rows = engine.cashbook_income_by_category(start, end)
    income_total = engine.cashbook_income(start, end)

    # A share per row, so the list reads for SHAPE and not only for figures —
    # the same bar the Profit page's expense lines carry. Computed here because
    # the template does no arithmetic.
    total = expense['total']
    for row in expense['by_category']:
        row['share'] = float(row['total'] / total * 100) if total else 0.0

    return {
        'expense_rows': expense['by_category'],
        'expense_total': total,
        'wage_suspects': expense['wage_suspects'],
        'wage_suspect_total': expense['wage_suspect_total'],
        'income_rows': income_rows,
        'income_total': income_total,
        'entries': sum(r['count'] for r in expense['by_category']),
        'income_entries': sum(r['count'] for r in income_rows),
    }


# ----------------------------------------------------------------- vehicles --
def _insight_vehicles(start, end):
    """
    Repeat vehicles and brand mix, keyed on the registration number.

    Customer name/contact are optional on a normal job card and usually left
    blank on the floor, so there is no customer-level analysis here — a car is
    identified by its plate.

    ⚠ THE SECTION STATES THAT RULE; IT NO LONGER PRINTS THE COVERAGE. It used to
    read "filled in on 0 of 47 job cards here", which is a true count and reads
    as a shortfall to go and fix — inviting an owner to chase staff into filling
    boxes that change nothing on this screen. `named_count` / `named_pct` are
    still computed and simply not rendered: they cost one query already in
    flight, and the day somebody wants the coverage back it is there.
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
    #
    # ⚠ CUT FROM THE SAME POPULATION AND THE SAME EXPRESSION AS THE PROFIT
    # PAGE'S `fleet_due`, not from `BulkPayer`'s stored totals. Those are GROSS
    # of discount (`update_totals` sums `total_bill_amount` alone) and span
    # every card including settled ones — so beside a "Billed" column that IS
    # net of discount, the two disagree the first time a fleet card carries one.
    # Same defect as the Profit page's fleet line, same fix.
    owed = F('total_bill_amount') - F('discount_amount') - F('received_amount')
    balances = {
        r['bulk_payer']: r['bal']
        for r in (live_jobcards()
                  .filter(bulk_payer__isnull=False)
                  .exclude(payment_status__in=('PAID', 'BULK_PAID'))
                  .values('bulk_payer')
                  .annotate(bal=Coalesce(Sum(owed, output_field=MONEY),
                                         Value(ZERO, output_field=MONEY), output_field=MONEY)))
    }
    advances = dict(BulkPayer.objects.values_list('id', 'advance_balance'))
    for r in rows:
        owing = balances.get(r['bulk_payer'], ZERO)
        credit = advances.get(r['bulk_payer'], ZERO)
        # The account's true position: what its unsettled cards still owe, less
        # any lump payment already banked ahead. `advance_balance` was computed
        # and never rendered before, so an account paid ahead read as "₹0 owed"
        # with the credit nowhere on the page.
        net = owing - credit
        r['owed'] = net if net > ZERO else ZERO
        r['credit'] = -net if net < ZERO else ZERO
        r['collected_pct'] = float(r['received'] / r['billed'] * 100) if r['billed'] else 0.0

    # AN ACCOUNT THAT OWES BUT DID NO WORK THIS PERIOD IS STILL LISTED.
    #
    # `rows` is built from job cards IN THE WINDOW, while "Balance now" is a
    # live figure spanning the account's whole history — so an account that
    # settled nothing and brought no cars in this month simply vanished, taking
    # its debt off the only screen that lists fleet balances. The Profit page's
    # fleet line counts it, so the two disagreed, and an owner adding up this
    # column got less than the tile.
    #
    # Not filtered by `is_trashed`, for the same reason `receivable` is not: a
    # balance must not depend on whether somebody tidied a list.
    seen = {r['bulk_payer'] for r in rows}
    outstanding = {pk for pk, bal in balances.items() if bal} | {
        pk for pk, adv in advances.items() if adv}
    missing = outstanding - seen
    if missing:
        names = dict(BulkPayer.objects.filter(id__in=missing)
                     .values_list('id', 'customer_name'))
        for pk in missing:
            owing, credit = balances.get(pk, ZERO), advances.get(pk, ZERO)
            net = owing - credit
            if net == ZERO:
                continue
            rows.append({
                'bulk_payer': pk,
                'bulk_payer__customer_name': names.get(pk, '—'),
                'jobs': 0, 'billed': ZERO, 'received': ZERO,
                'owed': net if net > ZERO else ZERO,
                'credit': -net if net < ZERO else ZERO,
                'collected_pct': 0.0,
                'no_jobs_here': True,
            })
        rows.sort(key=lambda r: (-r['billed'], -r['owed']))

    walkin = _cards_in(start, end).filter(bulk_payer__isnull=True)
    fleet_jobs, walkin_jobs = cards.count(), walkin.count()
    fleet_revenue = _sum(cards, _net_revenue())
    walkin_revenue = _sum(walkin, _net_revenue())
    all_jobs = fleet_jobs + walkin_jobs
    all_revenue = fleet_revenue + walkin_revenue

    return {
        'rows': rows,
        'fleet_jobs': fleet_jobs,
        'fleet_revenue': fleet_revenue,
        'walkin_jobs': walkin_jobs,
        'walkin_revenue': walkin_revenue,
        # THE SHARE IS THE POINT OF THE FOUR BOXES. Without it they are two
        # unrelated pairs of numbers; with it they read as one split, which is
        # the question this section exists to answer — how much of the work is
        # fleet. Computed here because the template does no arithmetic.
        'fleet_job_pct': round(fleet_jobs / all_jobs * 100, 1) if all_jobs else 0.0,
        'fleet_revenue_pct': (float(fleet_revenue / all_revenue * 100)
                              if all_revenue else 0.0),
        'total_jobs': all_jobs,
        # The two denominators, printed beside each share so "8.1%" is a
        # fraction of something the reader can see rather than of a figure they
        # have to go and find.
        'all_revenue': all_revenue,
        'accounts': BulkPayer.objects.filter(is_trashed=False).count(),
    }


# -------------------------------------------------------------------- shops --
def _insight_shops(start, end):
    """Where the parts money goes — spare shops (per job) and supplies shops."""
    from inventory.models import SupplierShop, SupplierRestockBill, SupplierPayment

    # `source=SHOP` stated OUTRIGHT rather than inferred from "has a shop".
    # A warehouse draw carries no shop today, so the two filters select the same
    # rows — but one of them is the rule and the other is a coincidence of how
    # the data happens to look, and only the rule survives somebody adding a
    # shop reference to the inventory route.
    spare_rows = list(
        JobCardSpareItem.objects.filter(
            source=JobCardSpareItem.SOURCE_SHOP,
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
    # Parts bought from this shop and not yet fitted to a car. They are inside
    # "Owed now" (the shop ledger counts them) and outside "Spent" (which is
    # scoped to job cards in this window), so without them the two columns look
    # like they should reconcile and do not. Normally zero.
    waiting = {
        r['shop']: r['t']
        for r in (JobCardSpareItem.objects
                  .filter(job_card__isnull=True,
                          source=JobCardSpareItem.SOURCE_SHOP, shop__isnull=False)
                  .values('shop')
                  .annotate(t=Coalesce(Sum(SPARE_COST, output_field=MONEY),
                                       Value(ZERO, output_field=MONEY), output_field=MONEY)))
    }
    for r in spare_rows:
        r['due'] = dues.get(r['shop'], ZERO)
        r['waiting'] = waiting.get(r['shop'], ZERO)
    sdues = {s['id']: s['total_billed_amount'] - s['total_paid_amount']
             for s in SupplierShop.objects.values('id', 'total_billed_amount', 'total_paid_amount')}
    for r in supplier_rows:
        r['due'] = sdues.get(r['supplier'], ZERO)

    return {
        'spare_rows': spare_rows,
        'supplier_rows': supplier_rows,
        'spare_total': sum((r['spend'] for r in spare_rows), ZERO),
        'supplier_total': sum((r['spend'] for r in supplier_rows), ZERO),
        # A SHOP purchase with nobody recorded as the payee. This section is
        # grouped BY shop, so such a row has no group to sit in and drops out
        # of the total above — while the Spare Parts section counts every
        # SOURCE_SHOP row and therefore reports MORE spent at the same shops,
        # in the same period. Two screens disagreeing about one figure with
        # nothing saying why is exactly what this section must not do.
        # Normally ₹0.
        'unattributed': engine.unattributed_spare_expense(start, end),
        # WHAT ACTUALLY LEFT THE DRAWER, which is a different question from
        # every other figure on this page and belongs HERE rather than on the
        # Profit page. Spend is what the work cost; this is cash paid against
        # the shops' ledgers, on their own instalment rhythm. Neither touches
        # profit, and putting a cash figure inside the profit equation is how
        # an owner ends up subtracting one of five terms and trusting the
        # answer.
        #
        # ⚠ BOTH SIDES ARE DATED BY THE DAY THE MONEY MOVED — `date`, never
        # `created_at`. This read `created_at__date__range` while the shop's
        # own page had already moved to `date`, so one payment back-dated at
        # month end sat in Last Month there and in This Month here: two screens
        # an owner opens in one sitting, quoting two figures for one ledger.
        #
        # They stay TWO figures even now that the basis is one, and the reason
        # changed rather than went away: a spare shop and a Supplies Shop are
        # two different trades on two different instalment rhythms, which is
        # how this whole section is already organised. Summing them would be a
        # product decision, not a consequence of the column landing.
        'supplier_paid': _sum(
            SupplierPayment.objects.filter(is_trashed=False, date__range=(start, end)),
            F('amount')),
        'spare_paid': _sum(
            SpareShopPayment.objects.filter(is_trashed=False, date__range=(start, end)),
            F('amount')),
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

    # HOW CUSTOMERS PAID — and the rows with no method are ACCOUNTED FOR, not
    # dropped. `payment_method` is blank on two kinds of card: a fleet card
    # (settled through its account, so the method sits on the fleet payment, not
    # here) and a card nobody has settled yet. Excluding both silently made the
    # table's own counts add to less than the job count with nothing on screen
    # saying why — 13 of 150 in the demo data, all of them fleet.
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

    no_method = cards.filter(Q(payment_method__isnull=True) | Q(payment_method=''))
    unmethoded = {
        'fleet': no_method.filter(bulk_payer__isnull=False).count(),
        'unsettled': no_method.filter(bulk_payer__isnull=True).count(),
    }

    total = cards.count()
    completed = cards.filter(completed=True).count()

    return {
        'monthly': monthly,
        'status': status,
        'methods': methods,
        'unmethoded': unmethoded,
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
