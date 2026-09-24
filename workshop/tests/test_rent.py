"""
DEPOSIT & RENT — the rent is what a month COST, the deposits are how it got PAID.

The section replaces a calculation the office does on paper every morning:
`(target − paid so far) ÷ days left`, against a book the collector writes in.
So the tests that matter most are the ones that put the office's own worked
examples in, verbatim, and check the page agrees — including the awkward one
the owner asked about, where the rent is raised in March with effect from
January and today's figure has to absorb three months of repricing at once.

The second group guards how the section reaches the money math, which changed
on 2026-09-04. It used to reach it NOWHERE — rent arrived at the Profit page as
a Cashbook category, and this file's own test asserted that boundary, saying it
should fail loudly on the day somebody moved rent onto its own expense line
because that should be a decision and not a side effect.

That day came, and the workflow forced it rather than anybody choosing it: once
the office started recording rent HERE instead of in the Cashbook, the boundary
quietly stopped meaning "no figure moves" and started meaning "rent is in the
books nowhere". September 2026 carried Rs 35,000 of real rent and the Profit
page charged Rs 900 of it.

So the rule under test is now the SPLIT rather than the silence:

    the RATE, in whole months, capped at the month in progress -> the expense
    the DEPOSITS, by the day the cash moved                    -> Cash Tracking
    the GAP between them                                       -> a position

and the invariant that matters most is the one that used to be the whole
boundary: **a deposit still moves no profit figure by a rupee.**
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop import analysis_engine as engine
from workshop import rent as rent_calc
from workshop.delete_window import OFFICE_WINDOW_HOURS
from workshop.money_dates import backdate_floor, is_too_far_back
from workshop.models import (CashbookEntry, DeletionLog, EditLog, FailedAttempt,
                             Notification, OwnerWithdrawal, RentDeposit,
                             RentRate)
from workshop.notifications import CRITICAL, EVENTS


def _rate(year, month, amount):
    return RentRate.objects.create(
        effective_from=date(year, month, 1), amount=D(amount))


def _deposit(when, amount):
    return RentDeposit.objects.create(date=when, amount=D(amount))


def _age(instance, days):
    """Push `created_at` back with `.update()`, so `auto_now_add` cannot restamp it."""
    type(instance).objects.filter(pk=instance.pk).update(
        created_at=timezone.now() - timedelta(days=days))
    instance.refresh_from_db()
    return instance


class TheOfficeOwnCalculationTests(TestCase):
    """`(target − paid) ÷ days left`, which is what is being replaced."""

    def setUp(self):
        _rate(2026, 9, '35000')      # September has 30 days

    def test_day_one_of_a_fresh_month_divides_the_whole_rent_by_the_whole_month(self):
        state = rent_calc.position(today=date(2026, 9, 1))
        self.assertEqual(state['remaining'], D('35000'))
        self.assertEqual(state['days_left'], 30)
        # 35000 / 30 = 1166.67, rounded UP to the rupee.
        self.assertEqual(state['pay_today'], D('1167'))

    def test_paying_more_than_asked_lowers_tomorrow(self):
        """The owner's own example: pay 2,000 against a 1,167 suggestion."""
        _deposit(date(2026, 9, 1), '2000')
        state = rent_calc.position(today=date(2026, 9, 2))
        self.assertEqual(state['remaining'], D('33000'))
        self.assertEqual(state['days_left'], 29)
        # 33000 / 29 = 1137.93
        self.assertEqual(state['pay_today'], D('1138'))

    def test_skipping_a_day_raises_tomorrow_without_anything_being_recorded(self):
        """A skipped day is the ABSENCE of a row — there is no "nil" entry."""
        _deposit(date(2026, 9, 1), '2000')
        self.assertEqual(RentDeposit.objects.count(), 1)
        day_two = rent_calc.position(today=date(2026, 9, 2))['pay_today']
        day_four = rent_calc.position(today=date(2026, 9, 4))['pay_today']
        self.assertGreater(day_four, day_two)

    def test_the_last_day_of_the_month_asks_for_the_whole_shortfall(self):
        """`days_left` includes today, so it is 1 on the 30th and never 0."""
        _deposit(date(2026, 9, 10), '30000')
        state = rent_calc.position(today=date(2026, 9, 30))
        self.assertEqual(state['days_left'], 1)
        self.assertEqual(state['pay_today'], D('5000'))

    def test_a_month_already_covered_asks_for_nothing_never_a_negative(self):
        _deposit(date(2026, 9, 3), '40000')
        state = rent_calc.position(today=date(2026, 9, 4))
        self.assertEqual(state['remaining'], D('0'))
        self.assertEqual(state['pay_today'], D('0'))


class TheCarryForwardTests(TestCase):
    """Over-deposit a month and the next one asks for less — the owner's rule."""

    def setUp(self):
        _rate(2026, 8, '35000')

    def test_over_depositing_august_lowers_septembers_whole_target(self):
        """40,000 against 35,000 leaves September needing 30,000, not 35,000."""
        _deposit(date(2026, 8, 15), '40000')
        state = rent_calc.position(today=date(2026, 9, 1))
        self.assertEqual(state['carry_direction'], 'ahead')
        self.assertEqual(state['carry_amount'], D('5000'))
        self.assertEqual(state['due'], D('30000'))
        self.assertEqual(state['pay_today'], D('1000'))     # 30000 / 30

    def test_under_depositing_august_raises_septembers_target(self):
        _deposit(date(2026, 8, 15), '30000')
        state = rent_calc.position(today=date(2026, 9, 1))
        self.assertEqual(state['carry_direction'], 'behind')
        self.assertEqual(state['carry_amount'], D('5000'))
        self.assertEqual(state['due'], D('40000'))

    def test_being_a_long_way_ahead_asks_for_nothing_rather_than_a_negative(self):
        _deposit(date(2026, 8, 2), '90000')
        state = rent_calc.position(today=date(2026, 9, 10))
        self.assertEqual(state['due'], D('0'))
        self.assertEqual(state['pay_today'], D('0'))


class ThePositionStopsAtTheEndOfLastMonthTests(TestCase):
    """
    ⚠ THE TWO FIGURES CHARGE DIFFERENT MONTHS, AND THAT IS THE WHOLE POINT.

    The pace charges the current month in full, because finishing it is what is
    being paced. The position stops at the end of LAST month — charge the
    current one there too and the page reads "behind ₹35,000" every month from
    the 1st to the 5th, which is alarming, meaningless, and precisely how a
    real ₹4,500 shortfall stops being noticed.
    """

    def setUp(self):
        _rate(2026, 8, '35000')

    def test_the_first_of_the_month_reads_square_not_behind_a_whole_rent(self):
        _deposit(date(2026, 8, 20), '35000')          # August paid in full
        state = rent_calc.position(today=date(2026, 9, 1))
        self.assertEqual(state['carry_direction'], 'square')
        # ...while the PACE still asks for the whole of September.
        self.assertEqual(state['remaining'], D('35000'))

    def test_a_real_shortfall_is_still_reported_all_the_way_through_the_month(self):
        _deposit(date(2026, 8, 20), '30500')          # August ended 4,500 short
        for day in (1, 10, 25):
            state = rent_calc.position(today=date(2026, 9, day))
            self.assertEqual(state['carry_direction'], 'behind')
            self.assertEqual(state['carry_amount'], D('4500'))

    def test_a_catch_up_clears_the_shortfall_once_its_month_ends(self):
        """
        Deposits and rent are cut at the SAME boundary, so money paid in
        September to cover August's shortfall settles the whole history the
        moment September closes.
        """
        _deposit(date(2026, 8, 20), '30500')          # 4,500 short
        _deposit(date(2026, 9, 20), '39500')          # 35,000 + the catch-up
        self.assertEqual(
            rent_calc.position(today=date(2026, 10, 1))['carry_direction'], 'square')


class ARentChangeRepricesTheMonthsItCoversTests(TestCase):
    """
    The owner's worked example, verbatim.

    Rent 35,000, January to March deposited 1,00,000 against 1,05,000 charged —
    5,000 short over 5 days is ₹1,000 a day. Raise the rent to 40,000 with
    effect from JANUARY and three months reprice at once: 1,20,000 charged
    against the same 1,00,000, so 20,000 over 5 days is ₹4,000 a day.
    """

    def setUp(self):
        _rate(2026, 1, '35000')
        _deposit(date(2026, 1, 20), '35000')
        _deposit(date(2026, 2, 20), '35000')
        _deposit(date(2026, 3, 10), '30000')

    def test_before_the_rise_it_asks_for_a_thousand(self):
        state = rent_calc.position(today=date(2026, 3, 27))   # 5 days left
        self.assertEqual(state['days_left'], 5)
        self.assertEqual(state['remaining'], D('5000'))
        self.assertEqual(state['pay_today'], D('1000'))

    def test_a_rise_backdated_to_january_asks_for_four_thousand(self):
        RentRate.objects.update_or_create(
            effective_from=date(2026, 1, 1), defaults={'amount': D('40000')})
        state = rent_calc.position(today=date(2026, 3, 27))
        self.assertEqual(state['carry_amount'], D('10000'))   # Jan + Feb
        self.assertEqual(state['remaining'], D('20000'))      # + March's own 10,000
        self.assertEqual(state['pay_today'], D('4000'))

    def test_a_rise_from_march_only_touches_march(self):
        """The same edit, one month later, must NOT reach January or February."""
        _rate(2026, 3, '40000')
        state = rent_calc.position(today=date(2026, 3, 27))
        self.assertEqual(state['carry_direction'], 'square')
        self.assertEqual(state['rent'], D('40000'))
        self.assertEqual(state['remaining'], D('10000'))

    def test_a_rent_cut_works_the_same_way_in_the_other_direction(self):
        RentRate.objects.update_or_create(
            effective_from=date(2026, 1, 1), defaults={'amount': D('32000')})
        state = rent_calc.position(today=date(2026, 3, 27))
        self.assertEqual(state['carry_direction'], 'ahead')
        self.assertEqual(state['carry_amount'], D('6000'))    # 3,000 x 2 months


class ARateDatedAheadChangesNothingYetTests(TestCase):
    """
    A hike announced now and effective in January is the ONE forward date this
    section allows — and it is safe because a rate is not money. `rate_for()`
    applies it only once its month arrives.
    """

    def test_the_current_month_keeps_the_old_rent(self):
        _rate(2026, 9, '35000')
        _rate(2026, 12, '40000')
        self.assertEqual(rent_calc.position(today=date(2026, 9, 15))['rent'], D('35000'))
        self.assertEqual(rent_calc.position(today=date(2026, 11, 30))['rent'], D('35000'))
        self.assertEqual(rent_calc.position(today=date(2026, 12, 1))['rent'], D('40000'))

    def test_a_ledger_that_has_not_started_renders_rather_than_dividing_by_none(self):
        _rate(2026, 12, '40000')
        state = rent_calc.position(today=date(2026, 9, 15))
        self.assertFalse(state['started'])
        self.assertTrue(state['has_rate'])
        self.assertEqual(state['pay_today'], D('0'))

    def test_no_rent_on_file_at_all_still_renders(self):
        state = rent_calc.position(today=date(2026, 9, 15))
        self.assertFalse(state['started'])
        self.assertFalse(state['has_rate'])


class TheMonthTableAgreesWithTheHeadlineTests(TestCase):
    """A running total that disagreed with the figure above it would be the one
    thing a money page may never do."""

    def setUp(self):
        _rate(2026, 7, '35000')
        _deposit(date(2026, 7, 15), '35000')
        _deposit(date(2026, 8, 15), '30500')

    def test_every_month_since_the_first_rate_is_listed_even_an_empty_one(self):
        rows = rent_calc.month_rows(today=date(2026, 9, 20))
        self.assertEqual([r['month'] for r in rows],
                         [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)])

    def test_the_running_position_after_last_month_is_the_carry_the_hero_prints(self):
        rows = rent_calc.month_rows(today=date(2026, 9, 20))
        last_closed = next(r for r in rows if r['month'] == date(2026, 8, 1))
        state = rent_calc.position(today=date(2026, 9, 20))
        self.assertEqual(last_closed['running'], state['carry'])
        self.assertEqual(last_closed['running'], D('-4500'))


class TwentyYearsStaysReadableTests(TestCase):
    """
    ⚠ NOTHING IN THIS SECTION IS CAPPED OR PAGINATED, and that is what makes it
    still usable in 2046. The history is COLLAPSED YEAR BLOCKS — Salary &
    Advance's own pattern — so two decades is twenty closed lines and one open
    year; the deposit log shows ONE MONTH, which is naturally bounded at about
    sixty rows however long the business runs.

    A row cap was the first answer and it was wrong the way caps usually are:
    everything past it becomes unreachable, and a money list that quietly stops
    is worse than a long one.
    """

    def setUp(self):
        _rate(2006, 1, '20000')
        _rate(2016, 1, '30000')
        _rate(2026, 1, '35000')
        # One deposit a month for twenty years, deliberately short every time,
        # so the running position has to accumulate across every rate change.
        for year in range(2006, 2027):
            for month in range(1, 13):
                if date(year, month, 1) <= date(2026, 9, 1):
                    _deposit(date(year, month, 15), '19000')

    def test_every_year_is_reachable_and_none_is_dropped(self):
        blocks = rent_calc.year_blocks(today=date(2026, 9, 20))
        self.assertEqual([b['year'] for b in blocks], list(range(2026, 2005, -1)))

    def test_only_the_running_year_is_open(self):
        blocks = rent_calc.year_blocks(today=date(2026, 9, 20))
        self.assertEqual([b['year'] for b in blocks if b['is_current']], [2026])

    def test_a_year_line_agrees_with_the_months_inside_it(self):
        for block in rent_calc.year_blocks(today=date(2026, 9, 20)):
            self.assertEqual(block['rent'], sum(m['rent'] for m in block['months']))
            self.assertEqual(block['paid'], sum(m['paid'] for m in block['months']))
            if not block['is_current']:
                # A past year's position is the end of it, taken from its
                # latest month rather than re-derived.
                self.assertEqual(block['running'], block['months'][0]['running'])

    def test_the_running_years_line_counts_only_months_that_have_finished(self):
        """
        ⚠ Taken from its latest month, the CURRENT year would carry an
        unfinished month's whole rent against a few days of deposits — so the
        running year would read a five-figure "behind" from the 1st of every
        month, on the one line whose job is to say whether that year needs
        opening. It is the hero's own "before this month" rule.
        """
        blocks = rent_calc.year_blocks(today=date(2026, 9, 20))
        state = rent_calc.position(today=date(2026, 9, 20))
        self.assertTrue(blocks[0]['is_current'])
        self.assertEqual(blocks[0]['running'], state['carry'])
        # ...while the current MONTH's own row still shows the figure in progress.
        september = blocks[0]['months'][0]
        self.assertEqual(september['month'], date(2026, 9, 1))
        self.assertNotEqual(september['running'], blocks[0]['running'])

    def test_the_oldest_year_still_carries_the_position_the_hero_prints(self):
        """A year opened halfway down twenty years must agree with the top."""
        blocks = rent_calc.year_blocks(today=date(2026, 9, 20))
        state = rent_calc.position(today=date(2026, 9, 20))
        august = next(m for m in blocks[0]['months'] if m['month'] == date(2026, 8, 1))
        self.assertEqual(august['running'], state['carry'])

    def test_the_deposit_log_is_scoped_to_one_month_however_long_the_history(self):
        self.assertEqual(RentDeposit.objects.count(), 249)
        self.assertEqual(len(rent_calc.deposits_in(date(2026, 8, 1))), 1)
        self.assertEqual(len(rent_calc.deposits_in(date(2011, 3, 1))), 1)
        self.assertEqual(len(rent_calc.deposits_in(date(2026, 12, 1))), 0)

    def test_the_page_costs_the_same_queries_over_twenty_years_as_over_one(self):
        """
        Asserted as the INVARIANT rather than against a magic number: the cost
        is a function of the rate changes and the months walked, never of how
        many deposits exist, so a twenty-year history and a one-month history
        must issue the same count. A number would go stale on the next query
        added; this cannot.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        FailedAttempt.objects.all().delete()
        user = User.objects.create_superuser('owner_q', 'q@x.com', 'pw')
        c = Client()
        c.force_login(user)

        c.get(reverse('rent_home'))                       # warm any one-off lookups
        with CaptureQueriesContext(connection) as long_history:
            c.get(reverse('rent_home'))

        RentDeposit.objects.filter(date__lt=date(2026, 9, 1)).delete()
        RentRate.objects.filter(effective_from__lt=date(2026, 1, 1)).delete()
        with CaptureQueriesContext(connection) as short_history:
            c.get(reverse('rent_home'))

        self.assertEqual(len(long_history), len(short_history))


class TheTableAndTheHeroCanNeverDisagreeTests(TestCase):
    """
    ⚠ THE ONE THING A MONEY PAGE MAY NEVER DO is print a figure the rows
    beneath it do not add up to. The hero and the month table are two walks
    over the same two tables — `position()` sums, `month_rows()` accumulates —
    so they are two answers free to drift, and both bugs this class pins down
    were exactly that.

    Asserted as a PROPERTY over several shapes of history rather than against
    fixed numbers, so a scenario nobody thought of still has to satisfy it.
    """

    WHEN = date(2026, 9, 12)

    def assert_agrees(self):
        """The last CLOSED month's running position is the hero's carry, and
        the current row is that plus this month's own movement."""
        rows = rent_calc.month_rows(today=self.WHEN)
        state = rent_calc.position(today=self.WHEN)
        current, closed = rows[-1], rows[-2] if len(rows) > 1 else None
        if closed is not None:
            self.assertEqual(closed['running'], state['carry'])
        self.assertEqual(current['running'],
                         state['carry'] + state['paid_this_month'] - state['rent'])
        # ...and the year blocks are the same rows, so they inherit it.
        blocks = rent_calc.year_blocks(today=self.WHEN)
        self.assertEqual(blocks[0]['months'][0]['running'], current['running'])

    def test_a_plain_history_agrees(self):
        _rate(2026, 6, '35000')
        for m in (6, 7, 8):
            _deposit(date(2026, m, 14), '35000')
        _deposit(date(2026, 9, 5), '4000')
        self.assert_agrees()

    def test_a_history_with_a_rate_change_agrees(self):
        _rate(2026, 1, '30000')
        _rate(2026, 7, '35000')
        _deposit(date(2026, 3, 9), '12000')
        _deposit(date(2026, 8, 9), '50000')
        self.assert_agrees()

    def test_a_history_with_empty_months_agrees(self):
        _rate(2026, 4, '35000')
        _deposit(date(2026, 4, 2), '35000')       # May, June, July, Aug: nothing
        self.assert_agrees()

    def test_a_history_with_no_deposits_at_all_agrees(self):
        _rate(2026, 7, '35000')
        self.assert_agrees()

    def test_MONEY_DEPOSITED_BEFORE_THE_LEDGER_STARTED_IS_IN_BOTH(self):
        """
        A deposit dated before the first rate's month is how an opening
        position is entered — the workshop was already ahead on the day the
        section was switched on. `position()` counts it because `paid_before`
        has no floor; the table used to start its walk at zero, so the money
        was in the hero and in none of the rows.
        """
        _rate(2026, 8, '35000')
        _deposit(date(2026, 5, 20), '20000')      # before the ledger begins
        _deposit(date(2026, 8, 20), '35000')
        state = rent_calc.position(today=self.WHEN)
        self.assertEqual(state['carry_direction'], 'ahead')
        self.assertEqual(state['carry_amount'], D('20000'))
        self.assert_agrees()
        self.assertEqual(rent_calc.month_rows(today=self.WHEN)[0]['running'], D('20000'))

    def test_A_FUTURE_DATED_ROW_IS_CUT_THE_SAME_WAY_BY_ALL_THREE(self):
        """
        `rent_deposit_add` refuses a future date, so this cannot arise through
        the UI — but the hero, the log and the table read one figure and have
        to cut it identically, or one of them is silently wrong. Left open,
        `paid_this_month` counted it while `deposits_in` and `month_rows` did
        not.
        """
        _rate(2026, 9, '35000')
        _deposit(date(2026, 9, 2), '5000')
        RentDeposit.objects.create(date=date(2026, 11, 4), amount=D('9000'))
        self.assertEqual(rent_calc.position(today=self.WHEN)['paid_this_month'], D('5000'))
        self.assertEqual(len(rent_calc.deposits_in(date(2026, 9, 1))), 1)
        self.assert_agrees()


class EveryShapeOfMonthTests(TestCase):
    """The calendar cases, which is where an off-by-one would live."""

    def test_february_in_a_leap_year_has_twenty_nine_days(self):
        _rate(2024, 2, '29000')
        self.assertEqual(rent_calc.position(today=date(2024, 2, 1))['days_left'], 29)
        self.assertEqual(rent_calc.position(today=date(2024, 2, 29))['days_left'], 1)
        # 29,000 over 29 days is a clean 1,000 — an off-by-one shows immediately.
        self.assertEqual(rent_calc.position(today=date(2024, 2, 1))['pay_today'], D('1000'))

    def test_february_in_a_common_year_has_twenty_eight(self):
        _rate(2026, 2, '28000')
        self.assertEqual(rent_calc.position(today=date(2026, 2, 1))['days_left'], 28)
        self.assertEqual(rent_calc.position(today=date(2026, 2, 1))['pay_today'], D('1000'))

    def test_a_thirty_one_day_month(self):
        _rate(2026, 7, '31000')
        self.assertEqual(rent_calc.position(today=date(2026, 7, 1))['days_left'], 31)
        self.assertEqual(rent_calc.position(today=date(2026, 7, 31))['days_left'], 1)

    def test_a_deposit_on_the_first_and_on_the_last_day_both_land_in_the_month(self):
        _rate(2026, 7, '35000')
        _deposit(date(2026, 7, 1), '1000')
        _deposit(date(2026, 7, 31), '2000')
        self.assertEqual(rent_calc.position(today=date(2026, 7, 31))['paid_this_month'],
                         D('3000'))
        self.assertEqual(len(rent_calc.deposits_in(date(2026, 7, 1))), 2)

    def test_the_carry_crosses_a_year_boundary(self):
        _rate(2025, 12, '35000')
        _deposit(date(2025, 12, 10), '30000')     # 5,000 short in December
        state = rent_calc.position(today=date(2026, 1, 8))
        self.assertEqual(state['carry_direction'], 'behind')
        self.assertEqual(state['carry_amount'], D('5000'))
        self.assertEqual(state['due'], D('40000'))
        blocks = rent_calc.year_blocks(today=date(2026, 1, 8))
        self.assertEqual([b['year'] for b in blocks], [2026, 2025])

    def test_paise_survive_the_whole_way_through(self):
        _rate(2026, 7, '35000.50')
        _deposit(date(2026, 7, 4), '1000.25')
        state = rent_calc.position(today=date(2026, 7, 5))
        self.assertEqual(state['rent'], D('35000.50'))
        self.assertEqual(state['paid_this_month'], D('1000.25'))
        self.assertEqual(state['remaining'], D('34000.25'))

    def test_every_month_square_across_three_rate_changes_leaves_zero(self):
        """What the seeded demo set looks like: nothing but zeroes."""
        _rate(2024, 1, '30000')
        _rate(2025, 1, '32000')
        _rate(2026, 1, '35000')
        month = date(2024, 1, 1)
        while month <= date(2026, 8, 1):
            _deposit(month.replace(day=15), rent_calc.rate_for(month))
            month = rent_calc.shift_month(month, 1)
        state = rent_calc.position(today=date(2026, 9, 10))
        self.assertEqual(state['carry'], D('0'))
        self.assertEqual(state['carry_direction'], 'square')
        for row in rent_calc.month_rows(today=date(2026, 9, 10))[:-1]:
            self.assertEqual(row['running'], D('0'), row['month'])

    def test_removing_a_rate_falls_back_to_the_one_before_it(self):
        _rate(2026, 1, '30000')
        later = _rate(2026, 7, '35000')
        self.assertEqual(rent_calc.position(today=date(2026, 8, 5))['rent'], D('35000'))
        later.delete()
        self.assertEqual(rent_calc.position(today=date(2026, 8, 5))['rent'], D('30000'))


class _Signed(TestCase):
    """Three logins, one per tier."""

    def setUp(self):
        FailedAttempt.objects.all().delete()
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.owner = User.objects.create_user('owner1', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.office = User.objects.create_user('office1', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.floor = User.objects.create_user('floor1', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))
        self.c = Client()

    def as_(self, user):
        self.c.force_login(user)
        return self.c


class WhoCanDoWhatTests(_Signed):
    """Recording is Office; deciding what the premises cost is not."""

    def setUp(self):
        super().setUp()
        _rate(timezone.localdate().year, timezone.localdate().month, '35000')

    def test_floor_cannot_open_the_page_at_all(self):
        self.assertEqual(self.as_(self.floor).get(reverse('rent_home')).status_code, 403)

    def test_office_can_open_it_and_record_a_deposit(self):
        self.assertEqual(self.as_(self.office).get(reverse('rent_home')).status_code, 200)
        self.as_(self.office).post(reverse('rent_deposit_add'), {'amount': '2000'})
        self.assertEqual(RentDeposit.objects.count(), 1)

    def test_office_cannot_set_the_rent(self):
        res = self.as_(self.office).post(
            reverse('rent_rate_set'), {'month': '2026-01', 'amount': '99000'})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(RentRate.objects.filter(amount=D('99000')).exists())

    def test_an_owner_can(self):
        self.as_(self.owner).post(
            reverse('rent_rate_set'), {'month': '2026-01', 'amount': '99000'})
        self.assertTrue(RentRate.objects.filter(amount=D('99000')).exists())

    def test_the_rent_controls_are_not_even_drawn_for_office(self):
        """A template gate must mirror its view's decorator — a door Office can
        see and cannot open is worse than no door. That covers the ⋮ in the
        hero as well as the form behind it."""
        body = self.as_(self.office).get(reverse('rent_home')).content.decode()
        self.assertNotIn(reverse('rent_rate_set'), body)
        self.assertNotIn('rtRentModal', body)
        owner_body = self.as_(self.owner).get(reverse('rent_home')).content.decode()
        self.assertIn(reverse('rent_rate_set'), owner_body)
        self.assertIn('rtRentModal', owner_body)


class WhichMonthTheLogIsShowingTests(_Signed):
    """
    The log shows ONE month, and the year blocks are how any other is reached.
    An unreadable or impossible month falls back to the current one rather than
    rendering an empty list under a heading naming a month — which would read
    as "nothing was deposited then", and that would be a lie.
    """

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.this_month = today.replace(day=1)
        _rate(2026, 1, '35000')
        _deposit(self.this_month, '2000')
        _deposit(date(2026, 3, 10), '1500')

    def get(self, **params):
        return self.as_(self.office).get(reverse('rent_home'), params)

    def test_it_opens_on_the_current_month(self):
        res = self.get()
        self.assertEqual(res.context['focus'], self.this_month)
        self.assertTrue(res.context['focus_is_current'])

    def test_an_older_month_shows_only_its_own_deposits(self):
        res = self.get(month='2026-03')
        self.assertEqual(res.context['focus'], date(2026, 3, 1))
        self.assertFalse(res.context['focus_is_current'])
        self.assertEqual(res.context['focus_total'], D('1500'))
        self.assertEqual(len(res.context['rows']), 1)

    def test_junk_and_a_future_month_both_fall_back_to_this_one(self):
        for bad in ('', 'abc', '2026-13', '9999-01', '2026-3', 'x'):
            self.assertEqual(self.get(month=bad).context['focus'], self.this_month)

    def test_a_month_with_nothing_in_it_says_so_rather_than_looking_broken(self):
        res = self.get(month='2026-02')
        self.assertEqual(res.context['focus'], date(2026, 2, 1))
        self.assertEqual(res.context['rows'], [])
        self.assertContains(res, 'Nothing deposited in February 2026')

    def test_a_month_before_the_log_can_start_falls_back_too(self):
        """The log starts at the first rate's month or the oldest deposit,
        whichever is earlier; a month before that can only be a typed URL."""
        for early in ('2025-12', '2000-01', '0001-01'):
            self.assertEqual(self.get(month=early).context['focus'], self.this_month)

    def test_another_month_has_one_way_back_and_this_month_has_none(self):
        """
        ⚠ NO ‹ › ARROWS (the owners' call, 2026-09-24): Month by month already
        opens any month in one tap, and arrows invite drifting into a month by
        accident. Away from this month there is exactly one control, and on
        this month there is nothing to go back to.
        """
        march = self.get(month='2026-03')
        self.assertContains(march, 'Back to this month', count=1)
        self.assertContains(march, f'href="{reverse("rent_home")}#deposits"')
        now = self.get()
        self.assertNotContains(now, 'Back to this month')
        for res in (march, now):
            self.assertNotIn('prev_month', res.context)
            self.assertNotContains(res, 'aria-label="Previous month"')
        # Every month is still reachable from Month by month.
        self.assertContains(now, 'href="?month=2026-03#deposits"')

    def test_an_opening_deposit_before_the_first_rate_is_reachable(self):
        """A go-live opening position is a deposit dated before the ledger
        starts, and `position()` counts it — so its month must be reachable."""
        _deposit(date(2025, 11, 20), '5000')
        res = self.get(month='2025-11')
        self.assertEqual(res.context['focus'], date(2025, 11, 1))
        self.assertEqual(res.context['focus_total'], D('5000'))

    def test_recording_from_an_older_month_returns_to_that_month(self):
        """A form posts the query string it was rendered under: the period
        changing under somebody who did not ask for it is how a page stops
        being trusted."""
        back = self.get(month='2026-03').context['back_qs']
        self.assertEqual(back, '?month=2026-03')
        res = self.as_(self.office).post(
            reverse('rent_deposit_add'),
            {'amount': '900', 'date': '2026-03-12', 'back': back})
        self.assertRedirects(res, '/rent/?month=2026-03', fetch_redirect_response=False)


class WhatTheFormRefusesTests(_Signed):

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        _rate(today.year, today.month, '35000')

    def test_a_sub_paisa_amount_is_refused_not_written_as_zero(self):
        """
        ⚠ `parse_money` rejects a zero BEFORE it quantises, so `0.004` passes
        every check inside it and comes back as `0.00`. The caller's own
        `<= 0` is what stops it, and the CheckConstraint is what would have
        turned it into a 500 rather than a message.
        """
        self.as_(self.office).post(reverse('rent_deposit_add'), {'amount': '0.004'})
        self.assertEqual(RentDeposit.objects.count(), 0)

    def test_junk_and_infinity_are_refused(self):
        for bad in ('', 'abc', '-500', 'Infinity', 'NaN', '99999999999'):
            self.as_(self.office).post(reverse('rent_deposit_add'), {'amount': bad})
        self.assertEqual(RentDeposit.objects.count(), 0)

    def test_a_deposit_cannot_be_dated_in_the_future(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.as_(self.office).post(
            reverse('rent_deposit_add'), {'amount': '2000', 'date': tomorrow})
        self.assertEqual(RentDeposit.objects.count(), 0)

    def test_a_deposit_can_be_back_dated_because_the_book_is_keyed_late(self):
        """Up to Office's three days — the limit itself is allowed. Older is an
        owner's, pinned in `HowFarBackMoneyMayBeFiledTests`."""
        three_back = timezone.localdate() - timedelta(days=3)
        self.as_(self.office).post(
            reverse('rent_deposit_add'), {'amount': '2000', 'date': three_back.isoformat()})
        self.assertEqual(RentDeposit.objects.get().date, three_back)

    def test_the_last_rent_on_file_cannot_be_removed(self):
        """With no rate at all every figure on the page silently becomes zero."""
        rate = RentRate.objects.get()
        self.as_(self.owner).post(reverse('rent_rate_delete', args=[rate.pk]))
        self.assertEqual(RentRate.objects.count(), 1)

    def test_restating_a_month_replaces_it_rather_than_adding_a_second_answer(self):
        self.as_(self.owner).post(
            reverse('rent_rate_set'), {'month': '2026-01', 'amount': '38000'})
        self.as_(self.owner).post(
            reverse('rent_rate_set'), {'month': '2026-01', 'amount': '40000'})
        rows = RentRate.objects.filter(effective_from=date(2026, 1, 1))
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().amount, D('40000'))

    def test_a_rate_is_always_pinned_to_the_first_of_its_month(self):
        RentRate.objects.create(effective_from=date(2026, 5, 17), amount=D('30000'))
        self.assertEqual(
            RentRate.objects.get(amount=D('30000')).effective_from, date(2026, 5, 1))


class HowFarBackMoneyMayBeFiledTests(_Signed):
    """
    ⚠ THE QUIET DIRECTION. A future date is caught the moment somebody reads
    the period it lands in; one dated three years back rewrites the running
    position of every month since, on rows nobody scrolls to, and reports
    nothing at all.

    The floor is THREE DAYS since 2026-09-22 (the owner's decision); it was the
    1st of last month, which let Office file into last month for the whole of
    this one. A late catch-up is an owner's now, and the other owner is told.
    """

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        _rate(today.year, today.month, '35000')
        self.floor = backdate_floor(today)

    def post(self, user, when):
        return self.as_(user).post(
            reverse('rent_deposit_add'), {'amount': '2000', 'date': when.isoformat()})

    def test_office_may_reach_back_three_days(self):
        self.post(self.office, self.floor)
        self.assertEqual(RentDeposit.objects.count(), 1)

    def test_office_is_refused_the_day_before_that(self):
        self.post(self.office, self.floor - timedelta(days=1))
        self.assertEqual(RentDeposit.objects.count(), 0)

    def test_the_refusal_names_the_rule_and_the_route(self):
        res = self.post(self.office, self.floor - timedelta(days=1))
        said = ' '.join(str(m) for m in get_messages(res.wsgi_request))
        self.assertIn(f"{self.floor.day} {self.floor:%B %Y}", said)
        self.assertIn("Ask an owner", said)

    def test_last_month_is_closed_to_office_by_the_28th(self):
        """The rule this replaced kept last month open to Office all month."""
        late = date(2026, 9, 28)
        self.assertEqual(backdate_floor(late), date(2026, 9, 25))
        self.assertTrue(is_too_far_back(date(2026, 8, 1), today=late))
        self.assertFalse(is_too_far_back(date(2026, 9, 25), today=late))

    def test_an_owner_is_not_refused_because_the_opening_entry_needs_it(self):
        """A go-live opening position is a deposit dated before the ledger even
        starts, so a floor that bound owners would make setup impossible."""
        self.post(self.owner, self.floor - timedelta(days=400))
        self.assertEqual(RentDeposit.objects.count(), 1)

    def test_the_date_box_carries_the_floor_for_office_and_none_for_an_owner(self):
        office = self.as_(self.office).get(reverse('rent_home'))
        self.assertEqual(office.context['min_date_iso'], self.floor.isoformat())
        owner = self.as_(self.owner).get(reverse('rent_home'))
        self.assertEqual(owner.context['min_date_iso'], '')


class AnOwnerCannotDoItSILENTLYTests(_Signed):
    """
    ⚠ THE ESCALATION STOPS AT THE OWNER, SO THE OWNER IS WHERE DETECTION TAKES
    OVER FROM PREVENTION. Every other guard in this section refuses Office and
    points at an owner; nothing can refuse an owner, and inventing an approval
    queue for a two-owner workshop would be machinery nobody uses. What is left
    — and what this codebase already relies on for every permanent delete — is
    that the act reaches the OTHER owner's phone within seconds.

    `notify()` excludes the actor, so an owner never buzzes themselves.
    """

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user('owner2', password='pw')
        self.other.groups.add(Group.objects.get(name='Owner'))
        today = timezone.localdate()
        _rate(today.year, today.month, '35000')
        Notification.objects.all().delete()

    def raised(self, event):
        return Notification.objects.filter(event=event)

    def test_a_deposit_filed_past_the_floor_reaches_the_other_owner(self):
        old = backdate_floor(timezone.localdate()) - timedelta(days=90)
        self.as_(self.owner).post(
            reverse('rent_deposit_add'), {'amount': '5000', 'date': old.isoformat()})
        rows = self.raised('DATED_BACK_PAST_LIMIT')
        self.assertEqual([r.recipient for r in rows], [self.other])
        self.assertIn('₹5,000', rows[0].body)
        self.assertIn(f"{old:%d %b %Y}", rows[0].body)

    def test_an_ordinary_deposit_raises_nothing(self):
        """Most days, every day. An alert here would be the noise that stops
        the ones that matter from being read."""
        self.as_(self.owner).post(reverse('rent_deposit_add'), {'amount': '2000'})
        self.assertFalse(Notification.objects.filter(
            event__in=('DATED_BACK', 'DATED_BACK_PAST_LIMIT')).exists())

    def test_office_dating_back_inside_the_window_reaches_only_the_bell(self):
        """Anything Office is allowed to do goes to the bell, never a phone —
        yesterday's handover typed this morning is ordinary work."""
        self.as_(self.office).post(
            reverse('rent_deposit_add'),
            {'amount': '2000', 'date': backdate_floor(timezone.localdate()).isoformat()})
        self.assertTrue(self.raised('DATED_BACK').exists())
        self.assertEqual(self.raised('DATED_BACK_PAST_LIMIT').count(), 0)

    def test_every_rent_change_is_announced_not_only_a_backdated_one(self):
        """What the premises cost is what every figure here is measured
        against, so the other owner wants to know it moved either way."""
        self.as_(self.owner).post(
            reverse('rent_rate_set'),
            {'month': f"{timezone.localdate():%Y-%m}", 'amount': '40000'})
        rows = self.raised('RENT_RATE_SET')
        self.assertEqual([r.recipient for r in rows], [self.other])
        self.assertIn('₹40,000', rows[0].body)

    def test_a_backdated_rent_says_how_many_months_it_re_prices(self):
        """The backdating rides in `detail` — the context, read second — so the
        body stays a complete statement on its own."""
        self.as_(self.owner).post(
            reverse('rent_rate_set'), {'month': '2026-01', 'amount': '40000'})
        row = self.raised('RENT_RATE_SET').first()
        self.assertIn('backdated', row.detail)
        self.assertIn('months re-priced', row.detail)

    def test_removing_a_rate_is_logged_and_pushes_like_every_other_delete(self):
        """It wrote nothing at all for one revision — the one act here that
        could rewrite what every past month cost and leave no trace."""
        _rate(2026, 1, '30000')
        rate = RentRate.objects.get(effective_from=date(2026, 1, 1))
        self.as_(self.owner).post(
            reverse('rent_rate_delete', args=[rate.pk]), {'reason': 'keyed the wrong year'})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_RENT_RATE)
        self.assertEqual(log.reason, 'keyed the wrong year')
        self.assertEqual(log.amount, D('30000'))
        self.assertEqual(self.raised('RECORD_DELETED').count(), 1)

    def test_the_actor_never_hears_about_their_own_action(self):
        old = backdate_floor(timezone.localdate()) - timedelta(days=90)
        self.as_(self.owner).post(
            reverse('rent_deposit_add'), {'amount': '5000', 'date': old.isoformat()})
        self.assertFalse(Notification.objects.filter(recipient=self.owner).exists())

    def test_both_new_events_are_critical_so_they_reach_a_phone(self):
        for key in ('RENT_RATE_SET', 'DATED_BACK_PAST_LIMIT'):
            self.assertEqual(EVENTS[key].severity, CRITICAL, key)

    def test_a_far_back_row_is_found_in_change_history_not_marked_here(self):
        """
        The trace of a back-dated deposit lives in ONE place — Change
        History's Back-dated tab, permanent and in red past the limit — so this
        page carries no marks of its own (2026-09-24, the owners' call).
        """
        old = backdate_floor(timezone.localdate()) - timedelta(days=90)
        self.as_(self.owner).post(
            reverse('rent_deposit_add'), {'amount': '5000', 'date': old.isoformat()})
        tab = self.as_(self.other).get(reverse('backdated_history'))
        self.assertContains(tab, 'Rent deposit')
        self.assertContains(tab, 'class="hx-days is-past"', count=1)
        page = self.as_(self.owner).get(reverse('rent_home'), {'month': f"{old:%Y-%m}"})
        self.assertNotContains(page, 'added=recent')

    def test_the_person_who_did_it_is_told_which_month_it_landed_in(self):
        """The alert excludes the actor, so without this the one confirmation
        they DO see says nothing about the one thing that was unusual."""
        old = backdate_floor(timezone.localdate()) - timedelta(days=90)
        res = self.as_(self.owner).post(
            reverse('rent_deposit_add'),
            {'amount': '5000', 'date': old.isoformat()}, follow=True)
        said = ' '.join(str(m) for m in get_messages(res.wsgi_request))
        self.assertIn(f"filed under {old:%B %Y}", said)
        self.assertIn("every month since has moved", said)

    def test_an_ordinary_deposit_is_confirmed_without_that_sentence(self):
        res = self.as_(self.owner).post(
            reverse('rent_deposit_add'), {'amount': '2000'}, follow=True)
        said = ' '.join(str(m) for m in get_messages(res.wsgi_request))
        self.assertIn("Recorded ₹2,000 deposited.", said)
        self.assertNotIn("every month since", said)

    def test_the_alert_lands_on_a_page_that_shows_what_changed(self):
        old = backdate_floor(timezone.localdate()) - timedelta(days=90)
        self.as_(self.owner).post(
            reverse('rent_deposit_add'), {'amount': '5000', 'date': old.isoformat()})
        row = self.raised('DATED_BACK_PAST_LIMIT').first()
        self.assertEqual(self.as_(self.other).get(row.url).status_code, 200)


class DeletingADepositTests(_Signed):
    """
    ⚠ QUIET INSIDE OFFICE'S 24 HOURS, KEPT AND ANNOUNCED PAST THEM — the
    Cashbook's rule, applied here on 2026-09-24 (the owners' call). A delete
    inside the window is the same as typing the row right the first time; the
    control that matters is after it, where only an owner can act.
    """

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user('owner2', password='pw')
        self.other.groups.add(Group.objects.get(name='Owner'))
        today = timezone.localdate()
        _rate(today.year, today.month, '35000')
        self.entry = _deposit(today, '2000')
        Notification.objects.all().delete()

    def test_inside_the_24_hours_it_goes_and_nothing_is_kept_or_said(self):
        res = self.as_(self.office).post(
            reverse('rent_deposit_delete', args=[self.entry.pk]),
            {'reason': 'keyed twice'}, follow=True)
        self.assertEqual(RentDeposit.objects.count(), 0)
        self.assertFalse(DeletionLog.objects.exists())
        self.assertFalse(Notification.objects.exists())
        said = ' '.join(str(m) for m in get_messages(res.wsgi_request))
        self.assertIn('Deposit deleted.', said)

    def test_an_owner_inside_the_24_hours_is_quiet_too(self):
        """The line is the RECORD's age, never the person."""
        self.as_(self.owner).post(reverse('rent_deposit_delete', args=[self.entry.pk]))
        self.assertEqual(RentDeposit.objects.count(), 0)
        self.assertFalse(DeletionLog.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_office_is_refused_past_the_window_and_an_owner_is_not(self):
        _age(self.entry, OFFICE_WINDOW_HOURS // 24 + 1)   # past the 24 hours
        self.as_(self.office).post(reverse('rent_deposit_delete', args=[self.entry.pk]))
        self.assertEqual(RentDeposit.objects.count(), 1)
        self.as_(self.owner).post(reverse('rent_deposit_delete', args=[self.entry.pk]))
        self.assertEqual(RentDeposit.objects.count(), 0)

    def test_an_owner_past_the_window_is_logged_and_the_other_owner_told(self):
        _age(self.entry, 2)
        self.as_(self.owner).post(
            reverse('rent_deposit_delete', args=[self.entry.pk]), {'reason': 'keyed twice'})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_RENT_DEPOSIT)
        self.assertEqual(log.reason, 'keyed twice')
        self.assertEqual(log.amount, D('2000'))
        rows = Notification.objects.filter(event='RECORD_DELETED')
        self.assertEqual([r.recipient for r in rows], [self.other])

    def test_the_window_follows_the_KEYSTROKE_not_the_money_date(self):
        """Back-dating is normal here — the office keys a forgotten day later
        in the week — so a money-date window would refuse Office permission to
        delete a typo they made thirty seconds ago."""
        old = RentDeposit.objects.create(
            date=timezone.localdate() - timedelta(days=90), amount=D('1500'))
        self.as_(self.office).post(reverse('rent_deposit_delete', args=[old.pk]))
        self.assertFalse(RentDeposit.objects.filter(pk=old.pk).exists())

    def test_the_delete_asks_for_a_reason_only_when_it_will_be_kept(self):
        """A reason box whose value goes nowhere is a field silently dropped."""
        fresh = self.as_(self.owner).get(reverse('rent_home')).content.decode()
        self.assertIn('data-logged="0"', fresh)
        self.assertNotIn('data-logged="1"', fresh)
        _age(self.entry, 2)
        aged = self.as_(self.owner).get(reverse('rent_home')).content.decode()
        self.assertIn('data-logged="1"', aged)


class EditingADepositTests(_Signed):
    """
    ⚠ A DEPOSIT CAN BE EDITED — reversing "deliberately no edit" (2026-09-24,
    the owners' call). That rule's first reason was that a correction must
    never silently overwrite what was there, and Edit History now keeps every
    edit that matters. The same line as the delete beside it:

      * inside Office's 24 hours — anyone, quiet, not kept;
      * past them, or a date moved past the three-day limit — only an owner,
        kept in Edit History, and the other owner's phone;
      * moving a date EARLIER inside the limits still reaches the bell, exactly
        as keying it there would.
    """

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user('owner2', password='pw')
        self.other.groups.add(Group.objects.get(name='Owner'))
        self.today = timezone.localdate()
        _rate(self.today.year, self.today.month, '35000')
        self.entry = _deposit(self.today, '2000')
        Notification.objects.all().delete()

    def edit(self, user, **data):
        payload = {'amount': '2000', 'date': self.entry.date.isoformat()}
        payload.update(data)
        return self.as_(user).post(reverse('rent_deposit_edit', args=[self.entry.pk]), payload)

    def said(self, res):
        return ' '.join(str(m) for m in get_messages(res.wsgi_request))

    def test_office_corrects_the_amount_inside_the_24_hours_quietly(self):
        self.edit(self.office, amount='1500')
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, D('1500'))
        self.assertFalse(EditLog.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_an_owner_inside_the_24_hours_is_quiet_too(self):
        self.edit(self.owner, amount='1500')
        self.assertFalse(EditLog.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_office_is_refused_past_the_24_hours(self):
        _age(self.entry, 2)
        res = self.edit(self.office, amount='1500')
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, D('2000'))
        self.assertIn('ask an owner to change this one', self.said(res))

    def test_an_owner_past_the_24_hours_is_kept_and_reaches_the_other_phone(self):
        _age(self.entry, 2)
        self.edit(self.owner, amount='1500')
        log = EditLog.objects.get()
        self.assertEqual(log.entity_type, EditLog.ENTITY_RENT_DEPOSIT)
        self.assertEqual(log.changes, [
            {'field': 'Amount', 'kind': 'money', 'before': '2000.00', 'after': '1500.00'}])
        rows = Notification.objects.filter(event='OLD_RECORD_CHANGED')
        self.assertEqual([r.recipient for r in rows], [self.other])
        self.assertIn('₹1,500', rows[0].body)

    def test_the_edit_is_on_the_edited_tab(self):
        _age(self.entry, 2)
        self.edit(self.owner, amount='1500')
        tab = self.as_(self.other).get(reverse('edit_history'))
        self.assertContains(tab, 'Rent deposit')
        self.assertContains(tab, '1,500')

    def test_moving_a_date_earlier_inside_the_limits_rings_the_bell(self):
        yesterday = self.today - timedelta(days=1)
        self.edit(self.office, date=yesterday.isoformat())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.date, yesterday)
        self.assertFalse(EditLog.objects.exists())
        self.assertTrue(Notification.objects.filter(event='DATED_BACK').exists())
        self.assertFalse(Notification.objects.filter(event='DATED_BACK_PAST_LIMIT').exists())

    def test_office_cannot_move_a_date_past_the_limit(self):
        too_old = backdate_floor(self.today) - timedelta(days=1)
        self.edit(self.office, date=too_old.isoformat())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.date, self.today)

    def test_an_owner_moving_it_past_the_limit_is_kept_and_reaches_the_phone(self):
        too_old = backdate_floor(self.today) - timedelta(days=10)
        self.edit(self.owner, date=too_old.isoformat())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.date, too_old)
        self.assertEqual(EditLog.objects.get().changes[0]['field'], 'Date')
        self.assertEqual(Notification.objects.filter(event='OLD_RECORD_CHANGED').count(), 1)

    def test_an_untouched_date_is_never_held_to_the_limit(self):
        """
        ⚠ THE FLOOR MOVES EVERY NIGHT. A row Office keyed yesterday for the
        floor of yesterday is past today's floor by this morning — and holding
        a date nobody touched to the limit would refuse Office a correction to
        the AMOUNT, inside their own 24 hours.
        """
        self.entry.date = backdate_floor(self.today) - timedelta(days=1)
        self.entry.save()
        self.edit(self.office, amount='1800', date=self.entry.date.isoformat())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, D('1800'))

    def test_a_future_date_and_a_bad_amount_are_refused(self):
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        self.edit(self.office, date=tomorrow)
        for bad in ('', 'abc', '0.004', '-5', 'Infinity', 'NaN'):
            self.edit(self.office, amount=bad)
        self.entry.refresh_from_db()
        self.assertEqual((self.entry.amount, self.entry.date), (D('2000'), self.today))

    def test_a_note_is_never_history(self):
        """Money fields only: a corrected note is kept by nobody, even past
        the 24 hours, and a blank note stores NULL."""
        _age(self.entry, 2)
        self.edit(self.owner, note='second handover')
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.note, 'second handover')
        self.assertFalse(EditLog.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.edit(self.owner, note='   ')
        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.note)

    def test_a_payload_with_no_date_keeps_the_date(self):
        """Falling back to today would move the money on a correction that
        never asked to."""
        self.entry.date = self.today - timedelta(days=2)
        self.entry.save()
        self.as_(self.office).post(
            reverse('rent_deposit_edit', args=[self.entry.pk]), {'amount': '1900'})
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.date, self.today - timedelta(days=2))
        self.assertEqual(self.entry.amount, D('1900'))

    def test_a_deposit_moved_to_another_month_says_where_it_went(self):
        last_month = self.today.replace(day=1) - timedelta(days=1)
        res = self.edit(self.owner, date=last_month.isoformat())
        self.assertIn(f"moved to {last_month:%B %Y}", self.said(res))
        same = self.edit(self.owner, date=last_month.isoformat(), amount='2100')
        self.assertIn('Deposit updated.', self.said(same))

    def test_floor_cannot_edit_and_a_get_changes_nothing(self):
        res = self.edit(self.floor, amount='1')
        self.assertEqual(res.status_code, 403)
        self.as_(self.office).get(reverse('rent_deposit_edit', args=[self.entry.pk]))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, D('2000'))

    def test_the_menu_offers_edit_inside_the_window_and_says_why_outside_it(self):
        """A door somebody can see and cannot open is worse than no door, so
        a locked row keeps its menu and says why."""
        fresh = self.as_(self.office).get(reverse('rent_home'))
        self.assertContains(fresh, reverse('rent_deposit_edit', args=[self.entry.pk]))
        self.assertNotContains(fresh, 'Older than 24 hours')
        _age(self.entry, 2)
        locked = self.as_(self.office).get(reverse('rent_home'))
        self.assertNotContains(locked, reverse('rent_deposit_edit', args=[self.entry.pk]))
        self.assertContains(locked, 'Older than 24 hours')
        owner = self.as_(self.owner).get(reverse('rent_home'))
        self.assertContains(owner, reverse('rent_deposit_edit', args=[self.entry.pk]))


def _month_start(day, back=0):
    """The 1st of `day`'s month, `back` months earlier (negative goes forward)."""
    total = day.year * 12 + (day.month - 1) - back
    return date(total // 12, total % 12 + 1, 1)


def _month_end(day):
    return _month_start(day, back=-1) - timedelta(days=1)


class TheRentIsAnExpenseAndTheDepositIsCashTests(TestCase):
    """
    ⚠ THE ONE RULE THIS SECTION EXISTS TO PROTECT, now that it reaches the
    money math. The rent is what a month COST — a fixed figure, whatever cash
    happened to move. The deposits are how it gets PAID.

    Collapse them and monthly profit swings on a cash-flow decision: a month
    where the office had a good week would report a higher rent than a lean
    one, on the page the owners read to decide distribution. It is the app's
    fourth instance of a rule it already follows three times — wages dated by
    the salary month, a supplier payment that never touches profit while the
    stock draw does, a spare-shop payment that never touches profit while the
    fitted part does.

    Everything here is built relative to the REAL today, because the cap is:
    the assertions then hold whatever day the suite runs on.
    """

    def setUp(self):
        self.today = timezone.localdate()
        self.month = _month_start(self.today)

    # -- the expense side ---------------------------------------------------
    def test_the_rent_reaches_the_profit_page_as_its_own_line(self):
        _rate(self.month.year, self.month.month, '35000')
        rep = engine.build_profit_report(self.month, _month_end(self.today))
        line = next(l for l in rep['expense_lines'] if l['key'] == 'rent')
        self.assertEqual(line['amount'], D('35000'))
        self.assertEqual(rep['rent']['total'], D('35000'))
        self.assertEqual(rep['expense_total'], D('35000'))

    def test_A_DEPOSIT_STILL_MOVES_NO_PROFIT_FIGURE(self):
        """
        ⚠ THE INVARIANT THAT USED TO BE THE WHOLE BOUNDARY, and the single most
        important assertion in this file. Handing over cash is not a cost; the
        cost was the rent, and it was charged when the month began.
        """
        _rate(self.month.year, self.month.month, '35000')
        before = engine.build_profit_report(self.month, _month_end(self.today))
        _deposit(self.today, '20000')
        after = engine.build_profit_report(self.month, _month_end(self.today))
        self.assertEqual(before['expense_total'], after['expense_total'])
        self.assertEqual(before['profit'], after['profit'])
        self.assertEqual(before['rent']['total'], after['rent']['total'])

    def test_the_expense_lines_still_sum_to_the_expense_total(self):
        _rate(self.month.year, self.month.month, '35000')
        rep = engine.build_profit_report(self.month, _month_end(self.today))
        self.assertEqual(sum(l['amount'] for l in rep['expense_lines']),
                         rep['expense_total'])

    def test_the_owners_own_breakdown_still_lands_on_the_same_profit(self):
        """The card states the profit a second way, so it has to be handed the
        rent like every other shared figure — left out it would land Rs 35,000
        a month above the equation printed directly over it."""
        _rate(self.month.year, self.month.month, '35000')
        rep = engine.build_profit_report(self.month, _month_end(self.today))
        self.assertEqual(rep['earnings']['profit'], rep['profit'])
        self.assertIn('rent', [r['key'] for r in rep['earnings']['spend']])

    def test_the_trend_chart_still_totals_to_the_headline(self):
        """The chart is built from per-month figures and the headline from one
        sum, so rent has to be capped and dated identically in both. It is the
        same function called twice, which is why."""
        start = _month_start(self.today, 2)
        _rate(start.year, start.month, '35000')
        end = _month_end(self.today)
        rep = engine.build_profit_report(start, end)
        series = engine.monthly_series(start, end)
        self.assertEqual(sum(m['expenses'] for m in series), rep['expense_total'])
        self.assertEqual(sum(m['profit'] for m in series), rep['profit'])

    # -- the cap ------------------------------------------------------------
    def test_A_MONTH_THAT_HAS_NOT_HAPPENED_IS_NEVER_CHARGED(self):
        """
        ⚠ RENT IS THE ONLY STREAM THAT NEEDS THIS, because it is the only one
        not summed from rows: no row exists in the future, so every other
        stream is self-limiting. "This Year" resolves to 1 Jan - 31 Dec on
        purpose, so an uncapped walk would charge twelve months in September -
        Rs 1,05,000 of expense that has not happened, on the page profit
        distribution is decided from.
        """
        _rate(self.month.year, self.month.month, '35000')
        whole_year = engine.build_profit_report(
            date(self.today.year, 1, 1), date(self.today.year, 12, 31))
        # The rate starts THIS month, so exactly one month has happened under
        # it however far the window runs past today.
        self.assertEqual(whole_year['rent']['total'], D('35000'))
        self.assertEqual(whole_year['rent']['months'], 1)

    def test_a_window_entirely_in_the_future_charges_nothing(self):
        _rate(self.month.year, self.month.month, '35000')
        nxt = _month_start(self.today, back=-1)
        self.assertEqual(
            rent_calc.charged_between(nxt, nxt + timedelta(days=400)), D('0'))

    def test_a_month_is_charged_when_its_FIRST_falls_inside_the_window(self):
        """Salary's own rule (`SalaryPayment.month__range`), so the two monthly
        costs in this app are dated by one rule. Consequence, shared with
        wages: a window that does not begin on a 1st charges neither."""
        _rate(self.month.year, self.month.month, '35000')
        self.assertEqual(
            rent_calc.charged_between(self.month, _month_end(self.today)),
            D('35000'))
        self.assertEqual(
            rent_calc.charged_between(self.month + timedelta(days=1),
                                      _month_end(self.today)),
            D('0'))

    # -- repricing ----------------------------------------------------------
    def test_a_BACKDATED_rate_reprices_the_profit_of_the_months_it_covers(self):
        """The owner's own case: a hike agreed late and applied from an earlier
        month. It reprices those months, and now that rent is an expense it
        reprices their PROFIT — which is why the act is Owner-only, confirmed
        by name, and alerted to the other owner."""
        start = _month_start(self.today, 2)
        _rate(start.year, start.month, '35000')
        end = _month_end(self.today)
        before = engine.build_profit_report(start, end)
        self.assertEqual(before['rent']['total'], D('105000'))      # 3 x 35,000

        mid = _month_start(self.today, 1)
        _rate(mid.year, mid.month, '40000')                          # backdated
        after = engine.build_profit_report(start, end)
        self.assertEqual(after['rent']['total'], D('115000'))        # 35+40+40
        self.assertEqual(after['profit'], before['profit'] - D('10000'))

    # -- the cash side ------------------------------------------------------
    def test_A_DEPOSIT_IS_CASH_OUT_AND_ONLY_THAT(self):
        _rate(self.month.year, self.month.month, '35000')
        start, end = self.month, _month_end(self.today)
        before = engine.cash_position(start, end)
        _deposit(self.today, '2500')
        after = engine.cash_position(start, end)
        self.assertEqual(after['total_out'], before['total_out'] + D('2500'))
        row = next(r for r in after['money_out'] if r['label'] == 'Rent deposits')
        self.assertEqual(row['amount'], D('2500'))

    def test_the_cash_card_no_longer_calls_the_cashbook_line_rent(self):
        """Rent is its own line on that card now, on its own basis. One card
        naming the same cost twice is how an owner comes to read one of the two
        figures as the other."""
        labels = [r['label'] for r in engine.cash_position(
            self.month, _month_end(self.today))['money_out']]
        self.assertIn('Rent deposits', labels)
        self.assertNotIn('Rent, power, consumables', labels)

    def test_a_deposit_outside_the_window_is_not_counted_as_cash_in_it(self):
        _rate(self.month.year, self.month.month, '35000')
        _deposit(_month_start(self.today, 3), '9999')
        cash = engine.cash_position(self.month, _month_end(self.today))
        row = next(r for r in cash['money_out'] if r['label'] == 'Rent deposits')
        self.assertEqual(row['amount'], D('0'))

    # -- the position -------------------------------------------------------
    def test_the_tile_says_PAID_AHEAD_rather_than_printing_a_minus(self):
        """Every balance on that card can go the other way, and the sign is
        turned into words in the engine — the rule `financial_position` already
        follows for a shop in credit."""
        _rate(self.month.year, self.month.month, '35000')
        _deposit(self.today, '40000')
        rent = next(t for t in engine.financial_position()['tiles']
                    if 'Rent' in t['label'])
        self.assertEqual(rent['label'], 'Rent paid ahead')
        self.assertEqual(rent['amount'], D('5000'))
        self.assertEqual(rent['direction'], 'credit')

    def test_the_tile_says_STILL_TO_DEPOSIT_when_the_rent_is_short(self):
        _rate(self.month.year, self.month.month, '35000')
        _deposit(self.today, '7500')
        rent = next(t for t in engine.financial_position()['tiles']
                    if 'Rent' in t['label'])
        self.assertEqual(rent['label'], 'Rent still to deposit')
        self.assertEqual(rent['amount'], D('27500'))
        self.assertEqual(rent['direction'], 'out')

    def test_the_rent_tile_is_the_same_shape_as_every_other(self):
        """
        It shipped FULL WIDTH for a day, with the stock tile, because a
        row-by-row grid of five half-width tiles always orphans one. The
        owner's call was that the two read as odd slabs under four normal
        boxes — so the card splits by DIRECTION instead and every tile is one
        shape. Nothing in the Django suite executes CSS, so the absence of the
        flag is what is asserted.
        """
        _rate(self.month.year, self.month.month, '35000')
        tiles = engine.financial_position()['tiles']
        for t in tiles:
            with self.subTest(label=t['label']):
                self.assertNotIn('wide', t)
        rent = next(t for t in tiles if 'Rent' in t['label'])
        self.assertTrue(rent['note'])

    def test_WHAT_WE_OWE_IS_ONE_COLUMN_AND_EVERYTHING_ELSE_THE_OTHER(self):
        """
        The owner's instruction: green and blue on the left, red on the right.
        It also makes this card speak the same spatial language as CASH
        TRACKING directly above it, where money in is the left column and
        money out is the right.
        """
        _rate(self.month.year, self.month.month, '35000')
        held, owed = engine.financial_position()['tile_columns']
        self.assertEqual([t['direction'] for t in owed], ['out', 'out', 'out'])
        self.assertNotIn('out', [t['direction'] for t in held])
        self.assertIn('Rent still to deposit', [t['label'] for t in owed])
        self.assertIn('Stock on the shelf', [t['label'] for t in held])

    def test_the_held_column_comes_FIRST_which_is_the_phone_order(self):
        """On a phone the grid collapses to one column and the two wrappers
        stack in DOM order, so this is the only thing deciding that the owner
        reads what is theirs before what they owe."""
        _rate(self.month.year, self.month.month, '35000')
        held, _owed = engine.financial_position()['tile_columns']
        self.assertEqual(held[0]['label'], 'Customers owe us')

    def test_THE_COLUMNS_ARE_EXACTLY_THE_TILES_no_more_and_no_fewer(self):
        """
        ⚠ THE INVARIANT, because the failure is invisible: a tile dropped from
        both columns still leaves a card that looks perfectly correct, and a
        tile in both is a figure printed twice. `tile_columns` is derived from
        `tiles` in one expression precisely so this cannot drift.
        """
        _rate(self.month.year, self.month.month, '35000')
        pos = engine.financial_position()
        held, owed = pos['tile_columns']
        self.assertEqual(len(held) + len(owed), len(pos['tiles']))
        for t in pos['tiles']:
            with self.subTest(label=t['label']):
                self.assertEqual((t in held) + (t in owed), 1)

    def test_A_CREDIT_SITS_WITH_WHAT_IS_HELD_NOT_WITH_WHAT_IS_OWED(self):
        """
        A shop paid ahead is money in the workshop's FAVOUR and is not a debt,
        so listing it in a column of debts would be the sign already turned
        into words and then contradicted by where it sits. The rule is simply
        `out` is owed, everything else is not.
        """
        _rate(self.month.year, self.month.month, '35000')
        _deposit(self.today, '40000')             # paid ahead -> 'credit'
        held, owed = engine.financial_position()['tile_columns']
        self.assertIn('Rent paid ahead', [t['label'] for t in held])
        self.assertNotIn('Rent paid ahead', [t['label'] for t in owed])

    def test_the_payable_total_is_deliberately_left_alone(self):
        """That figure is computed and never rendered, because spare + supplies
        is not the whole debt — an unsettled month's wages are a payable
        nowhere. Adding rent would make it less incomplete without making it
        true, so the rent balance is its own key."""
        _rate(self.month.year, self.month.month, '35000')
        pos = engine.financial_position()
        self.assertEqual(pos['payable_total'],
                         pos['payable_spare'] + pos['payable_supplier'])
        self.assertEqual(pos['rent_due'], D('35000'))

    # -- All Time -----------------------------------------------------------
    def test_ALL_TIME_REACHES_THE_FIRST_RENT_MONTH(self):
        """
        ⚠ THE SALARY BUG, ONE STREAM OVER. A stream missing from
        `_DATE_STREAMS` is money the widest filter in the section cannot see.
        Measured before the rent streams were added: All Time opened on
        2026-02-07 against a ledger reaching back to October 2023, hiding
        Rs 10,15,000 of rent while claiming to cover everything.

        The RATE has to be in that list as well as the deposit: a rate's month
        is a 1st, and a month is charged only when its 1st is inside the
        window, so anchoring on a deposit dated the 5th would silently drop the
        first month's rent.
        """
        old = _month_start(self.today, 24)
        _rate(old.year, old.month, '35000')
        _deposit(old + timedelta(days=4), '1000')
        start, end, _key, _label = engine.resolve_period('all_time')
        self.assertEqual(start, old)
        rep = engine.build_profit_report(start, end)
        self.assertEqual(rep['rent']['months'], 25)      # 24 back, plus this one
        self.assertEqual(rep['rent']['total'], D('875000'))

    def test_a_window_reaching_back_before_the_ledger_SAYS_SO(self):
        """The opening-balance answer this app gives everywhere: the figure is
        short for the period before the section existed, and the page says so
        rather than reading as though the premises had been free."""
        _rate(self.month.year, self.month.month, '35000')
        rent = engine.rent_expense(_month_start(self.today, 6),
                                   _month_end(self.today))
        self.assertTrue(rent['reaches_before'])
        self.assertIn('only recorded from', rent['hint'])
        self.assertIn(self.month.strftime('%B %Y'), rent['hint'])

    def test_no_rate_at_all_charges_nothing_and_says_that_instead(self):
        rent = engine.rent_expense(self.month, _month_end(self.today))
        self.assertEqual(rent['total'], D('0'))
        self.assertFalse(rent['reaches_before'])
        self.assertEqual(rent['hint'], 'No rent recorded for this period')

    def test_a_rate_dated_AHEAD_changes_no_profit_figure_yet(self):
        """The one forward date the section allows, and it is safe because a
        rate moves no money by itself until its month arrives."""
        nxt = _month_start(self.today, back=-1)
        _rate(nxt.year, nxt.month, '40000')
        rep = engine.build_profit_report(self.month, _month_end(self.today))
        self.assertEqual(rep['rent']['total'], D('0'))


class ACashbookRowNamedLikeRentIsFlaggedNotFilteredTests(TestCase):
    """
    Rent used to arrive at the Profit page AS a Cashbook category. Now that it
    has its own line, a row still filed there under a rent-shaped name is the
    same money twice — exactly as a row called "Staff Salaries" is.

    ⚠ FLAGGED, NEVER FILTERED. The category is free text: "Rent agreement stamp
    paper" is a real running cost, and a view that silently dropped rows
    matching a word list would hide real money. The owner is told and moves it.
    """

    def setUp(self):
        self.today = timezone.localdate()
        self.month = _month_start(self.today)
        _rate(self.month.year, self.month.month, '35000')

    def _report(self):
        return engine.build_profit_report(self.month, _month_end(self.today))

    def test_the_row_is_named_and_the_money_is_still_counted(self):
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Rent',
                                     amount=D('45000'), date=self.today)
        rep = self._report()
        self.assertEqual(rep['cashbook']['rent_suspect_total'], D('45000'))
        self.assertEqual([r['category'] for r in rep['cashbook']['rent_suspects']],
                         ['Rent'])
        # nothing removed: the cashbook total still carries it
        self.assertEqual(rep['cashbook']['total'], D('45000'))

    def test_THE_ELECTRICITY_BILL_IS_NOT_ACCUSED(self):
        """
        ⚠ WORD BOUNDARIES, NEVER A SUBSTRING. A contains-check for "rent" also
        matches "cur" + "rent", and this workshop calls its electricity bill
        "Current bill" — so a substring match would flag the single most common
        row in the ledger and be ignored inside a week.
        """
        for benign in ('Current bill', 'current', 'Electricity',
                       'Rental car hire', 'Parent company fee', 'Torrent'):
            with self.subTest(category=benign):
                self.assertFalse(engine.looks_like_rent(benign))
        for named in ('Rent', 'rent', 'RENT', 'Rents', 'Workshop Rent',
                      'Rent deposit', 'Building rent'):
            with self.subTest(category=named):
                self.assertTrue(engine.looks_like_rent(named))

    def test_the_flag_reaches_deep_analysis_on_the_same_rule(self):
        """The two screens must never disagree about which row is the suspect
        one, so neither computes its own answer."""
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Rent',
                                     amount=D('45000'), date=self.today)
        rows = engine.cashbook_expense(self.month,
                                       _month_end(self.today))['by_category']
        flagged = [r['category'] for r in rows if r['looks_like_rent']]
        self.assertEqual(flagged, ['Rent'])

    def test_the_steer_and_the_flag_read_ONE_word_list(self):
        """The entry-time question and the after-the-fact warning must never
        come to mean different things — the rule the shop words already follow.
        The steer is deliberately broader by one pair of words, because it only
        ASKS and never blocks."""
        from workshop.cashbook_views import _steers
        row = next(r for r in _steers() if 'rent' in r['words'])
        self.assertTrue(set(engine.RENT_WORDS) <= set(row['words']))
        self.assertEqual(sorted(set(row['words']) - set(engine.RENT_WORDS)),
                         ['deposit', 'deposits'])


class ThePreGoLivePurgeClearsTheRentLedgerTests(TestCase):
    """
    ⚠ THE COMMAND THE GO-LIVE RUNBOOK SAYS TO RUN AGAINST PRODUCTION, and it
    missed three money tables until 2026-09-04 — all three added to the app
    after it was written. Anything it forgets is DEMO MONEY surviving into the
    real books, and it reports success either way.

    `OwnerWithdrawal` feeds `cash_position()`; `RentRate` and `RentDeposit` now
    feed the PROFIT EQUATION. On the development data that was Rs 12,60,000 of
    fabricated rent and Rs 12,32,500 of fabricated cash out.
    """

    def test_it_clears_rent_rates_deposits_and_owner_withdrawals(self):
        from io import StringIO

        from django.core.management import call_command
        owner = User.objects.create_user('purge_owner', password='pw')
        _rate(2026, 1, '35000')
        _deposit(date(2026, 1, 5), '2500')
        OwnerWithdrawal.objects.create(owner=owner, amount=D('5000'),
                                       date=date(2026, 1, 6))
        # The command reports what it deleted through `self.stdout` regardless
        # of verbosity, which is right for an operator and noise in a suite.
        call_command('purge_business_data', yes=True, stdout=StringIO())
        self.assertEqual(RentRate.objects.count(), 0)
        self.assertEqual(RentDeposit.objects.count(), 0)
        self.assertEqual(OwnerWithdrawal.objects.count(), 0)
        # ...and the login it was attributed to is NOT touched.
        self.assertTrue(User.objects.filter(pk=owner.pk).exists())

    def test_a_dry_run_still_deletes_nothing(self):
        from io import StringIO

        from django.core.management import call_command
        _rate(2026, 1, '35000')
        _deposit(date(2026, 1, 5), '2500')
        call_command('purge_business_data', stdout=StringIO())
        self.assertEqual(RentRate.objects.count(), 1)
        self.assertEqual(RentDeposit.objects.count(), 1)


class TheSameHandoverKeyedTwiceTests(_Signed):
    """
    ⚠ THE COMMONEST MONEY MISTAKE IN A WORKSHOP THIS SIZE, and nothing in the
    schema prevents it: one person keying in a rush, or two people recording
    the same handover because neither knew the other had.

    The collector comes ONCE a day, so a second entry on one date is the shape
    a double-key takes here — there is no name to key on the way the Cashbook
    has, and the AMOUNT is the wrong key, because the same handover keyed twice
    by two people is often typed slightly differently.

    It ASKS, never refuses: a morning and an evening handover is real. The day
    total in the log is what catches whatever slips through either way.
    """

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.month = today.replace(day=1)
        _rate(today.year, today.month, '35000')
        self.taken = today              # inside the three-day window the counts cover
        RentDeposit.objects.create(date=self.taken, amount=D('2000'))

    def counts(self):
        return self.as_(self.office).get(reverse('rent_home')).context['day_counts']

    def test_a_day_that_already_has_one_is_counted(self):
        self.assertEqual(self.counts().get(self.taken.isoformat()), 1)

    def test_a_day_with_nothing_on_it_is_absent_rather_than_zero(self):
        """Absent and zero mean the same thing to the browser, and absent is
        the smaller payload — a month of empty days would otherwise ride over
        on every page load."""
        empty = (self.taken - timedelta(days=1)).isoformat()
        self.assertNotIn(empty, self.counts())

    def test_it_counts_every_deposit_of_that_day_not_just_the_first(self):
        RentDeposit.objects.create(date=self.taken, amount=D('1500'))
        self.assertEqual(self.counts().get(self.taken.isoformat()), 2)

    def test_the_amount_is_NOT_part_of_the_key(self):
        """The same handover keyed twice is often typed slightly differently —
        ₹2,000 and ₹2,050 — so keying on the amount would miss exactly the
        case this exists for."""
        RentDeposit.objects.create(date=self.taken, amount=D('999'))
        self.assertEqual(self.counts().get(self.taken.isoformat()), 2)

    def test_the_counts_cover_the_date_the_FORM_can_pick_not_the_month_shown(self):
        """
        ⚠ THE BUG THE QUERY TEST FOUND. The log can be showing May while the
        date box still defaults to TODAY, so counts built from the VIEWED month
        found nothing for the date actually about to be submitted — the check
        silently doing nothing on exactly the page state where somebody is
        least sure what they are looking at.
        """
        old = self.month - timedelta(days=40)
        RentDeposit.objects.create(date=old, amount=D('1000'))
        looking_back = self.as_(self.office).get(
            reverse('rent_home'), {'month': f"{old:%Y-%m}"})
        # Viewing an old month, TODAY's own count is still there...
        self.assertIn(self.taken.isoformat(), looking_back.context['day_counts'])

    def test_it_costs_one_query_however_many_deposits_exist(self):
        """It is one query, not free — worth saying plainly. What must hold is
        that the cost does not grow with the data."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        c = self.as_(self.office)
        c.get(reverse('rent_home'))
        with CaptureQueriesContext(connection) as busy:
            c.get(reverse('rent_home'))
        for day in range(2, 12):
            RentDeposit.objects.create(
                date=self.month.replace(day=day), amount=D('500'))
        with CaptureQueriesContext(connection) as busier:
            c.get(reverse('rent_home'))
        self.assertEqual(len(busy), len(busier))

    def test_the_page_hands_the_counts_over_as_data(self):
        html = self.as_(self.office).get(reverse('rent_home')).content.decode()
        self.assertIn('id="rtDayCounts"', html)
        # ...and the back-date question is asked FIRST, because it is the
        # bigger fact: it moves a closed month and reaches the other owner.
        script = html.split('rtAddForm', 1)[1]
        # Anchored on the two cards' TITLES, which are what each question is
        # called on screen. It used to match a phrase out of the body copy, and
        # that broke the day both questions moved from `window.confirm()` into
        # the app's own confirmation card and were reworded — the rule was
        # untouched, only the words it was pinned to.
        self.assertLess(script.index('Date it this far back?'),
                        script.index('Another one for '))


class ThePageIsFourBlocksTests(_Signed):
    """
    The page rebuilt 2026-09-24 on the owners' ask — no clutter, no
    confusion, phone first: pay today, record, one month, month by month.
    Nothing in this suite executes CSS, so these hold the markup the layout
    hangs off and the facts each block may say; the widths were measured in a
    browser at 320, 375, 768 and 1280px.
    """

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.this_month = self.today.replace(day=1)
        _rate(2026, 1, '35000')

    def page(self, **params):
        return self.as_(self.office).get(reverse('rent_home'), params)

    def test_the_carry_is_said_once_and_only_when_it_is_not_square(self):
        """It used to be said twice, in two cards one above the other."""
        # Every finished month since the ledger started, paid to the rupee.
        month = date(2026, 1, 1)
        while month < self.this_month:
            _deposit(month.replace(day=5), '35000')
            month = rent_calc.shift_month(month, 1)
        self.assertNotContains(self.page(), 'from earlier months')
        _deposit(self.this_month - timedelta(days=1), '1300')
        self.assertContains(self.page(), '&#8377;1,300 paid ahead from earlier months', count=1)

    def test_the_card_says_this_month_rather_than_its_name(self):
        """The card is always about the current month, and the log under it
        can be showing another one — so it says "this month"."""
        self.assertContains(self.page(), 'paid this month')

    def test_a_covered_month_says_where_more_money_goes(self):
        """The collector still comes once the month is covered, and the office
        still records the cash — "Nothing due" must not read as "stop"."""
        _deposit(self.today, '999999')
        res = self.page()
        self.assertContains(res, 'Nothing due')
        self.assertContains(res, 'This month is fully paid. Anything handed over now counts toward next month.')

    def test_a_normal_ahead_or_behind_is_words_in_one_calm_colour(self):
        """Nearly every month ends a little over or under the rent — the
        normal rhythm of daily cash, in the owners' words — so neither
        direction is painted red or green; the words say which way."""
        _deposit(self.this_month - timedelta(days=1), '1000')
        html = self.page().content.decode()
        self.assertIn('<td class="rt-pos">Behind', html)
        self.assertIn('<div class="rt-hero-carry">', html)

    def test_the_record_form_is_the_shared_payment_row(self):
        """One shape across every payment form (the owners' call): the row
        scrolls sideways on a phone with Record last in it, exactly as the
        other three cards draw it — and the Note takes the shared width
        rather than a wider one, so the swipe is no longer than theirs."""
        html = self.page().content.decode()
        form = html.split('id="rtAddForm"', 1)[1].split('</form>', 1)[0]
        self.assertIn('class="rpay-scroll"', form)
        self.assertIn('class="rpay-row"', form)
        self.assertIn('class="rpay-field rpay-f-note"', form)
        self.assertLess(form.index('rpay-f-note'), form.index('rpay-btn'))
        self.assertNotIn('rpay-f-note {', html)

    def test_two_deposits_on_one_day_are_two_rows_of_that_date(self):
        _deposit(self.today, '1500')
        _deposit(self.today, '1500')
        res = self.page()
        self.assertEqual(len(res.context['rows']), 2)
        self.assertContains(res, f'{self.today:%a}, {self.today.day} {self.today:%b}', count=2)
        self.assertEqual(res.context['focus_total'], D('3000'))

    def test_who_keyed_a_row_is_inside_its_menu(self):
        RentDeposit.objects.create(date=self.today, amount=D('2000'),
                                   recorded_by=self.office, note='second handover')
        res = self.page()
        self.assertContains(res, 'Added by office1')
        self.assertContains(res, 'second handover')

    def test_a_months_rent_is_printed_only_where_it_differed(self):
        _rate(self.this_month.year, self.this_month.month, '40000')
        res = self.page()
        self.assertContains(res, 'rent &#8377;35,000')
        self.assertNotContains(res, 'rent &#8377;40,000')

    def test_the_month_in_progress_says_so_rather_than_a_figure(self):
        res = self.page()
        self.assertContains(res, 'In progress', count=1)

    def test_tracking_lives_in_change_history_not_here(self):
        """No Recently-added view and no row marks: Change History keeps every
        edit, delete and back-dating that only an owner could make."""
        old = RentDeposit.objects.create(date=self.this_month, amount=D('999'))
        _age(old, 2)
        html = self.page().content.decode()
        self.assertNotIn('added=recent', html)
        self.assertNotIn('Recently added', html)
