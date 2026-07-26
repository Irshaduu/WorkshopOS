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


def _compute_net(salary, leave_days, advance):
    """Net = salary - (salary/30 * leave_days) - advance, rounded to paise."""
    salary = salary or Decimal('0')
    leave_days = leave_days or Decimal('0')
    advance = advance or Decimal('0')
    leave_deduction = (salary / Decimal('30') * leave_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return salary - leave_deduction - advance


def _prev_month(d):
    """First day of the month before the one `d` falls in."""
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


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
    })


@office_required
def salary_advance_add(request):
    """POST: record a cash advance given to a staff member."""
    if request.method == 'POST':
        staff = get_object_or_404(Mechanic, pk=request.POST.get('staff_id'))
        try:
            amount = Decimal(str(request.POST.get('amount', '0')).strip())
        except Exception:
            amount = Decimal('0')
        note = request.POST.get('note', '').strip()
        date_str = request.POST.get('date', '').strip()
        advance_date = timezone.localdate()
        if date_str:
            try:
                advance_date = date.fromisoformat(date_str)
            except ValueError:
                pass

        if amount <= 0:
            messages.error(request, "Enter a valid advance amount.")
        else:
            SalaryAdvance.objects.create(
                staff=staff, amount=amount, date=advance_date,
                note=note or None, created_by=request.user,
            )
            messages.success(request, f"₹{amount:,.0f} advance recorded for {staff.name}.")
    return redirect('salary_advance_home')


@office_required
@transaction.atomic
def salary_advance_delete(request, pk):
    """POST: permanently delete an advance entry (logged first)."""
    if request.method == 'POST':
        advance = get_object_or_404(SalaryAdvance, pk=pk)
        reason = request.POST.get('reason', '').strip()
        DeletionLog.record(
            DeletionLog.ENTITY_SALARY_ADVANCE, advance,
            user=request.user, reason=reason, amount=advance.amount,
            label=f"₹{advance.amount:,.0f} advance — {advance.staff.name} ({advance.date})",
        )
        advance.delete()
        messages.success(request, "Advance permanently deleted (logged to Deletion History).")
    return redirect('salary_advance_home')


@office_required
def salary_advance_staff_detail(request, staff_id):
    """AJAX fragment: a staff member's recent advance history, for the modal."""
    staff = get_object_or_404(Mechanic, pk=staff_id)
    advances = staff.salary_advances.all()[:24]
    return render(request, 'workshop/salary_advance/partials/staff_advances.html', {
        'staff': staff,
        'advances': advances,
    })


@office_required
def salary_set_amount(request, staff_id):
    """POST: set/update a staff member's current monthly salary."""
    if request.method == 'POST':
        staff = get_object_or_404(Mechanic, pk=staff_id)
        try:
            amount = Decimal(str(request.POST.get('amount', '0')).strip())
        except Exception:
            amount = Decimal('-1')
        if amount <= 0:
            messages.error(request, "Enter a valid salary amount.")
        else:
            staff.current_salary = amount
            staff.save(update_fields=['current_salary'])
            messages.success(request, f"{staff.name}'s salary set to ₹{amount:,.0f}.")
    next_url = request.POST.get('next') or reverse('salary_advance_home')
    return redirect(next_url)


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

    month_start, month_end = _month_bounds(year, month)
    payment = SalaryPayment.objects.filter(month=target_month).first()
    existing_lines = {}
    if payment:
        existing_lines = {line.staff_id: line for line in payment.lines.select_related('staff').all()}

    if request.method == 'POST':
        with transaction.atomic():
            if not payment:
                payment = SalaryPayment.objects.create(month=target_month, created_by=request.user)

            for staff in Mechanic.objects.filter(is_active=True):
                leave_key = f'leave_days_{staff.pk}'
                if leave_key not in request.POST or staff.current_salary is None:
                    continue

                try:
                    leave_days = Decimal(str(request.POST.get(leave_key, '0') or '0'))
                except Exception:
                    leave_days = Decimal('0')

                advance_used = SalaryAdvance.objects.filter(
                    staff=staff, date__gte=month_start, date__lt=month_end
                ).aggregate(
                    total=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField())
                )['total']

                net_amount = _compute_net(staff.current_salary, leave_days, advance_used)

                SalaryPaymentLine.objects.update_or_create(
                    payment=payment, staff=staff,
                    defaults={
                        'salary_used': staff.current_salary,
                        'leave_days': leave_days,
                        'advance_used': advance_used,
                        'net_amount': net_amount,
                    }
                )

        messages.success(request, f"{target_month.strftime('%B %Y')} salary settlement saved.")
        return redirect('salary_advance_home')

    rows, missing_salary = [], []
    for staff in Mechanic.objects.filter(is_active=True).order_by('name'):
        advance_used = SalaryAdvance.objects.filter(
            staff=staff, date__gte=month_start, date__lt=month_end
        ).aggregate(
            total=Coalesce(Sum('amount'), Decimal('0'), output_field=DecimalField())
        )['total']
        existing = existing_lines.get(staff.pk)
        leave_days = existing.leave_days if existing else Decimal('0')
        if staff.current_salary is None:
            missing_salary.append(staff)
        rows.append({
            'staff': staff,
            'advance_used': advance_used,
            'leave_days': leave_days,
            'net_amount': (
                _compute_net(staff.current_salary, leave_days, advance_used)
                if staff.current_salary is not None else None
            ),
        })

    return render(request, 'workshop/salary_advance/payment_form.html', {
        'target_month': target_month,
        'rows': rows,
        'payment': payment,
        'missing_salary': missing_salary,
        'payable_count': sum(1 for r in rows if r['net_amount'] is not None),
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
