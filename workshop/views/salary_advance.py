from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.db import transaction
from django.db.models import Sum, Q, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from ..decorators import office_required, owner_required
from ..models import Mechanic, SalaryAdvance, SalaryPayment, SalaryPaymentLine, DeletionLog
from ..money import parse_money, fit_text
from .. import delete_window
from ..notifications import notify

# The running month becomes settleable from this day of the month onward.
# Most workshops finish the previous month's attendance/salary math in the
# first few days of the *next* month (July settled ~Aug 1-3), not on the
# last night of July itself — kept as one named constant so tuning it is a
# one-line change, not a hunt through the view.
SETTLE_FROM_DAY = 27


def _month_bounds(year, month):
    """Returns (first_of_month, first_of_next_month) as dates."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _compute_net(salary, leave_days, advance, overtime=None):
    """Net = salary - (salary/30 * leave_days) + overtime - advance, to paise."""
    salary = salary or Decimal('0')
    leave_days = leave_days or Decimal('0')
    advance = advance or Decimal('0')
    overtime = overtime or Decimal('0')
    leave_deduction = (salary / Decimal('30') * leave_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return salary - leave_deduction + overtime - advance


def _prev_month(d):
    """First day of the month before the one `d` falls in."""
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def _days_in_month(year, month):
    start, end = _month_bounds(year, month)
    return (end - start).days


def _unsettleable_staff(month_start, month_end):
    """
    Staff who were handed an advance in this month but would get NO settlement
    line — because they have no salary recorded, or have been deactivated.

    `salary_payment_form` only writes a line for active staff with a salary, and
    `salary_expense()` stops counting a month's advances as "loose" the moment
    the month is settled. So an advance belonging to one of these people used to
    be counted in neither place: settling August dropped their cash out of the
    wage bill entirely, silently and permanently. Both are ordinary states —
    the home page has a whole "needs a salary" list, and staff do leave — so
    this is not an edge case, it is the second month of use.

    Returns a list of (staff, advance_total, reason).
    """
    rows = []
    with_advances = Mechanic.objects.filter(
        salary_advances__date__gte=month_start, salary_advances__date__lt=month_end,
    ).annotate(
        advanced=Coalesce(Sum('salary_advances__amount', filter=Q(
            salary_advances__date__gte=month_start,
            salary_advances__date__lt=month_end,
        )), Decimal('0'), output_field=DecimalField()),
    ).distinct().order_by('name')

    for staff in with_advances:
        if staff.current_salary is None:
            rows.append((staff, staff.advanced, "no salary recorded"))
        elif not staff.is_active:
            rows.append((staff, staff.advanced, "retired — reactivate to settle"))
    return rows


def _latest_settlement():
    """The most recently settled month, or None."""
    return SalaryPayment.objects.order_by('-month').first()


def _is_closed(payment):
    """
    True once a LATER month has been settled. A one-way door.

    Salary is worked out within a week of a month ending and the cash handed
    over immediately, so by the time the next month has been settled the
    previous one is history and must not be one unlock away from changing.

    Read from a stored flag rather than computed as "is this the latest?", and
    that distinction is the whole point. The computed version reopened the
    previous month whenever the newest settlement was deleted — which sounds
    like a tidy reversal and is actually a ratchet that turns both ways: delete
    the newest, the one before becomes editable, delete that, and you can walk
    backwards through the entire history one delete at a time. Observed doing
    exactly that on live data (13 settled months down to 10). `superseded` is
    set when a later month is settled and is never cleared, so stepping back
    over a closed month is impossible however many settlements are removed.

    Keyed to being superseded rather than to a date on the calendar,
    deliberately: a rule like "July closes once August opens for settling"
    closes a month the instant it is settled whenever settlement runs late,
    punishing exactly the month that was hardest to get right.
    """
    return bool(payment and payment.superseded)


def _close_earlier_months(month):
    """Mark every settled month before `month` as closed. Called on settle."""
    return SalaryPayment.objects.filter(
        month__lt=month, superseded=False).update(superseded=True)


def _settled_month_for(advance_date):
    """
    The SalaryPayment covering this date, if that month is already settled.

    An advance cannot be recorded into a month whose salary has been worked out
    and paid: the saved net would silently stop matching the advances on record,
    and the office would hand over a figure that is no longer right.

    Blocking it beats detecting it afterwards. A detector has to nag from
    another screen days later, and by existing it invites people back into
    reopening a closed month — which is exactly the habit worth discouraging.
    Refusing it here lets the message say what to do at the moment of the
    mistake.

    READ BY BOTH DIRECTIONS. A settled month's advances are FROZEN: nothing
    enters and nothing LEAVES. Only the first half used to be enforced, and the
    second is the worse one, because the paid line keeps claiming an
    `advance_used` there is no longer any record of. On a CLOSED month that
    mismatch is permanent — the settlement can never be re-saved to notice it.
    On the most recent one it is a real cash loss: re-saving the month sums the
    advances afresh, `advance_used` drops to zero, and the net jumps by exactly
    the amount already handed over, so the workshop pays it a second time.
    """
    if not advance_date:
        return None
    return SalaryPayment.objects.filter(month=advance_date.replace(day=1)).first()


def _is_owner(user):
    """
    The same either-or `owner_required` and `has_group` use everywhere else.

    Read wherever a refusal has to name a route the person can actually take:
    deleting a settlement is Owner-only, so telling Office to "delete it first"
    points them at a button they cannot see.
    """
    return user.is_superuser or user.groups.filter(name='Owner').exists()


def _mark_locked(advances):
    """
    Flag each advance whose month has already been settled, in ONE query.

    The delete is refused server-side whatever this says — that is the control.
    This is what stops the button being offered at all, on the rule the audit
    menu already follows: a door somebody can see but not open is worse than no
    door.
    """
    months = {a.date.replace(day=1) for a in advances}
    settled = set(
        SalaryPayment.objects.filter(month__in=months).values_list('month', flat=True)
    ) if months else set()
    for advance in advances:
        advance.locked = advance.date.replace(day=1) in settled
    return advances


@office_required
def salary_advance_home(request):
    """
    Salary & Advance landing page.

    Two jobs live here and they run on very different rhythms, so the page is
    ordered by urgency rather than by data model: anything still owing a
    settlement is surfaced first, then the everyday "give an advance" work,
    then settled history.

    Months are only ever listed from the point the workshop actually started
    using this section (earliest advance or settlement) — never a rolling
    fixed window, which would invent a wall of "missing" months that were
    never supposed to exist and train people to ignore the warning.
    """
    today = timezone.localdate()
    month_start, month_end = _month_bounds(today.year, today.month)

    staff = list(Mechanic.objects.filter(is_active=True).annotate(
        this_month_advance=Coalesce(
            Sum('salary_advances__amount', filter=Q(
                salary_advances__date__gte=month_start,
                salary_advances__date__lt=month_end,
            )),
            Decimal('0'), output_field=DecimalField(),
        )
    ).order_by('name'))

    this_month_total = sum((s.this_month_advance for s in staff), Decimal('0'))
    needs_salary = [s for s in staff if s.current_salary is None]

    # Where this section's history genuinely begins.
    first_advance = SalaryAdvance.objects.order_by('date').values_list('date', flat=True).first()
    first_payment = SalaryPayment.objects.order_by('month').values_list('month', flat=True).first()
    known_starts = [d.replace(day=1) for d in (first_advance, first_payment) if d]
    system_start = min(known_starts) if known_starts else month_start

    # Totals annotated in SQL — a settled-month list spanning years would
    # otherwise fire one query per month through SalaryPayment.total_amount.
    settled_map = {
        p.month: p for p in SalaryPayment.objects.annotate(
            total_net=Coalesce(Sum('lines__net_amount'), Decimal('0'), output_field=DecimalField())
        )
    }

    span = (month_start.year - system_start.year) * 12 + (month_start.month - system_start.month) + 1

    all_months = []  # newest first — every month this section has existed for
    cursor = month_start
    for _ in range(max(1, min(span, 240))):
        if cursor < system_start:
            break
        payment = settled_map.get(cursor)
        if payment:
            all_months.append({'month': cursor, 'payment': payment, 'due': False, 'overdue': False,
                               'is_current': cursor == month_start})
        elif cursor == month_start:
            # The running month becomes settleable from SETTLE_FROM_DAY —
            # salary is usually worked out in the first few days of the
            # following month (e.g. July settled ~Aug 1-3), not July 31st.
            all_months.append({'month': cursor, 'payment': None, 'due': today.day >= SETTLE_FROM_DAY,
                               'overdue': False, 'is_current': True})
        else:
            # A finished month is due immediately, but only reads as *late* once
            # the first few days of the following month have passed.
            overdue = today >= _month_bounds(cursor.year, cursor.month)[1] + timedelta(days=4)
            all_months.append({'month': cursor, 'payment': None, 'due': True,
                               'overdue': overdue, 'is_current': False})
        cursor = _prev_month(cursor)

    # Only the most recent couple of unsettled months get a banner. Over a
    # multi-year history any gap would otherwise pile up another warning at the
    # top until the whole strip is noise and people stop reading it — the full
    # picture lives in the year list below, in context.
    pending_all = [m for m in all_months if m['payment'] is None and m['due']]
    pending_months = pending_all[:2]
    pending_extra = len(pending_all) - len(pending_months)

    # A pending month is almost never the running month in practice (July is
    # settled in early August, not July 31st) — the "Advances given" total at
    # the top of the page always tracks *today's* month, which is near-zero
    # right when someone opens this page specifically to deal with last
    # month. So each pending banner gets its own real total for the month
    # it's actually about.
    for m in pending_months:
        p_start, p_end = _month_bounds(m['month'].year, m['month'].month)
        m['advance_total'] = SalaryAdvance.objects.filter(
            date__gte=p_start, date__lt=p_end
        ).aggregate(total=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField()))['total']

    # Grouped by year so several years stay readable — the running year opens
    # by default, older ones collapse behind their yearly total.
    year_blocks = []
    for entry in all_months:
        year = entry['month'].year
        if not year_blocks or year_blocks[-1]['year'] != year:
            year_blocks.append({'year': year, 'months': [], 'total': Decimal('0'), 'unsettled': 0})
        block = year_blocks[-1]
        block['months'].append(entry)
        if entry['payment'] is not None:
            block['total'] += entry['payment'].total_net
        elif entry['due']:
            block['unsettled'] += 1

    return render(request, 'workshop/salary_advance/home.html', {
        'staff': staff,
        'this_month_total': this_month_total,
        'needs_salary': needs_salary,
        'current_month': month_start,
        'pending_months': pending_months,
        'pending_extra': pending_extra,
        'year_blocks': year_blocks,
        'current_year': today.year,
        # Handed to the Give-an-Advance date box so it can refuse a settled
        # month while the date is being picked instead of after the whole form
        # has been filled in and submitted. `salary_advance_add` stays the
        # control; this is the settle screen's own "say it before the button"
        # rule applied one screen over. Already loaded — `settled_map` is the
        # query the year list is built from, so this costs nothing.
        'settled_month_keys': sorted(f"{m:%Y-%m}" for m in settled_map),
    })


@office_required
def salary_advance_add(request):
    """POST: record a cash advance given to a staff member."""
    if request.method == 'POST':
        # int() first: get_object_or_404(pk='abc') raises ValueError, not
        # Http404, so a garbled hidden field was a 500 rather than a 404.
        try:
            staff_id = int(request.POST.get('staff_id') or 0)
        except (TypeError, ValueError):
            staff_id = 0
        staff = get_object_or_404(Mechanic, pk=staff_id)
        amount = parse_money(request.POST.get('amount', '0'), SalaryAdvance, 'amount')
        # Trimmed to the column: a 400-char note into max_length=255 is stored
        # by SQLite and rejected by Postgres with "value too long".
        note = fit_text(request.POST.get('note', '').strip(), SalaryAdvance, 'note')
        today = timezone.localdate()
        date_str = request.POST.get('date', '').strip()
        advance_date = today
        if date_str:
            try:
                advance_date = date.fromisoformat(date_str)
            except ValueError:
                pass

        settled = _settled_month_for(advance_date)

        if amount is None:
            messages.error(request, "Enter a valid advance amount.")
        elif advance_date > today:
            # Cash cannot have been handed over on a day that has not arrived,
            # and a forward-dated advance lands in a month the settlement screen
            # will not reach for weeks.
            messages.error(request, "An advance can't be dated in the future.")
        elif settled:
            # The month is closed. Two honest ways forward, and which one to
            # offer depends on who is asking: deleting a settlement is
            # Owner-only, so telling Office to "delete it first" would send them
            # at a button they cannot see.
            if _is_owner(request.user):
                messages.error(
                    request,
                    f"{settled.month:%B %Y} is already settled. Delete that settlement "
                    f"first, then record this advance and settle the month again — "
                    f"or record it in {today:%B} with a note saying it was from "
                    f"{settled.month:%B}."
                )
            else:
                messages.error(
                    request,
                    f"{settled.month:%B %Y} is already settled. Ask an owner to delete "
                    f"that settlement so it can be added — or record it in "
                    f"{today:%B} with a note saying it was from {settled.month:%B}."
                )
        elif not staff.is_active and advance_date >= today:
            # A retired staff member takes no NEW cash — but a BACKDATED entry
            # is a correction, not a handout, and refusing it was the wrong
            # call: the workshop settles a month in the first days of the next
            # one, so "we forgot an advance from last month" is a routine
            # discovery, and the person it belongs to may well have left since.
            # Blocking that left the books permanently wrong about money that
            # really did leave the drawer. Only today's date is refused.
            messages.error(
                request,
                f"{staff.name} is retired, so a new advance dated today can't be recorded. "
                f"If this is one you forgot from while they were still here, date it to "
                f"the day it actually happened."
            )
        else:
            advance = SalaryAdvance.objects.create(
                staff=staff, amount=amount, date=advance_date,
                note=note or None, created_by=request.user,
            )
            notify(
                'SALARY_ADVANCE',
                f"{staff.name} given ₹{amount:,.0f} advance",
                detail=f"{advance_date:%d %b %Y}",
                actor=request.user,
                # Straight to the one-glance answer page, naming THIS advance so
                # it can be shown as the advance given rather than merely the
                # latest one.
                #
                # This link has moved twice. It first pointed at
                # `salary_advance_staff_detail` when that served only the modal's
                # fragment — a partial extending no base template, so the owner
                # landed on an unstyled scrap with no nav and no way back. It was
                # then repointed at the section, which fixed nothing already sent,
                # because a notification stores its url forever. The fix was to
                # make this URL serve a real page; now that it does, it is also
                # the right destination. See CLAUDE.md.
                url=(
                    reverse('salary_advance_staff_detail', args=[staff.pk])
                    + f"?advance={advance.pk}"
                ),
                object_type='SALARY_ADVANCE', object_id=advance.pk,
            )
            messages.success(request, f"₹{amount:,.0f} advance recorded for {staff.name}.")
    return redirect('salary_advance_home')


@office_required
@transaction.atomic
def salary_advance_delete(request, pk):
    """
    POST: permanently delete an advance entry (logged first).

    REFUSED once the month it belongs to has been settled — the other half of
    the rule `_settled_month_for` states, and the half that was missing. An
    advance can no more leave a settled month than enter one: its cash is
    already inside a paid `SalaryPaymentLine.advance_used`, and removing the row
    underneath leaves that figure claiming money nothing records.

    Measured before this guard existed: a ₹3,000 advance deleted out of a
    settled month left the line reading `advance_used = 3,000` with no advance
    behind it, and re-saving that month recomputed the advance to ₹0 and the net
    UP by ₹3,000 — the workshop paying cash it had already handed over. On a
    closed month the mismatch simply stands for ever, because that settlement
    can never be re-saved to notice it.

    The refusal names a route the reader can take, which differs three ways: an
    OPEN month has none of this (the delete just works), the most recent
    settlement can be deleted and re-made (Owner-only, hence the role branch),
    and a CLOSED month has no route at all and must say so rather than send
    somebody at a button that will refuse them.
    """
    if request.method == 'POST':
        advance = get_object_or_404(SalaryAdvance, pk=pk)

        settled = _settled_month_for(advance.date)
        if settled:
            money = f"₹{advance.amount:,.0f}"
            when = f"{settled.month:%B %Y}"
            if _is_closed(settled):
                messages.error(
                    request,
                    f"{when} is closed, so this {money} advance can't be deleted — "
                    f"it is part of a settlement nobody can change any more. If it "
                    f"was recorded in error, correct it in "
                    f"{timezone.localdate():%B} with a note saying what it was for."
                )
            elif _is_owner(request.user):
                messages.error(
                    request,
                    f"{when} is already settled, and this {money} advance was "
                    f"subtracted from that month's pay. Delete the {when} "
                    f"settlement first, then remove this advance and settle the "
                    f"month again — otherwise the settlement keeps claiming "
                    f"{money} that nothing records."
                )
            else:
                messages.error(
                    request,
                    f"{when} is already settled, and this {money} advance was "
                    f"subtracted from that month's pay. Ask an owner to delete the "
                    f"{when} settlement first, so the month can be settled again "
                    f"without it."
                )
            return redirect('salary_advance_home')

        # AFTER the settled-month branch, deliberately. That one refuses
        # EVERYBODY including an owner, and names the settlement standing in the
        # way — so it is both the stronger rule and the more useful message
        # whenever the two overlap. This one only separates Office from an owner
        # inside a month still open.
        #
        # `created_at`, never `advance.date`: the Give an Advance form has its
        # own date box, so an advance handed over on Monday and keyed on
        # Thursday must still be Office's to correct.
        stop = delete_window.refusal(
            request.user, advance.created_at, f"This ₹{advance.amount:,.0f} advance")
        if stop:
            messages.error(request, stop)
            return redirect('salary_advance_home')

        reason = request.POST.get('reason', '').strip()
        DeletionLog.record(
            DeletionLog.ENTITY_SALARY_ADVANCE, advance,
            user=request.user, reason=reason, amount=advance.amount,
            # Subject first, and no raw ISO date — it was the only one in
            # the feed. "advance" stays out: the entity type printed beside
            # this label is already "Salary Advance".
            label=f"{advance.staff.name} · ₹{advance.amount:,.0f}, {advance.date:%d %b %Y}",
        )
        advance.delete()
        messages.success(request, "Advance permanently deleted (logged to Deletion History).")
    return redirect('salary_advance_home')


#: How many advances either surface shows. Two years of a monthly advance, so
#: in practice the whole history for anyone the workshop currently employs.
STAFF_ADVANCE_ROWS = 24


@office_required
def salary_advance_staff_detail(request, staff_id):
    """
    One staff member's advance history — as a PAGE when navigated to, as a bare
    fragment when the history modal fetches it.

    It was fragment-only, and that was a defect rather than a limitation of the
    design. A `SALARY_ADVANCE` notification used to link here; the link was
    repointed at the section, but **notifications are permanent rows carrying a
    stored url**, so every alert raised before that fix still arrives at this
    view forever. Repointing new ones could never fix the old ones. An owner
    tapping a month-old alert on their phone got an unstyled wall of rows with
    no heading, no nav, and no way back but the browser's own Back button.

    So the URL is made to work rather than the callers made to avoid it. The
    fragment is now the *opt-in* branch: anything without the AJAX header gets
    the full page. That direction matters — if the header is ever dropped the
    modal degrades to showing a complete page inside itself, which is untidy,
    whereas the reverse would put a naked fragment back in front of an owner,
    which is the bug this exists to close.
    """
    staff = get_object_or_404(Mechanic, pk=staff_id)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/salary_advance/partials/staff_advances.html', {
            'staff': staff,
            # Marked so a settled month's advances show a lock where the bin
            # would be. The refusal in `salary_advance_delete` is the control;
            # this only stops the button being offered.
            'advances': _mark_locked(list(staff.salary_advances.all()[:STAFF_ADVANCE_ROWS])),
        })

    # Which advance the alert was about. New notifications name it with
    # `?advance=`; the ones sent before that carry no id, so the newest stands
    # in — right in the overwhelming case, because the alert is raised the
    # moment the advance is recorded and is normally read the same day.
    #
    # It is LABELLED differently in the two cases rather than guessing
    # confidently: an id that resolves says "Advance given", a fallback says
    # "Latest advance", so a months-old alert cannot present today's advance as
    # the one it was announcing. Scoped to this staff member, so a hand-typed id
    # belonging to somebody else resolves to nothing rather than showing another
    # person's money on this page.
    advance = None
    wanted = request.GET.get('advance')
    if wanted and wanted.isdigit():
        advance = staff.salary_advances.filter(pk=wanted).first()
    exact = advance is not None
    if advance is None:
        advance = staff.salary_advances.first()   # Meta.ordering: newest first

    # The month the ADVANCE falls in, not today's — an alert opened on the 2nd
    # about an advance given on the 31st must total the month that advance
    # belongs to, or the two figures on screen describe different months.
    focus = advance.date if advance else timezone.localdate()

    # Aggregated in the database over the whole calendar month — the same window
    # `salary_expense` settles on, so this figure and the one the settlement
    # screen deducts can never disagree.
    month = staff.salary_advances.filter(date__year=focus.year, date__month=focus.month)
    month_total = month.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    return render(request, 'workshop/salary_advance/staff_detail.html', {
        'staff': staff,
        'advance': advance,
        'advance_is_exact': exact,
        'month_total': month_total,
        'month_count': month.count(),
        'month_label': focus.strftime('%B %Y'),
    })


@office_required
def salary_set_amount(request, staff_id):
    """POST: set/update a staff member's current monthly salary."""
    if request.method == 'POST':
        staff = get_object_or_404(Mechanic, pk=staff_id)
        amount = parse_money(request.POST.get('amount', '0'), Mechanic, 'current_salary')
        if amount is None:
            messages.error(request, "Enter a valid salary amount.")
        else:
            staff.current_salary = amount
            staff.save(update_fields=['current_salary'])
            messages.success(request, f"{staff.name}'s salary set to ₹{amount:,.0f}.")
    # `next` is attacker-controllable, so it is validated the same way the login
    # form's is (auth_views._safe_next). Unchecked, a POST carrying
    # next=https://evil.example.com/… bounced an authenticated Office user
    # straight off-site — a convincing place to put a fake "session expired"
    # sign-in page.
    from ..auth_views import _safe_next
    return redirect(_safe_next(request) or reverse('salary_advance_home'))


@office_required
def salary_payment_form(request, year, month):
    """
    GET: render the settlement form for one calendar month — one row per
    active staff, with that month's advances auto-summed and leave days
    editable. POST: freeze each staff's salary/leave/advance/net for that
    month into a SalaryPaymentLine, creating or updating the month's
    SalaryPayment. Re-saving an already-settled month simply recomputes and
    overwrites its lines — safe, since nothing else in the system reads
    these figures until they're saved.
    """
    try:
        target_month = date(year, month, 1)
    except ValueError:
        messages.error(request, "Invalid month.")
        return redirect('salary_advance_home')

    # A month that has not started cannot be settled. The home page only ever
    # offers months it has listed, but this URL takes the year and month
    # directly, so /salary-advance/payment/2099/12/ created a Dec 2099
    # settlement — which then sat in the year list forever and counted as a
    # settled month in salary_expense().
    if target_month > timezone.localdate().replace(day=1):
        messages.error(request, f"{target_month:%B %Y} hasn't started yet — nothing to settle.")
        return redirect('salary_advance_home')

    month_start, month_end = _month_bounds(year, month)
    payment = SalaryPayment.objects.filter(month=target_month).first()
    # Captured BEFORE the POST branch, which creates the payment when settling
    # for the first time — after that `payment` is truthy either way and can no
    # longer answer "was this month already settled when the request arrived?".
    already_settled = payment is not None
    existing_lines = {}
    if payment:
        existing_lines = {line.staff_id: line for line in payment.lines.select_related('staff').all()}

    if request.method == 'POST':
        # SETTLEMENT LOCK. A month that is already settled opens read-only and
        # must be unlocked before it can be overwritten — the same rule the Job
        # Card applies to a PAID bill, and enforced the same way: the template
        # locks the fields, and this rejects the POST regardless, because a
        # client-side lock alone is bypassed by a raw request.
        #
        # These figures have already been paid out, and this screen is opened to
        # READ a past month as often as to correct one.
        # CLOSED: a month older than the most recent settlement cannot be
        # changed at all — not by unlocking, not by anyone. Refused here and not
        # merely hidden, because the menu that offers it is client-side.
        if _is_closed(payment):
            latest = _latest_settlement()
            messages.error(
                request,
                f"{target_month:%B %Y} is closed — only the most recent settlement "
                f"({latest.month:%B %Y}) can be changed. Record any correction in "
                f"{timezone.localdate():%B} with a note saying what it was for."
            )
            return redirect('salary_advance_home')

        if payment and request.POST.get('settlement_unlock') != 'true':
            messages.error(
                request,
                f"{target_month:%B %Y} is already settled and locked. Use "
                f"\"Edit this settlement\" on that page before saving changes."
            )
            return redirect('salary_payment_form', year=year, month=month)

        # GUARD: every rupee handed out this month must land on a line.
        blocked = _unsettleable_staff(month_start, month_end)
        if blocked:
            detail = "; ".join(
                f"{s.name} (₹{amt:,.0f}, {why})" for s, amt, why in blocked)
            messages.error(
                request,
                f"Can't settle {target_month:%B %Y} yet — {detail}. "
                f"Their advances are already out of the drawer, and settling now "
                f"would drop that cash off the Profit page. Set a salary or "
                f"reactivate them, then settle."
            )
            return redirect('salary_payment_form', year=year, month=month)

        # GUARD: leave days must be a real number of days in this month.
        # Unvalidated, -10 produced a net of ₹26,666 on a ₹20,000 salary (a
        # negative deduction pays MORE than the salary), and 400 produced
        # -₹246,666. Rejected outright rather than clamped: a clamp would save
        # a number nobody typed.
        max_days = _days_in_month(year, month)
        bad_leave, bad_overtime = [], []
        parsed_leave, parsed_overtime = {}, {}
        for staff in Mechanic.objects.filter(is_active=True):
            leave_key = f'leave_days_{staff.pk}'
            if leave_key not in request.POST:
                continue

            # OVERTIME. Parsed HERE rather than at write time, where an unusable
            # figure used to fall back to zero without a word — so `5,000` typed
            # with a comma (which `Decimal` cannot read) saved ₹0, underpaid by
            # ₹5,000, and left the screen showing the right number the whole
            # time. That is exactly what the leave-days rule below refuses to do:
            # a fallback saves a number nobody typed.
            #
            # The split is between NOTHING TYPED and SOMETHING UNUSABLE, and both
            # halves are load-bearing. An absent key is the ordinary case (the
            # box only exists on rows the form drew) and an empty one is somebody
            # clearing it; both mean no overtime and must stay ₹0, or an
            # untouched settlement would refuse itself. Anything actually typed
            # that `parse_money` cannot use is reported by name.
            ot_raw = request.POST.get(f'overtime_{staff.pk}')
            if ot_raw is None or not ot_raw.strip():
                parsed_overtime[staff.pk] = Decimal('0')
            else:
                overtime = parse_money(ot_raw, SalaryPaymentLine,
                                       'overtime_amount', allow_zero=True)
                if overtime is None:
                    bad_overtime.append(f"{staff.name} ({ot_raw.strip()})")
                else:
                    parsed_overtime[staff.pk] = overtime

            raw = (request.POST.get(leave_key, '0') or '0').strip()
            try:
                value = Decimal(raw)
            except Exception:
                bad_leave.append(f"{staff.name} ({raw!r})")
                continue
            if value < 0 or value > max_days:
                bad_leave.append(f"{staff.name} ({value})")
                continue
            parsed_leave[staff.pk] = value

        if bad_leave:
            messages.error(
                request,
                f"Leave days must be between 0 and {max_days} for "
                f"{target_month:%B %Y}. Check: {', '.join(bad_leave)}."
            )
        if bad_overtime:
            # The bound is READ from the column, never restated — the same rule
            # `parse_money` itself follows, so the message cannot drift from what
            # is actually accepted.
            # Whole rupees, and FLOORED rather than rounded. The true ceiling
            # is 99,999,999.99, which `:,.0f` rounds UP to 100,000,000 — a
            # figure the guard itself rejects, so the message would name a
            # bound that does not work. Stating the integer below it can only
            # ever understate what is accepted.
            ot_field = SalaryPaymentLine._meta.get_field('overtime_amount')
            ot_max = Decimal(10) ** (ot_field.max_digits - ot_field.decimal_places) - 1
            messages.error(
                request,
                f"Overtime must be a plain number, 0 to {ot_max:,.0f} — no commas "
                f"or symbols. Leave it empty if there is none. "
                f"Check: {', '.join(bad_overtime)}."
            )
        # Both are reported before returning, so a form wrong in both places is
        # corrected in one pass rather than one round trip per mistake.
        if bad_leave or bad_overtime:
            return redirect('salary_payment_form', year=year, month=month)

        # GUARD: the second half of "every rupee handed out lands on a line".
        #
        # `_unsettleable_staff` catches the two STANDING reasons somebody gets
        # no line — no salary, retired. This catches the SITUATIONAL one, which
        # a stale browser tab produces on an ordinary working day: the form is
        # open while the office types leave days for seven people, somebody is
        # hired and handed an advance in the meantime, and the submitted payload
        # carries no `leave_days_<pk>` box for them. The loop below skips
        # anyone whose key is absent, so they get no line — and their advance is
        # now inside a settled month, which `salary_expense` excludes from its
        # loose-advance pass. The cash is then counted in NEITHER place and
        # drops off the Profit page permanently, silently.
        #
        # Refused rather than papered over: writing them a line here would
        # price it at today's salary with leave days nobody entered, which is
        # the same defect `ASettledMonthIsAClosedSetOfPeopleTests` pins down.
        # Reloading the page is the whole remedy.
        #
        # Scoped to the FIRST settlement, and that scope is the point. The harm
        # is the TRANSITION — settling is what moves the month out of
        # `salary_expense`'s loose-advance pass. On a month already settled the
        # cash is already counted or already lost, and re-saving changes
        # neither, so blocking there would refuse an ordinary correction (fixing
        # somebody else's leave days) over a state the re-save did not cause and
        # cannot fix. Nothing new can be stranded either way: an advance cannot
        # be recorded into a settled month, nor deleted out of one.
        stranded = [] if already_settled else [
            staff for staff in Mechanic.objects.filter(
                salary_advances__date__gte=month_start,
                salary_advances__date__lt=month_end,
            ).distinct().order_by('name')
            if f'leave_days_{staff.pk}' not in request.POST
        ]
        if stranded:
            messages.error(
                request,
                f"Can't settle {target_month:%B %Y} — "
                f"{', '.join(s.name for s in stranded)} received an advance this "
                f"month but this form has no line for them, so that cash would be "
                f"counted nowhere. Reload this page and settle again."
            )
            return redirect('salary_payment_form', year=year, month=month)

        with transaction.atomic():
            if not payment:
                payment = SalaryPayment.objects.create(month=target_month, created_by=request.user)

            # Settling a month closes every earlier one, for good.
            _close_earlier_months(target_month)

            for staff in Mechanic.objects.filter(is_active=True):
                leave_key = f'leave_days_{staff.pk}'
                if leave_key not in request.POST or staff.current_salary is None:
                    continue

                leave_days = parsed_leave.get(staff.pk, Decimal('0'))

                # A month keeps the salary it was FIRST settled at. Only a new
                # settlement reads the staff member's current salary.
                #
                # This is what makes "settle the finished month, then apply the
                # raise" safe as a working rule, and it needs no interface: a
                # month re-saved later — to correct leave days, say — can never
                # be silently repriced by a raise entered since. To settle a
                # month at a different salary, delete the settlement and settle
                # again, which is deliberate, Owner-only and logged.
                existing_line = existing_lines.get(staff.pk)

                # A SETTLED month is a closed set of people, not a live roster.
                # Re-saving one must never enrol somebody who was not paid in
                # it — a staff member hired after the month was settled has no
                # line, and without this guard `update_or_create` below would
                # write them a brand new one priced at TODAY's salary, adding
                # a wage that month never carried to `salary_expense`. The
                # frozen-salary rule protected everyone who already had a line
                # and said nothing about people who had none.
                # Adding someone to a past month is deliberately not an edit:
                # delete the settlement and settle again, which is Owner-only
                # and logged — the same remedy as repricing one.
                if already_settled and existing_line is None:
                    continue

                salary_used = existing_line.salary_used if existing_line else staff.current_salary

                # Already parsed and refused above, so this is a lookup rather
                # than a second reading of the same box.
                overtime = parsed_overtime.get(staff.pk, Decimal('0'))

                advance_used = SalaryAdvance.objects.filter(
                    staff=staff, date__gte=month_start, date__lt=month_end
                ).aggregate(
                    total=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField())
                )['total']

                net_amount = _compute_net(salary_used, leave_days, advance_used, overtime)

                SalaryPaymentLine.objects.update_or_create(
                    payment=payment, staff=staff,
                    defaults={
                        'salary_used': salary_used,
                        'leave_days': leave_days,
                        'overtime_amount': overtime,
                        'advance_used': advance_used,
                        'net_amount': net_amount,
                    }
                )

        # Raised after the atomic block, so a rolled-back settlement never
        # announces itself. `total_amount` is read here rather than accumulated
        # in the loop so the figure is whatever actually landed in the database.
        notify(
            'SALARY_SETTLED',
            f"{target_month:%B %Y} salary settled",
            detail=f"₹{payment.total_amount:,.0f} across "
                   f"{payment.lines.count()} staff",
            actor=request.user,
            url=reverse('salary_advance_home'),
            object_type='SALARY_PAYMENT', object_id=payment.pk,
        )

        messages.success(request, f"{target_month.strftime('%B %Y')} salary settlement saved.")
        return redirect('salary_advance_home')

    # Say it BEFORE the button, not after. The POST guard refuses a settlement
    # that would strand someone's advances, but a page that only reveals that on
    # submit makes the office fill in a whole month's leave days first. Retired
    # staff with advances block too and are absent from `rows` entirely, so
    # without this they were invisible until the error fired.
    blockers = _unsettleable_staff(month_start, month_end)
    blocker_ids = {s.pk for s, _amt, _why in blockers}

    # A SETTLED month is its lines; an UNSETTLED one is the roster.
    #
    # This used to read the roster either way, and a staff member with no line
    # then fell through to `staff.current_salary` below — so a month marked
    # "Closed — paid and settled" rendered anyone hired since at TODAY's salary,
    # with a live "Pay now" figure that was never paid and never will be. On a
    # real system that is the normal case, not an edge one: every month settled
    # before a new hire would show them.
    # `existing_lines` already carries `select_related('staff')`, so reading the
    # settled set costs no extra query.
    if payment:
        staff_for_rows = sorted(
            (line.staff for line in existing_lines.values()),
            key=lambda s: s.name,
        )
    else:
        staff_for_rows = Mechanic.objects.filter(is_active=True).order_by('name')

    rows, missing_salary = [], []
    for staff in staff_for_rows:
        advance_used = SalaryAdvance.objects.filter(
            staff=staff, date__gte=month_start, date__lt=month_end
        ).aggregate(
            total=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField())
        )['total']
        existing = existing_lines.get(staff.pk)
        leave_days = existing.leave_days if existing else Decimal('0')
        # Re-opening a settled month must offer the salary it was SETTLED at,
        # not today's — otherwise a raise since then would silently re-price it
        # the moment anyone saved the month again for an unrelated reason.
        salary_used = existing.salary_used if existing else staff.current_salary
        overtime_amount = existing.overtime_amount if existing else Decimal('0')
        # Only the ones who are merely left out. Anyone in `blockers` stops the
        # settlement outright and is reported by the stronger banner instead —
        # listing them twice, under two different consequences, would be worse
        # than listing them once.
        # "Needs a salary before this month can be settled" is a statement about
        # settling. On a month already settled it is both untrue and alarming —
        # and it would fire for anyone whose salary was cleared *after* they
        # were paid, whose line is sitting right there with a real figure on it.
        if not payment and staff.current_salary is None and staff.pk not in blocker_ids:
            missing_salary.append(staff)
        rows.append({
            'staff': staff,
            'advance_used': advance_used,
            'leave_days': leave_days,
            'salary_used': salary_used,
            'overtime_amount': overtime_amount,
            'net_amount': (
                _compute_net(salary_used, leave_days, advance_used, overtime_amount)
                if salary_used is not None else None
            ),
        })

    return render(request, 'workshop/salary_advance/payment_form.html', {
        'target_month': target_month,
        'rows': rows,
        'payment': payment,
        'missing_salary': missing_salary,
        'blockers': [{'staff': s, 'amount': amt, 'reason': why} for s, amt, why in blockers],
        'payable_count': sum(1 for r in rows if r['net_amount'] is not None),
        'is_closed': _is_closed(payment),
        'latest_settlement': _latest_settlement(),
    })


@owner_required
def salary_payment_delete(request, pk):
    """
    GET: show a dedicated confirmation page (month, total, staff, reason) —
    same pattern as jobcard_delete's confirm page, not a browser popup, since
    this erases a whole month's payroll settlement in one action.
    POST: permanently delete a month's whole salary settlement (logged first).

    Owner-only, unlike the rest of this section — correcting a mistake (wrong
    leave days, a missed advance) is already covered by editing the numbers
    and re-saving, which never loses data. This is only for un-recording a
    month entirely (e.g. settled by mistake), so it's kept behind a higher
    bar than Office's day-to-day settling.
    """
    payment = get_object_or_404(SalaryPayment, pk=pk)

    # Only the most recent settlement can be removed. Anything older has been
    # paid and moved on from, and this is checked on the GET as well so the
    # confirmation page for a closed month never even renders.
    if _is_closed(payment):
        latest = _latest_settlement()
        messages.error(
            request,
            f"{payment.month:%B %Y} is closed and can't be deleted — only the most "
            f"recent settlement ({latest.month:%B %Y}) can be changed. Record any "
            f"correction in {timezone.localdate():%B} instead."
        )
        return redirect('salary_advance_home')

    lines = payment.lines.select_related('staff').all()

    if request.method != 'POST':
        return render(request, 'workshop/salary_advance/payment_confirm_delete.html', {
            'payment': payment,
            'lines': lines,
        })

    reason = request.POST.get('reason', '').strip()
    month_label = payment.month.strftime('%B %Y')
    line_summary = [
        {
            'staff': line.staff.name,
            'salary_used': str(line.salary_used),
            'leave_days': str(line.leave_days),
            'advance_used': str(line.advance_used),
            'net_amount': str(line.net_amount),
        }
        for line in lines
    ]

    with transaction.atomic():
        DeletionLog.record(
            DeletionLog.ENTITY_SALARY_PAYMENT, payment,
            user=request.user, reason=reason, amount=payment.total_amount,
            label=f"{month_label} salary settlement",
            extra={'lines': line_summary},
        )
        payment.delete()

    messages.success(request, f"{month_label} settlement permanently deleted (logged to Deletion History).")
    return redirect('salary_advance_home')
