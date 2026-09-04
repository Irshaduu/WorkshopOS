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

EXPENSES — five real, non-overlapping money-out streams, ALL ON ONE BASIS:
what the work done in this period cost.
  1. Spare Shops ....... Parts bought from a spare shop *for a specific job*:
                         the `unit_price` LINE TOTAL on JobCardSpareItem rows
                         that have source=SHOP and a shop recorded. Not
                         multiplied by quantity — see SHOP_LINE_COST.
  2. Inventory Used .... Parts taken off the warehouse shelf onto a job card:
                         source=INVENTORY rows at their weighted-average cost.
  3. Salary ............ From the Salary & Advance section — never from the
                         Cashbook. See salary_expense() for how advances are
                         folded in without double counting.
  4. General Cashbook .. CashbookEntry(entry_type=EXPENSE) — power, water,
                         consumables, tools…  Shown broken down by category.
  5. Rent .............. What the premises cost for the whole months in the
                         window, from `RentRate` — never from the deposits.
                         See rent_expense().

  (+) Other spare purchases — a *transparency* line, normally ₹0. See
      unattributed_spare_expense().

⚠ RENT COMES FROM THE RATE, AND THE DAILY DEPOSITS ARE CASH. Until 2026-09-04
rent arrived here as a Cashbook category and the Deposit & Rent section touched
this module nowhere — a deliberate boundary, and a correct one for exactly as
long as the office kept keying the monthly bill into the Cashbook. It stopped
being correct when they started recording rent in its own section instead:
September 2026 carried ₹35,000 of real rent and this page charged ₹900 of it,
while May–August carried ₹45,000 Cashbook rows against a stored rate of
₹35,000. Two different rents in one system, and neither page aware of the
other.

So a Cashbook category NAMED like rent is now a DOUBLE COUNT, and is flagged
(`RENT_WORDS`) exactly as a wage-looking one is — never filtered, because
"Rent agreement stamp paper" is a real running cost.

⚠ A SUPPLIES SHOP BILL IS NOT AN EXPENSE. Buying stock converts cash (or a
promise to pay) into goods on a shelf; it is not a cost until the goods are
used. The bill moves the payable in `financial_position()` and raises the
shelf; an instalment paid against it moves the payable again. NEITHER TOUCHES
PROFIT. `supplier_billed()` still reports what was billed, and must never be
added back alongside stream 2 — that is one delivery charged twice.

--------------------------------------------------------------------------
AND THE SAME PROFIT, SAID THE OWNER'S WAY
--------------------------------------------------------------------------
The owners do not think "turnover minus expenses". Asked what the workshop
earns from, the answer is four things:

    LABOUR + SPARE PARTS MARGIN + INVENTORY MARGIN + CASHBOOK INCOME
        (less discounts given)             =  GROSS EARNINGS
    less SALARY, RENT and CASHBOOK EXPENSE =  THE SAME PROFIT

`earnings_breakdown()` builds the second, and it closes with NOTHING in
between — no conversion, no reconciling line. That is only true because both
descriptions charge stock at the same moment. If a bridging line ever has to
come back, the two bases have drifted apart and that is the bug.

⚠ EVERY SHARED FIGURE IS HANDED IN, RENT INCLUDED. Leaving it out of `spend`
would land this card on a profit ₹35,000 a month above the equation printed
directly over it — and the whole safety of saying the profit twice is that the
second statement lands on the first.

--------------------------------------------------------------------------
THE DOUBLE-COUNT RULE — the single most important thing in this file
--------------------------------------------------------------------------
A part is charged EXACTLY ONCE, at the moment it is fitted to a car. Which
route it took is **stored**, in `JobCardSpareItem.source`, and never inferred:

  SHOP      — ordered from a spare shop for that job   → stream 1
  INVENTORY — taken off the warehouse shelf            → stream 2

The two routes partition the spare rows exactly, so every rupee of parts cost
lands in exactly one stream: none lost, none doubled.

⚠ THE DOUBLE COUNT TO GUARD AGAINST IS THE RESTOCK BILL. Stream 2 charges the
shelf when it is emptied; adding `supplier_billed()` on top would charge the
same goods again when they were bought. Against the seeded data that is roughly
₹6.9L of invented expense.

(Until 2026-08-25 the rule pointed the other way: the BILL was the expense and
the draw was excluded. It changed on the owner's decision, because it put the
two parts routes on two different bases — the spare route has always been dated
by `job_card__admitted_date`, with `unassigned_spare_purchases` holding back
what is not yet fitted — and because it made monthly profit lumpy: a delivery
month carried a whole bill while the months that consumed it looked rich. The
guard is the same shape, it just names a different second helping.)

Until 2026-07-30 there was no `source` column and this module GUESSED the route:
a NULL shop plus a case-insensitive match of `spare_part_name` against
`Item.name`. The stock signals guessed by a *different* rule, so the two could
disagree — a shop-bought part whose name happened to equal a stock product was
billed to the shop here *and* deducted from the shelf there, one part paid for
once but counted twice, with the shelf drifting down until a restock bill
covered it. Do not reintroduce name matching in either place.

A SHOP row with no shop recorded is real money with no payee. It gets its own
line rather than being silently dropped — that is what
unattributed_spare_expense() is for.

--------------------------------------------------------------------------
DATING RULE
--------------------------------------------------------------------------
Every stream is dated by its own natural date, so a period never mixes bases:
    car bills + ALL their parts cost → JobCard.admitted_date
    cashbook (income & expense)      → CashbookEntry.date
    salary                           → SalaryPayment.month (the 1st)
    rent                             → the rent month (the 1st), capped at
                                       the month in progress
Keeping a job's revenue and that job's parts cost on the same date is what
makes a month's margin internally consistent — and it is now true of BOTH
parts routes, which is what let the reconciling line go.

`SupplierRestockBill.bill_date` still dates two things, neither of which is an
expense: the shelf's average cost (`inventory/costing.py` replays receipts in
date order) and `supplier_billed()`.

⚠ RENT IS THE ONLY STREAM THAT NEEDS A CAP, because it is the only one not
summed from rows. No row exists in the future, so every other stream is
self-limiting; `this_year` resolves to 1 Jan – 31 Dec deliberately, so an
uncapped rent walk charges twelve months on 4 September — ₹1,05,000 of expense
that has not happened, on the page distribution is decided from. The cap lives
in `rent.charged_by_month`, which is the only implementation of it.

--------------------------------------------------------------------------
PERFORMANCE
--------------------------------------------------------------------------
Built for 5+ years of history (live: 5,478 job cards over 2021→2026):
  • pure SQL aggregates — never a Python loop over a queryset
  • every filter narrows by date before aggregating
  • Coalesce() on every Sum, so NULL money never becomes None
  • the monthly series is a fixed number of grouped queries, not one per month
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest, TruncMonth
from django.utils import timezone

from .models import (
    JobCard, JobCardSpareItem, CashbookEntry,
    SalaryPayment, SalaryPaymentLine, SalaryAdvance,
    SpareShop, SpareShopPayment, BulkPayer, BulkPaymentHistory,
    OwnerWithdrawal, RentRate, RentDeposit,
)
# ⚠ THE RENT ARITHMETIC IS NOT RESTATED HERE. `workshop/rent.py` owns the
# rate spans, the month boundaries and the cap; this module calls it. A second
# walk over the rate table would be a second answer, free to drift from the one
# the Deposit & Rent page prints — the `SPARE_COST` rule, applied again.
from . import rent as rent_calc

# Wide enough that a multi-year Sum of 10-digit money columns cannot overflow.
MONEY = DecimalField(max_digits=20, decimal_places=2)
ZERO = Decimal('0')

# =============================================================================
# WHAT A SPARE COST — one definition, and the two routes are NOT the same shape
# =============================================================================
# Changed 2026-08-17 on the owner's instruction, and it is a change of MEANING
# on one route, not of arithmetic on both.
#
#   SHOP     `unit_price` is the LINE TOTAL the shop billed for that row, as
#            Office typed it off the shop's own bill. Nothing multiplies it.
#   WAREHOUSE `unit_price` is the weighted-average cost of ONE unit, snapshotted
#            from `Item.avg_cost` by `JobCardSpareItem.save()` and rewritten by
#            `inventory/costing.py`'s replay. It is per unit by construction —
#            it is derived from the shelf, never typed — so a draw's cost is
#            still `× quantity` and must stay that way.
#
# Why the shop side moved: this workshop enters what it was billed, not a rate.
# A row reading 5,000 with a Qty of 2 was being read as ₹10,000 owed, and the
# owner confirmed staff type the whole amount — so the multiplication was
# inventing money nobody was billed. It also removes the last division from the
# path between a typed figure and a ledger: what is typed is what is owed.
#
# Both are declared here because five places used to hand-roll this expression
# (this module, `SpareShop.update_totals`, and three aggregates in
# `views/spare_shop.py`), which is five chances for one of them to be fixed and
# the rest left behind — and they would disagree exactly where it matters, as a
# shop page and the Profit page quoting different debts for the same rows.
# A missing price is ₹0 on both routes; a missing quantity is 1 unit, which now
# only matters to the warehouse side.
SHOP_LINE_COST = Coalesce(F('unit_price'), Value(ZERO, output_field=MONEY))

WAREHOUSE_LINE_COST = Coalesce(F('unit_price'), Value(ZERO, output_field=MONEY)) * \
                      Coalesce(F('quantity'), Value(Decimal('1'), output_field=MONEY))

#: What a spare cost, whichever shelf it came off. Route-aware, so a queryset
#: spanning both (a car profile's gross profit) gets each row costed by its own
#: rule rather than by whichever one the caller happened to pick.
SPARE_COST = Case(
    When(source=JobCardSpareItem.SOURCE_SHOP, then=SHOP_LINE_COST),
    default=WAREHOUSE_LINE_COST,
    output_field=MONEY,
)


#: What a SUPPLIES SHOP bill cost — total less its discount, FLOORED AT ZERO.
#:
#: The floor is the whole point, and it was missing here for a month while
#: `SupplierRestockBill.get_effective_amount` has always carried it. A discount
#: larger than the bill it sits on makes `total − discount` negative, and a
#: negative EXPENSE *raises* reported profit — a mistyped extra zero on one
#: bill silently made the workshop look richer on the one page profit is
#: distributed from. The model property refused to do that; three aggregates
#: on this page hand-rolled the subtraction and did.
#:
#: Two ways a bill reaches that state even though the entry forms reject it:
#: `update_bill_discount` validated only `discount >= 0` (fixed alongside
#: this), and `update_totals()` recomputes `total_amount` from the bill's
#: lines without re-checking the discount — so deleting a line from an already
#: discounted bill can push the discount above the new total.
#:
#: Declared once, here, for the same reason SPARE_COST is: the expression was
#: written out in three places (`inventory_expense`, `monthly_series`, and
#: `_insight_shops` in `analysis_views`), which is three chances to fix one and
#: leave two — and they would disagree exactly where it hurts, as the Profit
#: page's Supplies Shops line and the Shops insight quoting different spend for
#: the same bills.
SUPPLIER_BILL_COST = Greatest(
    F('total_amount') - F('discount_amount'),
    Value(ZERO, output_field=MONEY),
    output_field=MONEY,
)


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


#: Every stream that can hold the earliest or latest money in the system, as
#: (manager, date field). ⚠ A stream missing from this list is money the widest
#: filter in the section cannot see — leaving salary out is what made All Time
#: report the wage bill ₹1,22,167 short.
_DATE_STREAMS = (
    (lambda: JobCard.objects, 'admitted_date'),
    (lambda: CashbookEntry.objects, 'date'),
    (lambda: _restock_manager(), 'bill_date'),
    (lambda: SalaryPayment.objects, 'month'),
    (lambda: SalaryAdvance.objects, 'date'),
    # An owner withdrawal touches no PROFIT figure, but it is real cash out
    # on the same card, so All Time has to reach it or the widest filter
    # under-reports what left the drawer. Leaving salary out of this list is
    # what made All Time report the wage bill ₹1,22,167 short.
    (lambda: OwnerWithdrawal.objects, 'date'),
    # ⚠ THE RATE IS IN THIS LIST AS WELL AS THE DEPOSIT, and leaving it out
    # would repeat the salary bug exactly. A rate's month is a 1st, so a
    # ledger opened in October 2023 whose first deposit fell on the 5th would
    # anchor All Time to 5 October — and `charged_by_month` charges a month
    # only when its 1st is inside the window, so that month's rent would drop
    # out of the widest filter in the section. Measured before both were
    # added: All Time opened on 2026-02-07 against a ledger reaching back to
    # October 2023, hiding ₹10,15,000 of rent while claiming to cover
    # everything.
    (lambda: RentRate.objects, 'effective_from'),
    (lambda: RentDeposit.objects, 'date'),
)


def _restock_manager():
    from inventory.models import SupplierRestockBill      # avoids a circular import
    return SupplierRestockBill.objects


def _stream_bounds(latest=True):
    """(earliest, latest) dates across every money stream, as two lists.

    `latest=False` skips the five MAX lookups. `first_record_date()` is called
    on every profit page render and only ever needs the MINs, and this page is
    a small enough number of queries that ten avoidable ones are worth not
    issuing.
    """
    firsts, lasts = [], []
    for get_manager, field in _DATE_STREAMS:
        manager = get_manager()
        lo = manager.order_by(field).values_list(field, flat=True).first()
        if lo:
            firsts.append(lo)
        if latest:
            hi = manager.order_by(f'-{field}').values_list(field, flat=True).first()
            if hi:
                lasts.append(hi)
    return firsts, lasts


def first_record_date():
    """The earliest date this system holds any money for, or None if empty."""
    firsts, _ = _stream_bounds(latest=False)
    return min(firsts) if firsts else None


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
        # Earliest date across EVERY money stream, not just job cards. A workshop
        # going live seeds opening stock and shop balances *before* its first job
        # card, and anchoring to job cards alone would silently drop that spend
        # out of "All Time". Five indexed MIN lookups, only on this branch.
        #
        # ⚠ SALARY IS ONE OF THE FIVE, and leaving it out was not theoretical.
        # A salary month is dated the 1st, and the earliest job card here fell
        # on the 17th — so the window opened on 17 August, that month's
        # settlement sat outside it, and All Time reported the wage bill
        # ₹1,22,167 short while claiming to cover everything. Any stream this
        # list forgets is money the widest filter in the section cannot see.
        firsts, lasts = _stream_bounds()
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


def _add_months(day, n):
    """`day` shifted by n calendar months, clamping the day-of-month.

    Calendar months, never a fixed number of days: 365 days before 1 Jan is
    1 Jan in a common year and 2 Jan after a leap year, so a day-based shift
    makes a year-on-year comparison drift.

    Falls off either end of the calendar rather than raising. A custom range
    starting near year 1 — a mis-keyed year in a date box is enough — pushed
    this below year 0, and the ValueError reached the browser as a 500 on the
    profit page.
    """
    month = day.month - 1 + n
    year = day.year + month // 12
    month = month % 12 + 1
    if year < date.min.year:
        return date.min
    if year > date.max.year:
        return date.max
    last = _month_end(day.replace(year=year, month=month, day=1)).day
    return day.replace(year=year, month=month, day=min(day.day, last))


def _clamp(day):
    """Keep a computed date inside what `datetime.date` can hold."""
    return max(date.min, min(day, date.max))


def comparison_window(start, end):
    """
    The window to compare this one against, and what to CALL that comparison.

    Returns (prev_start, prev_end, read_to, label, is_partial).

    THE PROBLEM THIS SOLVES. `this_month` and `this_year` deliberately resolve
    to the WHOLE calendar month/year, so the header reads "01 Jan — 31 Dec" and
    a card dated later this month is never silently outside the window. But the
    data only reaches today. Comparing that against a FULL previous period was
    therefore always comparing a part-period against a whole one, and it always
    read as a decline: on 25 Aug 2026 the page reported "−8.5% vs previous" for
    the year while the workshop was actually running ~11% AHEAD per trading day.
    A number that says "down" on the page profit is distributed from, when the
    truth is "up", is the worst thing this section could do.

    So an INCOMPLETE period is compared like for like — 1 Jan–25 Aug 2026
    against 1 Jan–25 Aug 2025 — by shifting BOTH ends back one whole period.
    A FINISHED period (Last Month, Last Year, a custom range wholly in the
    past) is unchanged: the whole span against the equally long span before it.

    ⚠ `read_to` is the date the COMPARISON reads to, not the window's own end.
    The headline still covers the full window; only the two figures being
    compared are trimmed, or the comparison would be measuring the trim.

    A period is "year-shaped" past 45 days, which is simply the only place a
    month and a year can be told apart without special-casing the range key —
    and it has to work for a custom range too.
    """
    today = timezone.localdate()
    is_partial = start <= today < end
    read_to = min(end, today) if is_partial else end

    # How far back "one period" is — see the note on "year-shaped" above.
    step = -12 if (end - start).days + 1 > 45 else -1

    if is_partial:
        return (_clamp(_add_months(start, step)),
                _clamp(_add_months(read_to, step)),
                read_to,
                'vs same period last year' if step == -12 else 'vs same days last month',
                True)

    # A FINISHED CALENDAR PERIOD compares against the previous CALENDAR period,
    # never "the same number of days earlier". July is 31 days, so the day-count
    # version put Last Month's comparison at 31 May – 30 June — a window
    # straddling two months, labelled as the month before. 2024 was a leap year,
    # so Last Year's landed on 2 Jan – 31 Dec 2024 and quietly dropped New
    # Year's Day. Both were arithmetically defensible and neither was the period
    # anybody meant.
    if start.day == 1 and end == _month_end(end):
        months = (end.year - start.year) * 12 + (end.month - start.month) + 1
        prev_start = _clamp(_add_months(start, -months))
        return prev_start, _clamp(start - timedelta(days=1)), end, 'vs previous', False

    span = (end - start).days + 1
    prev_end = _clamp(start - timedelta(days=1))
    prev_start = _clamp(prev_end - timedelta(days=span - 1))
    return prev_start, prev_end, end, 'vs previous', False


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


def cashbook_income_by_category(start, end):
    """
    The income side, broken down — scrap, black oil, the occasional oddity.

    Deliberately NOT folded into `cashbook_income()`: the Profit page needs one
    figure and nothing else, and making it carry a list it never reads would
    put a `values().annotate()` on the hot path of the page that has to load
    fastest. Deep Analysis is the only caller, and it is the mirror of
    `cashbook_expense()`'s own `by_category`.

    No wage flagging here, unlike the expense side — money coming IN cannot be
    a duplicate of the wage bill going out.
    """
    return list(
        CashbookEntry.objects
        .filter(entry_type='INCOME', date__range=(start, end))
        .values('category')
        .annotate(total=Coalesce(Sum('amount', output_field=MONEY),
                                 Value(ZERO, output_field=MONEY), output_field=MONEY),
                  count=Count('id'))
        .order_by('-total')
    )


# =============================================================================
# EXPENSES
# =============================================================================

def _live_spares(start, end):
    """Spare rows on real job cards admitted in this window — the common filter
    the three classifiers below each narrow by `source`."""
    return JobCardSpareItem.objects.filter(
        job_card__isnull=False,
        job_card__is_deleted=False,
        job_card__admitted_date__range=(start, end),
    )


def spare_shop_expense(start, end):
    """
    Stream 1 — parts bought from a spare shop for a specific job.

    `source=SHOP` **and** a shop actually named. A shop row with no shop link is
    money that left the workshop with nothing to attribute it to, so it falls to
    unattributed_spare_expense() rather than being quietly counted here.
    """
    qs = _live_spares(start, end).filter(
        source=JobCardSpareItem.SOURCE_SHOP, shop__isnull=False)
    return _sum(qs, SPARE_COST)


def unattributed_spare_expense(start, end):
    """
    Transparency line — normally ₹0.

    A shop purchase where nobody recorded which shop. Real money with no payee,
    so it gets its own line rather than being dropped (understating expenses) or
    folded into Spare Shops (misfiling it).
    """
    qs = _live_spares(start, end).filter(
        source=JobCardSpareItem.SOURCE_SHOP, shop__isnull=True)
    return _sum(qs, SPARE_COST)


def warehouse_drawn_spare_cost(start, end):
    """
    Stream 2 — the cost of stock pulled off the shelf onto job cards here.

    Costed from each row's own `unit_price`: the weighted-average warehouse cost
    as at that draw's own date, written by `JobCardSpareItem.save()` and kept
    true by `inventory/costing.py`'s date-ordered replay.

    ⚠ CHANGED 2026-08-25, ON THE OWNER'S DECISION. This used to be reported and
    NOT charged, while the Supplies Shop BILL was charged on its bill date. Two
    things were wrong with that, and the second is what settled it:

      • It made monthly profit lumpy for no reason an owner could act on. A
        month with a big delivery carried the whole bill; the months that
        consumed it looked rich. Measured against the meeting data, July read
        ₹5,36,500 where the work done that month actually earned ₹4,33,500.
      • THE OTHER PARTS ROUTE NEVER WORKED THAT WAY. `spare_shop_expense` is
        dated by `job_card__admitted_date` and only counts rows attached to a
        card — a shop part is expensed when it is FITTED, not when the shop
        billed it, and `unassigned_spare_purchases` exists precisely to hold
        the ones not yet fitted out of the figure. So the workshop had two
        parts routes on two different bases, and this was the odd one out.

    Both routes now answer one question: what did the parts fitted to cars in
    this period cost us. That is also what makes the earnings breakdown close
    with no conversion line — see `earnings_breakdown`.

    ⚠ A DRAW WITH NO COST COUNTS AS ₹0, so it reads as a free part and pushes
    profit UP. That was a footnote when this figure was only reported; it now
    moves the headline, which is why `uncosted_draw_count()` is load-bearing
    rather than decorative. Expect a count on go-live day, until each product
    has its first restock bill.
    """
    qs = _live_spares(start, end).filter(source=JobCardSpareItem.SOURCE_INVENTORY)
    return _sum(qs, SPARE_COST)


def unassigned_spare_purchases():
    """
    Shop purchases not yet fitted to a car — a running total, not a window.

    WHAT THESE ARE: a part ordered from a spare shop for one car, not used on
    it, and kept on the shelf for the next car that needs it. The workshop owes
    the shop for it either way. Returning it to the shop is the one other exit,
    and that deletes the row.

    NOT AN EXPENSE YET, AND THAT IS CORRECT — the cost lands when the part goes
    onto a job card, which is the same rule every other spare-shop purchase
    follows (`_live_spares` dates spare cost by `job_card__admitted_date`, so a
    part's cost sits in the same month as the revenue it helped earn).

    ⚠ THE ALTERNATIVE IS WORSE THAN IT LOOKS, so do not "fix" this by counting
    them. There is no route in the app that attaches an unassigned row to a job
    card — `unassigned_spare_edit` never writes `job_card` — so the part is
    fitted by typing it onto the card and deleting the unassigned row. Expensing
    it while it waits would therefore mean a PAST month's profit CHANGES on the
    day somebody fits the part: the August expense disappears with the deleted
    row and reappears in September. A settled month's profit moving because of
    something done weeks later is far worse than a cost arriving a month late.

    What was actually wrong is that the page said nothing. `SpareShop
    .update_totals()` counts these rows, so they sit inside "We owe spare
    shops", while `spare_shop_expense` filters `job_card__isnull=False` and
    leaves them out — a debt on screen with no cost behind it and no way to tell
    why the two did not reconcile.
    """
    qs = JobCardSpareItem.objects.filter(
        job_card__isnull=True, source=JobCardSpareItem.SOURCE_SHOP, shop__isnull=False)
    return {'amount': _sum(qs, SHOP_LINE_COST), 'count': qs.count()}


def uncosted_draw_count(start, end):
    """
    Warehouse draws whose cost is genuinely UNKNOWN — `unit_price` is NULL.

    Two ways to get here, both honest rather than broken:
      • opening stock counted onto the shelf before any supplier bill exists
        (the go-live case), so nothing ever established what it cost;
      • a product whose only restock bill was later deleted, taking the cost
        basis with it.

    Reported as a count rather than folded into a rupee figure, because the
    correct value is "we don't know". These lines contribute ₹0 to
    warehouse_drawn_spare_cost, so surfacing the count is what stops that ₹0
    being read as "these parts were free" — and it is exactly the queue of
    products someone needs to put an opening cost against.
    """
    return _live_spares(start, end).filter(
        source=JobCardSpareItem.SOURCE_INVENTORY, unit_price__isnull=True
    ).count()


def uncosted_shop_count(start, end):
    """
    SHOP rows whose cost is genuinely UNKNOWN — `unit_price` is NULL.

    The twin of `uncosted_draw_count`, and it was missing while its sibling
    existed. Both routes cost a NULL `unit_price` at ₹0 through `SPARE_COST`,
    both therefore report a part as FREE, and both push profit UP by exactly
    that much — but only the warehouse one was counted and warned about, so an
    uncosted SHOP part was the last remaining way this page could be wrong
    without looking wrong.

    Measured on the demo data: one shop row with no price left July's Spare
    Shops expense ₹1,000 short and its profit ₹1,000 high, while the page
    reported "0 uncosted" because that count only ever looked at draws.

    On this route a NULL means the office has not keyed the shop's bill yet.
    `unassigned_spare_add` deliberately stores NULL rather than 0 when Floor
    records a part, because zero would say the shop gave it away — so this is
    the queue of rows waiting for a figure, not a fault.
    """
    return _live_spares(start, end).filter(
        source=JobCardSpareItem.SOURCE_SHOP, unit_price__isnull=True
    ).count()


def supplier_billed(start, end):
    """
    What the Supplies Shops BILLED in this window — reported, NOT an expense.

    ⚠ THIS IS NO LONGER A PROFIT STREAM (changed 2026-08-25, owner's decision).
    It was `inventory_expense`, and it fed the equation directly: a bill hit
    profit on its bill date, whether or not any of that stock had been used.
    The cost of a warehouse part now lands when the part is FITTED — see
    `warehouse_drawn_spare_cost` — which is the rule the spare-shop route has
    always followed.

    Kept because "what did the supplies shops bill us this period" is a real
    question with a real answer, and because it is the guard that keeps the
    floored expression honest. Do NOT add it back alongside the draw cost: that
    is the double count, one delivery charged twice.

    At the bill's effective (post-discount)
    amount, mirroring SupplierRestockBill.get_effective_amount.
    """
    from inventory.models import SupplierRestockBill
    qs = SupplierRestockBill.objects.filter(bill_date__range=(start, end))
    return _sum(qs, SUPPLIER_BILL_COST)


def salary_expense(start, end, gaps=True):
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

    # `gaps=False` on a report built only to be compared against — see
    # `build_profit_report`. The MONEY is identical either way; only the list of
    # months to name in the banner is skipped.
    unsettled = unsettled_months(start, end) if gaps else []
    return {
        'hint': _salary_hint(len(months), unsettled),
        'settled_net': settled_net,
        'settled_advance': settled_advance,
        'unsettled_advance': loose_advance,
        'settled_months': len(months),
        'unsettled_months': unsettled,
        'total': settled_net + settled_advance + loose_advance,
    }


def _salary_hint(settled_count, missing):
    """The one line under the Salary figure, on the expense list and the card.

    It used to read "0 month(s) settled" — the count of what IS in the figure,
    which says nothing about what is missing from it. On the default view, every
    month, what was missing was the entire wage bill.

    Computed here rather than in the view or the template because it is a
    statement ABOUT the money, and the two places that print it must not be free
    to word it differently.
    """
    if missing:
        if len(missing) == 1:
            return f"{missing[0]} not settled — its wages are not counted"
        return f"{len(missing)} months not settled — their wages are not counted"
    return f"{settled_count} month{'' if settled_count == 1 else 's'} settled"


def unsettled_months(start, end):
    """
    Months inside this window whose wages are NOT in the figure above — as
    ['August 2026', …], newest first.

    THIS IS THE MOST IMPORTANT WARNING ON THE PROFIT PAGE, because it fires on
    the DEFAULT view every single month.

    A month is settled in the first days of the NEXT one (see CLAUDE.md, Salary
    months have three states). So for the whole of any month, "This Month" —
    the filter the page opens on — contains a month with no settlement, and
    `salary_expense` can only count what was settled. Measured against the real
    data on 25 Aug 2026: the page reported ₹4,90,577 profit at a 44.4% margin
    with the salary line reading ₹0, against a true wage bill of about
    ₹1,20,000 a month. That is a THIRD of the profit missing from the one page
    profit distribution is decided from, and the only thing on screen saying so
    was the words "0 month(s) settled" under a heading.

    Nothing is estimated. Inventing a wage figure from last month's settlement
    would put a number nobody paid into the profit equation, and this codebase
    does not guess money — a NULL warehouse cost is reported as unknown rather
    than as ₹0 for exactly the same reason. The page names the months and lets
    the owner do the arithmetic they were going to do anyway.

    TWO BOUNDS, so it only ever names a month a settlement is genuinely owed
    for:
      • never a FUTURE month — "This Year" runs to 31 December, and reporting
        ten months that have not happened as unsettled would bury the one that
        matters;
      • never a month BEFORE the workshop's first salary activity — a window
        wider than the section's own history (All Time reaches back to the
        earliest job card) would otherwise flag every month up to the day
        Salary & Advance was first used, which is noise, not a gap.
    """
    today = timezone.localdate()

    # The first month the workshop recorded any wage activity at all. Before
    # that there is no settlement to be missing.
    first_pay = SalaryPayment.objects.order_by('month').values_list('month', flat=True).first()
    first_adv = SalaryAdvance.objects.order_by('date').values_list('date', flat=True).first()
    firsts = [d for d in (first_pay, first_adv) if d]
    if not firsts:
        return []

    cursor = max(start.replace(day=1), min(firsts).replace(day=1))
    ceiling = min(end, today).replace(day=1)
    if cursor > ceiling:
        return []

    # ⚠ SETTLEMENTS ARE LOOKED UP OVER THE MONTHS BEING WALKED, NOT OVER THE
    # WINDOW. A salary month is dated the 1st, so a window that does not start
    # on a 1st — any mid-month custom range — excludes that month's settlement
    # from `salary_expense`'s own filter, which is the dating rule working
    # correctly. Reusing that list here would then read "August is settled
    # nowhere" and raise the banner on a month that HAS been settled: a false
    # warning, which on this page is worse than no warning at all.
    settled_keys = {
        (m.year, m.month)
        for m in SalaryPayment.objects.filter(month__gte=cursor, month__lte=ceiling)
                                      .values_list('month', flat=True)
        if m
    }

    out = []
    while cursor <= ceiling:
        if (cursor.year, cursor.month) not in settled_keys:
            out.append(cursor.strftime('%B %Y'))
        cursor = _add_months(cursor, 1)
    out.reverse()
    return out


# Cashbook categories are free text, so wages can only be spotted by name.
# Used for a *warning*, never to silently filter — see cashbook_expense().
WAGE_WORDS = ('salary', 'salaries', 'wage', 'wages', 'payroll', 'staff pay')

#: Rent has its own expense line now (stream 5), so a Cashbook category NAMED
#: like rent is the same money charged twice. Flagged, never filtered, exactly
#: as the wage double-count is.
#:
#: ⚠ MATCHED ON WORD BOUNDARIES, NEVER AS A SUBSTRING, and this is the one
#: place in the engine where that distinction is load-bearing: a `in` test for
#: 'rent' also matches 'current', and this workshop calls its electricity
#: bill "Current bill". A contains-check would flag the single most common row
#: in the ledger as a rent double count, and a warning that fires on the
#: obvious wrong thing is ignored inside a week. Done in Python over the rows
#: already grouped below rather than as a database regex, because `\b` means a
#: word boundary in Python and a BACKSPACE in PostgreSQL's POSIX regex — so a
#: DB-side pattern would behave one way under test (SQLite) and another in
#: production, which is the worst available outcome.
#:
#: ⚠ THE STEER ON THE CASHBOOK FORM READS THIS LIST AND ADDS ONE WORD. It is
#: deliberately BROADER than this one — see `cashbook_views.CASHBOOK_STEERS`.
#: A steer asks a question and never blocks, so a false positive costs a
#: second; this flag ASSERTS that the profit figure above it is wrong, and a
#: false warning on that page is worse than no warning at all.
RENT_WORDS = ('rent', 'rents')

_RENT_RE = re.compile(
    r'\b(?:%s)\b' % '|'.join(re.escape(w) for w in RENT_WORDS), re.IGNORECASE)


def looks_like_rent(category):
    """Is this free-text Cashbook category naming rent? Word boundaries only."""
    return bool(_RENT_RE.search(category or ''))


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
        row['looks_like_rent'] = looks_like_rent(row['category'])

    suspect = [r for r in by_category if r['looks_like_wages']]
    rent_suspect = [r for r in by_category if r['looks_like_rent']]
    return {
        'total': _sum(qs, 'amount'),
        'by_category': by_category,
        'wage_suspects': suspect,
        'wage_suspect_total': sum((r['total'] for r in suspect), ZERO),
        # Rent moved onto its own expense line, so a row still filed here
        # under a rent-shaped category is counted twice. Named so the owner
        # can move it; nothing is removed.
        'rent_suspects': rent_suspect,
        'rent_suspect_total': sum((r['total'] for r in rent_suspect), ZERO),
    }


def _rent_hint(months, starts, reaches_before):
    """The one line under the Rent figure, on the expense list and the card.

    `_salary_hint`'s shape: say what is IN the figure, and when the window
    reaches further back than the ledger, say what is missing from it instead.
    Computed here rather than in the view or the template because it is a
    statement ABOUT the money, and the two places that print it must not be
    free to word it differently.
    """
    if reaches_before:
        return (f"{months} month{'' if months == 1 else 's'} — rent is only "
                f"recorded from {starts.strftime('%B %Y')}")
    if not months:
        return 'No rent recorded for this period'
    return f"{months} whole month{'' if months == 1 else 's'}, by the rent month"


def rent_expense(start, end):
    """
    Stream 5 — what the premises cost for the whole months inside this window.

    FROM THE RATE, NEVER FROM THE DEPOSITS, and that is the whole point of the
    Deposit & Rent section. The rent is a fixed ₹35,000 a month whatever cash
    happened to move; the daily handovers are how it gets PAID. Charge the
    deposits instead and a month where the office had a good cash week reports
    a higher rent than a lean one, so monthly profit swings on a cash-flow
    decision rather than on what the month cost — on the page the owners read
    to decide distribution.

    That is the app's fourth instance of one rule, not a new idea: wages are
    dated by the salary MONTH and not the day the cash left; a
    `SupplierPayment` never touches profit while the stock DRAW does; a
    spare-shop payment never touches profit while the part fitted does. The
    cash side of rent is reported by `cash_position()` and appears here
    nowhere.

    ⚠ UNTIL 2026-09-04 THIS STREAM DID NOT EXIST, and the equation had four.
    That was correct while rent still arrived as a Cashbook category, and it
    stopped being correct when the office started recording rent in its own
    section: September 2026 carried ₹35,000 of real rent and the page charged
    ₹900 of it, while May–August carried ₹45,000 Cashbook rows against a
    stored rate of ₹35,000 — two different rents in one system, neither page
    aware of the other. Cashbook rows named like rent are now FLAGGED as the
    double count they have become (`RENT_WORDS`), never filtered.

    The cap, the month-dating rule and the reasoning for both are in
    `rent.charged_by_month`, which is the only implementation.
    """
    # ONE rate lookup for all three reads. The table holds one row per rent
    # CHANGE, so this is a handful of rows for the life of the business — but
    # this report is built up to three times per page render, and three walks
    # each was 9 of the 15 rent queries on a 109-query page.
    all_rates = rent_calc.rates()
    total = rent_calc.charged_between(start, end, all_rates=all_rates)
    by_month = rent_calc.charged_by_month(start, end, all_rates=all_rates)
    starts = rent_calc.ledger_starts(all_rates=all_rates)
    # A window reaching back FURTHER than the rent ledger charges nothing for
    # those months. That is the answer opening stock and the owner-withdrawal
    # history already get — the figure is short for the period before the
    # section existed — and like both of those the page says so rather than
    # reading as though the premises had been free. It clears itself as
    # history accumulates.
    reaches_before = bool(starts and start < starts)
    return {
        'total': total,
        'months': len(by_month),
        'starts': starts,
        'reaches_before': reaches_before,
        'hint': _rent_hint(len(by_month), starts, reaches_before),
    }


# =============================================================================
# WHAT EARNED THE MONEY  —  the same profit, decomposed a second way
# =============================================================================
# Added 2026-08-25 on the owner's instruction. The owner does not think of this
# business as "turnover minus expenses"; they think of it as FOUR THINGS THAT
# EARN — labour, the margin on parts bought per job, the margin on parts taken
# off the shelf, and the occasional bit of scrap income — less the running
# costs. Both descriptions are true of the same workshop, and the page states
# both rather than making the owner translate between them.
#
#     LABOUR + SPARE PARTS MARGIN + INVENTORY MARGIN + CASHBOOK INCOME
#         (less discounts given)              =  GROSS EARNINGS
#     less SALARY and GENERAL CASHBOOK        =  THE SAME PROFIT
#
# ⚠ THAT IS THE WHOLE ARITHMETIC, and it closes with nothing in between.
#
# It did not, briefly. This card first shipped alongside an equation that
# charged a Supplies Shop BILL on its bill date while the card charged stock
# when it was USED, so a "stock movement" line had to sit at the bottom
# converting one basis into the other. It reconciled to the rupee and it was
# the wrong answer to the problem: a page that has to explain itself to itself
# is a page nobody trusts, and the owner said so plainly — "I am more confused
# now."
#
# The fix was to pick ONE basis, not to word the bridge better. Both parts
# routes now cost what was FITTED to cars in the period, which is what
# `spare_shop_expense` had always done — that route is dated by
# `job_card__admitted_date` and `unassigned_spare_purchases` exists to hold
# back the parts not yet fitted. The warehouse route was the odd one out.
#
# THE DISCOUNT IS ITS OWN LINE, and leaving it out was the easy mistake here —
# the identity does not close without it. A discount is given on the WHOLE
# bill, so it belongs to neither the labour line nor either margin. Shown only
# when there is some, exactly as the Turnover card shows it.
#
# NOT DEDUCTED TWICE: `unattributed_spare_expense` — a shop purchase with no
# shop recorded — is already inside the shop side's cost here, because
# `parts_trading` costs every SOURCE_SHOP row whether or not a shop was named.
# The equation splits it out as its own expense line; this one absorbs it.
# Deducting it here as well would understate profit by that amount.

def labour_revenue(start, end):
    """
    Labour charged on cards admitted in the window.

    Off `JobCard.labour_amount`, never off the job lines — work is quoted whole
    at this workshop and `JobCardLabourItem.amount` is a dormant column, so
    summing the lines would report every card created since that change as ₹0.
    """
    return _sum(live_jobcards().filter(admitted_date__range=(start, end)), F('labour_amount'))


def parts_trading(start, end):
    """
    What parts SOLD for and what they COST, split by route.

    One implementation, read by three surfaces: the Profit page's earnings
    breakdown and the two Deep Analysis parts sections. They ask one question
    at two depths, and a second copy would be two answers free to disagree —
    which, one tap apart, reads as the app contradicting itself about a margin.

    Revenue is `total_price`, the customer price, on both routes. Cost is
    `SPARE_COST`, which is route-aware and must stay that way: a shop line's
    cost is the line total as typed, a warehouse draw's is a weighted average
    x quantity.

    ⚠ An uncosted draw (`unit_price` NULL) costs ₹0 here, so it reads as a FREE
    part and pushes the stock margin UP. `uncosted_draw_count()` is what says
    so — do not quietly exclude those rows instead, or the subtotal would stop
    adding up from the table printed above it.

    It returns BOTH sides even though each parts section reads only one, which
    costs that section one extra aggregate. That is the price of the guarantee
    and it is worth paying: because both sides come out of one call, they
    partition the spare rows exactly — no row counted twice, none dropped — and
    the Profit page's two margin lines are the same two figures the sections
    print. Splitting this into a per-route function to save the query would
    hand that guarantee back.
    """
    base = _live_spares(start, end)

    def side(qs):
        agg = qs.aggregate(
            revenue=Coalesce(Sum('total_price', output_field=MONEY),
                             Value(ZERO, output_field=MONEY), output_field=MONEY),
            cost=Coalesce(Sum(SPARE_COST, output_field=MONEY),
                          Value(ZERO, output_field=MONEY), output_field=MONEY),
            lines=Count('id'),
        )
        agg['profit'] = agg['revenue'] - agg['cost']
        agg['margin'] = float(agg['profit'] / agg['revenue'] * 100) if agg['revenue'] else 0.0
        return agg

    return {
        'shop': side(base.filter(source=JobCardSpareItem.SOURCE_SHOP)),
        'stock': side(base.filter(source=JobCardSpareItem.SOURCE_INVENTORY)),
    }


def earnings_breakdown(start, end, bills, cb_income, salary_total, cashbook_total,
                       rent_total):
    """
    The same profit, said the owner's way. Every shared figure is HANDED IN.

    Nothing already computed by `build_profit_report` is fetched again, and
    that is correctness rather than thrift: a breakdown that looked up its own
    salary or its own cashbook total could disagree with the equation printed
    directly above it. Only the two figures the equation has no use for —
    labour, and the parts split — are fetched here.

    Returns `earn` (what came in) and `spend` (what it cost to run) as lists of
    rows the template prints in order, plus both totals. `negative` says which
    way a row goes, so the template never decides a sign. Sub-figures are
    handed over RAW, never pre-formatted: rupees are written with the `inr`
    filter's Indian grouping everywhere in this app, and an f-string here would
    print '1,125,000' beside the same figure written '11,25,000'.
    """
    labour = labour_revenue(start, end)
    parts = parts_trading(start, end)
    discount = bills['discount']

    gross = (labour + parts['shop']['profit'] + parts['stock']['profit']
             + cb_income - discount)

    earn = [
        {'key': 'labour', 'label': 'Labour', 'icon': 'bi-tools',
         'hint': 'The Job Performed charge on every card',
         'amount': labour, 'negative': False},
        # ⚠ `cost`/`cost_word`, NOT `paid`. These are what the parts COST, and
        # the field was called `paid` with the shop row reading "paid to
        # shops" — which is cash on every other screen in Analysis. The Shops
        # section prints actual cash out as "Paid to spare shops", and on the
        # demo data the two read 1.85L and 6L: one word, two meanings, two
        # figures, on pages an owner opens in one sitting. The two are
        # deliberately different numbers, because shops are settled in
        # instalments, so the WORD is the only thing telling them apart.
        {'key': 'spare_margin', 'label': 'Spare Parts margin',
         'icon': 'bi-gear-wide-connected', 'hint': '',
         'charged': parts['shop']['revenue'], 'cost': parts['shop']['cost'],
         'cost_word': 'spent at shops',
         'amount': parts['shop']['profit'], 'negative': False},
        {'key': 'stock_margin', 'label': 'Inventory margin',
         'icon': 'bi-box-seam', 'hint': '',
         'charged': parts['stock']['revenue'], 'cost': parts['stock']['cost'],
         'cost_word': 'of stock used',
         'amount': parts['stock']['profit'], 'negative': False},
    ]
    # Both of these are normally absent, and a permanent ₹0 between two figures
    # that matter is how a row stops being read. They appear the moment the
    # money does; `gross` is unchanged either way.
    if cb_income:
        earn.append({'key': 'cashbook_income', 'label': 'Cashbook Income',
                     'icon': 'bi-journal-plus', 'hint': 'Scrap, black oil, misc',
                     'amount': cb_income, 'negative': False})
    if discount:
        earn.append({'key': 'discount', 'label': 'Less: discounts given',
                     'icon': 'bi-scissors', 'hint': 'Billed but never earned',
                     'amount': discount, 'negative': True})

    # ⚠ THERE IS NO STOCK-MOVEMENT LINE HERE ANY MORE, and its absence is the
    # point rather than an omission. It existed to convert between two bases —
    # the equation charged a Supplies Shop BILL on its bill date while this
    # card charged stock when it was USED — and a page that has to reconcile
    # itself to itself is a page nobody trusts. Both now charge stock when it
    # is used, so `gross − salary − cashbook` IS the profit, with nothing in
    # between. If a conversion line ever needs to come back, the two bases have
    # drifted apart again and that is the bug.
    spend = [
        {'key': 'salary', 'label': 'Salary & Advance', 'icon': 'bi-cash-coin',
         'hint': 'The wage bill', 'amount': salary_total, 'negative': True},
        # ⚠ RENT IS HANDED IN LIKE EVERY OTHER SHARED FIGURE. Left out, this
        # card would land on a profit ₹35,000 a month above the equation
        # printed directly over it — and the whole safety of stating the
        # profit twice is that the second statement lands on the first with
        # nothing in between.
        {'key': 'rent', 'label': 'Rent', 'icon': 'bi-house-door',
         'hint': 'What the premises cost', 'amount': rent_total, 'negative': True},
        # "Cashbook Expense", not "General Cashbook": `Cashbook Income` sits
        # four rows above it in this same card. One ledger, two directions,
        # and the two names have to say so.
        #
        # ⚠ THE HINT NO LONGER SAYS "RENT". Rent has its own row directly
        # above, so naming it here too would point the reader at the wrong
        # line for the largest fixed cost in the list.
        {'key': 'cashbook', 'label': 'Cashbook Expense', 'icon': 'bi-journal-text',
         'hint': 'Power, water, consumables', 'amount': cashbook_total, 'negative': True},
    ]

    net_spend = sum((r['amount'] if r['negative'] else -r['amount']) for r in spend)

    return {
        'labour': labour,
        'parts': parts,
        'discount': discount,
        'cashbook_income': cb_income,
        'earn': earn,
        'spend': spend,
        'gross': gross,
        'net_spend': net_spend,
        'profit': gross - net_spend,
    }


# =============================================================================
# THE REPORT
# =============================================================================

def build_profit_report(start, end, disclosures=True):
    """
    One dict with every number the Profit page shows.

    Deliberately eager rather than lazy/AJAX: this is a handful of indexed
    aggregate queries, and an owner checking profit should get the whole
    picture in one load rather than watching cards populate.

    `disclosures=False` skips the four figures that exist ONLY to be printed as
    footnotes — unsettled salary months, unassigned shop purchases, warehouse
    draws and uncosted draws. The page builds this report up to three times (the
    window itself, the comparison period, and the window trimmed to today when
    it is unfinished) and reads nothing but `turnover` and `profit` off the last
    two, so computing their footnotes was six wasted queries each.

    ⚠ THE EQUATION IS NOT WHAT IS BEING SKIPPED. Every stream that reaches
    `expense_total` runs either way, so the comparison can never be measuring a
    different definition of profit from the headline — which is what a separate
    lightweight "just the totals" function would have risked, and why there
    isn't one.
    """
    bills = car_bill_turnover(start, end)
    cb_income = cashbook_income(start, end)
    turnover = bills['net'] + cb_income

    spares = spare_shop_expense(start, end)
    # THE COST OF STOCK USED, not of stock bought. Always computed — it is part
    # of the equation now, so `disclosures=False` must never skip it or the
    # comparison period would be measuring a different definition of profit.
    stock_used = warehouse_drawn_spare_cost(start, end)
    salary = salary_expense(start, end, gaps=disclosures)
    cashbook = cashbook_expense(start, end)
    other_spares = unattributed_spare_expense(start, end)
    # WHAT THE PREMISES COST, from the rate — never from the deposits, and
    # ALWAYS computed. Like `stock_used` it is part of the equation, so
    # `disclosures=False` must never skip it or the comparison period would be
    # measuring a different definition of profit from the headline.
    rent = rent_expense(start, end)

    expense_total = (spares + stock_used + salary['total'] + cashbook['total']
                     + other_spares + rent['total'])
    profit = turnover - expense_total

    # Ordered biggest-first so the page reads as "where the money went".
    #
    # ⚠ BOTH PARTS LINES ARE THE SAME QUESTION ON THE SAME BASIS: what did the
    # parts fitted to cars in this period cost us. One route was bought from a
    # spare shop for the job, the other came off the warehouse shelf. Neither
    # is "what we were billed this period" and neither is "what we paid this
    # period" — a Supplies Shop bill moves the payable in
    # `financial_position()`, and an instalment paid against it moves the
    # payable again. Neither touches profit. That is why both hints say so.
    expense_lines = [
        {'key': 'spare_shops', 'label': 'Spare Shops',
         'hint': 'Parts bought per job, not payments', 'amount': spares, 'icon': 'bi-tools'},
        {'key': 'inventory', 'label': 'Inventory Used',
         'hint': 'Parts taken off the warehouse shelf', 'amount': stock_used,
         'icon': 'bi-box-seam'},
        {'key': 'salary', 'label': 'Salary & Advance',
         'hint': salary['hint'], 'amount': salary['total'], 'icon': 'bi-cash-coin'},
        # ⚠ THE HINT NO LONGER SAYS "RENT" — rent is the line below.
        {'key': 'cashbook', 'label': 'Cashbook Expense',
         'hint': 'Power, water, consumables', 'amount': cashbook['total'], 'icon': 'bi-journal-text'},
        {'key': 'rent', 'label': 'Rent',
         'hint': rent['hint'], 'amount': rent['total'], 'icon': 'bi-house-door'},
    ]
    if other_spares > ZERO:
        expense_lines.append({
            'key': 'other_spares', 'label': 'Other Spare Purchases',
            'hint': 'Bought for a job, no shop recorded',
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
        'rent': rent,
        'warehouse_drawn': stock_used,
        # Skipped with the footnotes on the comparison reports, which read
        # nothing but `turnover` and `profit`.
        'earnings': earnings_breakdown(
            start, end, bills, cb_income, salary['total'], cashbook['total'],
            rent['total'],
        ) if disclosures else None,
        'uncosted_draws': uncosted_draw_count(start, end) if disclosures else 0,
        'uncosted_shop': uncosted_shop_count(start, end) if disclosures else 0,
        'unassigned_spares': unassigned_spare_purchases() if disclosures else {'amount': ZERO, 'count': 0},
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

    rev = grouped(live_jobcards().filter(admitted_date__range=(start, end)),
                  'admitted_date', F('total_bill_amount') - F('discount_amount'))
    inc = grouped(CashbookEntry.objects.filter(entry_type='INCOME', date__range=(start, end)),
                  'date', F('amount'))
    sp = grouped(_live_spares(start, end).filter(
                     source=JobCardSpareItem.SOURCE_SHOP, shop__isnull=False),
                 'job_card__admitted_date', SPARE_COST)
    # BOTH PARTS ROUTES GROUPED BY THE JOB CARD'S DATE, because both are now
    # charged when the part is fitted. This used to group `SupplierRestockBill`
    # by `bill_date`; leaving it that way would have put the chart on a
    # different basis from the headline, which `ConsistencyTests` catches — the
    # chart must always total to `build_profit_report`.
    inv = grouped(_live_spares(start, end).filter(
                      source=JobCardSpareItem.SOURCE_INVENTORY),
                  'job_card__admitted_date', SPARE_COST)
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

    other_qs = _live_spares(start, end).filter(
        source=JobCardSpareItem.SOURCE_SHOP, shop__isnull=True)
    oth = grouped(other_qs, 'job_card__admitted_date', SPARE_COST)

    # ⚠ RENT IS NOT A GROUPED QUERY — it is DERIVED, so it comes from the one
    # implementation rather than from rows. It also has to join `keys`: a month
    # carrying rent and nothing else must still draw a bar, or the chart stops
    # totalling to the headline and `ConsistencyTests` fails. Same cap and same
    # month-dating rule as the headline, because it is literally the same
    # function.
    rnt = rent_calc.charged_by_month(start, end)

    keys = sorted(set(rev) | set(inc) | set(sp) | set(inv) | set(cb) | set(sal)
                  | set(adv) | set(oth) | set(rnt))
    rows = []
    for k in keys:
        t = rev.get(k, ZERO) + inc.get(k, ZERO)
        e = (sp.get(k, ZERO) + inv.get(k, ZERO) + cb.get(k, ZERO)
             + sal.get(k, ZERO) + adv.get(k, ZERO) + oth.get(k, ZERO)
             + rnt.get(k, ZERO))
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

def warehouse_stock_value():
    """
    What is ON the shelf right now, at what the shelf paid for it.

    A POSITION, not a flow — deliberately not window-scoped, like every other
    figure in `financial_position()`. Read by two surfaces: the Profit page's
    position tiles and the Inventory insight section. It lives here rather than
    in a view because it is money math, and because two copies would be two
    answers to "what is the stock worth" on two screens an owner reads together.

    ⚠ UNKNOWN COST IS `avg_cost == 0`, NOT NULL. The column is
    `default=0, null=False`, so an `isnull` filter matches nothing and would
    quietly value opening stock that has never had a supplier bill behind it at
    ₹0 — reporting it as worthless rather than as unknown. Those products are
    excluded from the figure and COUNTED instead, the rule
    `uncosted_draw_count()` follows: "we don't know" is the correct answer and
    ₹0 is a wrong one. **Expect a count here on go-live day**, until the first
    restock bill for each product is entered.

    Negative stock is left NEGATIVE, not clamped. It is allowed by design and
    it means a Supplies Shop bill is missing, so flooring it would delete the
    signal — the same defect the old `Greatest(…, ZERO)` clamp caused on the
    shelf itself.
    """
    from inventory.models import Item

    qs = Item.objects.all()
    return {
        'value': _sum(qs.exclude(avg_cost=0), F('current_stock') * F('avg_cost')),
        'uncosted_products': qs.filter(avg_cost=0).exclude(current_stock=0).count(),
    }


def cash_position(start, end):
    """
    MONEY IN AND MONEY OUT — cash, by the day it actually moved.

    ⚠ THIS IS NOT PROFIT AND MUST NEVER BE MIXED INTO IT. Profit and cash
    differ by five things at once — stock bought but unused, stock used but
    bought earlier, bills unpaid, bills paid from earlier periods, and customer
    bills unpaid — so an owner who subtracts one from the other gets a number
    that is not anything. Nothing here appears in `build_profit_report`, and
    nothing from the profit equation is reported here as though it were cash.
    The card that draws this says so on its face.

    THE THREE TRAPS, each of which would break it silently:

    1. A FLEET CARD'S `received_amount` IS CUMULATIVE. Summing job cards for
       fleet money counts a card's whole life on the day it finally closed — a
       1,10,000 card collected over three months landing entirely in the third.
       Fleet cash comes from `BulkPaymentHistory`, one row per payment, dated
       by the day the money moved (the column added in `0072` for this). So the
       walk-in half is `payment_status='PAID'` ONLY: including `BULK_PAID`
       would count every fleet rupee twice.

    2. WAGES ARE DATED BY THE SALARY MONTH, not the day they were handed over,
       and that is deliberate. Settlement happens at month end or the 1st or
       2nd of the next month — it straddles the boundary — so the same wage
       bill would land in different months depending on which side of midnight
       somebody pressed a button. The salary month never moves, the owners
       already think of August's wages as August's cost, and every other figure
       in this app agrees (`salary_expense` filters `SalaryPayment.month`).
       Accepted consequence: August's wages are shown in August though the cash
       left in early September — a constant one-month shift that repeats
       identically every month, so it never accumulates.

    3. IT REUSES `salary_expense`, it does not restate it. That function
       already carries the guard that an advance inside a settled month is not
       counted twice — once inside its settlement and again as a loose advance.
       A second implementation here would be a second answer, free to drift.

    Everything else is dated by its own `date` column: both shop ledgers, the
    Cashbook, and now fleet payments. Every one of those is the day the money
    moved, so the streams are on one basis and can honestly be added.
    """
    from inventory.models import SupplierPayment

    # ---- in ----------------------------------------------------------------
    walkin = _sum(
        live_jobcards().filter(payment_status='PAID',
                               paid_date__date__range=(start, end)),
        F('received_amount'))
    fleet = _sum(
        BulkPaymentHistory.objects.filter(is_trashed=False, date__range=(start, end)),
        F('amount'))
    other_income = cashbook_income(start, end)

    # ---- out ---------------------------------------------------------------
    spare_paid = _sum(
        SpareShopPayment.objects.filter(is_trashed=False, date__range=(start, end)),
        F('amount'))
    supplies_paid = _sum(
        SupplierPayment.objects.filter(is_trashed=False, date__range=(start, end)),
        F('amount'))
    # `gaps=True`: an UNSETTLED month has had only its advances handed out, so
    # the wage line reads far below a real month's pay — 9,000 against 1,24,000
    # on the demo data. That is arithmetically right and reads as a windfall,
    # so the card names the months exactly as the profit equation does. Same
    # fact, same signal, one implementation.
    salary = salary_expense(start, end)
    wages = salary['total']
    running = cashbook_expense(start, end)   # a dict: the card wants its total
    # ⚠ REAL CASH, HANDED TO A MAN WITH A BOOK, and it reached this card
    # nowhere until 2026-09-04. ₹12,32,500 had been paid out over three years
    # and the only screen in the app that reports cash movement did not know:
    # September read ₹15,660 out against a true ₹23,160. It is the DEPOSITS
    # here and the RATE in the profit equation, which is the same split a
    # supplier payment and a stock draw already get.
    rent_paid = rent_calc.deposited_between(start, end)
    owner_taken = _sum(
        OwnerWithdrawal.objects.filter(date__range=(start, end)), F('amount'))

    money_in = [
        {'label': 'Customer bills settled', 'hint': 'walk-in, by the day it was settled',
         'amount': walkin},
        {'label': 'Fleet account payments', 'hint': 'by the day the money moved',
         'amount': fleet},
        {'label': 'Scrap and other income', 'hint': 'from the cashbook',
         'amount': other_income},
    ]
    money_out = [
        {'label': 'Spare shops', 'hint': 'paid against their ledgers', 'amount': spare_paid},
        {'label': 'Supplies shops', 'hint': 'paid against their ledgers', 'amount': supplies_paid},
        {'label': 'Wages and advances', 'hint': 'counted in the month they are for',
         'amount': wages},
        {'label': 'Rent deposits', 'hint': 'handed over, by the day it moved',
         'amount': rent_paid},
        # ⚠ THE LABEL NO LONGER SAYS "RENT". Rent is the line directly above,
        # on its own basis, and one card naming the same cost twice is how an
        # owner comes to read one of the two figures as the other.
        {'label': 'Power, water, consumables', 'hint': 'from the cashbook',
         'amount': running['total']},
        # ⚠ CASH ONLY. An owner withdrawal is profit being TAKEN, not a cost of
        # earning it, so this is the only figure in the whole engine that reads
        # `OwnerWithdrawal` — it appears nowhere in `build_profit_report`, in no
        # expense line and in no margin. Adding it to profit would shrink the
        # figure the next distribution is decided from, by exactly the amount
        # already distributed.
        {'label': 'Owner withdrawals', 'hint': 'profit taken out, not a cost',
         'amount': owner_taken},
    ]

    total_in = sum((r['amount'] for r in money_in), ZERO)
    total_out = sum((r['amount'] for r in money_out), ZERO)
    movement = total_in - total_out

    return {
        'money_in': money_in,
        'money_out': money_out,
        'total_in': total_in,
        'total_out': total_out,
        # THE SIGN IS TURNED INTO WORDS HERE, never in the template — the same
        # rule `financial_position` follows. `movement` is kept signed for
        # tests and any future caller; `direction` and `magnitude` are what the
        # card prints.
        'movement': movement,
        'magnitude': abs(movement),
        'direction': 'in' if movement >= ZERO else 'out',
        # ⚠ NOT A BALANCE, AND THE CARD MUST NEVER CALL IT ONE. There is no
        # opening cash figure anywhere in this system, so what can be reported
        # is the CHANGE over the window and never the position. An owner who
        # reads "in the account", checks the bank and sees something else stops
        # believing the whole app.
        'is_balance': False,
        # The Cashbook is free text, so "Paid Ninoos 20,000" typed there is
        # counted twice — once as a cashbook expense, once as a shop payment.
        # Flagged, never filtered, exactly as the wage double-count is on the
        # profit page: a keyword filter would hide real money.
        'shoplike_cashbook': _shoplike_cashbook_count(start, end),
        # Named, never estimated. A wage figure nobody paid, inside a figure
        # labelled cash, is how this card would go from incomplete to wrong.
        'unsettled_months': salary['unsettled_months'],
    }


SHOP_WORDS = ('shop', 'spare', 'parts', 'supplier', 'supplies')


def _shoplike_cashbook_count(start, end):
    """Cashbook expenses whose category reads like a shop payment.

    Both would then be counted: the cashbook row here, and the shop payment
    itself. Reported so the owner can move it, never filtered out — a category
    is free text, so a keyword filter would quietly drop real running costs
    that happen to mention a shop.
    """
    qs = CashbookEntry.objects.filter(entry_type='EXPENSE', date__range=(start, end))
    match = Q()
    for word in SHOP_WORDS:
        match |= Q(category__icontains=word)
    return qs.filter(match).count()


def financial_position():
    """
    What the workshop is owed and what it owes, as of today.

    Independent of the page's date filter on purpose: a balance is a running
    total, and slicing it by month would produce a number that means nothing.

    EVERY FIGURE HERE CAN LEGITIMATELY GO NEGATIVE, and the page has to say so
    in words rather than printing a minus sign. A spare shop paid ahead of its
    purchases is in credit, not owed ₹-7,65,938 — which is what the tile read
    before, and reads as a broken figure rather than a real position. So each
    balance is returned with the SIGN ALREADY INTERPRETED: a label, a positive
    magnitude, and a direction. The template prints what it is handed; deciding
    what a minus sign means is arithmetic, and arithmetic lives here.
    """
    from inventory.models import SupplierShop

    unsettled = live_jobcards().exclude(payment_status='PAID').exclude(payment_status='BULK_PAID')
    owed_expr = F('total_bill_amount') - F('discount_amount') - F('received_amount')

    receivable = _sum(unsettled, owed_expr)
    # THE FLEET LINE IS A SLICE OF THE LINE ABOVE IT, so it must be cut from the
    # same population by the same expression. It used to be
    # `Sum(BulkPayer.total_billed_amount − total_paid_amount)`, which differs
    # twice over: those stored totals are GROSS of discount (`update_totals`
    # sums `total_bill_amount` alone), and they span every card on the account
    # including settled ones, while `receivable` counts only unsettled cards
    # net of discount. The two agree today only because no fleet card in the
    # data carries a discount — the first one that does would make the page
    # contradict itself, claiming a slice bigger than the whole.
    #
    # Still deliberately NOT filtered by `is_trashed`: `receivable` has no such
    # filter, and a balance must not depend on whether someone tidied a list.
    fleet_due = _sum(unsettled.filter(bulk_payer__isnull=False), owed_expr)

    # NOT FILTERED BY THE ARCHIVE FLAG, for the same reason `fleet_due` is not —
    # and this side was strictly worse. A receivable that vanishes understates
    # what is owed TO the workshop; a PAYABLE that vanishes silently RAISES
    # reported profit, on the page profit is distributed from. Archiving a shop
    # the workshop owed ₹50,000 removed that ₹50,000 from the only screen that
    # counts it, with nothing said. Money owed does not stop being owed because
    # somebody tidied a list. (`AUD-0082`; both archive views now refuse a shop
    # carrying a balance, so a live shop cannot get into that state either.)
    spare_due = _sum(SpareShop.objects.all(),
                     F('total_purchased_amount') - F('total_paid_amount'))
    supplier_due = _sum(SupplierShop.objects.all(),
                        F('total_billed_amount') - F('total_paid_amount'))

    def tile(amount, owed_label, credit_label, direction):
        """One balance, with its sign already turned into words."""
        if amount < ZERO:
            return {'label': credit_label, 'amount': -amount, 'direction': 'credit'}
        return {'label': owed_label, 'amount': amount, 'direction': direction}

    # WHAT THE WORKSHOP HOLDS, beside what it owes for it.
    #
    # The owner's question, in their words: "we have to pay Supplies Shops
    # ₹1,00,000, but we have ₹1,20,000 worth of stock in the workshop." That is
    # two facts about one relationship, and until now they lived on two
    # different pages — the payable here, the stock value in Deep Analysis.
    #
    # ⚠ THEY ARE STATED, NEVER NETTED, and that restraint is the whole of it.
    # There is no accounting identity between them: the payable covers every
    # unpaid bill whether or not those goods are still on the shelf, and the
    # shelf holds goods from bills long since paid. A "net" figure would be
    # arithmetic on two numbers that do not belong to each other. Printed side
    # by side they answer the real question — is the debt backed by goods we
    # still hold — and the owner does that reading, not the page.
    #
    # It is also AT COST, not at what it would sell for, which is why the tile
    # says so: valuing the shelf at retail would put an unearned margin into a
    # balance figure.
    stock = warehouse_stock_value()
    if stock['value'] < ZERO:
        # Only reachable when overdrawn products outweigh the rest — a data
        # state meaning several Supplies Shop bills are missing, not a real
        # negative asset. Said in words like every other tile here, so the card
        # can never print a minus.
        stock_tile = {'label': 'Stock recorded short', 'amount': -stock['value'],
                      'direction': 'hold'}
    else:
        stock_tile = {'label': 'Stock on the shelf', 'amount': stock['value'],
                      'direction': 'hold'}
    stock_tile['note'] = 'at what it cost'
    stock_tile['uncosted_products'] = stock['uncosted_products']

    # RENT CHARGED AGAINST RENT DEPOSITED, as of today.
    #
    # ⚠ STATED, NEVER NETTED against anything, and the sign turned into words
    # like every other tile here: a workshop that has handed over more than the
    # months charged so far is PAID AHEAD, not owed — ₹27,500.
    #
    # It charges the current month in full, to agree with the expense line one
    # card up. That is deliberately a DIFFERENT question from the Deposit &
    # Rent hero's carry figure, which stops at the end of last month because it
    # asks whether the FINISHED months are square; charge September there and
    # the page reads "behind ₹35,000" from the 1st to the 5th every month.
    #
    # ⚠ IT WAS FULL WIDTH FOR A DAY, AND SO WAS THE STOCK TILE. The card was
    # a two-column grid filled row by row, so five half-width tiles always
    # orphaned one in a half-empty row, and a tile carrying a `note` line is
    # taller than its neighbours — hence a row each. The owner's call
    # (2026-09-04) was that the two read as odd slabs under four normal boxes:
    # **every tile is the same shape now**, and the card splits by DIRECTION
    # instead. See `tile_columns` below.
    rent_due = rent_calc.outstanding()
    rent_tile = tile(rent_due, 'Rent still to deposit', 'Rent paid ahead', 'out')
    rent_tile['note'] = 'charged to the end of this month'

    tiles = [
        tile(receivable, 'Customers owe us', 'Customers paid ahead', 'in'),
        # "Of that" only holds while it IS a slice of a positive figure. In
        # credit it is not, and the longer phrase wrapped to two lines in a
        # 305px tile on a phone where every other label sits on one.
        tile(fleet_due, 'Of that, fleet accounts', 'Fleet accounts in credit', 'in'),
        tile(spare_due, 'We owe spare shops', 'Spare shops paid ahead', 'out'),
        tile(supplier_due, 'We owe supplies shops', 'Supplies shops paid ahead', 'out'),
        # A thing the workshop HOLDS, not a debt in either direction, so it
        # wears neither green nor red — and it is what makes the left-hand
        # column read as "ours" rather than only "owed to us".
        #
        # ⚠ IT NO LONGER SITS DIRECTLY UNDER THE SUPPLIES PAYABLE, and that
        # adjacency was recorded as answering the owner's own question — "we
        # have to pay Supplies Shops ₹1,00,000, but we have ₹1,20,000 worth of
        # stock". Splitting the card by direction puts the two figures in
        # opposite columns instead of one above the other, so the comparison is
        # made ACROSS the card. Both are still on one screen without scrolling,
        # which was the original point; the lever, if the owner wants them
        # level again, is the order of the owed column.
        stock_tile,
        # A debt, so it wears the same red rail as the two shop payables, but
        # it is the only one here DERIVED from a rate rather than read off a
        # stored ledger balance, and the only one settled in daily cash —
        # hence the note.
        rent_tile,
    ]

    return {
        'receivable': receivable,
        'fleet_due': fleet_due,
        'payable_spare': spare_due,
        'payable_supplier': supplier_due,
        'payable_total': spare_due + supplier_due,
        'stock_value': stock['value'],
        'uncosted_products': stock['uncosted_products'],
        # ⚠ DELIBERATELY NOT ADDED TO `payable_total`. That figure is computed
        # and never rendered, for the recorded reason that spare + supplies is
        # not the whole debt — an unsettled month's wages are tracked as a
        # payable nowhere — so a figure labelled "total debt" would exclude the
        # largest monthly obligation the workshop has. Adding rent would make it
        # less incomplete without making it true.
        'rent_due': rent_due,
        'tiles': tiles,
        # WHAT WE HOLD ON THE LEFT, WHAT WE OWE ON THE RIGHT — the owner's
        # instruction (2026-09-04), and it makes this card speak the same
        # spatial language as CASH TRACKING directly above it, where money in
        # is the left column and money out is the right.
        #
        # ⚠ DERIVED FROM `tiles` IN ONE EXPRESSION, never built alongside it.
        # Two hand-maintained lists would be two orders free to drift, and they
        # would drift the day a tile is added — which is exactly how this card
        # ended up with a five-tile grid that orphaned one.
        #
        # ⚠ `credit` GOES LEFT WITH THE REST, not right. A shop paid ahead is
        # money in the workshop's favour and is NOT a debt, so listing it in a
        # column of debts would be the sign already turned into words and then
        # contradicted by where it sits. The rule is simply: `out` is owed,
        # everything else is not.
        #
        # The template loops columns and then tiles, so the tile markup exists
        # once. On a phone the grid collapses to one column and these stack in
        # order, which is why the held column is first: the owner reads what is
        # theirs before what they owe.
        'tile_columns': [
            [t for t in tiles if t['direction'] != 'out'],
            [t for t in tiles if t['direction'] == 'out'],
        ],
    }
