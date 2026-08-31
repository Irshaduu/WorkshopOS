"""
Owner Withdrawals — cash the owners take out of the business for themselves.

⚠ THE ONE RULE: A WITHDRAWAL IS NOT AN EXPENSE, AND NOTHING HERE MAY EVER
REACH `build_profit_report`. Profit is what is *available* to take; taking it
cannot reduce it. See `OwnerWithdrawal`'s own docstring for what goes wrong if
it does — the error compounds, because the next distribution is decided from a
profit figure the last distribution shrank.

It IS cash out of the drawer, so it appears in exactly one figure:
`cash_position()`'s money-out list, dated by `date`. Two lines in
`analysis_engine`, and that is the whole footprint on the money math.

THREE THINGS ABOUT THE SHAPE OF THE PAGE:

  * **One page, no per-owner drill-down.** With two owners the comparison IS
    the question — "what have we each taken" — and a page per owner answers
    half of it at a time. Tapping an owner card narrows the history instead,
    which is the same gesture with the other half still on screen.

  * **The two figures are never netted.** Both totals are printed and the
    difference is not, because what a gap MEANS depends on the partnership
    split and this system does not hold one. Same reasoning as "what we owe
    and what we hold sit together and are never netted" on the Profit page:
    print both honestly, let the owner do the reading.

  * **The Profit page's date vocabulary, not the day-to-day lists'.** This is
    owner money, taken a handful of times a month — Today and This Week would
    return an empty page nearly every time, which reads as a broken screen
    rather than a quiet period. `engine.resolve_period` is called directly, so
    there is one implementation of the window and All Time comes free.

DELIBERATELY NO EDIT. Every other ledger has one because a row keyed on the
wrong day would otherwise be stuck in the wrong month for good — but that
argument assumes a role that cannot delete it. This page is Owner-only end to
end, delete is always available, and re-adding takes one line of the form. One
fewer surface, and every correction lands in Deletion History rather than
silently overwriting what was there.
"""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import urlencode

from .. import analysis_engine as engine
from ..decorators import owner_accounts, owner_required
from ..models import DeletionLog, OwnerWithdrawal
from ..money import fit_text, parse_money
from ..money_dates import is_future, posted_date

#: One page of history. 45 matches every other list view in the app.
PAGE_SIZE = 45

#: ONE COLOUR PER OWNER, so a card, its filter chip and its rows in the list
#: are recognisably the same person without reading a name. Assigned by
#: POSITION in `owner_accounts()`, which is ordered by the displayed name — so
#: the colours are stable between renders, and a third owner gets one for free
#: rather than needing the palette extended.
#:
#: ⚠ Deliberately no red and no green. Those two are spoken for app-wide as
#: the DIRECTION of money, and this page prints a red amount on every row; an
#: owner who happened to be red would read as the urgent one.
#:
#: DARK BLUE AND DARK VIOLET, on the owner's instruction (2026-08-31), and the
#: darkness is load-bearing rather than taste now: the card is FILLED with this
#: colour and carries white type, so the value has to clear 4.5:1 against white
#: after the card's own 12% white overlay has lightened it. Measured at that
#: lightest corner: #1e3a8a reads 7.4:1 and #5b21b6 6.8:1.
#:
#: ⚠ THE BLUE IS NAVY RATHER THAN THE APP'S OWN #2563eb, deliberately. A card
#: filled with the primary blue reads as a control — every button and active
#: pill in the system is that colour — so the one object on this page that is
#: purely a figure would have looked like the one thing to press.
#:
#: A third and fourth owner still get a colour for free, held to the same three
#: rules: dark enough for white type, not red, not green. (#9d174d was tried
#: for the fourth and the red guard in the tests caught it.)
OWNER_TINTS = ('#1e3a8a', '#5b21b6', '#0e7490', '#78350f')


def tint_for(index):
    return OWNER_TINTS[index % len(OWNER_TINTS)]

# Summing DecimalField(10,2) rows needs somewhere wider to land than the column
# itself, or a long period overflows the declared precision on PostgreSQL.
_MONEY = DecimalField(max_digits=20, decimal_places=2)
_ZERO = Value(Decimal('0'), output_field=_MONEY)


def display_name(user):
    """What an owner is called on screen.

    `get_full_name()` when the account carries one, else the username — which
    is what these accounts actually have, since `manage_create_user` collects
    only a username and the owner rows are seeded by `sync_owner_identity`.
    One helper, so a card, the picker and a history row cannot spell the same
    person three ways.
    """
    return (user.get_full_name() or user.username).strip() or user.username


@owner_required
def withdrawal_home(request):
    """The section: what each owner took, the form, and the history."""
    start, end, range_key, range_label = engine.resolve_period(
        request.GET.get('range'), request.GET.get('start'), request.GET.get('end'),
    )

    in_window = OwnerWithdrawal.objects.filter(date__range=(start, end))

    # Per owner, in one round trip. Owners with nothing in the window are still
    # drawn — at ₹0, which is honest here in a way it is not on the fleet
    # table: an owner exists for the whole period, so "took nothing" is a fact
    # about them rather than a claim about a period they were not in.
    tallied = {
        row['owner']: row for row in
        in_window.values('owner').annotate(
            total=Coalesce(Sum('amount', output_field=_MONEY), _ZERO),
            n=Count('id'),
        )
    }

    # EVERY OWNER WITH MONEY IN THE WINDOW GETS A CARD, INCLUDING ONE WHO IS NO
    # LONGER IN `owner_accounts()`. The headline is the sum of the cards, so an
    # owner that query misses is a row the list prints and the total does not
    # count -- the hero disagreeing with the rows underneath it, silently,
    # which is the one thing a money page may never do. `owner_accounts()`
    # filters `is_active`, so deactivating an account is enough to produce it.
    #
    # The extra query fires only when there IS a stray, which is never in the
    # ordinary case. They are appended rather than merged in, so a real owner's
    # position -- and therefore their colour -- cannot move.
    owners = list(owner_accounts())
    current_ids = {o.pk for o in owners}
    strays = [pk for pk in tallied if pk not in current_ids]
    if strays:
        owners += list(User.objects.filter(pk__in=strays)
                       .order_by('first_name', 'username'))
    owner_ids = {o.pk for o in owners}

    who = request.GET.get('who')
    try:
        who = int(who) if who else None
    except (TypeError, ValueError):
        who = None
    # An unknown or non-owner pk shows everybody rather than an empty page: the
    # only way to reach one is a hand-edited URL, and a blank list under a
    # heading naming a period reads as "nothing happened", which would be a lie.
    if who not in owner_ids:
        who = None

    cards = []
    tints = {}
    for index, person in enumerate(owners):
        row = tallied.get(person.pk)
        tints[person.pk] = tint_for(index)
        cards.append({
            'pk': person.pk,
            'name': display_name(person),
            'total': row['total'] if row else Decimal('0'),
            'count': row['n'] if row else 0,
            'active': who == person.pk,
            'tint': tints[person.pk],
            # A stray is listed and counted; it is NOT offered in the picker,
            # because `withdrawal_add` validates against `owner_accounts()` and
            # would refuse it. An option leading to a refusal is a door
            # somebody can see and cannot open.
            'current': person.pk in current_ids,
        })

    # The headline and the "Everyone" chip are summed off the SAME aggregate
    # the cards are built from, so the three can never disagree — and, with the
    # stray pass above, they are the whole window by construction. The
    # Cashbook's own rule: one aggregate behind every figure on the page.
    period_total = sum((c['total'] for c in cards), Decimal('0'))
    period_count = sum(c['count'] for c in cards)

    rows = in_window.select_related('owner')
    if who is not None:
        rows = rows.filter(owner_id=who)
    page_obj = Paginator(rows, PAGE_SIZE).get_page(request.GET.get('page'))
    # One answer to "what is this owner called", the same one the cards and the
    # picker use. The template had its own `get_full_name|default:username`,
    # which is a second implementation free to disagree the moment an account
    # gains a first name.
    listed = list(page_obj.object_list)
    for row in listed:
        row.display = display_name(row.owner)
        row.tint = tints.get(row.owner_id, OWNER_TINTS[0])

    # THE QUERY STRINGS ARE BUILT HERE, not stitched together in the template.
    # `{% with %}` scope ends at `{% endwith %}`, so the obvious template
    # version - a nested with/endwith to append `&who=` - sets a variable that
    # is already out of scope on the next line, silently. `base_qs` also stops
    # the same five parameters being re-assembled in six places (two card
    # links, the clear button, both pagers) where one of them would eventually
    # be written differently.
    base_qs = urlencode({'range': range_key,
                         'start': request.GET.get('start', ''),
                         'end': request.GET.get('end', '')})
    who_qs = base_qs + (f'&who={who}' if who is not None else '')

    today = timezone.localdate()   # IST-aware — never date.today()
    return render(request, 'workshop/withdrawals/withdrawal_home.html', {
        'base_qs': base_qs,
        'who_qs': who_qs,
        # Posted by both forms so an action returns the reader to the period
        # they were reading. Page is left out on purpose: after recording a
        # withdrawal you want the top of the list, not page 3.
        'back_qs': '?' + who_qs,
        'cards': cards,
        # The picker's own list -- see `current` on the card above.
        'owner_choices': [c for c in cards if c['current']],
        'period_total': period_total,
        'period_count': period_count,
        'who': who,
        'who_name': next((c['name'] for c in cards if c['pk'] == who), ''),
        'page_obj': page_obj,
        'withdrawals': listed,
        'range_key': range_key,
        'range_label': range_label,
        'period_choices': engine.PERIOD_CHOICES,
        'custom_start': request.GET.get('start', ''),
        'custom_end': request.GET.get('end', ''),
        'today_iso': today.isoformat(),
    })


def _back(request):
    """Return to the page with its filter intact, never to a bare /withdrawals/.

    A form posts the query string it was rendered under. Without it, recording
    a withdrawal while reading Last Year would answer by silently moving the
    reader to This Month — the period changing under somebody who did not ask
    for that is how a page stops being trusted.
    """
    keep = request.POST.get('back', '').strip()
    return redirect(f"/withdrawals/{keep}" if keep.startswith('?') else 'withdrawal_home')


@owner_required
def withdrawal_add(request):
    """Record cash an owner has taken."""
    if request.method != 'POST':
        return redirect('withdrawal_home')

    # WHICH OWNER IS VALIDATED AGAINST THE OWNER LIST, not merely read. Hiding
    # a name from a <select> is presentation; this is the control. Without it a
    # crafted POST could file a withdrawal against the Floor account, where it
    # would sit on a page that role cannot open, attributed to somebody who
    # never took the money.
    owner = owner_accounts().filter(pk=request.POST.get('owner') or 0).first()
    if owner is None:
        messages.error(request, "Choose which owner took the money.")
        return _back(request)

    # `<= 0` AS WELL AS None, and the second half is not belt-and-braces:
    # `parse_money` refuses a zero or a negative BEFORE quantising, so `0.004`
    # gets through and comes back as `0.00` -- and `OwnerWithdrawal` carries a
    # CheckConstraint that `amount > 0`, so writing it is an IntegrityError and
    # therefore a 500 rather than a message. The browser cannot catch it
    # either: its own guard is `parseFloat(amount) <= 0`, which 0.004 passes.
    amount = parse_money(request.POST.get('amount', ''), OwnerWithdrawal, 'amount')
    if amount is None or amount <= 0:
        messages.error(request, "Enter a valid amount.")
        return _back(request)

    when = posted_date(request.POST.get('date'))
    if is_future(when):
        messages.error(request, "A withdrawal can't be dated in the future.")
        return _back(request)

    method = request.POST.get('payment_method', 'CASH')
    if method not in dict(OwnerWithdrawal.PAYMENT_METHODS):
        method = 'CASH'

    # Blank stores NULL: nobody wrote a note is a different fact from somebody
    # writing nothing. `fit_text` rather than a crash on an oversized one — the
    # SQLite-accepts / Postgres-500s split, on a screen where money moves.
    note = fit_text((request.POST.get('note') or '').strip(), OwnerWithdrawal, 'note')

    OwnerWithdrawal.objects.create(
        owner=owner, amount=amount, payment_method=method,
        note=note or None, date=when, recorded_by=request.user,
    )
    messages.success(
        request, f"Recorded ₹{amount:,.0f} taken by {display_name(owner)}.")
    return _back(request)


@owner_required
def withdrawal_delete(request, pk):
    """Permanently delete a withdrawal, logged to Deletion History."""
    if request.method != 'POST':
        return redirect('withdrawal_home')

    entry = get_object_or_404(OwnerWithdrawal.objects.select_related('owner'), pk=pk)
    reason = request.POST.get('reason', '').strip()

    # No `delete_window` guard here: that rule escalates an OFFICE delete to an
    # owner, and this whole section is already Owner-only, so it could never
    # refuse anybody. Calling it would be a check that reads like a control.
    with transaction.atomic():
        # The label LEADS WITH THE SUBJECT, so `RECORD_DELETED` reads
        # "Sahad · ₹50,000 withdrawal deleted" — a complete statement ending in
        # what happened, rather than a thing with the verb left to a glyph.
        DeletionLog.record(
            DeletionLog.ENTITY_OWNER_WITHDRAWAL, entry,
            user=request.user, reason=reason, amount=entry.amount,
            label=f"{display_name(entry.owner)} · ₹{entry.amount:,.0f} withdrawal",
        )
        entry.delete()
    messages.success(request, "Withdrawal deleted (logged to Deletion History).")
    return _back(request)
