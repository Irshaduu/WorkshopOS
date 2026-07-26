"""
workshop/analysis_engine.py
===========================
The money math behind Owner → Analysis & Reports.

This module holds **no view code and no HTML** — only pure functions that take a
date window and return Decimals/dicts. That separation is deliberate: the Profit
page is what the owners use to decide profit distribution, so the arithmetic has
to be testable on its own, without going through a request.

--------------------------------------------------------------------------
THE PROFIT MODEL  (agreed with the owner, 2026-07-27)
--------------------------------------------------------------------------

        TOTAL TURNOVER  −  TOTAL EXPENSES  =  PROFIT

TURNOVER
  • Car Bills .......... JobCard.total_bill_amount − discount_amount
                         Discounts are money never earned, so they are netted
                         off rather than shown as an expense. For a settled
                         card this lands exactly on received_amount (verified
                         against live data), which is why it is the honest
                         "what the workshop actually earned" figure.
  • Cashbook Income .... CashbookEntry(entry_type=INCOME) — scrap sales etc.

EXPENSES — four real, non-overlapping money-out streams
  1. Spare Shops ....... Parts bought from a spare shop *for a specific job*:
                         unit_price × quantity on JobCardSpareItem rows that
                         carry a shop link.
  2. Supplies Shops .... Warehouse restocking: SupplierRestockBill effective
     (Inventory)         amount (total − discount).
  3. Salary ............ From the Salary & Advance section — never from the
                         Cashbook. See salary_expense() for how advances are
                         folded in without double counting.
  4. General Cashbook .. CashbookEntry(entry_type=EXPENSE) — rent, power,
                         consumables, tools…  Shown broken down by category.

  (+) Other spare purchases — a *transparency* line, normally ₹0. See
      unattributed_spare_expense().

--------------------------------------------------------------------------
THE DOUBLE-COUNT RULE — the single most important thing in this file
--------------------------------------------------------------------------
A spare fitted to a car reaches the workshop by one of two routes, and each
route is paid for exactly once:

  route A — ordered from a spare shop for that job
            → JobCardSpareItem.shop is set
            → the money leaves via the SPARE SHOP stream (stream 1)

  route B — taken off the warehouse shelf
            → JobCardSpareItem.shop is NULL and the part name matches an
              inventory Item (that is precisely what the stock-deduction
              signal in inventory/signals.py keys on)
            → the money already left earlier, when the shelf was filled by a
              supplier restock bill (stream 2)

So route-B spare cost must NEVER be added as an expense: it would charge the
workshop twice for one part. Against live data the two routes partition the
spare rows exactly — ₹14,881,597 route A vs ₹9,785,190 route B — and adding
route B on top would have overstated expenses by ~₹9.8M.

Anything left over (no shop link *and* no inventory match) is genuinely
unaccounted, so it is surfaced separately instead of being silently dropped —
that is what unattributed_spare_expense() is for.

--------------------------------------------------------------------------
DATING RULE
--------------------------------------------------------------------------
Every stream is dated by its own natural date, so a period never mixes bases:
    car bills + their spare cost  → JobCard.admitted_date
    cashbook (income & expense)   → CashbookEntry.date
    restock bills                 → SupplierRestockBill.bill_date
    salary                        → SalaryPayment.month (the 1st)
Keeping a job's revenue and that job's spare cost on the same date is what
makes a month's margin internally consistent.

--------------------------------------------------------------------------
PERFORMANCE
--------------------------------------------------------------------------
Built for 5+ years of history (live: 5,478 job cards over 2021→2026):
  • pure SQL aggregates — never a Python loop over a queryset
  • every filter narrows by date before aggregating
  • Coalesce() on every Sum, so NULL money never becomes None
  • the monthly series is a fixed number of grouped queries, not one per month
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Sum, Count, Q, F, Value, DecimalField
from django.db.models.functions import Coalesce, Lower, TruncMonth
from django.utils import timezone

from .models import (
    JobCard, JobCardSpareItem, CashbookEntry,
    SalaryPayment, SalaryPaymentLine, SalaryAdvance,
    SpareShop, BulkPayer,
)

# Wide enough that a multi-year Sum of 10-digit money columns cannot overflow.
MONEY = DecimalField(max_digits=20, decimal_places=2)
ZERO = Decimal('0')

# Mirrors SpareShop.update_totals() exactly: a missing price is ₹0, a missing
# quantity is 1 unit. Kept identical so the shop ledger and this engine can
# never disagree about what a spare cost.
SPARE_COST = Coalesce(F('unit_price'), Value(ZERO, output_field=MONEY)) * \
             Coalesce(F('quantity'), Value(Decimal('1'), output_field=MONEY))


def _sum(qs, expr, alias='t'):
    """Sum an expression to a Decimal, never None."""
    return qs.aggregate(**{alias: Coalesce(Sum(expr, output_field=MONEY), Value(ZERO, output_field=MONEY),
                                           output_field=MONEY)})[alias]


# =============================================================================
# PERIOD RESOLUTION
# =============================================================================
# The owner asked for exactly these windows. Deliberately a shorter list than
# the day-to-day list views (no Today / This Week): profit is not a daily
# number, and offering one invites reading noise as signal.
PERIOD_CHOICES = [
    ('this_month', 'This Month'),
    ('last_month', 'Last Month'),
    ('this_year',  'This Year'),
    ('last_year',  'Last Year'),
    ('all_time',   'All Time'),
    ('custom',     'Custom'),
]
DEFAULT_PERIOD = 'this_month'


def _parse_date(value):
    """'YYYY-MM-DD' → date, or None if unusable."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError):
        return None


def _month_end(day):
    """Last date of the calendar month containing `day`."""
    first_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next - timedelta(days=1)


def resolve_period(range_key, start_str=None, end_str=None):
    """
    Returns (start_date, end_date, range_key, label).

    Both ends are INCLUSIVE — every query in this module uses __range or
    __gte/__lte accordingly, so a bill dated on the last day is counted.

    Uses timezone.localdate() (never date.today()): the server may run in UTC
    while the workshop runs on IST, and near midnight those are different days.
    """
    today = timezone.localdate()

    if range_key == 'last_month':
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, 'last_month', end.strftime('%B %Y')

    if range_key == 'this_year':
        return today.replace(month=1, day=1), today.replace(month=12, day=31), 'this_year', str(today.year)

    if range_key == 'last_year':
        y = today.year - 1
        return today.replace(year=y, month=1, day=1), today.replace(year=y, month=12, day=31), 'last_year', str(y)

    if range_key == 'all_time':
        # Earliest date across every money stream, not just job cards. A workshop
        # going live seeds opening stock and shop balances *before* its first job
        # card, and anchoring to job cards alone would silently drop that spend
        # out of "All Time". Three indexed MIN lookups, only on this branch.
        from inventory.models import SupplierRestockBill
        streams = (
            (JobCard.objects, 'admitted_date'),
            (CashbookEntry.objects, 'date'),
            (SupplierRestockBill.objects, 'bill_date'),
        )
        firsts, lasts = [], []
        for manager, field in streams:
            lo = manager.order_by(field).values_list(field, flat=True).first()
            hi = manager.order_by(f'-{field}').values_list(field, flat=True).first()
            if lo:
                firsts.append(lo)
            if hi:
                lasts.append(hi)
        start = min(firsts) if firsts else today.replace(month=1, day=1)
        # Never end before today, and never cut off a forward-dated record.
        end = max([today] + lasts)
        return start, end, 'all_time', 'All Time'

    if range_key == 'custom':
        start = _parse_date(start_str)
        end = _parse_date(end_str)
        if start and end:
            if start > end:
                start, end = end, start
            label = f"{start.strftime('%d %b %Y')} — {end.strftime('%d %b %Y')}"
            return start, end, 'custom', label
        # Incomplete custom input falls back to the default rather than 500ing.

    # DEFAULT: this month
    return today.replace(day=1), _month_end(today), 'this_month', today.strftime('%B %Y')


# =============================================================================
# TURNOVER
# =============================================================================

def live_jobcards():
    """
    Every job card that counts as real business.

    is_deleted is a dormant column (cards are hard-deleted now) but pre-existing
    rows may still carry the flag, so it stays filtered for correctness.
    """
    return JobCard.objects.filter(is_deleted=False)


def car_bill_turnover(start, end):
    """
    What the workshop earned from vehicles in the window.

    net = gross bills − discounts. The discount field is where a part-paid
    bill's shortfall is booked (a deliberate business rule — see CLAUDE.md), so
    netting it off is what makes this equal money actually earned.
    """
    qs = live_jobcards().filter(admitted_date__range=(start, end))
    agg = qs.aggregate(
        gross=Coalesce(Sum('total_bill_amount', output_field=MONEY), Value(ZERO, output_field=MONEY), output_field=MONEY),
        discount=Coalesce(Sum('discount_amount', output_field=MONEY), Value(ZERO, output_field=MONEY), output_field=MONEY),
        received=Coalesce(Sum('received_amount', output_field=MONEY), Value(ZERO, output_field=MONEY), output_field=MONEY),
        cards=Count('id'),
    )
    agg['net'] = agg['gross'] - agg['discount']
    # Billed but not yet collected, for the cards in this window.
    agg['outstanding'] = agg['net'] - agg['received']
    return agg


def cashbook_income(start, end):
    return _sum(CashbookEntry.objects.filter(entry_type='INCOME', date__range=(start, end)), 'amount')


# =============================================================================
# EXPENSES
# =============================================================================

def _inventory_item_names():
    """
    Lowercased inventory product names, used to tell a warehouse-drawn spare
    from a shop-bought one.

    Small by design — one product per category+name for a single workshop — so
    pulling the list and using it in a SQL IN() is cheaper than a join, and it
    matches how the stock-deduction signal itself resolves names.
    """
    from inventory.models import Item
    return [n.lower() for n in Item.objects.values_list('name', flat=True) if n]


def spare_shop_expense(start, end):
    """
    Stream 1 — parts bought from a spare shop for a specific job.

    Only rows carrying a shop link. Rows without one came off the warehouse
    shelf and were already paid for through a restock bill; charging them here
    is the double count this module exists to prevent.
    """
    qs = JobCardSpareItem.objects.filter(
        shop__isnull=False,
        job_card__isnull=False,
        job_card__is_deleted=False,
        job_card__admitted_date__range=(start, end),
    )
    return _sum(qs, SPARE_COST)


def unattributed_spare_expense(start, end, names=None):
    """
    Transparency line — normally ₹0.

    A spare with no shop link AND no matching inventory product came from
    nowhere the system can account for. Rather than silently dropping that
    money (understating expenses) or lumping it into Spare Shops (misfiling
    it), it gets its own line that only appears when it is non-zero.
    """
    qs = JobCardSpareItem.objects.filter(
        shop__isnull=True,
        job_card__isnull=False,
        job_card__is_deleted=False,
        job_card__admitted_date__range=(start, end),
    )
    names = _inventory_item_names() if names is None else names
    if names:
        qs = qs.annotate(_lname=Lower('spare_part_name')).exclude(_lname__in=names)
    return _sum(qs, SPARE_COST)


def warehouse_drawn_spare_cost(start, end, names=None):
    """
    Value of stock pulled off the shelf onto job cards in this window.

    NOT an expense — it was paid for by a restock bill. Reported only so the
    Profit page can show *why* it is excluded instead of appearing to lose it.
    """
    names = _inventory_item_names() if names is None else names
    if not names:
        return ZERO
    qs = JobCardSpareItem.objects.filter(
        shop__isnull=True,
        job_card__isnull=False,
        job_card__is_deleted=False,
        job_card__admitted_date__range=(start, end),
    ).annotate(_lname=Lower('spare_part_name')).filter(_lname__in=names)
    return _sum(qs, SPARE_COST)


def inventory_expense(start, end):
    """
    Stream 2 — warehouse restocking, at the bill's effective (post-discount)
    amount, mirroring SupplierRestockBill.get_effective_amount.
    """
    from inventory.models import SupplierRestockBill
    qs = SupplierRestockBill.objects.filter(bill_date__range=(start, end))
    return _sum(qs, F('total_amount') - F('discount_amount'))


def salary_expense(start, end):
    """
    Stream 3 — wages, from the Salary & Advance section only.

    "Logically calculated with Advance too", per the owner. An advance is cash
    that has already left the drawer, and a settlement pays only the remainder
    (net = salary − leave − advance). So for a settled month the true wage cost
    is net + advance, which is simply salary-minus-leave — counting the two
    parts of one month's pay exactly once.

    A month that has not been settled yet still had advances handed out, and
    that cash is genuinely gone, so those are counted on their own. Settled
    months are excluded from that second pass to avoid counting an advance
    twice (once inside its settlement, once as a loose advance).

    Returns the parts as well as the total, because "how much of the wage bill
    was already handed out as advances" is a question owners actually ask.
    """
    months = list(
        SalaryPayment.objects.filter(month__range=(start, end)).values_list('month', flat=True)
    )

    lines = SalaryPaymentLine.objects.filter(payment__month__range=(start, end))
    settled_net = _sum(lines, F('net_amount'))
    settled_advance = _sum(lines, F('advance_used'))

    loose = SalaryAdvance.objects.filter(date__range=(start, end))
    if months:
        loose = loose.annotate(_m=TruncMonth('date')).exclude(_m__in=months)
    loose_advance = _sum(loose, 'amount')

    return {
        'settled_net': settled_net,
        'settled_advance': settled_advance,
        'unsettled_advance': loose_advance,
        'settled_months': len(months),
        'total': settled_net + settled_advance + loose_advance,
    }


# Cashbook categories are free text, so wages can only be spotted by name.
# Used for a *warning*, never to silently filter — see cashbook_expense().
WAGE_WORDS = ('salary', 'salaries', 'wage', 'wages', 'payroll', 'staff pay')


def cashbook_expense(start, end):
    """
    Stream 4 — general running costs, with a per-category breakdown.

    Wages are owned by the Salary & Advance section, so the Cashbook should
    only ever hold general running costs. Nothing is filtered out here though:
    the category is free text, and a view that silently dropped rows matching a
    word list would hide real money the moment someone named a category
    "Salary Advance Recovery" or similar.

    Instead, wage-looking categories are flagged. The Profit page turns that
    into a visible warning so the owner can move the entry, rather than the
    page quietly deciding for them.
    """
    qs = CashbookEntry.objects.filter(entry_type='EXPENSE', date__range=(start, end))
    by_category = list(
        qs.values('category')
          .annotate(total=Coalesce(Sum('amount', output_field=MONEY), Value(ZERO, output_field=MONEY),
                                   output_field=MONEY),
                    count=Count('id'))
          .order_by('-total')
    )
    for row in by_category:
        row['looks_like_wages'] = any(w in (row['category'] or '').lower() for w in WAGE_WORDS)

    suspect = [r for r in by_category if r['looks_like_wages']]
    return {
        'total': _sum(qs, 'amount'),
        'by_category': by_category,
        'wage_suspects': suspect,
        'wage_suspect_total': sum((r['total'] for r in suspect), ZERO),
    }


# =============================================================================
# THE REPORT
# =============================================================================

def build_profit_report(start, end):
    """
    One dict with every number the Profit page shows.

    Deliberately eager rather than lazy/AJAX: this is a handful of indexed
    aggregate queries, and an owner checking profit should get the whole
    picture in one load rather than watching cards populate.
    """
    names = _inventory_item_names()
    bills = car_bill_turnover(start, end)
    cb_income = cashbook_income(start, end)
    turnover = bills['net'] + cb_income

    spares = spare_shop_expense(start, end)
    inventory = inventory_expense(start, end)
    salary = salary_expense(start, end)
    cashbook = cashbook_expense(start, end)
    other_spares = unattributed_spare_expense(start, end, names=names)

    expense_total = spares + inventory + salary['total'] + cashbook['total'] + other_spares
    profit = turnover - expense_total

    # Ordered biggest-first so the page reads as "where the money went".
    expense_lines = [
        {'key': 'spare_shops', 'label': 'Spare Shops',
         'hint': 'Parts bought per job', 'amount': spares, 'icon': 'bi-tools'},
        {'key': 'inventory', 'label': 'Supplies Shops',
         'hint': 'Warehouse restocking', 'amount': inventory, 'icon': 'bi-truck'},
        {'key': 'salary', 'label': 'Salary & Advance',
         'hint': f"{salary['settled_months']} month(s) settled", 'amount': salary['total'], 'icon': 'bi-cash-coin'},
        {'key': 'cashbook', 'label': 'General Cashbook',
         'hint': 'Rent, power, consumables', 'amount': cashbook['total'], 'icon': 'bi-journal-text'},
    ]
    if other_spares > ZERO:
        expense_lines.append({
            'key': 'other_spares', 'label': 'Other Spare Purchases',
            'hint': 'No shop and no stock match — needs filing',
            'amount': other_spares, 'icon': 'bi-question-circle',
        })
    expense_lines.sort(key=lambda r: r['amount'], reverse=True)

    for line in expense_lines:
        line['share'] = float(line['amount'] / expense_total * 100) if expense_total else 0.0

    margin = float(profit / turnover * 100) if turnover else 0.0

    return {
        'turnover': turnover,
        'bills': bills,
        'cashbook_income': cb_income,
        'expense_total': expense_total,
        'expense_lines': expense_lines,
        'salary': salary,
        'cashbook': cashbook,
        'warehouse_drawn': warehouse_drawn_spare_cost(start, end, names=names),
        'profit': profit,
        'margin': margin,
    }


def monthly_series(start, end):
    """
    Month-by-month turnover / expenses / profit for the trend chart.

    Five grouped queries total, merged in Python by 'YYYY-MM'. Cost is a
    function of how many months are in range, not how many rows exist, so a
    five-year window is as cheap as a one-month one.
    """
    def grouped(qs, date_field, expr):
        return {
            r['m'].strftime('%Y-%m'): r['t']
            for r in qs.annotate(m=TruncMonth(date_field))
                       .values('m')
                       .annotate(t=Coalesce(Sum(expr, output_field=MONEY), Value(ZERO, output_field=MONEY),
                                            output_field=MONEY))
            if r['m']
        }

    from inventory.models import SupplierRestockBill

    rev = grouped(live_jobcards().filter(admitted_date__range=(start, end)),
                  'admitted_date', F('total_bill_amount') - F('discount_amount'))
    inc = grouped(CashbookEntry.objects.filter(entry_type='INCOME', date__range=(start, end)),
                  'date', F('amount'))
    sp = grouped(JobCardSpareItem.objects.filter(
                     shop__isnull=False, job_card__isnull=False, job_card__is_deleted=False,
                     job_card__admitted_date__range=(start, end)),
                 'job_card__admitted_date', SPARE_COST)
    inv = grouped(SupplierRestockBill.objects.filter(bill_date__range=(start, end)),
                  'bill_date', F('total_amount') - F('discount_amount'))
    cb = grouped(CashbookEntry.objects.filter(entry_type='EXPENSE', date__range=(start, end)),
                 'date', F('amount'))
    sal = grouped(SalaryPaymentLine.objects.filter(payment__month__range=(start, end)),
                  'payment__month', F('net_amount') + F('advance_used'))

    # The two streams below are usually empty, which makes them easy to forget —
    # and forgetting them makes the chart quietly disagree with the headline
    # total. They are summed here for exactly the same reason
    # build_profit_report() counts them.
    loose_qs = SalaryAdvance.objects.filter(date__range=(start, end))
    settled = list(SalaryPayment.objects.filter(month__range=(start, end)).values_list('month', flat=True))
    if settled:
        loose_qs = loose_qs.annotate(_m=TruncMonth('date')).exclude(_m__in=settled)
    adv = grouped(loose_qs, 'date', F('amount'))

    other_qs = JobCardSpareItem.objects.filter(
        shop__isnull=True, job_card__isnull=False, job_card__is_deleted=False,
        job_card__admitted_date__range=(start, end))
    names = _inventory_item_names()
    if names:
        other_qs = other_qs.annotate(_lname=Lower('spare_part_name')).exclude(_lname__in=names)
    oth = grouped(other_qs, 'job_card__admitted_date', SPARE_COST)

    keys = sorted(set(rev) | set(inc) | set(sp) | set(inv) | set(cb) | set(sal) | set(adv) | set(oth))
    rows = []
    for k in keys:
        t = rev.get(k, ZERO) + inc.get(k, ZERO)
        e = (sp.get(k, ZERO) + inv.get(k, ZERO) + cb.get(k, ZERO)
             + sal.get(k, ZERO) + adv.get(k, ZERO) + oth.get(k, ZERO))
        y, m = k.split('-')
        rows.append({
            'key': k,
            'label': f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m)-1]} {y[2:]}",
            'turnover': t, 'expenses': e, 'profit': t - e,
        })
    return rows


# =============================================================================
# FINANCIAL POSITION  (a balance "right now", not a windowed figure)
# =============================================================================

def financial_position():
    """
    What the workshop is owed and what it owes, as of today.

    Independent of the page's date filter on purpose: a balance is a running
    total, and slicing it by month would produce a number that means nothing.
    """
    from inventory.models import SupplierShop

    receivable = _sum(
        live_jobcards().exclude(payment_status='PAID')
                       .exclude(payment_status='BULK_PAID'),
        F('total_bill_amount') - F('discount_amount') - F('received_amount'),
    )
    fleet_due = _sum(BulkPayer.objects.filter(is_trashed=False),
                     F('total_billed_amount') - F('total_paid_amount'))
    spare_due = _sum(SpareShop.objects.filter(is_trashed=False),
                     F('total_purchased_amount') - F('total_paid_amount'))
    supplier_due = _sum(SupplierShop.objects.filter(is_active=True),
                        F('total_billed_amount') - F('total_paid_amount'))

    return {
        'receivable': receivable,
        'fleet_due': fleet_due,
        'payable_spare': spare_due,
        'payable_supplier': supplier_due,
        'payable_total': spare_due + supplier_due,
    }
