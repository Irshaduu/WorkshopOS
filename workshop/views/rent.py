"""
Deposit & Rent — the daily cash that pays for the premises.

The arithmetic is ALL in `workshop/rent.py` and none of it is here: this module
resolves the request, calls that module, and renders. Same split
`analysis_views` keeps from `analysis_engine`, and for the same reason — the
pace calculation is the whole feature and it has to be testable without a
request.

WHO DOES WHAT:

  * **Recording a deposit is Office**, because the office is who hands the
    collector the cash and keys it off his book afterwards. **Correcting or
    removing one is Office within 24 hours of keying it**, and an owner's
    after that — `delete_window`, the rule every money section follows.
  * **Setting the rent is Owner-only.** It is a business term, it decides what
    every figure on the page is measured against, and a backdated rate
    reprices months that have already been read.
  * **Floor sees none of it** — there is no drawer entry and every view here is
    gated at Office or above.

⚠ WHAT IS KEPT AND WHAT IS ANNOUNCED IS THE CASHBOOK'S RULE (2026-09-24, the
owners' call). An edit or delete inside Office's 24 hours is neither kept nor
announced — it gives Office no power the add did not, since they could have
typed any figure in the first place. Only what ONLY AN OWNER can do — an edit
or delete past the 24 hours, or a date moved past the three-day limit — is kept
in Change History and reaches the other owner's phone. Back-dating is never
quiet: on the add, and on an edit that moves a date earlier, it reaches the
bell inside the three days and the phone past them.
"""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .. import rent as rent_calc
from ..decorators import is_owner, office_required, owner_required
from ..delete_window import is_past_window, refusal
from ..models import DeletionLog, EditLog, RentDeposit, RentRate
from ..money import fit_text, parse_money
from ..money_dates import backdate_floor, is_future, posted_date, too_far_back
from ..notifications import notify, notify_dated_back


def _focus_month(raw, today, starts):
    """Which month the deposit log is showing — 'YYYY-MM' from the URL.

    Anything unreadable, a month past today, or one before the log can start
    falls back to the current one: the only way to reach any of them is a
    hand-edited URL, and an empty list under a heading naming a month reads as
    "nothing was deposited", which would be a lie. Same fallback the
    dashboard's crew filter and the Estimates list give an unrecognised value.
    """
    this_month = rent_calc.month_of(today)
    raw = (raw or '').strip()
    if len(raw) == 7:
        try:
            chosen = date(int(raw[:4]), int(raw[5:]), 1)
        except ValueError:
            return this_month
        return chosen if starts <= chosen <= this_month else this_month
    return this_month


@office_required
def rent_home(request):
    """The section: what to pay today, one month's deposits, and the history."""
    today = timezone.localdate()   # IST-aware — never date.today()
    this_month = rent_calc.month_of(today)
    starts = rent_calc.log_starts(today=today)
    focus = _focus_month(request.GET.get('month'), today, starts)
    rows = rent_calc.deposits_in(focus)

    # WHAT THIS VIEWER MAY STILL CHANGE, decided once here rather than per row
    # in the template. A door somebody can see and cannot open is worse than no
    # door — the rule the frozen-advance menu follows — so a locked row still
    # shows its menu and says why, and the view refuses again either way.
    # `logged` is the same test, read the other way: an edit or delete past
    # the 24 hours is kept in Change History, so only then does the delete ask
    # for a reason (a reason box whose value goes nowhere is a field dropped).
    viewer_is_owner = is_owner(request.user)
    for row in rows:
        row.logged = is_past_window(row.created_at)
        row.locked = row.logged and not viewer_is_owner

    return render(request, 'workshop/rent/rent_home.html', {
        'state': rent_calc.position(today=today),
        'focus': focus,
        'focus_is_current': focus == this_month,
        'rows': rows,
        'focus_total': sum((r.amount for r in rows), Decimal('0')),
        'years': rent_calc.year_blocks(today=today),
        'rates': rent_calc.rates()[::-1],
        'is_owner': viewer_is_owner,
        # PRESENTATION ONLY — `too_far_back()` in the view is the control. An
        # owner gets no floor at all, which is what lets a go-live opening
        # position be dated before the ledger starts.
        'min_date_iso': '' if viewer_is_owner else backdate_floor(today).isoformat(),
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
        'back_qs': '' if focus == this_month else f"?month={focus:%Y-%m}",
    })


def _back(request):
    """Return to the page the form was posted from, never to a bare /rent/."""
    keep = request.POST.get('back', '').strip()
    return redirect(f"/rent/{keep}" if keep.startswith('?') else 'rent_home')


def _month_url(day):
    return reverse('rent_home') + f"?month={day:%Y-%m}"


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

    today = timezone.localdate()
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

    # Filed into a month that has already finished — that month's position,
    # and every month's since, moves. The one fact about a back-dated deposit
    # worth more than its date.
    earlier_month = (when.year, when.month) < (today.year, today.month)

    with transaction.atomic():
        deposit = RentDeposit.objects.create(
            amount=amount, date=when, note=note or None, recorded_by=request.user)
        # Inside the transaction, so a rolled-back write leaves no announcement
        # behind. The bell inside Office's three days, the other owner's phone
        # past them — `notify_dated_back` decides, from the date alone.
        notify_dated_back(
            f"₹{amount:,.0f} rent deposit filed under {when:%d %b %Y}",
            when,
            detail=("the position of every month since has moved"
                    if earlier_month else "Rent deposit"),
            actor=request.user,
            url=_month_url(when),
            object_type='RentDeposit',
            object_id=deposit.pk,
        )
    # THE MESSAGE NAMES THE MONTH when the entry did not land in this one. The
    # alert excludes the actor, so without this the person who just back-dated
    # something is told only "Recorded ₹5,000 deposited" — the one confirmation
    # that says nothing about the one thing that was unusual about it.
    if earlier_month:
        messages.success(
            request,
            f"Recorded ₹{amount:,.0f} deposited, filed under {when:%B %Y}. "
            f"The position of every month since has moved.")
    else:
        messages.success(request, f"Recorded ₹{amount:,.0f} deposited.")
    return _back(request)


@office_required
def rent_deposit_edit(request, pk):
    """Correct a deposit's amount, date or note.

    ⚠ THIS REVERSES "DELIBERATELY NO EDIT ON A DEPOSIT" (the owners' call,
    2026-09-24). That rule's first reason was that every correction should
    land in the history rather than silently overwrite what was there — and
    since Edit History exists, an edit that matters does exactly that.
    """
    if request.method != 'POST':
        return redirect('rent_home')

    entry = get_object_or_404(RentDeposit, pk=pk)

    # ONE WINDOW FOR BOTH DOORS. An edit can do everything a delete can —
    # retype ₹2,000 as ₹200 — so it answers the rule the delete beside it
    # does: Office within 24 hours of keying the row, an owner after that.
    # Measured on `created_at`, because back-dating is normal here.
    blocked = refusal(request.user, entry.created_at,
                      f"This ₹{entry.amount:,.0f} deposit", action='change')
    if blocked:
        messages.error(request, blocked)
        return _back(request)

    amount = parse_money(request.POST.get('amount', ''), RentDeposit, 'amount')
    if amount is None or amount <= 0:
        messages.error(request, "Enter a valid amount.")
        return _back(request)

    # A payload with no date keeps the one the row has, rather than falling
    # back to today and moving the money on a correction that never asked to.
    raw_date = (request.POST.get('date') or '').strip()
    when = posted_date(raw_date) if raw_date else entry.date
    if is_future(when):
        messages.error(request, "A deposit can't be dated in the future.")
        return _back(request)
    # ⚠ ONLY A DATE THAT MOVES IS HELD TO THE LIMIT. The floor moves every
    # night, so a row Office keyed yesterday for three days before that is
    # past it by this morning — and asking about a date nobody touched would
    # refuse Office a correction to the amount, inside their own 24 hours.
    if when != entry.date:
        blocked = too_far_back(when, request.user, "A deposit")
        if blocked:
            messages.error(request, blocked)
            return _back(request)

    # READ BEFORE ANYTHING IS WRITTEN — the two money fields are about to be
    # overwritten on this same instance, so "what it was" has to be taken now.
    was_amount, was_date = entry.amount, entry.date
    entry.amount = amount
    entry.date = when
    # Only honoured when the form posts the key at all, so a payload without it
    # keeps the note rather than silently clearing it. Blank stores NULL.
    if 'note' in request.POST:
        entry.note = fit_text(request.POST.get('note', '').strip(),
                              RentDeposit, 'note') or None

    said = []
    if was_amount != entry.amount:
        said.append(f"was ₹{was_amount:,.0f}")
    if was_date != entry.date:
        said.append(f"was {was_date:%d %b %Y}")

    with transaction.atomic():
        entry.save()
        # KEPT AND ANNOUNCED ONLY PAST OFFICE'S LIMITS — `only_past_limits`,
        # the Cashbook's rule — and then it reaches the other owner's phone.
        # The amount and the day it lands on are the whole of the money here;
        # a corrected note is never history.
        kept = EditLog.record(
            EditLog.ENTITY_RENT_DEPOSIT, entry,
            [EditLog.change('Amount', was_amount, entry.amount),
             EditLog.change('Date', was_date, entry.date, kind=EditLog.KIND_DATE)],
            label=f"Rent deposit · {entry.date:%d %b %Y}",
            user=request.user,
            stamp=entry.created_at,
            moved_to=entry.date if was_date != entry.date else None,
            headline=f"Rent deposit · ₹{entry.amount:,.0f} edited",
            detail=' · '.join(said),
            url=_month_url(entry.date),
            object_type='RentDeposit',
            only_past_limits=True,
        )
        # ⚠ BACK-DATING IS NEVER QUIET, on an edit as on an add: moving a
        # deposit to an EARLIER day inside the limits still reaches the bell,
        # exactly as keying it there would have — or the quiet rule would be a
        # way round the add form's alert.
        if kept is None and entry.date < was_date:
            notify_dated_back(
                f"₹{entry.amount:,.0f} rent deposit moved to {entry.date:%d %b %Y}",
                entry.date,
                detail=f"Rent deposit · was dated {was_date:%d %b %Y}",
                actor=request.user,
                url=_month_url(entry.date),
                object_type='RentDeposit',
                object_id=entry.pk,
            )

    # A deposit moved OUT of the month on screen disappears from it, so the
    # message says where it went rather than leaving somebody to wonder.
    if (entry.date.year, entry.date.month) != (was_date.year, was_date.month):
        messages.success(request, f"Deposit updated — moved to {entry.date:%B %Y}.")
    else:
        messages.success(request, "Deposit updated.")
    return _back(request)


@office_required
def rent_deposit_delete(request, pk):
    """Permanently delete a deposit.

    ⚠ LOGGED ONLY PAST OFFICE'S 24 HOURS — the Cashbook's rule (2026-09-24,
    the owners' call). A delete inside the window is the same as typing the
    row right the first time, which Office could have done with any figure;
    the control that matters is after it, and that delete is written to
    Change History and reaches the other owner's phone.
    """
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
    # Same test the refusal above uses, so "Office may delete it" and "it is
    # not logged" are one line, never two.
    logged = is_past_window(entry.created_at)
    with transaction.atomic():
        if logged:
            # The label LEADS WITH THE SUBJECT, so the alert reads "Rent deposit
            # · ₹2,000 deleted" rather than a figure with the verb left to a glyph.
            DeletionLog.record(
                DeletionLog.ENTITY_RENT_DEPOSIT, entry,
                user=request.user, reason=reason, amount=entry.amount,
                label=f"Rent deposit · ₹{entry.amount:,.0f} of {entry.date:%d %b %Y}",
            )
        entry.delete()
    messages.success(request, "Deposit deleted (logged to Change History)."
                     if logged else "Deposit deleted.")
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
    # what every past month cost and leave no trace. `DeletionLog.record()` is
    # the choke point: one call gives the audit row, the reason, the snapshot
    # AND `RECORD_DELETED` at CRITICAL to the other owner, so no separate
    # `notify()` belongs here.
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
