"""
Deposit & Rent — one question, one implementation: *how much should we hand the
collector today?*

The workshop rents its premises for a fixed amount a month, and pays for it in
daily cash instalments. A man with a book comes round every day and the office
gives him whatever it can spare — commonly ₹1,500 to ₹3,000, sometimes nothing.
The landlord draws the accumulated pot every few months. The office works out
what to pay by hand: `(target − paid so far) ÷ days left`, redone every morning.
This module is that arithmetic, and nothing else.

⚠ **THE RENT AND THE DEPOSIT ARE TWO DIFFERENT NUMBERS AND MUST NEVER BECOME
ONE.** The rent is what is OWED for a month — a fixed ₹35,000, whatever cash
happened to move. The deposits are how it gets PAID. Collapse them and a month
where the office had a good week reports a higher rent than a month where it
did not, so monthly profit swings on a cash-flow decision rather than on what
the month cost. This is the rule the app already follows three times over:
wages are dated by the salary MONTH and not the day the cash was handed over; a
supplier PAYMENT never touches profit while the stock DRAW does; a spare-shop
payment never touches profit while the part fitted does. Rent is the fourth.

⚠ **NOTHING IS STORED EXCEPT THE RATE AND THE DEPOSITS.** There is deliberately
no "this month's target" column, no carry-forward column, and no per-month
charge row. Everything below is derived on read from two tables, which is what
makes the whole feature four screens' worth of arithmetic instead of a ledger
somebody has to keep in step. A stored carry would be a second copy of a figure
that is already implied, free to drift from it, and it would drift at a month
boundary — the only place anybody would notice.

THE PACE, in full:

    carry_in  = deposits BEFORE this month − rent charged for months BEFORE this
    due       = max(0, this month's rent − carry_in)
    remaining = max(0, due − deposits so far this month)
    pay today = remaining ÷ days left in the month, including today

One expression covers every case the owner asked about, with no branches:

  * **Overpay** — carry_in goes positive, so `due` falls and next month's daily
    figure drops. Deposit ₹40,000 against ₹35,000 and next month asks for
    ₹30,000, exactly as requested.
  * **Skip a day** — nothing is recorded, `remaining` is unchanged and
    `days_left` is one smaller, so tomorrow asks for slightly more.
  * **A rent change, even backdated** — `charged_through()` reads the rate in
    force for each month, so raising the rent from January in March reprices
    January and February and the shortfall appears in today's figure the moment
    it is saved. Worked example, from the owner:

        before   Jan–Mar charged 1,05,000, deposited 1,00,000
                 → short 5,000 ÷ 5 days = ₹1,000 today
        after    rent +5,000 from January
                 → Jan–Mar charged 1,20,000, deposited 1,00,000
                 → short 20,000 ÷ 5 days = ₹4,000 today

  * **Paid a long way ahead** — `due` floors at zero, so the page says so
    rather than printing a negative figure to pay.

⚠ **THE POSITION AND THE PACE CHARGE DIFFERENT MONTHS, ON PURPOSE.** `pay_today`
charges the CURRENT month in full, because finishing this month is the thing
being paced. `carry_in` stops at the END OF LAST MONTH, because it answers a
different question — are the months that are *done* square? Charge the current
month there too and the page would read "behind ₹35,000" every month from the
1st to the 5th, which is alarming, meaningless, and precisely how a real ₹4,500
shortfall stops being noticed. Both cut deposits by the same boundary, so a
catch-up paid in September clears August's shortfall the moment September ends.

THERE IS NO OPENING-BALANCE FIELD, and none is needed: the ledger begins at the
first rate's month, so a workshop starting mid-September sets the rate from
September and keys this month's deposits off the collector's book. Whatever was
settled before that month is history between the workshop and the landlord, the
same answer opening stock gets.
"""

from calendar import monthrange
from datetime import date
from decimal import ROUND_CEILING, Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from .models import RentDeposit, RentRate

ZERO = Decimal('0')

#: Summing DecimalField(10,2) rows needs somewhere wider to land than the
#: column itself, or a long history overflows the declared precision on
#: PostgreSQL. Same reason `views/withdrawal.py` carries one.
_MONEY = DecimalField(max_digits=20, decimal_places=2)
_ZERO_MONEY = Value(ZERO, output_field=_MONEY)

#: NOTHING HERE IS CAPPED OR PAGINATED, and after twenty years it still is not.
#: The history is grouped into COLLAPSED YEAR BLOCKS — Salary & Advance's own
#: pattern, where the running year opens and older ones sit behind a one-line
#: total — so two decades is twenty closed rows and one open year of twelve.
#: The deposit log is scoped to a single MONTH, which is naturally bounded at
#: about sixty rows however long the business runs.
#:
#: A cap was the first answer and it was wrong in the way caps usually are:
#: everything past it becomes unreachable, and a money list that quietly stops
#: is worse than a long one.


# =============================================================================
# MONTH ARITHMETIC
# =============================================================================
# Every month in this module is a `date` on the 1st. `RentRate.save()` pins its
# own column to that, so a rate and a month can always be compared directly.

def month_of(day):
    """The 1st of whatever month this date falls in."""
    return day.replace(day=1)


def shift_month(month, n):
    """`n` months on from a first-of-month date; negative goes back."""
    total = month.year * 12 + (month.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def months_between(first, last):
    """How many months from `first` to `last`, counting both ends."""
    if last < first:
        return 0
    return (last.year - first.year) * 12 + (last.month - first.month) + 1


def days_left_in_month(day):
    """Days remaining in `day`'s month, INCLUDING `day` itself.

    Including today is what makes the last day of the month ask for the whole
    remaining shortfall rather than dividing by zero.
    """
    return monthrange(day.year, day.month)[1] - day.day + 1


# =============================================================================
# THE RATE
# =============================================================================

def rates():
    """Every rent rate, oldest first.

    Fetched whole rather than queried per month: a rate row is created only
    when the rent actually changes, so this is a handful of rows for the life
    of the business, and one list makes `charged_through` a loop over spans
    instead of a query per month.
    """
    return list(RentRate.objects.order_by('effective_from'))


def rate_for(month, all_rates=None):
    """The rent in force for `month`, or None if the ledger has not started.

    The latest rate whose `effective_from` is on or before the month — so a
    rate dated ahead (a hike agreed now, effective in January) changes nothing
    until January arrives.
    """
    all_rates = rates() if all_rates is None else all_rates
    chosen = None
    for rate in all_rates:
        if rate.effective_from > month:
            break
        chosen = rate
    return chosen.amount if chosen else None


def charged_through(month, all_rates=None):
    """Total rent owed from the first rate's month through `month`, inclusive.

    Walks the rate SPANS rather than the months, so a decade of history costs
    one iteration per rent change rather than one per month.
    """
    all_rates = rates() if all_rates is None else all_rates
    if not all_rates or month < all_rates[0].effective_from:
        return ZERO

    total = ZERO
    for index, rate in enumerate(all_rates):
        if rate.effective_from > month:
            break
        following = all_rates[index + 1].effective_from if index + 1 < len(all_rates) else None
        # This rate runs until the month before the next one starts, or to the
        # end of the window, whichever comes first.
        if following is None or following > month:
            span_end = month
        else:
            span_end = shift_month(following, -1)
        total += rate.amount * months_between(rate.effective_from, span_end)
    return total


# =============================================================================
# THE DEPOSITS
# =============================================================================

def _deposited(**filters):
    """One Sum over the deposit table, never None."""
    return RentDeposit.objects.filter(**filters).aggregate(
        t=Coalesce(Sum('amount', output_field=_MONEY), _ZERO_MONEY))['t']


# =============================================================================
# WHERE WE STAND
# =============================================================================

def position(today=None):
    """
    Everything the page prints, from two Sums and one small table of rates.

    `today` is injectable so the tests can stand on a fixed day; callers pass
    nothing and get `timezone.localdate()` — never `date.today()`, which calls
    the small hours of an IST morning "tomorrow".
    """
    today = today or timezone.localdate()
    this_month = month_of(today)
    all_rates = rates()

    # THE LEDGER HAS NOT STARTED. Either no rent has ever been recorded, or the
    # only rate on file is dated ahead. Both are real states on the day the
    # section is switched on, and both must render a page rather than divide by
    # a rent of None.
    first_month = all_rates[0].effective_from if all_rates else None
    if first_month is None or this_month < first_month:
        return {
            'started': False,
            'has_rate': bool(all_rates),
            'today': today,
            'this_month': this_month,
            'rent': rate_for(this_month, all_rates) or ZERO,
            'starts': first_month,
            'paid_this_month': ZERO,
            'due': ZERO,
            'remaining': ZERO,
            'pay_today': ZERO,
            'days_left': days_left_in_month(today),
            'carry': ZERO,
            'carry_direction': 'square',
            'carry_amount': ZERO,
        }

    rent = rate_for(this_month, all_rates)

    # BOTH SIDES ARE CUT AT THE SAME BOUNDARY — the 1st of this month. Deposits
    # before it against rent charged before it is the position on months that
    # are finished; deposits since it against this month's rent is the pace.
    # Mixing the two boundaries is what would make a catch-up payment look like
    # a surplus while the month it belongs to still read as short.
    paid_before = _deposited(date__lt=this_month)
    # ⚠ BOUNDED AT BOTH ENDS, exactly like `deposits_in()` and the last row of
    # `month_rows()`. Left open, a row dated into a FUTURE month would be
    # counted here and in neither of those, so the hero, the log and the table
    # would quietly disagree about the same money. `rent_deposit_add` refuses a
    # future date, so this cannot arise through the UI — but three surfaces
    # reading one figure have to cut it identically or one of them is wrong.
    paid_this_month = _deposited(date__gte=this_month,
                                 date__lt=shift_month(this_month, 1))
    charged_before = charged_through(shift_month(this_month, -1), all_rates)

    # SIGNED: positive is paid ahead, negative is behind. The page turns it
    # into a word and a positive magnitude — the rule `financial_position()`
    # already follows, because "behind ₹4,500" is a position and "-4,500" reads
    # as a broken figure.
    carry = paid_before - charged_before

    # A surplus reduces what this month needs; a shortfall adds to it. Floored,
    # so being a long way ahead asks for ₹0 rather than a negative.
    due = max(ZERO, rent - carry)
    remaining = max(ZERO, due - paid_this_month)
    days_left = days_left_in_month(today)

    # ROUNDED UP TO THE RUPEE. Down would leave a few rupees uncovered on the
    # last day of every month, and paise are not something anybody hands over.
    # The figure self-corrects tomorrow whatever is actually paid, so rounding
    # up costs nothing and can never leave the month short.
    pay_today = (remaining / days_left).to_integral_value(rounding=ROUND_CEILING)

    if carry > ZERO:
        direction = 'ahead'
    elif carry < ZERO:
        direction = 'behind'
    else:
        direction = 'square'

    return {
        'started': True,
        'has_rate': True,
        'today': today,
        'this_month': this_month,
        'starts': first_month,
        'rent': rent,
        'paid_this_month': paid_this_month,
        'due': due,
        'remaining': remaining,
        'pay_today': pay_today,
        'days_left': days_left,
        'carry': carry,
        'carry_direction': direction,
        'carry_amount': abs(carry),
        # What the progress bar draws. Against `due` rather than `rent`, so a
        # month carrying last month's shortfall shows the real finish line.
        'progress': min(100, int(paid_this_month / due * 100)) if due else 100,
    }


def month_rows(today=None):
    """
    One row per month since the first rate — rent, deposited, and the running
    position after it. Oldest first, because a running total is accumulated
    forwards; `year_blocks()` is what reverses it for display.

    ⚠ EVERY MONTH IS WALKED, not just the ones drawn. The running figure is the
    whole point of the table and it is only correct if it carries the entire
    history, so a year opened halfway down twenty years still agrees with the
    hero. Twenty years is 240 iterations over one grouped query — nothing.
    """
    today = today or timezone.localdate()
    this_month = month_of(today)
    all_rates = rates()
    if not all_rates or this_month < all_rates[0].effective_from:
        return []

    by_month = {
        row['m']: row['t'] for row in
        RentDeposit.objects.annotate(m=TruncMonth('date')).values('m').annotate(
            t=Coalesce(Sum('amount', output_field=_MONEY), _ZERO_MONEY))
    }
    # `TruncMonth` on a DateField gives back a date; on some backends a
    # datetime. Normalise to a plain first-of-month date so the lookup below
    # cannot silently miss every row.
    by_month = {(k.date() if hasattr(k, 'date') else k): v for k, v in by_month.items()}

    rows = []
    first_month = all_rates[0].effective_from

    # ⚠ THE TABLE OPENS ON WHATEVER WAS PAID BEFORE THE LEDGER STARTED, or it
    # disagrees with the hero. A deposit dated before the first rate's month is
    # how an opening position is entered — the workshop was already ahead or
    # behind on the day the section was switched on — and `position()` counts
    # it, because its `paid_before` is every deposit before this month with no
    # floor. Starting this walk at zero left that money in the hero and out of
    # every row, which is the one thing a money page may never do: the total
    # above disagreeing with the rows beneath it.
    running = _deposited(date__lt=first_month)

    month = first_month
    while month <= this_month:
        rent = rate_for(month, all_rates)
        paid = by_month.get(month, ZERO)
        running += paid - rent
        # (a row dated into a future month is impossible through the UI and is
        # left in its own month rather than folded in here — see `position()`)
        rows.append({
            'month': month,
            'rent': rent,
            'paid': paid,
            'diff': paid - rent,
            'running': running,
            'is_current': month == this_month,
        })
        month = shift_month(month, 1)
    return rows


def year_blocks(today=None):
    """
    The month history grouped into years, newest first — the shape that makes
    twenty years readable.

    Salary & Advance's own pattern: the running year opens and every older one
    collapses behind a single line carrying that year's rent, that year's
    deposits, and the position it ended on. So the archive costs one row per
    year until somebody opens one, and NOTHING is hidden behind a cap.

    ⚠ A YEAR'S POSITION COUNTS ONLY MONTHS THAT HAVE FINISHED — the same rule
    the hero's "before this month" follows, and for the same reason. Taken from
    its latest month, the CURRENT year would carry the whole of an unfinished
    month's rent against a few days of deposits, so the running year would read
    a five-figure "behind" every month from the 1st, on a line whose whole job
    is to say whether that year needs looking at. Nothing is lost: the month
    still shows its own in-progress figure in the row inside.

    For a past year that is simply the position at the end of it, so only the
    current block is ever adjusted, and it is adjusted by subtracting the
    current month's own movement rather than by a second query.
    """
    today = today or timezone.localdate()
    this_month = month_of(today)
    blocks = []
    for row in month_rows(today=today):
        year = row['month'].year
        if not blocks or blocks[-1]['year'] != year:
            blocks.append({'year': year, 'months': [],
                           'rent': ZERO, 'paid': ZERO, 'running': ZERO})
        block = blocks[-1]
        block['months'].append(row)
        block['rent'] += row['rent']
        block['paid'] += row['paid']
        block['running'] = row['running']       # the latest month reached so far
        if row['month'] == this_month:
            # Back out the month in progress: what is left is the position on
            # everything that has actually finished.
            block['running'] -= row['paid'] - row['rent']

    for block in blocks:
        block['is_current'] = block['year'] == today.year
        block['months'].reverse()               # newest month first, like every list here
    blocks.reverse()
    return blocks


#: How many rows "Recently added" draws. It answers "what did I just do?", so
#: it is a short list by construction — anything older is found by opening the
#: month, which the row's own mark makes obvious once you are there.
RECENT_ROWS = 40


def backdating(deposit, today=None):
    """
    How far back a row was filed WHEN IT WAS KEYED — `''`, `'late'` or `'closed'`.

    ⚠ THE ONE RULE BOTH VIEWS READ, so the month log and the Recently-added
    list can never disagree about the same row. Every deposit stores two dates
    and nothing used to show them: `date` is when the money moved, `created_at`
    is when somebody typed it.

    The two tiers are not decoration — they are different amounts of harm:

      * `late`   — dated back inside its OWN month. The month's total is
                   unchanged and no closed period moved; only the day is off.
      * `closed` — filed into an EARLIER month. That month's position, and
                   every month since, has moved on a page nobody re-reads.

    A row keyed on the day it is dated, or keyed the next morning for
    yesterday's handover, is the ordinary case and gets nothing at all —
    marking that would make the mark meaningless by the second row.
    """
    if deposit.created_at is None:
        return ''
    keyed = timezone.localtime(deposit.created_at).date()
    if month_of(keyed) > month_of(deposit.date):
        return 'closed'
    if keyed > deposit.date:
        return 'late'
    return ''


def recently_added(limit=RECENT_ROWS):
    """
    Deposits by the day they were KEYED, newest first, across every month.

    ⚠ THE ANSWER TO "I KNOW I DID IT, BUT I CANNOT FIND WHERE." The row mark
    is only visible once the right month is open, so a mis-dated entry stayed
    findable only by hunting month by month — the owner found one by eye and
    only because the demo data was uniform enough for it to stand out. Ordered
    by `created_at`, this puts whatever was just done at the top whatever month
    it was filed under.

    Ordered by `-created_at` explicitly: the model's own ordering is
    `-date, -created_at`, which is the money order and the exact opposite of
    what this list is for.
    """
    return list(RentDeposit.objects.select_related('recorded_by')
                .order_by('-created_at', '-id')[:limit])


def deposit_days(start, end):
    """
    `{'YYYY-MM-DD': n}` over a date range — how many deposits each day has.

    ⚠ THE COLLECTOR COMES ONCE A DAY, so a second entry on one date is the
    shape a double-key takes here. There is no name to key on the way the
    Cashbook has, and the amount is the wrong key: the same handover keyed
    twice by two people is often typed slightly differently.

    A second deposit in a day is not WRONG — a morning and an evening handover
    happen — which is why the page asks rather than refuses, and why the day
    total in the log exists to catch what slips through either way.

    ⚠ IT TAKES A RANGE, NOT THE MONTH BEING VIEWED, and that was a real bug:
    the log can be showing May while the form's date box still defaults to
    TODAY, so counts for the viewed month would find nothing for the date
    actually about to be submitted — the check silently doing nothing on
    exactly the page state where somebody is least sure what they are looking
    at. The range is the whole window Office can pick, floor to today. An owner
    reaching further back gets no count and the back-date question instead,
    which fires first and is the bigger fact anyway.
    """
    counts = {}
    for row in RentDeposit.objects.filter(
            date__gte=start, date__lte=end).values_list('date', flat=True):
        key = row.isoformat()
        counts[key] = counts.get(key, 0) + 1
    return counts


def deposits_in(month):
    """Every deposit filed under one month, newest first.

    Deliberately NOT paginated: a month holds about sixty rows at the very
    most, however long the business runs, so the natural bound is the month
    itself. A pager over twenty years of deposits would be 130 pages deep and
    the only way to reach March 2019 would be to walk them.
    """
    return list(RentDeposit.objects.filter(
        date__gte=month, date__lt=shift_month(month, 1)
    ).select_related('recorded_by'))
