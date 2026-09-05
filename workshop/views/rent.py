"""
Deposit & Rent — the daily cash that pays for the premises.

The arithmetic is ALL in `workshop/rent.py` and none of it is here: this module
resolves the request, calls that module, and renders. Same split
`analysis_views` keeps from `analysis_engine`, and for the same reason — the
pace calculation is the whole feature and it has to be testable without a
request.

⚠ **NOTHING HERE REACHES `analysis_engine`, YET.** This is the section on its
own: a deposit log and today's figure, which is what the owners asked for. Rent
still reaches the Profit page the way it always has, as a Cashbook category, so
switching this on changes no reported figure by a rupee. Moving rent out of the
Cashbook and into an expense line of its own is a SEPARATE change with real
reach — the equation, the earnings card, All Time, the trend chart and the
historical cashbook rows all have to move together, and doing it in the same
edit as this would put a working tool behind a risky one.

WHO DOES WHAT:

  * **Recording a deposit is Office**, because the office is who hands the
    collector the cash and keys it off his book afterwards.
  * **Setting the rent is Owner-only.** It is a business term, it decides what
    every figure on the page is measured against, and a backdated rate
    reprices months that have already been read.
  * **Floor sees none of it** — there is no drawer entry and every view here is
    gated at Office or above.
"""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .. import rent as rent_calc
from ..decorators import is_owner, office_required, owner_required
from ..delete_window import is_past_window, refusal
from ..models import DeletionLog, RentDeposit, RentRate
from ..money import fit_text, parse_money
from ..money_dates import (backdate_floor, is_future, is_too_far_back,
                           posted_date, too_far_back)
from ..notifications import notify


def _group_by_day(rows):
    """The page's deposits as day blocks, each with its own total.

    ⚠ THE DAY TOTAL IS THE POINT, not decoration. The collector's book is the
    truth and this is a copy of it, so the realistic failure here is the same
    handover being keyed TWICE — which silently lowers today's figure and is
    invisible until month end. Two rows under one date, adding to a total that
    does not match the book, is what makes that findable. It is not blocked:
    two genuine handovers in one day are perfectly normal.

    The rows arrive ordered `-date`, so equal dates are already contiguous and
    this is one pass with no sorting.
    """
    blocks = []
    for row in rows:
        if not blocks or blocks[-1]['day'] != row.date:
            blocks.append({'day': row.date, 'rows': [], 'total': 0})
        blocks[-1]['rows'].append(row)
        blocks[-1]['total'] += row.amount
    return blocks


def _focus_month(raw, today):
    """Which month the deposit log is showing — 'YYYY-MM' from the URL.

    Anything unreadable, or a month past today, falls back to the current one:
    the only way to reach either is a hand-edited URL, and an empty list under
    a heading naming a month reads as "nothing was deposited", which would be a
    lie. Same fallback the dashboard's crew filter and the Estimates list give
    an unrecognised value.
    """
    this_month = rent_calc.month_of(today)
    raw = (raw or '').strip()
    if len(raw) == 7:
        try:
            chosen = date(int(raw[:4]), int(raw[5:]), 1)
        except ValueError:
            return this_month
        return chosen if chosen <= this_month else this_month
    return this_month


@office_required
def rent_home(request):
    """The section: today's figure, one month's deposits, and the year history."""
    today = timezone.localdate()   # IST-aware — never date.today()
    state = rent_calc.position(today=today)
    focus = _focus_month(request.GET.get('month'), today)

    # TWO WAYS TO READ THE SAME LOG, and the second exists because the first
    # cannot answer "I know I did it, but where?". By MONEY DATE it is one
    # month at a time (bounded at about sixty rows however long the business
    # runs, so there is nothing to page). By KEYSTROKE it is whatever was done
    # most recently, across every month — which is the only view that finds a
    # row filed into a month nobody would think to open.
    recent = request.GET.get('added') == 'recent'
    rows = rent_calc.recently_added() if recent else rent_calc.deposits_in(focus)

    # Whether each row may still be deleted by THIS user, decided once here
    # rather than per row in the template. A door somebody can see and cannot
    # open is worse than no door — the rule the frozen-advance menu follows —
    # so the item is annotated rather than hidden, and the view refuses again.
    #
    # ⚠ AND WHETHER IT WAS KEYED LATE, WHICH IS THE ONLY PERMANENT TRACE THERE
    # IS. A notification is a FEED — read rows are swept after 14 days — and
    # `notify()` excludes the actor, so the person who back-dated an entry is
    # the one person it never reaches. Both dates have been on the row since
    # the first migration; nothing showed them. `date` is when the money moved,
    # `created_at` is when somebody typed it, and a row whose two dates fall in
    # different MONTHS is money filed into a month that had already closed.
    #
    # The month is the threshold rather than the day, for the reason the
    # backdate floor uses one: keying yesterday's handover this morning is the
    # ordinary case and marking it would make the mark meaningless by the
    # second row.
    # ⚠ `is_owner` ONCE, THE AGE RULE PER ROW. `refusal()` calls `is_owner`,
    # which is `user.groups.filter(...)` — a fresh query every time, uncached —
    # so asking it per row put one extra query on every deposit in the list.
    # Sixty rows, sixty queries, found by a test asserting the cost does not
    # grow with the data.
    viewer_is_owner = is_owner(request.user)
    for row in rows:
        row.locked = not viewer_is_owner and is_past_window(row.created_at)
        # ONE rule for both views — `rent.backdating()` — so the month log and
        # the Recently-added list can never mark the same row differently.
        row.tier = rent_calc.backdating(row)
        row.added_late = row.tier == 'closed'
        row.added_on = timezone.localtime(row.created_at).date() if row.created_at else None

    return render(request, 'workshop/rent/rent_home.html', {
        'state': state,
        'recent': recent,
        # How many of the rows on screen were filed backwards, so the heading
        # can say it without the reader counting chips.
        'off_count': sum(1 for r in rows if r.tier),
        'focus': focus,
        'focus_is_current': focus == rent_calc.month_of(today),
        'focus_total': sum((r.amount for r in rows), Decimal('0')),
        # ⚠ NO DAY GROUPING IN RECENT MODE. The day header carries a day TOTAL,
        # and that total is only true when the block holds every deposit of
        # that day. Ordered by keystroke this list is a slice — two rows of one
        # day can be pages apart — so a header here would print a "day total"
        # that is really "the part of that day I happen to be showing". Each
        # row stands alone instead, with its own money date.
        'days': ([{'day': r.date, 'rows': [r], 'total': r.amount} for r in rows]
                 if recent else _group_by_day(rows)),
        'years': rent_calc.year_blocks(today=today),
        'rates': rent_calc.rates()[::-1],
        'is_owner': is_owner(request.user),
        # PRESENTATION ONLY — `too_far_back()` in the view is the control. An
        # owner gets no floor at all, which is what lets a go-live opening
        # position be dated before the ledger starts.
        'min_date_iso': '' if is_owner(request.user) else backdate_floor(today).isoformat(),
        # Always handed over, owner or not: the browser asks before the button
        # on a date past it, which is the only guard an owner meets at all.
        'floor_iso': backdate_floor(today).isoformat(),
        # ⚠ HOW MANY DEPOSITS EACH DAY ALREADY HAS, for the repeat check. The
        # collector comes ONCE a day, so a second entry on one date is the
        # shape a double-key takes here — there is no name to key on the way
        # the Cashbook has.
        #
        # ⚠ THE RANGE IS THE BACK-DATE WINDOW, NOT THE MONTH BEING VIEWED. The
        # log can be showing May while the form's date box still defaults to
        # TODAY, so the viewed month's counts would find nothing for the date
        # actually about to be submitted. Floor to today covers everything
        # Office can choose; it costs one query, which is worth saying plainly
        # rather than claiming it is free.
        'day_counts': rent_calc.deposit_days(backdate_floor(today), today),
        'today_iso': today.isoformat(),
        'this_month_iso': f"{today:%Y-%m}",
        'back_qs': '' if focus == rent_calc.month_of(today) else f"?month={focus:%Y-%m}",
    })


def _back(request):
    """Return to the page the form was posted from, never to a bare /rent/."""
    keep = request.POST.get('back', '').strip()
    return redirect(f"/rent/{keep}" if keep.startswith('?') else 'rent_home')


@office_required
def rent_deposit_add(request):
    """Record a handover of cash to the collector."""
    if request.method != 'POST':
        return redirect('rent_home')

    # `<= 0` AS WELL AS None, and the second half is the one that bites:
    # `parse_money` refuses a zero BEFORE quantising, so `0.004` passes every
    # check inside it and comes back as `0.00` — which this table's
    # CheckConstraint turns into an IntegrityError, and therefore a 500 rather
    # than a message. The browser's own `parseFloat(x) <= 0` passes it too.
    amount = parse_money(request.POST.get('amount', ''), RentDeposit, 'amount')
    if amount is None or amount <= 0:
        messages.error(request, "Enter a valid amount.")
        return _back(request)

    when = posted_date(request.POST.get('date'))
    if is_future(when):
        messages.error(request, "A deposit can't be dated in the future.")
        return _back(request)

    # ⚠ HOW FAR BACK, AND ONE CONSTANT DECIDES BOTH HALVES. A deposit dated
    # into a closed month rewrites the running position of every month since,
    # on rows nobody scrolls back to — the quiet direction, unlike a future
    # date, which is caught the moment somebody reads the period it lands in.
    # Office is REFUSED past the floor; an owner is not, because a go-live
    # opening entry is dated before the ledger even starts. What covers the
    # owner is that it cannot happen silently — the same `is_too_far_back`
    # answer that refuses Office is what raises the alert below, so the rule
    # enforced and the rule announced can never drift apart.
    blocked = too_far_back(when, request.user, "A deposit")
    if blocked:
        messages.error(request, blocked)
        return _back(request)

    note = fit_text((request.POST.get('note') or '').strip(), RentDeposit, 'note')

    with transaction.atomic():
        RentDeposit.objects.create(
            amount=amount, date=when, note=note or None, recorded_by=request.user)
        if is_too_far_back(when):
            # Inside the transaction, so a rolled-back write leaves no
            # announcement behind. The actor is excluded by `notify()`, so this
            # reaches the OTHER owner — which is the whole point of raising it.
            notify(
                'RENT_BACKDATED',
                f"₹{amount:,.0f} deposit filed under {when:%B %Y}",
                detail=f"Recorded by {request.user.username} · "
                       f"the position of every month since has moved",
                actor=request.user,
                url='/rent/',
            )
    # THE MESSAGE NAMES THE MONTH when the entry did not land in this one. The
    # alert excludes the actor, so without this the person who just back-dated
    # something is told only "Recorded ₹5,000 deposited" — the one confirmation
    # that says nothing about the one thing that was unusual about it.
    if is_too_far_back(when):
        messages.success(
            request,
            f"Recorded ₹{amount:,.0f} deposited, filed under {when:%B %Y}. "
            f"The position of every month since has moved.")
    else:
        messages.success(request, f"Recorded ₹{amount:,.0f} deposited.")
    return _back(request)


@office_required
def rent_deposit_delete(request, pk):
    """Permanently delete a deposit, logged to Deletion History."""
    if request.method != 'POST':
        return redirect('rent_home')

    entry = get_object_or_404(RentDeposit, pk=pk)

    # OFFICE CORRECTS A RECENT MISTAKE; AN OWNER TAKES ANYTHING OLDER. The
    # refusal names the row, its age and who to ask — measured on `created_at`,
    # because back-dating is normal here and the question is how long the row
    # has been sitting in the books, not which day the cash moved.
    blocked = refusal(request.user, entry.created_at,
                      f"This ₹{entry.amount:,.0f} deposit")
    if blocked:
        messages.error(request, blocked)
        return _back(request)

    reason = request.POST.get('reason', '').strip()
    with transaction.atomic():
        # The label LEADS WITH THE SUBJECT, so the alert reads "Rent deposit ·
        # ₹2,000 deleted" rather than a figure with the verb left to a glyph.
        DeletionLog.record(
            DeletionLog.ENTITY_RENT_DEPOSIT, entry,
            user=request.user, reason=reason, amount=entry.amount,
            label=f"Rent deposit · ₹{entry.amount:,.0f} of {entry.date:%d %b %Y}",
        )
        entry.delete()
    messages.success(request, "Deposit deleted (logged to Deletion History).")
    return _back(request)


@owner_required
def rent_rate_set(request):
    """Set the rent from a stated month onward — Owner only."""
    if request.method != 'POST':
        return redirect('rent_home')

    amount = parse_money(request.POST.get('amount', ''), RentRate, 'amount')
    if amount is None or amount <= 0:
        messages.error(request, "Enter a valid monthly rent.")
        return _back(request)

    # An `<input type="month">` posts 'YYYY-MM'. Parsed here rather than handed
    # to a DateField: a crafted or empty value reaching the ORM as a string is
    # a `DataError` on PostgreSQL — a 500 — which is the defect
    # `SupplierRestockBill.bill_date` carried until it was found by audit.
    raw = (request.POST.get('month') or '').strip()
    month = posted_date(f"{raw}-01" if len(raw) == 7 else raw)
    month = month.replace(day=1)

    note = fit_text((request.POST.get('note') or '').strip(), RentRate, 'note')

    # `update_or_create` on the month: one rent per month, and restating a
    # month corrects it rather than adding a second answer for it. The rate is
    # deliberately NOT refused for being backdated — a hike agreed late and
    # applied from an earlier month is ordinary, and refusing it would leave
    # the books wrong for good. It is Owner-only and it says what it did.
    with transaction.atomic():
        RentRate.objects.update_or_create(
            effective_from=month,
            defaults={'amount': amount, 'note': note or None, 'set_by': request.user},
        )
        # ⚠ EVERY rate change is announced, not only a backdated one. What the
        # premises cost is the figure every number in the section is measured
        # against, and the other owner wants to know it moved whether or not it
        # reached back. The BACKDATING rides in `detail`, which is what that
        # field is for — the context, read second, while the body stays a
        # complete statement on its own.
        reach = rent_calc.month_of(timezone.localdate())
        detail = f"Set by {request.user.username}"
        if month < reach:
            months = rent_calc.months_between(month, reach)
            detail += f" · backdated, {months} month{'' if months == 1 else 's'} re-priced"
        notify(
            'RENT_RATE_SET',
            f"Rent set to ₹{amount:,.0f} a month from {month:%B %Y}",
            detail=detail, actor=request.user, url='/rent/',
        )
    messages.success(
        request, f"Rent set to ₹{amount:,.0f} a month from {month:%B %Y}.")
    return _back(request)


@owner_required
def rent_rate_delete(request, pk):
    """Remove a rent rate — Owner only.

    The last one standing cannot be removed: with no rate at all the section
    has nothing to measure against, and every figure on the page would quietly
    become zero rather than saying anything.
    """
    if request.method != 'POST':
        return redirect('rent_home')

    rate = get_object_or_404(RentRate, pk=pk)
    if RentRate.objects.count() <= 1:
        messages.error(
            request, "This is the only rent on file — change it instead of removing it.")
        return _back(request)

    # ⚠ LOGGED LIKE EVERY OTHER PERMANENT DELETE. This wrote nothing at all for
    # one revision, which made it the one act in the section that could rewrite
    # what every past month cost and leave no trace — worse than deleting a
    # deposit, which was logged from the start. `DeletionLog.record()` is the
    # choke point: one call gives the audit row, the reason, the snapshot AND
    # `RECORD_DELETED` at CRITICAL to the other owner, so no separate `notify()`
    # belongs here.
    label = f"{rate.effective_from:%B %Y}"
    reason = request.POST.get('reason', '').strip()
    with transaction.atomic():
        DeletionLog.record(
            DeletionLog.ENTITY_RENT_RATE, rate,
            user=request.user, reason=reason, amount=rate.amount,
            label=f"Rent of ₹{rate.amount:,.0f} from {label}",
        )
        rate.delete()
    messages.success(request, f"Removed the rent change dated {label}.")
    return _back(request)
