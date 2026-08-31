"""
Owner Withdrawals — cash the owners take out of the business.

The tests are ordered by how much damage the rule prevents. The first class is
the whole reason the feature exists: a withdrawal is NOT an expense, so the
profit figure must not move by a single rupee when one is recorded. Everything
after it protects that, or protects the money getting in and out honestly.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop import analysis_engine as engine
from workshop.decorators import owner_accounts
from workshop.models import CashbookEntry, DeletionLog, Notification, OwnerWithdrawal


class WithdrawalTestBase(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')

        self.owner = User.objects.create_user(username='sahad', password='pw')
        self.owner.groups.add(self.owner_group)
        self.owner2 = User.objects.create_user(username='rijas', password='pw')
        self.owner2.groups.add(self.owner_group)

        self.office = User.objects.create_user(username='office', password='pw')
        self.office.groups.add(self.office_group)
        self.floor = User.objects.create_user(username='floor', password='pw')
        self.floor.groups.add(self.floor_group)

        self.today = timezone.localdate()
        self.month_start = self.today.replace(day=1)

    def as_owner(self):
        self.client.force_login(self.owner)

    def take(self, amount='50000', who=None, when=None, **extra):
        """Record one withdrawal directly, bypassing the view."""
        return OwnerWithdrawal.objects.create(
            owner=who or self.owner, amount=Decimal(amount),
            date=when or self.today, recorded_by=self.owner, **extra)


class AWithdrawalIsNotAnExpenseTests(WithdrawalTestBase):
    """
    THE RULE THE WHOLE FEATURE EXISTS FOR.

    Profit is what is available to take; taking it cannot reduce it. If a
    withdrawal ever reached `build_profit_report` the error would COMPOUND —
    profit falls, so the page reports less left to distribute, over money that
    has already been distributed, and the next distribution is decided from the
    smaller figure.
    """

    def test_recording_one_moves_no_figure_in_the_profit_report(self):
        # Some real business, so the report is not all zeros and a change would
        # actually show.
        CashbookEntry.objects.create(
            entry_type='INCOME', category='Scrap', amount=Decimal('9000'),
            date=self.today, created_by=self.owner)
        CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Electricity', amount=Decimal('2000'),
            date=self.today, created_by=self.owner)

        before = engine.build_profit_report(self.month_start, self.today)
        self.take('250000')
        after = engine.build_profit_report(self.month_start, self.today)

        self.assertEqual(
            repr(before), repr(after),
            "Recording a withdrawal changed the profit report. It is a "
            "distribution of profit, never a cost of earning it.")

    def test_the_word_never_appears_anywhere_in_the_profit_report(self):
        """A stronger form: not merely equal figures, but no line at all.

        Equality above would still pass if somebody added a withdrawals line
        that happened to be zero for this window.
        """
        self.take('250000')
        report = engine.build_profit_report(self.month_start, self.today)
        self.assertNotIn('withdraw', repr(report).lower())
        labels = [line.get('label', '') for line in report.get('expense_lines', [])]
        self.assertNotIn('Owner withdrawals', labels)

    def test_it_is_not_in_the_cashbook_either(self):
        """It has its own table precisely so it cannot land in the one ledger
        that DOES feed the profit equation as General Cashbook."""
        self.take('250000')
        self.assertEqual(CashbookEntry.objects.count(), 0)
        self.assertEqual(engine.cashbook_expense(self.month_start, self.today)['total'],
                         Decimal('0'))


class ItIsCashOutAndSaysSoOnceTests(WithdrawalTestBase):
    """It IS real cash leaving the drawer, in exactly one figure."""

    def test_it_appears_in_cash_tracking_money_out(self):
        self.take('75000')
        cash = engine.cash_position(self.month_start, self.today)
        line = [r for r in cash['money_out'] if r['label'] == 'Owner withdrawals']
        self.assertEqual(len(line), 1, "Expected exactly one owner-withdrawals line.")
        self.assertEqual(line[0]['amount'], Decimal('75000'))

    def test_the_money_out_total_moves_by_exactly_the_amount(self):
        before = engine.cash_position(self.month_start, self.today)['total_out']
        self.take('75000')
        after = engine.cash_position(self.month_start, self.today)['total_out']
        self.assertEqual(after - before, Decimal('75000'))

    def test_it_is_dated_by_the_day_the_money_moved_not_the_keystroke(self):
        """`date` is typed and `created_at` is the keystroke — the column
        `CashbookEntry.date` exists for, applied here."""
        last_month_end = self.month_start - timedelta(days=1)
        self.take('40000', when=last_month_end)
        # This month's window must not see it...
        this_month = engine.cash_position(self.month_start, self.today)
        self.assertEqual(
            [r['amount'] for r in this_month['money_out']
             if r['label'] == 'Owner withdrawals'][0], Decimal('0'))
        # ...and the window it was dated into must.
        prev = engine.cash_position(last_month_end.replace(day=1), last_month_end)
        self.assertEqual(
            [r['amount'] for r in prev['money_out']
             if r['label'] == 'Owner withdrawals'][0], Decimal('40000'))

    def test_all_time_reaches_a_withdrawal_older_than_every_other_stream(self):
        """`_DATE_STREAMS` anchors the widest filter.

        A stream missing from that list is money All Time cannot see — leaving
        salary out of it is what made All Time report the wage bill ₹1,22,167
        short. Nothing else in this test database holds any money, so the
        withdrawal is the ONLY thing that can set the lower bound.
        """
        long_ago = self.today - timedelta(days=900)
        self.take('60000', when=long_ago)
        start, end, key, _label = engine.resolve_period('all_time')
        self.assertEqual(key, 'all_time')
        self.assertLessEqual(start, long_ago,
                             "All Time opened after the earliest withdrawal.")
        cash = engine.cash_position(start, end)
        self.assertEqual(
            [r['amount'] for r in cash['money_out']
             if r['label'] == 'Owner withdrawals'][0], Decimal('60000'))


class OnlyAnOwnerCanSeeOrMoveThisMoneyTests(WithdrawalTestBase):
    """Owner-only end to end — the page describes and moves owner money."""

    def test_office_and_floor_are_refused_every_door(self):
        target = self.take('1000')
        doors = [
            reverse('withdrawal_home'),
            reverse('withdrawal_add'),
            reverse('withdrawal_delete', args=[target.pk]),
        ]
        for user in (self.office, self.floor):
            self.client.force_login(user)
            for door in doors:
                self.assertEqual(self.client.get(door).status_code, 403, door)
                self.assertEqual(self.client.post(door, {}).status_code, 403, door)

    def test_an_anonymous_visitor_gets_the_sign_in_page(self):
        r = self.client.get(reverse('withdrawal_home'))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.url.startswith('/login/'))

    def test_the_drawer_offers_it_to_an_owner_and_not_to_office(self):
        """A template gate must mirror its view's decorator — a door Office can
        see but not open is worse than no door."""
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('withdrawal_home')), '/withdrawals/')

        self.client.force_login(self.office)
        # The cashbook is a page Office CAN open, so the drawer renders there.
        body = self.client.get(reverse('cashbook')).content.decode()
        self.assertNotIn('/withdrawals/', body)

    def test_the_section_lights_the_manage_button(self):
        from workshop.templatetags.custom_filters import is_drawer_section
        self.assertTrue(is_drawer_section('/withdrawals/'))


class MoneyCannotBeAttributedToSomebodyWhoDidNotTakeItTests(WithdrawalTestBase):
    """Hiding a name from a <select> is presentation; the view is the control."""

    def test_a_crafted_post_cannot_attribute_a_withdrawal_to_a_non_owner(self):
        self.as_owner()
        self.client.post(reverse('withdrawal_add'), {
            'owner': self.floor.pk, 'amount': '5000',
            'date': self.today.isoformat(),
        })
        self.assertEqual(OwnerWithdrawal.objects.filter(owner=self.floor).count(), 0)
        self.assertEqual(OwnerWithdrawal.objects.count(), 0)

    def test_a_missing_owner_is_refused_rather_than_guessed(self):
        self.as_owner()
        self.client.post(reverse('withdrawal_add'),
                         {'amount': '5000', 'date': self.today.isoformat()})
        self.assertEqual(OwnerWithdrawal.objects.count(), 0)

    def test_owner_accounts_finds_a_superuser_carrying_no_group(self):
        """The either-or is load-bearing, and it is the same one `is_owner`
        uses. A reseeded database routinely leaves both owners
        `is_superuser=True` with an EMPTY Owner group until somebody runs
        `sync_owner_identity --yes`; group membership alone went dark that way
        on two demo deployments.
        """
        lone = User.objects.create_user(username='fresh', password='pw',
                                        is_superuser=True)
        self.assertIn(lone, list(owner_accounts()))

    def test_an_inactive_owner_is_not_offered(self):
        self.owner2.is_active = False
        self.owner2.save()
        self.assertNotIn(self.owner2, list(owner_accounts()))


class TheTypedFiguresGoThroughTheAppsOwnGuardsTests(WithdrawalTestBase):
    """`money.py` and `money_dates.py`, not a hand-rolled `Decimal(...) > 0`."""

    def post(self, **over):
        data = {'owner': self.owner.pk, 'amount': '1000',
                'date': self.today.isoformat()}
        data.update(over)
        return self.client.post(reverse('withdrawal_add'), data)

    def test_a_future_date_is_refused(self):
        self.as_owner()
        self.post(date=(self.today + timedelta(days=2)).isoformat())
        self.assertEqual(OwnerWithdrawal.objects.count(), 0)

    def test_infinity_and_nan_are_refused(self):
        """Both parse as valid Decimals and break a bare `> 0` guard in two
        different ways: Infinity IS greater than zero and gets written,
        poisoning every aggregate; an ordered comparison against NaN raises,
        which 500s the page."""
        self.as_owner()
        for bad in ('Infinity', '-Infinity', 'NaN', 'abc', '', '0', '-5'):
            self.post(amount=bad)
        self.assertEqual(OwnerWithdrawal.objects.count(), 0)

    def test_a_figure_too_wide_for_the_column_is_refused(self):
        """numeric(10,2) — SQLite stores it silently, Postgres 500s."""
        self.as_owner()
        self.post(amount='99999999999')
        self.assertEqual(OwnerWithdrawal.objects.count(), 0)

    def test_an_oversized_note_is_trimmed_rather_than_crashing(self):
        self.as_owner()
        self.post(note='x' * 900)
        row = OwnerWithdrawal.objects.get()
        self.assertEqual(len(row.note), 255)

    def test_a_blank_note_stores_NULL_not_an_empty_string(self):
        """Nobody wrote a note is a different fact from somebody writing
        nothing — the rule `BulkPaymentHistory.note` already follows."""
        self.as_owner()
        self.post(note='   ')
        self.assertIsNone(OwnerWithdrawal.objects.get().note)

    def test_an_unknown_payment_method_falls_back_to_cash(self):
        self.as_owner()
        self.post(payment_method='BITCOIN')
        self.assertEqual(OwnerWithdrawal.objects.get().payment_method, 'CASH')

    def test_a_good_one_is_written_with_everything_it_was_given(self):
        self.as_owner()
        yesterday = self.today - timedelta(days=1)
        self.post(amount='12500.50', payment_method='UPI', note='August share',
                  date=yesterday.isoformat())
        row = OwnerWithdrawal.objects.get()
        self.assertEqual(row.owner, self.owner)
        self.assertEqual(row.amount, Decimal('12500.50'))
        self.assertEqual(row.payment_method, 'UPI')
        self.assertEqual(row.note, 'August share')
        self.assertEqual(row.date, yesterday)
        self.assertEqual(row.recorded_by, self.owner)


class DeletingOneIsLoggedAndAnnouncedTests(WithdrawalTestBase):

    def test_it_is_written_to_deletion_history_with_a_reason(self):
        row = self.take('50000')
        self.as_owner()
        self.client.post(reverse('withdrawal_delete', args=[row.pk]),
                         {'reason': 'keyed twice'})
        self.assertFalse(OwnerWithdrawal.objects.filter(pk=row.pk).exists())
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_OWNER_WITHDRAWAL)
        self.assertEqual(log.amount, Decimal('50000'))
        self.assertEqual(log.reason, 'keyed twice')
        self.assertEqual(log.deleted_by, self.owner)

    def test_the_label_leads_with_the_subject_so_the_alert_is_a_statement(self):
        """`RECORD_DELETED`'s body is built from `entity_label`, and it has to
        read as a complete statement ending in what happened —
        "Sahad · ₹50,000 withdrawal deleted", never an arrow pointing at a verb.
        """
        row = self.take('50000')
        self.as_owner()
        self.client.post(reverse('withdrawal_delete', args=[row.pk]), {})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_OWNER_WITHDRAWAL)
        self.assertTrue(log.entity_label.startswith('sahad'),
                        f"Label should open with the owner: {log.entity_label!r}")
        self.assertIn('withdrawal', log.entity_label)

    def test_the_other_owner_is_told_and_the_actor_is_not(self):
        row = self.take('50000')
        self.as_owner()
        self.client.post(reverse('withdrawal_delete', args=[row.pk]), {})
        told = set(Notification.objects.filter(event='RECORD_DELETED')
                   .values_list('recipient_id', flat=True))
        self.assertIn(self.owner2.pk, told)
        self.assertNotIn(self.owner.pk, told)

    def test_a_reason_is_optional(self):
        row = self.take('50000')
        self.as_owner()
        self.client.post(reverse('withdrawal_delete', args=[row.pk]), {})
        self.assertFalse(OwnerWithdrawal.objects.filter(pk=row.pk).exists())

    def test_a_GET_deletes_nothing(self):
        row = self.take('50000')
        self.as_owner()
        self.client.get(reverse('withdrawal_delete', args=[row.pk]))
        self.assertTrue(OwnerWithdrawal.objects.filter(pk=row.pk).exists())


class ThePageAnswersTheOwnersQuestionTests(WithdrawalTestBase):

    def test_every_owner_gets_a_card_even_with_nothing_taken(self):
        """₹0 is honest here in a way it is not on the fleet table: an owner
        exists for the whole period, so "took nothing" is a fact about them
        rather than a claim about a period they were not in. A missing card
        would read as a missing owner."""
        self.take('50000', who=self.owner)
        self.as_owner()
        cards = self.client.get(reverse('withdrawal_home')).context['cards']
        self.assertEqual(len(cards), 2)
        quiet = [c for c in cards if c['pk'] == self.owner2.pk][0]
        self.assertEqual(quiet['total'], Decimal('0'))
        self.assertEqual(quiet['count'], 0)

    def test_the_two_totals_are_printed_and_never_netted(self):
        """What a GAP between two owners means depends on the partnership
        split, and this system does not hold one. Same rule as "what we owe and
        what we hold sit together and are never netted": print both, let the
        owner do the reading.
        """
        self.take('80000', who=self.owner)
        self.take('30000', who=self.owner2)
        self.as_owner()
        r = self.client.get(reverse('withdrawal_home'))
        self.assertEqual(r.context['period_total'], Decimal('110000'))
        body = r.content.decode()
        # The difference (50,000) must appear nowhere.
        self.assertNotIn('50,000', body)

    def test_a_chip_narrows_the_history_and_never_the_cards(self):
        self.take('80000', who=self.owner)
        self.take('30000', who=self.owner2)
        self.as_owner()
        r = self.client.get(reverse('withdrawal_home'), {'who': self.owner2.pk})
        self.assertEqual(len(r.context['withdrawals']), 1)
        self.assertEqual(r.context['withdrawals'][0].owner, self.owner2)
        # Both cards stay, so the comparison never leaves the screen.
        self.assertEqual(len(r.context['cards']), 2)
        self.assertEqual(r.context['period_total'], Decimal('110000'))

    def test_the_chip_row_states_every_option_at_all_times(self):
        """It replaced a link that rendered only WHILE a filter was on, so the
        way back was visible and the way in was not. The Cashbook settled this
        shape for exactly this job: one chip per option, each carrying its
        count, whichever is active."""
        self.take('80000', who=self.owner)
        self.take('30000', who=self.owner2)
        self.as_owner()
        for query in ({}, {'who': self.owner2.pk}):
            body = self.client.get(reverse('withdrawal_home'), query).content.decode()
            # Scoped to the row itself: `.wd-chip.is-active` is declared twice
            # in the page's own <style>, so a whole-page count finds three.
            row = body[body.index('class="wd-chips"'):]
            row = row[:row.index('</div>', row.index('wd-chip'))]
            self.assertEqual(row.count('class="wd-chip '), 3,
                             f"Expected Everyone + one per owner ({query}).")
            self.assertEqual(row.count('is-active'), 1,
                             f"Exactly one chip may be active ({query}).")

    def test_each_owner_keeps_one_colour_across_the_card_the_chip_and_the_rows(self):
        """A list of two people has to read as two people without anybody
        reading a name — so the colour is decided once, in the view, and used
        in all three places rather than picked per template."""
        self.take('80000', who=self.owner)
        self.take('30000', who=self.owner2)
        self.as_owner()
        r = self.client.get(reverse('withdrawal_home'))
        tints = {c['pk']: c['tint'] for c in r.context['cards']}
        self.assertEqual(len(set(tints.values())), 2, "Two owners, two colours.")
        for row in r.context['withdrawals']:
            self.assertEqual(row.tint, tints[row.owner_id])
        # Every colour reaches the page as a custom property.
        body = r.content.decode()
        for tint in tints.values():
            self.assertIn(f'--tint: {tint}', body)

    def test_no_owner_colour_is_red_or_green(self):
        """Both are spoken for app-wide as the DIRECTION of money, and this
        page prints a red amount on every row — an owner who happened to be
        red would read as the urgent one."""
        from workshop.views.withdrawal import OWNER_TINTS
        for tint in OWNER_TINTS:
            r, g, b = (int(tint[i:i + 2], 16) for i in (1, 3, 5))
            self.assertFalse(r > 150 and g < 110 and b < 110, f"{tint} reads as red")
            self.assertFalse(g > 150 and r < 110 and b < 130, f"{tint} reads as green")

    def test_a_crafted_who_shows_everybody_rather_than_an_empty_page(self):
        """A blank list under a heading naming a period reads as "nothing
        happened", which would be a lie."""
        self.take('80000')
        self.as_owner()
        for bad in ('99999', 'abc', str(self.floor.pk), ''):
            r = self.client.get(reverse('withdrawal_home'), {'who': bad})
            self.assertIsNone(r.context['who'], bad)
            self.assertEqual(len(r.context['withdrawals']), 1, bad)

    def test_it_uses_the_profit_pages_date_vocabulary_not_the_daily_lists(self):
        """Owner money is taken a handful of times a month, so Today and This
        Week would return an empty page nearly every time — which reads as a
        broken screen rather than a quiet period."""
        self.as_owner()
        keys = [k for k, _ in
                self.client.get(reverse('withdrawal_home')).context['period_choices']]
        self.assertIn('all_time', keys)
        self.assertNotIn('today', keys)
        self.assertNotIn('this_week', keys)

    def test_recording_returns_to_the_period_that_was_being_read(self):
        """The period changing under somebody who did not ask for that is how a
        page stops being trusted."""
        self.as_owner()
        r = self.client.post(reverse('withdrawal_add'), {
            'owner': self.owner.pk, 'amount': '1000',
            'date': self.today.isoformat(), 'back': '?range=last_year',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/withdrawals/?range=last_year')

    def test_a_hostile_back_value_cannot_redirect_off_site(self):
        self.as_owner()
        r = self.client.post(reverse('withdrawal_add'), {
            'owner': self.owner.pk, 'amount': '1000',
            'date': self.today.isoformat(), 'back': 'https://evil.example/x',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse('withdrawal_home'))

    def test_no_owner_is_preselected_in_the_picker(self):
        """Whoever opens this page is not necessarily whoever took the money.

        A picker that opens on a name files the money against that name for
        anybody who does not look — and WHO took it is the one field on the row
        that the figures themselves can never catch afterwards. So the select
        opens on a valueless option and both the browser and the view refuse
        without it.
        """
        self.as_owner()
        body = self.client.get(reverse('withdrawal_home')).content.decode()
        picker = body[body.index('id="wdOwner"'):]
        picker = picker[:picker.index('</select>')]
        self.assertIn('value="" disabled selected', picker,
                      "The owner picker must open on nothing chosen.")
        # No real owner may carry `selected`.
        for person in (self.owner, self.owner2):
            self.assertNotIn(f'value="{person.pk}" selected', picker)

    def test_the_heading_echoes_the_choice_rather_than_making_it(self):
        """The chip is a mirror, never a second control — two things setting
        one value is how they start disagreeing — and it says NOTHING until
        there is something to say. A placeholder announcing that nothing has
        been chosen is a second telling of what the picker already says."""
        self.as_owner()
        body = self.client.get(reverse('withdrawal_home')).content.decode()
        head = body[body.index('rpay-head'):]
        head = head[:head.index('rpay-body')]
        self.assertIn('id="wdOwnerEcho"', head)
        self.assertIn('hidden', head)
        self.assertNotIn('<select', head,
                         "The heading must not carry a second owner control.")
        # It ships with no text of its own — the script fills it in.
        echo = head[head.index('id="wdOwnerEcho"'):]
        self.assertTrue(echo[echo.index('>') + 1:].lstrip().startswith('</span>'),
                        "The echo chip must render empty.")

    def test_the_page_says_it_is_not_an_expense_where_the_amount_is_typed(self):
        """The most important sentence in the feature, and it sits under the box
        somebody is about to type into rather than in a heading already
        scrolled past."""
        self.as_owner()
        body = self.client.get(reverse('withdrawal_home')).content.decode()
        self.assertIn('Not a business expense', body)
        self.assertIn('profit does not change', body)


class TheHistoryListCanAlwaysBeActedOnTests(WithdrawalTestBase):
    """
    ⚠ A ROUNDED LIST HOLDING A DROPDOWN MUST NOT CLIP.

    Popper cannot escape a clipping ancestor, and it fails invisibly AND only
    sometimes: with several rows the ⋮ menu flips upward and stays inside the
    box, so it looks perfectly correct. Measured in a browser with ONE row, 33px
    of a 44px menu was cut off — putting the only delete there is out of reach
    on exactly the list that has one thing to delete.

    Nothing in the Django suite executes CSS, so this asserts the DECLARATION
    that caused it. It is the closest a template test can get, and it is worth
    having: the failure mode is silent everywhere else.
    """

    def test_the_history_list_is_not_a_clipping_ancestor(self):
        import re
        from pathlib import Path
        from django.conf import settings

        path = (Path(settings.BASE_DIR) / 'workshop' / 'templates' / 'workshop'
                / 'withdrawals' / 'withdrawal_home.html')
        css = path.read_text(encoding='utf-8')
        block = re.search(r'\.wd-list\s*\{([^}]*)\}', css)
        self.assertIsNotNone(block, "Could not find the .wd-list rule.")
        self.assertNotIn('overflow', block.group(1),
                         "`.wd-list` holds a ⋮ dropdown, so it must not clip. "
                         "Round the corners on the rows instead.")
