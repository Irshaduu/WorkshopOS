"""
Three years of Deposit & Rent data where every month is paid EXACTLY.

Deliberately boring: one rent for the whole span, and each month's daily
handovers summing to it to the rupee, so every closed month reads

    RENT 35,000   DEPOSITED 35,000   POSITION 0

A demo set with drifting surpluses makes the page look wrong when it is right —
the position column climbs and nobody can tell the arithmetic from the data.
With everything square, any figure that is not zero later is a real difference
worth looking at.

The current month is paid on pace up to YESTERDAY, so "pay today" lands on the
month's own daily rate rather than on something lumpy.

DRY RUN unless --yes, like every other destructive seeder here. Only rent data
is touched; nothing else in the database is read or written.
"""
import random
from datetime import date, timedelta
from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django.utils import timezone

from workshop.models import RentDeposit, RentRate

# What the office actually hands over, and the closing amounts it uses to
# finish a month off. Every value is a multiple of 500 and so is the rent, so a
# month can always be closed out exactly.
CHUNKS = [1500, 2000, 2000, 2500, 2500, 3000]
CLOSERS = [500, 1000, 1500, 2000, 2500, 3000]

MONTHS = 36
RENT = D('35000')


def _month_of(day):
    return day.replace(day=1)


def _shift(month, n):
    total = month.year * 12 + (month.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _days_in(month):
    return (_shift(month, 1) - month).days


def _split(total, rng):
    """Chunky handovers that sum to `total` exactly."""
    out, left = [], int(total)
    while left > 0:
        pick = rng.choice(CHUNKS)
        if left - pick < 500:            # would leave an unpayable remainder
            pick = left if left in CLOSERS else min(CLOSERS, key=lambda c: abs(c - left))
            pick = min(pick, left)
        out.append(min(pick, left))
        left -= out[-1]
    return out


def _days_for(month, count, rng, limit=None):
    """`count` distinct days in the month, spread out, skipping some."""
    last = limit or _days_in(month)
    if count >= last:
        return list(range(1, last + 1))
    return sorted(rng.sample(range(1, last + 1), count))


class Command(BaseCommand):
    help = "Seed 3 years of perfectly-paid Deposit & Rent data (DRY RUN unless --yes)."

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true',
                            help="Actually write. Without it, prints the plan only.")
        parser.add_argument('--seed', type=int, default=20260903,
                            help="RNG seed, so a re-run reproduces the same set.")

    def handle(self, *args, **options):
        rng = random.Random(options['seed'])
        write = options['yes']

        today = timezone.localdate()          # IST-aware — never date.today()
        this_month = _month_of(today)
        start = _shift(this_month, -(MONTHS - 1))

        existing = RentDeposit.objects.count() + RentRate.objects.count()
        self.stdout.write(
            f"Rent ledger: {start:%b %Y} to {this_month:%b %Y} "
            f"({MONTHS} months) at Rs {RENT:,.0f} a month, every month paid exactly.")
        if existing:
            self.stdout.write(self.style.WARNING(
                f"  Replaces {RentDeposit.objects.count()} deposit(s) and "
                f"{RentRate.objects.count()} rate(s) already on file."))

        rows, total = [], 0
        month = start
        while month <= this_month:
            if month == this_month:
                # On pace through YESTERDAY, rounded to the nearest 500 so the
                # figures stay the shape the office actually hands over. Day 1
                # gets nothing, which is correct: nothing has been paid yet.
                days_in = _days_in(month)
                target = int(RENT) * (today.day - 1) // days_in
                target = (target // 500) * 500
                if target <= 0:
                    month = _shift(month, 1)
                    continue
                amounts = _split(target, rng)
                days = _days_for(month, len(amounts), rng, limit=today.day - 1)
            else:
                amounts = _split(RENT, rng)
                days = _days_for(month, len(amounts), rng)

            for day, amount in zip(days, amounts):
                rows.append(RentDeposit(date=date(month.year, month.month, day),
                                        amount=D(amount)))
                total += amount
            month = _shift(month, 1)

        self.stdout.write(f"  {len(rows)} deposits, Rs {total:,} in total.")

        if not write:
            self.stdout.write(self.style.WARNING("DRY RUN — pass --yes to write."))
            return

        RentDeposit.objects.all().delete()
        RentRate.objects.all().delete()
        RentRate.objects.create(effective_from=start, amount=RENT)
        RentDeposit.objects.bulk_create(rows)

        # ⚠ AGE `created_at` TO MATCH THE MONEY DATE, or every seeded row reads
        # as keyed late. `created_at` is `auto_now_add`, so a bulk_create stamps
        # all 560 rows with today — and the page marks a row whose two dates
        # fall in different months, which is exactly what three years of
        # same-day inserts look like. The demo set would show 560 back-dated
        # entries and bury the one signal the mark exists to give.
        #
        # `.update()` rather than save(): auto_now_add cannot be assigned.
        # Stamped at 18:00 local on the money date — the collector comes in the
        # afternoon, and a time inside the day keeps the IST/UTC conversion in
        # `age_in_days` landing on the right calendar day.
        for row in RentDeposit.objects.all():
            RentDeposit.objects.filter(pk=row.pk).update(
                created_at=timezone.make_aware(
                    timezone.datetime.combine(row.date, timezone.datetime.min.time())
                ) + timedelta(hours=18))

        # Report the position the page will show, so a bad seed is caught here
        # rather than by eye on the page.
        from workshop import rent as rent_calc
        state = rent_calc.position(today=today)
        self.stdout.write(self.style.SUCCESS(
            f"Done. Before this month: {state['carry_direction']} "
            f"Rs {state['carry_amount']:,.0f}  |  pay today Rs {state['pay_today']:,.0f} "
            f"(Rs {state['remaining']:,.0f} over {state['days_left']} days)."))
        if state['carry'] != 0:
            self.stdout.write(self.style.ERROR(
                "  Expected every closed month square — the split is wrong."))
