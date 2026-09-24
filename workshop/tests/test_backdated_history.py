"""
THE BACK-DATED TAB — money typed in on a later day than it moved
(2026-09-24, the owners' request: "back date adding is not normal").

The third tab of the history page, beside Deleted and Edited. ⚠ NOTHING IS
STORED FOR IT: every money table already keeps `date` (when the money moved)
and `created_at` (when somebody typed it), and a back-dated row loses neither —
so a second copy would only be a second answer free to drift. What these tests
hold is that the tab reads those two dates the way the rest of the app does:

  * a row is listed when the day it was TYPED (in IST) is after its money date;
  * red is `filed_past_limit` — the three-day limit AS AT THE DAY IT WAS TYPED,
    the same predicate that refused Office and tiered the alert at that moment;
  * all seven money tables whose date can be typed are read;
  * the three payment ledgers now say WHO keyed a payment (`recorded_by`), and
    a row keyed before that column existed says "unknown", never a guess.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from inventory.models import SupplierPayment, SupplierShop
from workshop.models import (BulkPayer, BulkPaymentHistory, CashbookEntry, Mechanic,
                             OwnerWithdrawal, RentDeposit, SalaryAdvance, SpareShop,
                             SpareShopPayment)
from workshop.tests.test_money_change_rules import _age, _People
from workshop.views.deletion_history import BACKDATED_SOURCES, _bounds, backdated_rows


def _this_month(today):
    return _bounds(today.replace(day=1), today)


class _Tab(_People):

    def setUp(self):
        super().setUp()
        self.url = reverse('backdated_history')

    def cash(self, days_back, **kw):
        return CashbookEntry.objects.create(
            entry_type='EXPENSE', category=kw.pop('category', 'Petrol'),
            amount=kw.pop('amount', D('500')), payment_method='CASH',
            created_by=kw.pop('by', self.office),
            date=self.today - timedelta(days=days_back))

    def rows(self, **kw):
        start, end = _this_month(self.today)
        return backdated_rows(start, end, **kw)


class WhatCountsAsBackDatedTests(_Tab):

    def test_a_row_typed_on_its_own_day_is_not_listed(self):
        self.cash(0)
        self.assertEqual(self.rows(), [])

    def test_a_row_typed_after_its_money_date_is_listed_with_how_far_back(self):
        self.cash(2, category='Diesel')
        (row,) = self.rows()
        self.assertEqual(row['title'], 'Diesel')
        self.assertEqual(row['days_back'], 2)
        self.assertEqual(row['who'], 'mc_office')
        self.assertFalse(row['past_limit'], 'two days back is inside the limit')

    def test_past_the_limit_is_red(self):
        self.cash(5)
        self.assertTrue(self.rows()[0]['past_limit'])

    def test_the_red_mark_is_judged_as_at_the_day_it_was_TYPED(self):
        """
        Typed 8 days ago for a day 10 days ago: two days back when it was
        typed, so NOT past the limit — even though its money date is now ten
        days old. Judging against today would creep a mark onto every row as
        the weeks passed.
        """
        entry = self.cash(10)
        _age(entry, days=8)
        first = (self.today - timedelta(days=40)).replace(day=1)
        (row,) = backdated_rows(*_bounds(first, self.today))
        self.assertEqual(row['days_back'], 2)
        self.assertFalse(row['past_limit'])

    def test_newest_keystroke_first(self):
        # Two days back, so a minute's ageing can never pull the older row onto
        # its own money day just after midnight.
        older = self.cash(2, category='Older')
        _age(older, minutes=1)
        self.cash(2, category='Newer')
        self.assertEqual([r['title'] for r in self.rows()], ['Newer', 'Older'])


class EveryMoneyTableIsReadTests(_Tab):

    def test_all_seven_tables_whose_date_can_be_typed(self):
        yesterday = self.today - timedelta(days=1)
        mech = Mechanic.objects.create(name='Amlah')
        self.cash(1)
        RentDeposit.objects.create(date=yesterday, amount=D('2000'), recorded_by=self.office)
        SpareShopPayment.objects.create(
            shop=SpareShop.objects.create(name='Spare Club'), amount=D('900'),
            payment_method='CASH', date=yesterday)
        SupplierPayment.objects.create(
            supplier=SupplierShop.objects.create(name='Fluid Manjeri'), amount=D('900'),
            payment_method='CASH', date=yesterday)
        BulkPaymentHistory.objects.create(
            bulk_payer=BulkPayer.objects.create(customer_name='Hafsi'), amount=D('9000'),
            jobs_affected=0, details='[]', date=yesterday)
        SalaryAdvance.objects.create(staff=mech, amount=D('1000'), date=yesterday)
        OwnerWithdrawal.objects.create(owner=self.owner, amount=D('5000'), date=yesterday)

        kinds = {r['source'] for r in self.rows()}
        self.assertEqual(kinds, {key for key, *_ in BACKDATED_SOURCES})
        self.assertEqual(len(kinds), 7)

    def test_the_type_filter_narrows_it(self):
        self.cash(1)
        RentDeposit.objects.create(date=self.today - timedelta(days=1), amount=D('2000'))
        self.assertEqual({r['source'] for r in self.rows(kind='RENT_DEPOSIT')}, {'RENT_DEPOSIT'})


class ThePaymentLedgersSayWhoKeyedThemTests(_Tab):
    """`recorded_by` on the three ledgers that had no "who" at all."""

    def test_the_three_payment_screens_save_who_keyed_the_payment(self):
        yesterday = (self.today - timedelta(days=1)).isoformat()
        spare = SpareShop.objects.create(name='Spare Club')
        supplier = SupplierShop.objects.create(name='Fluid Manjeri')
        fleet = BulkPayer.objects.create(customer_name='Hafsi')
        c = self.as_(self.office)
        c.post(reverse('spare_shop_pay', args=[spare.pk]),
               {'lump_sum': '500', 'payment_method': 'CASH', 'date': yesterday})
        c.post(reverse('add_shop_payment', args=[supplier.pk]),
               {'amount': '900', 'payment_method': 'CASH', 'date': yesterday})
        c.post(reverse('bulk_payer_pay', args=[fleet.pk]),
               {'lump_sum': '5000', 'payment_method': 'UPI', 'date': yesterday})
        self.assertEqual(spare.payments.get().recorded_by, self.office)
        self.assertEqual(supplier.payments.get().recorded_by, self.office)
        self.assertEqual(fleet.payment_history.get().recorded_by, self.office)
        self.assertEqual({r['who'] for r in self.rows()}, {'mc_office'})

    def test_a_row_keyed_before_the_column_existed_says_unknown(self):
        SpareShopPayment.objects.create(
            shop=SpareShop.objects.create(name='Spare Club'), amount=D('900'),
            payment_method='CASH', date=self.today - timedelta(days=1))
        self.assertEqual(self.rows()[0]['who'], '')
        self.assertContains(self.as_(self.owner).get(self.url), 'unknown')


class TheBackdatedTabTests(_Tab):

    def test_it_is_owner_only_like_the_other_two_tabs(self):
        self.assertEqual(self.as_(self.office).get(self.url).status_code, 403)
        self.assertEqual(self.as_(self.owner).get(self.url).status_code, 200)

    def test_a_row_says_when_it_was_dated_and_marks_past_the_limit(self):
        self.cash(2, category='Diesel')
        self.cash(6, category='Tyres', by=self.owner)
        res = self.as_(self.owner).get(self.url)
        self.assertContains(res, 'class="hx-row"', count=2)
        self.assertContains(res, '2 days back')
        self.assertContains(res, '6 days back')
        self.assertContains(res, 'class="hx-days is-past"', count=1)
        self.assertContains(res, '<h1><i class="bi bi-clock-history"></i>Change History</h1>')

    def test_a_title_is_never_followed_by_its_own_type(self):
        """ "Rent deposit" over "Rent Deposit" said one fact twice."""
        RentDeposit.objects.create(date=self.today - timedelta(days=1), amount=D('2000'))
        self.cash(1, category='Diesel')
        by_title = {r['title']: r['kind'] for r in self.rows()}
        self.assertEqual(by_title['Rent deposit'], '')
        self.assertEqual(by_title['Diesel'], 'Cashbook Entry')

    def test_the_month_is_the_month_it_was_TYPED(self):
        """A row typed last month for the month before is filed under last
        month here — the question is when somebody reached back, not where
        the money landed."""
        # Typed 35 days ago — always an earlier calendar month than today's,
        # since no month is longer than 31 days.
        entry = self.cash(40, category='Old one')
        _age(entry, days=35)
        typed = timezone.localtime(entry.created_at).date()
        here = self.as_(self.owner).get(self.url, {'month': typed.strftime('%Y-%m')})
        self.assertContains(here, 'Old one')
        self.assertNotContains(self.as_(self.owner).get(self.url), 'Old one')

    def test_a_future_or_unreadable_month_falls_back_to_this_one(self):
        this = self.today.strftime('%B %Y')
        # '0001-01' used to 500: the month before it does not exist, and its
        # midnight cannot be converted to UTC.
        for raw in ('2099-01', 'banana', '2026-13', '0001-01', '1999-12'):
            res = self.as_(self.owner).get(self.url, {'month': raw})
            self.assertEqual(res.context['month'], self.today.replace(day=1), raw)
            self.assertContains(res, this)

    def test_there_is_no_next_step_from_this_month(self):
        res = self.as_(self.owner).get(self.url)
        self.assertIsNone(res.context['next_month'])
        self.assertNotContains(res, 'aria-label="Next month"')
        self.assertContains(res, 'class="hx-step is-off"')

    def test_the_three_tabs_point_at_each_other_for_the_same_month(self):
        month = self.today.strftime('%Y-%m')
        urls = [reverse('deletion_history'), reverse('edit_history'), self.url]
        for u in urls:
            html = self.as_(self.owner).get(u).content.decode()
            for other in urls:
                self.assertIn(f'href="{other}?month={month}"', html, (u, other))
            self.assertEqual(html.count('aria-current="page"'), 1)
        html = self.as_(self.owner).get(self.url).content.decode()
        self.assertIn(f'href="{self.url}?month={month}" aria-current="page"', html)

    def test_each_tab_carries_its_count_for_the_month(self):
        self.cash(1)
        self.cash(2)
        res = self.as_(self.owner).get(reverse('deletion_history'))
        self.assertEqual(res.context['counts']['backdated'], 2)
        self.assertContains(res, 'Back-dated <span class="hx-n">2</span>')

    def test_the_drawer_lights_the_menu_entry_on_this_tab_too(self):
        self.assertTrue(self.url.startswith('/deletion-history/'))

    def test_an_empty_month_says_so(self):
        res = self.as_(self.owner).get(self.url)
        self.assertContains(res, f'Nothing back-dated in {self.today:%B %Y}')
