"""
HOW FAR BACK MONEY MAY BE FILED — the same rule on every screen that takes a
typed money date.

`is_future()` closed one end of the range years ago. This closes the other, and
it is the end where the damage is quiet: a figure dated FORWARD is caught the
moment somebody reads the period it lands in, while one dated three years BACK
rewrites a month nobody scrolls to and reports nothing at all.

⚠ THE FLOOR IS A CALENDAR MONTH, NEVER A DAY COUNT. A fixed "14 days" was the
obvious alternative and it breaks at exactly the moment the rule exists for:
the office reconciles LAST month against its books in the first days of THIS
one, so a gap found on the 3rd may belong to the 5th of last month. The month
boundary is the rhythm the work actually follows — the same lesson
`delete_window` records for measuring on `created_at` rather than the money
date.

⚠ IT BINDS OFFICE, NOT OWNERS. `delete_window`'s escalation, not a wall: an
owner keeps every route open because a go-live opening figure and an audit
correction are both legitimately older than the floor. What covers the owner is
that the act cannot happen silently — see `AnOwnerCannotDoItSILENTLYTests` in
`test_rent.py` for the section where that half is built.

These tests are deliberately shaped as ONE list of screens rather than one
class per section: the point of the rule living in `money_dates` is that every
screen answers it identically, and a per-section copy would be the drift this
is meant to prevent.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.models import (BulkPayer, CashbookEntry, FailedAttempt, SpareShop)
from workshop.money_dates import (BACKDATE_MONTHS, backdate_floor,
                                  is_too_far_back, too_far_back)

from inventory.models import SupplierShop


class TheFloorItselfTests(TestCase):
    """The rule, before any screen uses it."""

    def test_it_is_the_first_of_last_month(self):
        from datetime import date
        self.assertEqual(backdate_floor(today=date(2026, 9, 4)), date(2026, 8, 1))
        self.assertEqual(backdate_floor(today=date(2026, 9, 30)), date(2026, 8, 1))
        self.assertEqual(backdate_floor(today=date(2026, 1, 2)), date(2025, 12, 1))

    def test_it_holds_for_the_WHOLE_month_which_a_day_count_would_not(self):
        """
        On the 28th the 1st of last month is 58 days back and must still be
        reachable — the office is reconciling that month right now. A rolling
        14- or 30-day rule closed it weeks earlier, which is the correction the
        feature exists to keep easy.
        """
        from datetime import date
        for day in (1, 14, 28):
            self.assertFalse(is_too_far_back(date(2026, 8, 1), today=date(2026, 9, day)))
        self.assertTrue(is_too_far_back(date(2026, 7, 31), today=date(2026, 9, 1)))

    def test_it_crosses_a_year_boundary(self):
        from datetime import date
        self.assertFalse(is_too_far_back(date(2025, 12, 1), today=date(2026, 1, 20)))
        self.assertTrue(is_too_far_back(date(2025, 11, 30), today=date(2026, 1, 20)))

    def test_the_constant_is_read_not_restated(self):
        """The messages and the guard must never be able to name different
        numbers — the rule `OFFICE_DELETE_WINDOW_DAYS` already follows."""
        self.assertEqual(BACKDATE_MONTHS, 1)

    def test_an_owner_is_never_refused(self):
        from datetime import date
        owner = User.objects.create_superuser('o1', 'o1@x.com', 'pw')
        self.assertIsNone(too_far_back(date(2020, 1, 1), owner, "A payment"))

    def test_the_refusal_names_the_rule_and_the_route(self):
        from datetime import date
        Group.objects.get_or_create(name='Office')
        staff = User.objects.create_user('off1', password='pw')
        staff.groups.add(Group.objects.get(name='Office'))
        said = too_far_back(date(2020, 1, 1), staff, "A payment")
        floor = backdate_floor()
        self.assertIn(f"{floor.day} {floor:%B %Y}", said)
        self.assertIn("Ask an owner", said)

    def test_the_date_is_spelled_the_same_on_every_platform(self):
        """
        `%-d` is glibc and `%#d` is MSVC, so a strftime day-of-month code
        prints differently on the development machine and the server. The day
        is interpolated as a plain integer instead.
        """
        from datetime import date
        Group.objects.get_or_create(name='Office')
        staff = User.objects.create_user('off2', password='pw')
        staff.groups.add(Group.objects.get(name='Office'))
        said = too_far_back(date(2020, 1, 1), staff, "A payment",
                            today=date(2026, 9, 4))
        self.assertIn("1 August 2026", said)
        self.assertNotIn("01 August", said)


class _Screens(TestCase):
    """One Office login and one Owner login, plus the rows the forms need."""

    def setUp(self):
        FailedAttempt.objects.all().delete()
        for name in ('Owner', 'Office'):
            Group.objects.get_or_create(name=name)
        self.owner = User.objects.create_user('owner_bd', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.office = User.objects.create_user('office_bd', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.c = Client()

        self.floor = backdate_floor()
        self.ok = self.floor                       # the earliest allowed day
        self.old = self.floor - timedelta(days=1)  # one day past it

        self.spare_shop = SpareShop.objects.create(name='Backdate Spares')
        self.supplier = SupplierShop.objects.create(name='Backdate Supplies')
        self.fleet = BulkPayer.objects.create(customer_name='Backdate Fleet')

    def as_(self, user):
        self.c.force_login(user)
        return self.c

    def said(self, response):
        return ' '.join(str(m) for m in get_messages(response.wsgi_request))


class EveryMoneyDateFormAnswersTheSameRuleTests(_Screens):
    """
    ⚠ FIVE SCREENS, ONE RULE. Each of these took ANY past date before this —
    so a cashbook expense could be back-dated three years into a Profit period
    an owner had already read and distributed against, and a fleet receipt (the
    largest the workshop takes) into a closed month of Cash Tracking.
    """

    # ---- Cashbook: add -----------------------------------------------------

    def test_cashbook_add_refuses_office_past_the_floor(self):
        self.as_(self.office).post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE', 'category': 'Tea', 'amount': '100',
            'payment_method': 'CASH', 'date': self.old.isoformat()})
        self.assertEqual(CashbookEntry.objects.count(), 0)

    def test_cashbook_add_allows_office_on_the_floor_itself(self):
        self.as_(self.office).post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE', 'category': 'Tea', 'amount': '100',
            'payment_method': 'CASH', 'date': self.ok.isoformat()})
        self.assertEqual(CashbookEntry.objects.get().date, self.ok)

    def test_cashbook_add_allows_an_owner_anywhere(self):
        self.as_(self.owner).post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE', 'category': 'Tea', 'amount': '100',
            'payment_method': 'CASH', 'date': self.old.isoformat()})
        self.assertEqual(CashbookEntry.objects.get().date, self.old)

    # ---- Cashbook: edit ----------------------------------------------------

    def test_cashbook_edit_is_guarded_too_because_it_is_the_date_correcting_screen(self):
        """Moving an entry back into a closed month is the same act as filing
        one there, and this is the screen that exists to change a date."""
        entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Tea', amount=D('100'),
            payment_method='CASH', date=timezone.localdate())
        self.as_(self.office).post(reverse('manage_edit_cashbook_entry', args=[entry.pk]), {
            'entry_type': 'EXPENSE', 'category': 'Tea', 'amount': '100',
            'payment_method': 'CASH', 'date': self.old.isoformat()})
        entry.refresh_from_db()
        self.assertEqual(entry.date, timezone.localdate())

    # ---- Spare shop payment ------------------------------------------------

    def test_spare_shop_payment_refuses_office_past_the_floor(self):
        self.as_(self.office).post(
            reverse('spare_shop_pay', args=[self.spare_shop.pk]),
            {'lump_sum': '500', 'payment_method': 'CASH', 'date': self.old.isoformat()})
        self.assertEqual(self.spare_shop.payments.count(), 0)

    def test_spare_shop_payment_allows_an_owner(self):
        self.as_(self.owner).post(
            reverse('spare_shop_pay', args=[self.spare_shop.pk]),
            {'lump_sum': '500', 'payment_method': 'CASH', 'date': self.old.isoformat()})
        self.assertEqual(self.spare_shop.payments.get().date, self.old)

    # ---- Fleet payment -----------------------------------------------------

    def test_fleet_payment_refuses_office_past_the_floor(self):
        self.as_(self.office).post(
            reverse('bulk_payer_pay', args=[self.fleet.pk]),
            {'lump_sum': '5000', 'payment_method': 'UPI', 'date': self.old.isoformat()})
        self.assertEqual(self.fleet.payment_history.count(), 0)

    def test_fleet_payment_allows_an_owner(self):
        self.as_(self.owner).post(
            reverse('bulk_payer_pay', args=[self.fleet.pk]),
            {'lump_sum': '5000', 'payment_method': 'UPI', 'date': self.old.isoformat()})
        self.assertEqual(self.fleet.payment_history.get().date, self.old)

    # ---- Supplies Shop payment ---------------------------------------------

    def test_supplier_payment_refuses_office_past_the_floor(self):
        self.as_(self.office).post(
            reverse('add_shop_payment', args=[self.supplier.pk]),
            {'amount': '900', 'payment_method': 'CASH', 'date': self.old.isoformat()})
        self.assertEqual(self.supplier.payments.count(), 0)

    def test_supplier_payment_allows_an_owner(self):
        self.as_(self.owner).post(
            reverse('add_shop_payment', args=[self.supplier.pk]),
            {'amount': '900', 'payment_method': 'CASH', 'date': self.old.isoformat()})
        self.assertEqual(self.supplier.payments.get().date, self.old)


class TheBrowserSaysItBeforeTheButtonTests(_Screens):
    """
    The `min` attribute is PRESENTATION — every guard above is in the view and
    refuses a crafted POST that never rendered a box. It is here so Office is
    told at the picker instead of after the form is filled in, which is the
    "say it before the button" rule the advance date box already follows.
    """

    def setUp(self):
        super().setUp()
        # ⚠ THE PAYMENT CARD RENDERS ONLY WHILE MONEY IS OWED — both shop pages
        # gate on their balance, which is the rule that lets the card carry a
        # travelling light without becoming permanent furniture. A shop with no
        # purchases draws no form at all, so there would be no date box to
        # assert about and the test would pass for the wrong reason.
        self.spare_shop.total_purchased_amount = D('5000')
        self.spare_shop.save(update_fields=['total_purchased_amount'])
        self.supplier.total_billed_amount = D('5000')
        self.supplier.save(update_fields=['total_billed_amount'])

    def pages(self):
        return (
            reverse('cashbook'),
            reverse('spare_shop_detail', args=[self.spare_shop.pk]),
            reverse('bulk_payer_detail', args=[self.fleet.pk]),
            reverse('supplier_shop_detail', args=[self.supplier.pk]),
        )

    def test_office_gets_the_floor_on_every_money_date_box(self):
        for url in self.pages():
            res = self.as_(self.office).get(url)
            self.assertEqual(res.status_code, 200, url)
            self.assertEqual(res.context['floor_iso'], self.floor.isoformat(), url)
            self.assertIn(f'min="{self.floor.isoformat()}"', res.content.decode(), url)

    def test_an_owner_gets_no_floor_at_all(self):
        for url in self.pages():
            res = self.as_(self.owner).get(url)
            self.assertEqual(res.context['floor_iso'], '', url)
            self.assertNotIn(f'min="{self.floor.isoformat()}"', res.content.decode(), url)

    def test_the_cashbook_DATE_FILTER_is_never_floored(self):
        """
        ⚠ Reading last year is not filing money into it. The custom range
        pickers sit on the same page and were briefly given the floor by a
        blanket edit — which would have made the ledger's own history
        unreachable from its filter.
        """
        body = self.as_(self.office).get(reverse('cashbook')).content.decode()
        start = body[body.index('id="cbStart"'):]
        self.assertNotIn('min=', start[:start.index('>')])


class TheCashbookSteersAnEntryToTheSectionThatOwnsItTests(TestCase):
    """
    Three kinds of money have a dedicated section AND land wrong if they are
    typed into the Cashbook instead: wages are counted TWICE, an owner draw
    quietly CUTS reported profit, and a rent deposit is charged on top of the
    monthly rent. The ledger asks before taking one.

    ⚠ IT ASKS, IT NEVER BLOCKS, and there is no server guard at all — this
    catches a typo made in a rush, and a crafted POST is not that. The money
    rules themselves are unchanged and still live in the views.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user('sahad_test', password='pw')
        self.owner.first_name = 'Sahad'
        self.owner.save()
        self.owner.groups.add(Group.objects.get(name='Owner'))

    def matched(self, text):
        """The steer the browser would show, by the browser's own rule."""
        import re
        from workshop.cashbook_views import _steers
        for row in _steers():
            for word in row['words']:
                if re.search(r'\b' + re.escape(word) + r'\b', text, re.I):
                    return row
        return None

    def test_EVERY_STEER_ASKS_A_QUESTION_AND_NAMES_THE_CONSEQUENCE(self):
        """
        ⚠ THE SHAPE THE OWNER ASKED FOR, after the first attempt shipped as
        flat statements and they said it did not read as stopping them.
        "Sahad is an owner — money they took belongs in Owner Withdrawals" is
        a FACT, and a fact slides past somebody in a hurry. A question makes
        the reader answer it; naming what goes wrong is what makes answering
        worth the second it costs.
        """
        from workshop.cashbook_views import _steers
        for row in _steers():
            with self.subTest(words=row['words']):
                self.assertTrue(row['ask'].endswith('?'), row['ask'])
                self.assertLess(len(row['ask']), 42,
                                "the heading is scanned, not read")
                # ...and the reason says what breaks, in the reader's terms.
                # "on top of the rent" was a third alternative here until the
                # rent steer stopped needing it — see
                # test_RENT_AND_THE_DEPOSIT_NOW_GO_THE_SAME_WAY.
                self.assertRegex(row['why'], r'counted|profit look smaller')

    def test_the_owner_names_come_from_the_database_not_from_code(self):
        """
        "Sahad" and "Rijas" are who the owners happen to be today. A third
        owner, or one renamed, must be protected without a code change — the
        same reason `owner_accounts()` is the one answer to who the owners are.
        """
        self.assertIn('Sahad', (self.matched('Sahad 5000') or {}).get('ask', ''))
        self.owner.first_name = 'Nasrin'
        self.owner.save()
        self.assertIsNone(self.matched('Sahad 5000'))
        self.assertIn('Nasrin', (self.matched('Nasrin 5000') or {}).get('ask', ''))

    def test_the_rush_typo_the_owner_described_is_caught(self):
        self.assertIsNotNone(self.matched('Sahad 5000'))
        self.assertIsNotNone(self.matched('Staff salary'))
        self.assertIsNotNone(self.matched('Take out'))
        self.assertIsNotNone(self.matched('Deposit'))

    def test_WORD_BOUNDARIES_keep_the_commonest_entry_in_the_ledger_quiet(self):
        """
        ⚠ A substring match on "rent" also matches "cur-rent", and the
        electricity bill is called "Current bill" here — so a plain
        contains-check would question the single most common row in the ledger
        and be ignored inside a week.
        """
        for benign in ('Current bill', 'Electricity', 'Advanced diagnostics',
                       'Tea', 'Courier Charges', 'Water'):
            with self.subTest(category=benign):
                self.assertIsNone(self.matched(benign))

    def test_RENT_AND_THE_DEPOSIT_NOW_GO_THE_SAME_WAY(self):
        """
        ⚠ THE MESSAGE THAT COULD FINALLY BE SHORTENED, and the old comment
        predicted exactly this. It used to ask "Is this a rent DEPOSIT?" and
        had to: the plain wording would have been FALSE and would have cost
        ₹35,000 a month, because the monthly charge still reached the Profit
        page AS a Cashbook category while Deposit & Rent touched
        `analysis_engine` nowhere.

        Rent has its own expense line since 2026-09-04, read from the rate. So
        there is no exception left to explain and no distinction the reader has
        to hold: BOTH halves belong in Deposit & Rent, and either one typed
        here is counted twice.

        (The header comment above `CASHBOOK_STEERS` used to say the opposite of
        its own code — '"RENT" IS DELIBERATELY NOT IN THIS LIST' sitting
        directly above `(['rent', 'deposit'], …)`, both written in the same
        commit. The reasoning was sound and the code never matched it.)
        """
        said = self.matched('Rent')
        self.assertIsNotNone(said)
        self.assertIn('Deposit', said['why'])
        # ONE question for both halves, so a person keying either is asked the
        # same thing.
        self.assertEqual(said, self.matched('Deposit'))
        self.assertEqual(said['ask'], 'Is this rent?')
        self.assertIn('twice', said['why'])
        # ...and it no longer says the charge is welcome here.
        self.assertNotIn('on top of the rent', said['why'])

    def test_NO_STEER_EXPLAINS_WHEN_THE_CASHBOOK_IS_STILL_RIGHT(self):
        """
        ⚠ THE RENT STEER CARRIED AN EXCEPTION LINE FOR A REVISION AND IT WAS
        REMOVED. It read "The one monthly rent bill is still fine here" — true
        today, and the owner read it as "workshop rent is fine to add here",
        the opposite of the point. It was answering a question nobody had asked
        yet.

        The heading already disambiguates: somebody keying the monthly bill
        reads "Is this a rent DEPOSIT?", answers no, and carries on. A steer is
        a question and a consequence, and nothing else.
        """
        from workshop.cashbook_views import _steers
        for row in _steers():
            with self.subTest(words=row['words'][:3]):
                self.assertEqual(set(row) - {'words', 'ask', 'why'}, set())

    def test_the_SHOPS_are_named_from_the_database_like_the_owners(self):
        """
        ⚠ CLAUDE.md's own example of the double-count is "Paid Ninoos 20,000",
        and Ninoos is a row in a table, not a word in a source file. Paying a
        shop from the Cashbook is counted twice — once here, once against that
        shop's ledger — which the Profit page already warns about AFTER the
        fact via `_shoplike_cashbook_count`.
        """
        SpareShop.objects.create(name='Ninoos Auto')
        self.assertIsNotNone(self.matched('Ninoos Auto 20000'))
        # ...and the generic words come from `SHOP_WORDS`, imported rather than
        # restated, so the entry-time steer and the Profit page warning can
        # never come to mean different things.
        from workshop.analysis_engine import SHOP_WORDS
        from workshop.cashbook_views import _steers
        generic = next(r for r in _steers() if 'supplier' in r['words'])
        self.assertEqual(sorted(generic['words']), sorted(SHOP_WORDS))

    def test_a_very_short_shop_name_is_not_used_as_a_keyword(self):
        """A shop called "Oil" or "AC" would match half the ledger and make
        every steer noise. Four characters minimum, and the whole name."""
        SpareShop.objects.create(name='Oil')
        self.assertIsNone(self.matched('Oil filter'))

    def test_a_word_that_merely_contains_a_keyword_is_still_quiet(self):
        for benign in ('Current bill', 'Advanced diagnostics', 'Rental car hire'):
            with self.subTest(category=benign):
                self.assertIsNone(self.matched(benign))

    def test_the_page_hands_the_list_over_as_data_never_as_markup(self):
        """An owner's name is free text, so `json_script` rather than
        interpolation — the app's rule for handing data to JS."""
        Group.objects.get_or_create(name='Office')
        staff = User.objects.create_user('off_steer', password='pw')
        staff.groups.add(Group.objects.get(name='Office'))
        c = Client()
        c.force_login(staff)
        html = c.get(reverse('cashbook')).content.decode()
        self.assertIn('id="cbSteers"', html)
        self.assertIn('application/json', html.split('id="cbSteers"')[0][-200:])

    def test_THE_ADD_PATH_IS_HOOKED_BEFORE_THE_RECAP_NOT_ON_SUBMIT(self):
        """
        ⚠ THE BUG THIS SHIPPED WITH, found by the owner trying it: nothing
        happened on the one door people actually use.

        The Add control opens a recap modal whose confirm button calls
        `addForm.submit()` PROGRAMMATICALLY, and a programmatic `.submit()`
        FIRES NO SUBMIT EVENT — the trap CLAUDE.md already records for three
        other templates. A delegated `submit` listener therefore caught the
        edit screen and was silent on Add. The steer is asked inside
        `openAddConfirm()` instead, before the recap opens, so the two
        questions come in the right order and never stack.
        """
        Group.objects.get_or_create(name='Office')
        staff = User.objects.create_user('off_hook', password='pw')
        staff.groups.add(Group.objects.get(name='Office'))
        c = Client()
        c.force_login(staff)
        html = c.get(reverse('cashbook')).content.decode()

        # Asked before the recap opens...
        recap = html.split('function openAddConfirm', 1)[1].split("openModal('cbAddModal')", 1)[0]
        self.assertIn('steerFor(', recap)
        self.assertIn('askSteer(', recap)
        # ...and the delegated listener covers the EDIT form, which has a real
        # submit button and so does fire the event.
        self.assertIn("closest('#cbEditForm')", html)
        # ...and it asks in THIS page's modal, never window.confirm().
        self.assertIn('cbSteerModal', html)
        self.assertNotIn('window.confirm(say', html)
