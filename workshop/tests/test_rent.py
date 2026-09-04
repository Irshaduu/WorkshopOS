"""
DEPOSIT & RENT — the rent is what a month COST, the deposits are how it got PAID.

The section replaces a calculation the office does on paper every morning:
`(target − paid so far) ÷ days left`, against a book the collector writes in.
So the tests that matter most are the ones that put the office's own worked
examples in, verbatim, and check the page agrees — including the awkward one
the owner asked about, where the rent is raised in March with effect from
January and today's figure has to absorb three months of repricing at once.

The second group guards the boundary this section was deliberately built
inside: NOTHING here reaches `analysis_engine`. Rent still becomes an expense
the way it always has, through the Cashbook, so switching this on moves no
reported figure by a rupee. Moving rent onto its own expense line is a separate
change with real reach, and `test_nothing_here_touches_the_profit_report` is
what will fail loudly on the day somebody starts it — which is the point.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop import rent as rent_calc
from workshop.delete_window import OFFICE_DELETE_WINDOW_DAYS
from workshop.money_dates import backdate_floor, is_too_far_back
from workshop.models import (DeletionLog, FailedAttempt, Notification,
                             RentDeposit, RentRate)
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
        self.assertEqual(sum(len(b['rows']) for b in res.context['days']), 1)

    def test_junk_and_a_future_month_both_fall_back_to_this_one(self):
        for bad in ('', 'abc', '2026-13', '9999-01', '2026-3', 'x'):
            self.assertEqual(self.get(month=bad).context['focus'], self.this_month)

    def test_a_month_with_nothing_in_it_says_so_rather_than_looking_broken(self):
        res = self.get(month='2026-02')
        self.assertEqual(res.context['focus'], date(2026, 2, 1))
        self.assertEqual(res.context['days'], [])
        self.assertContains(res, 'Nothing deposited in February 2026')

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
        week_ago = (timezone.localdate() - timedelta(days=7)).isoformat()
        self.as_(self.office).post(
            reverse('rent_deposit_add'), {'amount': '2000', 'date': week_ago})
        self.assertEqual(RentDeposit.objects.get().date,
                         timezone.localdate() - timedelta(days=7))

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

    The floor is a CALENDAR MONTH, never a day count. A fixed "14 days" breaks
    at exactly the moment the rule exists for: the office reconciles last month
    against the collector's book in the first days of this one, so a gap found
    on the 3rd may belong to the 5th of last month.
    """

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        _rate(today.year, today.month, '35000')
        self.floor = backdate_floor(today)

    def post(self, user, when):
        return self.as_(user).post(
            reverse('rent_deposit_add'), {'amount': '2000', 'date': when.isoformat()})

    def test_office_may_reach_back_to_the_first_of_last_month(self):
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

    def test_the_window_holds_for_the_WHOLE_month_not_a_rolling_count(self):
        """On the 28th, the 1st of last month must still be reachable — a day
        count would have closed it weeks earlier."""
        late = date(2026, 9, 28)
        self.assertEqual(backdate_floor(late), date(2026, 8, 1))
        self.assertFalse(is_too_far_back(date(2026, 8, 1), today=late))
        self.assertTrue(is_too_far_back(date(2026, 7, 31), today=late))

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
        rows = self.raised('RENT_BACKDATED')
        self.assertEqual([r.recipient for r in rows], [self.other])
        self.assertIn('₹5,000', rows[0].body)
        self.assertIn(f"{old:%B %Y}", rows[0].body)

    def test_an_ordinary_deposit_raises_nothing(self):
        """Most days, every day. An alert here would be the noise that stops
        the ones that matter from being read."""
        self.as_(self.owner).post(reverse('rent_deposit_add'), {'amount': '2000'})
        self.assertEqual(self.raised('RENT_BACKDATED').count(), 0)

    def test_office_recording_inside_the_window_raises_nothing_either(self):
        self.as_(self.office).post(
            reverse('rent_deposit_add'),
            {'amount': '2000', 'date': backdate_floor(timezone.localdate()).isoformat()})
        self.assertEqual(self.raised('RENT_BACKDATED').count(), 0)

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
        for key in ('RENT_RATE_SET', 'RENT_BACKDATED'):
            self.assertEqual(EVENTS[key].severity, CRITICAL, key)

    def test_the_row_ITSELF_says_it_was_keyed_late_and_that_is_permanent(self):
        """
        ⚠ THE ANSWER TO "I CANNOT TRACK IT". The alert is a FEED — read rows
        are swept after 14 days — and `notify()` excludes the actor, so the
        person who back-dated an entry is the one person it never reaches. Both
        dates have been on the row since the first migration and nothing showed
        them: `date` is when the money moved, `created_at` is when somebody
        typed it, and a row whose two fall in different months is money filed
        into a month that had already closed.
        """
        old = date(2026, 5, 20)
        RentDeposit.objects.create(date=old, amount=D('5000'))
        res = self.as_(self.owner).get(reverse('rent_home'), {'month': '2026-05'})
        row = res.context['days'][0]['rows'][0]
        self.assertTrue(row.added_late)
        self.assertContains(res, 'added')
        self.assertContains(res, 'rt-late')

    def test_an_entry_keyed_in_its_own_month_carries_no_mark(self):
        """Keying yesterday's handover this morning is the ordinary case, and
        marking it would make the mark meaningless by the second row."""
        today = timezone.localdate()
        RentDeposit.objects.create(date=today - timedelta(days=1), amount=D('2000'))
        res = self.as_(self.owner).get(reverse('rent_home'))
        self.assertFalse(res.context['days'][0]['rows'][0].added_late)

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
        row = self.raised('RENT_BACKDATED').first()
        self.assertEqual(self.as_(self.other).get(row.url).status_code, 200)


class DeletingADepositTests(_Signed):

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        _rate(today.year, today.month, '35000')
        self.entry = _deposit(today, '2000')

    def test_office_may_delete_something_keyed_today_and_it_is_logged(self):
        self.as_(self.office).post(
            reverse('rent_deposit_delete', args=[self.entry.pk]), {'reason': 'keyed twice'})
        self.assertEqual(RentDeposit.objects.count(), 0)
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_RENT_DEPOSIT)
        self.assertEqual(log.reason, 'keyed twice')
        self.assertEqual(log.amount, D('2000'))

    def test_office_is_refused_past_the_window_and_an_owner_is_not(self):
        _age(self.entry, OFFICE_DELETE_WINDOW_DAYS + 1)
        self.as_(self.office).post(reverse('rent_deposit_delete', args=[self.entry.pk]))
        self.assertEqual(RentDeposit.objects.count(), 1)
        self.as_(self.owner).post(reverse('rent_deposit_delete', args=[self.entry.pk]))
        self.assertEqual(RentDeposit.objects.count(), 0)

    def test_the_window_follows_the_KEYSTROKE_not_the_money_date(self):
        """Back-dating is normal here — the office keys a forgotten day later
        in the week — so a money-date window would refuse Office permission to
        delete a typo they made thirty seconds ago."""
        old = RentDeposit.objects.create(
            date=timezone.localdate() - timedelta(days=90), amount=D('1500'))
        self.as_(self.office).post(reverse('rent_deposit_delete', args=[old.pk]))
        self.assertFalse(RentDeposit.objects.filter(pk=old.pk).exists())


class TheSectionStandsOnItsOwnTests(_Signed):
    """
    ⚠ THE BOUNDARY THIS WAS BUILT INSIDE. Rent still reaches the Profit page as
    a Cashbook category, exactly as it always has, so switching this section on
    changes no reported figure by a rupee. Moving rent onto an expense line of
    its own is a separate change touching the equation, the earnings card, All
    Time and the trend chart — and this test is what fails on the day it
    starts, which is deliberate: it should be a decision, not a side effect.
    """

    def test_nothing_here_touches_the_profit_report(self):
        from workshop import analysis_engine as engine
        _rate(2026, 9, '35000')
        before = engine.build_profit_report(date(2026, 9, 1), date(2026, 9, 30))
        _deposit(date(2026, 9, 10), '20000')
        after = engine.build_profit_report(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(before['expense_total'], after['expense_total'])
        self.assertEqual(before['profit'], after['profit'])

    def test_the_engine_does_not_import_the_rent_models(self):
        import inspect
        from workshop import analysis_engine as engine
        source = inspect.getsource(engine)
        self.assertNotIn('RentDeposit', source)
        self.assertNotIn('RentRate', source)


class TheDayTotalMakesADoubleEntryVisibleTests(_Signed):
    """
    The collector's book is the truth and this is a copy of it, so the
    realistic failure is one handover keyed twice — which quietly lowers
    today's figure and is invisible until month end. Two rows under one date,
    adding to a total that disagrees with the book, is what makes it findable.
    It is never blocked: two genuine handovers in one day are ordinary.
    """

    def test_two_deposits_on_one_day_are_grouped_under_one_dated_total(self):
        today = timezone.localdate()
        _rate(today.year, today.month, '35000')
        _deposit(today, '1500')
        _deposit(today, '1500')
        res = self.as_(self.office).get(reverse('rent_home'))
        blocks = res.context['days']
        self.assertEqual(len(blocks), 1)
        self.assertEqual(len(blocks[0]['rows']), 2)
        self.assertEqual(blocks[0]['total'], D('3000'))
