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
                         the `unit_price` LINE TOTAL on JobCardSpareItem rows
                         that have source=SHOP and a shop recorded. Not
                         multiplied by quantity — see SHOP_LINE_COST.
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
route is paid for exactly once. Which route it took is **stored**, in
`JobCardSpareItem.source`, and is never inferred:

  SHOP      — ordered from a spare shop for that job
            → the money leaves via the SPARE SHOP stream (stream 1)

  INVENTORY — taken off the warehouse shelf
            → the money already left earlier, when the shelf was filled by a
              supplier restock bill (stream 2)

So INVENTORY spare cost must NEVER be added as an expense: it would charge the
workshop twice for one part. The two routes partition the spare rows exactly,
and adding the warehouse side on top would overstate expenses by roughly the
entire value of stock consumed in the period.

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

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest, TruncMonth
from django.utils import timezone

from .models import (
    JobCard, JobCardSpareItem, CashbookEntry,
    SalaryPayment, SalaryPaymentLine, SalaryAdvance,
    SpareShop, BulkPayer,
)

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
    Value of stock pulled off the shelf onto job cards in this window.

    NOT an expense — it was paid for by a restock bill. Reported only so the
    Profit page can show *why* it is excluded instead of appearing to lose it.

    Costed from each row's own `unit_price`, which is the weighted-average
    warehouse cost frozen onto the line the moment the part was drawn.
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


def inventory_expense(start, end):
    """
    Stream 2 — warehouse restocking, at the bill's effective (post-discount)
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
    inventory = inventory_expense(start, end)
    salary = salary_expense(start, end, gaps=disclosures)
    cashbook = cashbook_expense(start, end)
    other_spares = unattributed_spare_expense(start, end)

    expense_total = spares + inventory + salary['total'] + cashbook['total'] + other_spares
    profit = turnover - expense_total

    # Ordered biggest-first so the page reads as "where the money went".
    expense_lines = [
        {'key': 'spare_shops', 'label': 'Spare Shops',
         'hint': 'Parts bought per job', 'amount': spares, 'icon': 'bi-tools'},
        {'key': 'inventory', 'label': 'Supplies Shops',
         'hint': 'Warehouse restocking', 'amount': inventory, 'icon': 'bi-truck'},
        {'key': 'salary', 'label': 'Salary & Advance',
         'hint': salary['hint'], 'amount': salary['total'], 'icon': 'bi-cash-coin'},
        {'key': 'cashbook', 'label': 'General Cashbook',
         'hint': 'Rent, power, consumables', 'amount': cashbook['total'], 'icon': 'bi-journal-text'},
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
        'warehouse_drawn': warehouse_drawn_spare_cost(start, end) if disclosures else ZERO,
        'uncosted_draws': uncosted_draw_count(start, end) if disclosures else 0,
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

    from inventory.models import SupplierRestockBill

    rev = grouped(live_jobcards().filter(admitted_date__range=(start, end)),
                  'admitted_date', F('total_bill_amount') - F('discount_amount'))
    inc = grouped(CashbookEntry.objects.filter(entry_type='INCOME', date__range=(start, end)),
                  'date', F('amount'))
    sp = grouped(_live_spares(start, end).filter(
                     source=JobCardSpareItem.SOURCE_SHOP, shop__isnull=False),
                 'job_card__admitted_date', SPARE_COST)
    inv = grouped(SupplierRestockBill.objects.filter(bill_date__range=(start, end)),
                  'bill_date', SUPPLIER_BILL_COST)
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

    return {
        'receivable': receivable,
        'fleet_due': fleet_due,
        'payable_spare': spare_due,
        'payable_supplier': supplier_due,
        'payable_total': spare_due + supplier_due,
        'tiles': [
            tile(receivable, 'Customers owe us', 'Customers paid ahead', 'in'),
            # "Of that" only holds while it IS a slice of a positive figure. In
            # credit it is not, and the longer phrase wrapped to two lines in a
            # 305px tile on a phone where every other label sits on one.
            tile(fleet_due, 'Of that, fleet accounts', 'Fleet accounts in credit', 'in'),
            tile(spare_due, 'We owe spare shops', 'Spare shops paid ahead', 'out'),
            tile(supplier_due, 'We owe supplies shops', 'Supplies shops paid ahead', 'out'),
        ],
    }
