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
