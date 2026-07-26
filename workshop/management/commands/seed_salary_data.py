"""
Management command: seed_salary_data
-------------------------------------
Seeds realistic Salary & Advance history on top of whatever real data already
exists — additive only. Never overwrites a staff member's existing
current_salary, never re-settles a month that already has a SalaryPayment,
and never touches the running month (settling it is a real decision for the
office to make when the month actually ends, not something to fake).

Creates:
  - A role-appropriate current_salary for any active staff who doesn't
    already have one.
  - A handful of cash advances per staff per month across the requested
    range, varying in amount and date.
  - A settled SalaryPayment (+ per-staff SalaryPaymentLine) for most past
    months in range, computed with the real /30 leave-day formula.
  - A few fixed, reproducible gaps — months deliberately left un-settled —
    so Month History has something real to demonstrate instead of every
    month being a uniform wall of green checkmarks.

Usage:
    python manage.py seed_salary_data
    python manage.py seed_salary_data --years 3
    python manage.py seed_salary_data --seed 7
"""

import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from workshop.models import Mechanic, SalaryAdvance, SalaryPayment, SalaryPaymentLine

ROLE_SALARY_RANGE = {
    Mechanic.ROLE_MECHANIC: (18000, 24000),
    Mechanic.ROLE_ASSISTANT_MECHANIC: (13000, 17000),
    Mechanic.ROLE_OFFICE_STAFF: (14000, 18000),
    Mechanic.ROLE_GENERAL_HELPER: (9000, 12000),
}

ADVANCE_NOTES = [
    "Personal emergency", "Festival advance", "Medical need",
    "Family function", "Cash from owner", None, None,
]

# Months back from the running month that are deliberately left unsettled,
# so the "unrecorded" state has real examples across different years.
GAP_OFFSETS = {5, 17, 30}


def _month_bounds(year, month):
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _prev_month(d):
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def _next_month(d):
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _compute_net(salary, leave_days, advance):
    leave_deduction = (salary / Decimal('30') * leave_days).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return salary - leave_deduction - advance


class Command(BaseCommand):
    help = "Seed realistic Salary & Advance history: staff salaries, advances, and monthly settlements."

    def add_arguments(self, parser):
        parser.add_argument('--years', type=int, default=3, help="How many years of history to seed (default 3)")
        parser.add_argument('--seed', type=int, default=2026, help="Random seed for reproducible output")

    def handle(self, *args, **options):
        random.seed(options['seed'])
        years = options['years']
        if years < 1:
            raise CommandError("--years must be at least 1.")

        staff = list(Mechanic.objects.filter(is_active=True))
        if not staff:
            raise CommandError("No active staff found — register staff first (Control Hub -> Staff Registration).")

        today = timezone.localdate()
        current_month = today.replace(day=1)

        # Fill in a salary only where one doesn't already exist — never
        # overwrite what a real user already set.
        given_salary = []
        with transaction.atomic():
            for s in staff:
                if s.current_salary is None:
                    lo, hi = ROLE_SALARY_RANGE.get(s.role, (12000, 16000))
                    s.current_salary = Decimal(random.randrange(lo, hi, 500))
                    s.save(update_fields=['current_salary'])
                    given_salary.append((s.name, s.current_salary))

        start_month = current_month
        for _ in range(years * 12):
            start_month = _prev_month(start_month)

        advances_created = 0
        payments_created = 0
        skipped_existing = 0
        gaps_left = []

        cursor = start_month
        while cursor < current_month:
            offset = (current_month.year - cursor.year) * 12 + (current_month.month - cursor.month)
            month_start, month_end = _month_bounds(cursor.year, cursor.month)

            with transaction.atomic():
                for s in staff:
                    for _ in range(random.choice([0, 1, 1, 2, 2, 3])):
                        day_offset = random.randint(0, (month_end - month_start).days - 1)
                        SalaryAdvance.objects.create(
                            staff=s,
                            amount=Decimal(random.randrange(500, 6000, 100)),
                            date=month_start + timedelta(days=day_offset),
                            note=random.choice(ADVANCE_NOTES),
                        )
                        advances_created += 1

                already_settled = SalaryPayment.objects.filter(month=month_start).exists()
                if already_settled:
                    skipped_existing += 1
                elif offset in GAP_OFFSETS:
                    gaps_left.append(month_start)
                else:
                    payment = SalaryPayment.objects.create(month=month_start)
                    for s in staff:
                        advance_total = SalaryAdvance.objects.filter(
                            staff=s, date__gte=month_start, date__lt=month_end
                        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                        leave_days = Decimal(random.choice([0, 0, 0, 1, 1, 2, 3]))
                        net = _compute_net(s.current_salary, leave_days, advance_total)
                        SalaryPaymentLine.objects.create(
                            payment=payment, staff=s, salary_used=s.current_salary,
                            leave_days=leave_days, advance_used=advance_total, net_amount=net,
                        )
                    payments_created += 1

            cursor = _next_month(cursor)

        if given_salary:
            self.stdout.write("Salary set for:")
            for name, amt in given_salary:
                self.stdout.write(f"  {name}: Rs.{amt:,.0f}/month")

        self.stdout.write(self.style.SUCCESS(
            f"\n[DONE] {advances_created} advances, {payments_created} monthly settlements created "
            f"({start_month} to {_prev_month(current_month)})."
        ))
        if skipped_existing:
            self.stdout.write(f"  {skipped_existing} already-settled month(s) left untouched.")
        if gaps_left:
            self.stdout.write(
                f"  {len(gaps_left)} month(s) deliberately left unsettled to demo the "
                f"'unrecorded' state: {', '.join(d.strftime('%b %Y') for d in gaps_left)}"
            )
