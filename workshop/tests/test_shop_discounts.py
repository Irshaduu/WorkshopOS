"""
A SHOP DISCOUNT — money a spare shop or Supplies Shop let the workshop off.

A DISCOUNT IS A PAYMENT WITH NO CASH (the owners' decision, 2026-09-29). The
owners' own case: a spare shop is owed ₹22,150 and says "just pay ₹22,000" —
that is a ₹22,000 payment and a ₹150 discount, and the shop is then settled.

It settles the debt exactly as a payment does (the balance, the waterfall, the
archive guard), it is PROFIT on the day it was given ("Discounts from shops" in
Turnover), and it moves no cash and never changes what anything cost.

The rules are `workshop/discounts.py`; the two models are `SpareShopDiscount`
and `inventory.SupplierDiscount`.
"""
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    Category, Item, SupplierDiscount, SupplierPayment, SupplierRestockBill,
    SupplierRestockItem, SupplierShop,
)
from workshop import analysis_engine as engine
from workshop.models import (
    DeletionLog, JobCard, JobCardSpareItem, SpareShop, SpareShopDiscount,
    SpareShopPayment,
)
from workshop.views.deletion_history import backdated_rows, _bounds

SHOP = JobCardSpareItem.SOURCE_SHOP


def _user(role):
    user = User.objects.create_user(username=f'sd_{role.lower()}', password='pw')
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


class ShopDiscountBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.biljo = SpareShop.objects.create(name='Biljo')
        self.fluid = SupplierShop.objects.create(name='Fluid Manjeri')
        self.oil = Item.objects.create(category=Category.objects.create(name='Engine Oil'),
                                       name='Castrol Edge 5W30', average_stock=D('20'))
        self.owner = _user('Owner')
        self.office = _user('Office')
        self.client.force_login(self.owner)
        self._cards = 0

    # ── helpers ────────────────────────────────────────────────────────────
    def bought(self, price, days_ago=0):
        """A part bought from Biljo and fitted to a car."""
        self._cards += 1
        card = JobCard.objects.create(registration_number=f'KL 10 SD {1000 + self._cards}',
                                      admitted_date=self.today - timedelta(days=days_ago))
        return JobCardSpareItem.objects.create(job_card=card, source=SHOP, shop=self.biljo,
                                               spare_part_name='Brake pads front',
                                               quantity=D('1'), unit_price=D(str(price)))

    def billed(self, amount, days_ago=0):
        """A Supplies Shop bill of one line."""
        bill = SupplierRestockBill.objects.create(
            supplier=self.fluid, bill_date=self.today - timedelta(days=days_ago))
        SupplierRestockItem.objects.create(bill=bill, item=self.oil, quantity=D('10'),
                                           total_price=D(str(amount)))
        self.fluid.refresh_from_db()
        self.fluid.update_totals()
        return bill

    def spare_discount(self, amount, **extra):
        data = {'amount': str(amount), 'date': self.today.isoformat(), 'note': ''}
        data.update(extra)
        return self.client.post(reverse('spare_shop_discount', args=[self.biljo.pk]),
                                data, follow=True)

    def supply_discount(self, amount, **extra):
        data = {'amount': str(amount), 'date': self.today.isoformat(), 'note': ''}
        data.update(extra)
        return self.client.post(reverse('add_shop_discount', args=[self.fluid.pk]),
                                data, follow=True)

    def fresh(self, obj):
        obj.refresh_from_db()
        return obj

    def month(self):
        return self.today.replace(day=1), self.today


class TheOwnersCaseTests(ShopDiscountBase):
    """₹22,150 owed, ₹22,000 paid, ₹150 let off — settled."""

    def test_a_payment_and_a_discount_settle_the_shop_to_zero(self):
        self.bought(22150)
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('22000'))
        self.spare_discount(150)
        shop = self.fresh(self.biljo)
        self.assertEqual(shop.total_discount_amount, D('150'))
        self.assertEqual(shop.total_paid_amount, D('22000'))    # Paid is still cash only
        self.assertEqual(shop.get_pending_balance, D('0'))

    def test_the_part_is_marked_covered_by_the_discount(self):
        self.bought(22150)
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('22000'))
        page = self.client.get(reverse('spare_shop_detail', args=[self.biljo.pk]), {'filter': 'all'})
        self.assertEqual(page.context['items'][0].covered_status, 'PARTIAL')
        self.spare_discount(150)
        page = self.client.get(reverse('spare_shop_detail', args=[self.biljo.pk]), {'filter': 'all'})
        self.assertEqual(page.context['items'][0].covered_status, 'COVERED')

    def test_a_settled_shop_can_then_be_archived(self):
        self.bought(22150)
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('22000'))
        self.client.post(reverse('spare_shop_delete', args=[self.biljo.pk]))
        self.assertFalse(self.fresh(self.biljo).is_trashed)          # ₹150 still owed
        self.spare_discount(150)
        self.client.post(reverse('spare_shop_delete', args=[self.biljo.pk]))
        self.assertTrue(self.fresh(self.biljo).is_trashed)

    def test_the_supplies_shop_settles_the_same_way(self):
        self.billed(4000)
        SupplierPayment.objects.create(supplier=self.fluid, amount=D('3900'))
        self.fresh(self.fluid).update_totals()
        detail = reverse('supplier_shop_detail', args=[self.fluid.pk])
        self.assertEqual(self.client.get(detail, {'filter': 'all'}).context['bills'][0].covered_status,
                         'PARTIAL')
        self.supply_discount(100)
        self.assertEqual(self.fresh(self.fluid).get_pending_balance, D('0'))
        self.assertEqual(self.client.get(detail, {'filter': 'all'}).context['bills'][0].covered_status,
                         'COVERED')
        chunk = self.client.get(reverse('ajax_supplier_bills', args=[self.fluid.pk]))
        self.assertEqual(chunk.context['bills'][0].covered_status, 'COVERED')
        self.client.post(reverse('deactivate_supplier_shop', args=[self.fluid.pk]))
        self.assertFalse(self.fresh(self.fluid).is_active)

    def test_a_discount_never_changes_what_the_stock_cost(self):
        self.billed(4000)
        cost_before = self.fresh(self.oil).avg_cost
        self.supply_discount(400)
        self.assertEqual(self.fresh(self.oil).avg_cost, cost_before)


class TheBalanceIsOneSumTests(ShopDiscountBase):
    """balance = billed + opening − paid − discounted, on every reader."""

    def test_the_invariant_holds_for_both_shops_and_every_reader(self):
        self.biljo.opening_balance = D('5000')
        self.biljo.save()
        self.bought(8000)
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('6000'))
        self.spare_discount(500)
        self.fluid.opening_balance = D('2000')
        self.fluid.save()
        self.billed(9000)
        SupplierPayment.objects.create(supplier=self.fluid, amount=D('4000'))
        self.fresh(self.fluid).update_totals()
        self.supply_discount(250)

        spare = self.fresh(self.biljo)
        self.assertEqual(spare.get_pending_balance, D('8000') + D('5000') - D('6000') - D('500'))
        supply = self.fresh(self.fluid)
        self.assertEqual(supply.get_pending_balance, D('9000') + D('2000') - D('4000') - D('250'))

        position = engine.financial_position()
        self.assertEqual(position['payable_spare'], spare.get_pending_balance)
        self.assertEqual(position['payable_supplier'], supply.get_pending_balance)

        listed = self.client.get(reverse('spare_shop_list'))
        row = next(s for s in listed.context['shops'] if s.pk == spare.pk)
        self.assertEqual(row.total_balance, spare.get_pending_balance)

    def test_the_opening_balance_is_still_settled_first(self):
        self.biljo.opening_balance = D('1000')
        self.biljo.save()
        self.bought(2000)
        self.spare_discount(800)            # all of it goes on the opening balance
        shop = self.fresh(self.biljo)
        self.assertEqual(shop.opening_balance_left, D('200'))
        page = self.client.get(reverse('spare_shop_detail', args=[self.biljo.pk]), {'filter': 'all'})
        self.assertEqual(page.context['items'][0].covered_status, 'UNPAID')

    def test_the_printed_report_adds_up_with_it(self):
        self.bought(18700)
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('6700'))
        self.spare_discount(150, note='Rounded off')
        page = self.client.get(reverse('spare_shop_print', args=[self.biljo.pk]))
        self.assertEqual(page.context['total_discount'], D('150'))
        self.assertEqual(page.context['total_balance'], D('11850'))
        self.assertContains(page, 'Rounded off')


class ADiscountIsProfitAndNeverCashTests(ShopDiscountBase):

    def figures(self):
        start, end = self.month()
        report = engine.build_profit_report(start, end)
        cash = engine.cash_position(start, end)
        return report, cash

    def test_it_is_turnover_and_profit_on_its_date_and_moves_no_cash(self):
        self.bought(5000)
        self.billed(6000)
        before, cash_before = self.figures()
        self.spare_discount(150)
        self.supply_discount(250)
        after, cash_after = self.figures()
        self.assertEqual(after['shop_discounts'], D('400'))
        self.assertEqual(after['turnover'], before['turnover'] + D('400'))
        self.assertEqual(after['profit'], before['profit'] + D('400'))
        self.assertEqual(after['expense_total'], before['expense_total'])
        self.assertEqual((cash_after['total_in'], cash_after['total_out']),
                         (cash_before['total_in'], cash_before['total_out']))

    def test_the_owners_view_of_the_profit_still_lands_on_the_same_figure(self):
        self.bought(5000)
        self.spare_discount(150)
        report, _ = self.figures()
        rows = {r['key']: r for r in report['earnings']['earn']}
        self.assertEqual(rows['shop_discounts']['amount'], D('150'))
        self.assertFalse(rows['shop_discounts']['negative'])
        self.assertEqual(report['earnings']['profit'], report['profit'])

    def test_it_lands_on_the_day_it_was_given_not_the_day_it_was_keyed(self):
        self.bought(5000, days_ago=40)
        given = self.today - timedelta(days=2)
        self.spare_discount(150, date=given.isoformat())
        before = engine.build_profit_report(given - timedelta(days=30), given - timedelta(days=1))
        on = engine.build_profit_report(given, given)
        self.assertEqual(before['shop_discounts'], D('0'))
        self.assertEqual(on['shop_discounts'], D('150'))

    def test_the_monthly_chart_adds_up_to_the_headline(self):
        self.bought(5000, days_ago=45)
        self.spare_discount(150)
        self.billed(3000, days_ago=45)
        self.supply_discount(250)
        s, e, _k, _l = engine.resolve_period('all_time')
        report = engine.build_profit_report(s, e)
        series = engine.monthly_series(s, e)
        self.assertEqual(sum((m['turnover'] for m in series), D('0')), report['turnover'])
        self.assertEqual(sum((m['profit'] for m in series), D('0')), report['profit'])

    def test_the_profit_page_names_the_line_only_when_there_is_one(self):
        self.bought(5000)
        url = reverse('analysis_dashboard')
        self.assertNotContains(self.client.get(url, {'filter': 'this_month'}), 'Discounts from shops')
        self.spare_discount(150)
        self.assertContains(self.client.get(url, {'filter': 'this_month'}), 'Discounts from shops')


class WhatIsRefusedTests(ShopDiscountBase):
    """Every refusal writes nothing."""

    def setUp(self):
        super().setUp()
        self.bought(1000)
        self.billed(1000)

    def assertRefused(self, response, words):
        self.assertContains(response, words)
        self.assertEqual(SpareShopDiscount.objects.count() + SupplierDiscount.objects.count(), 0)

    def test_more_than_is_owed(self):
        self.assertRefused(self.spare_discount(1001), 'more than the ₹1,000 still owed')
        self.assertRefused(self.supply_discount(1000.5), 'more than the ₹1,000 still owed')

    def test_nothing_owed_nothing_to_discount(self):
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('1000'))
        self.assertRefused(self.spare_discount(10), 'Nothing is owed')

    def test_amounts_that_are_not_money(self):
        for bad in ('0', '0.004', '-50', 'NaN', 'Infinity', 'abc', ''):
            with self.subTest(bad=bad):
                self.assertRefused(self.spare_discount(bad), 'Enter a valid discount amount')

    def test_a_future_date(self):
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        self.assertRefused(self.spare_discount(100, date=tomorrow), 'future')
        self.assertRefused(self.supply_discount(100, date=tomorrow), 'future')

    def test_office_is_held_to_three_days_back_and_an_owner_is_not(self):
        four_back = (self.today - timedelta(days=4)).isoformat()
        self.client.force_login(self.office)
        self.spare_discount(100, date=four_back)
        self.supply_discount(100, date=four_back)
        self.assertEqual(SpareShopDiscount.objects.count() + SupplierDiscount.objects.count(), 0)
        self.client.force_login(self.owner)
        self.spare_discount(100, date=four_back)
        self.assertEqual(SpareShopDiscount.objects.get().date, self.today - timedelta(days=4))

    def test_floor_cannot_record_one(self):
        self.client.force_login(_user('Floor'))
        response = self.client.post(reverse('spare_shop_discount', args=[self.biljo.pk]),
                                    {'amount': '100', 'date': self.today.isoformat()})
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(SpareShopDiscount.objects.exists())

    def test_an_archived_shop_takes_none(self):
        SpareShop.objects.filter(pk=self.biljo.pk).update(is_trashed=True)
        response = self.client.post(reverse('spare_shop_discount', args=[self.biljo.pk]),
                                    {'amount': '100', 'date': self.today.isoformat()})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SpareShopDiscount.objects.exists())

    def test_an_oversized_note_is_trimmed_not_crashed(self):
        self.spare_discount(100, note='x' * 400)
        self.assertEqual(len(SpareShopDiscount.objects.get().note), 255)

    def test_a_blank_note_stores_null(self):
        self.spare_discount(100, note='   ')
        self.assertIsNone(SpareShopDiscount.objects.get().note)


class DeletingOneTests(ShopDiscountBase):
    """The payment delete's rule exactly: Office 24 hours, then an owner; logged."""

    def setUp(self):
        super().setUp()
        self.bought(1000)
        self.billed(1000)
        self.spare_discount(100)
        self.supply_discount(200)
        self.sd = SpareShopDiscount.objects.get()
        self.su = SupplierDiscount.objects.get()

    def delete_both(self, reason='wrong shop'):
        self.client.post(reverse('spare_shop_discount_delete', args=[self.biljo.pk, self.sd.pk]),
                         {'reason': reason})
        self.client.post(reverse('delete_shop_discount', args=[self.fluid.pk, self.su.pk]),
                         {'reason': reason})

    def test_a_delete_puts_the_money_back_on_the_balance_and_is_logged(self):
        self.delete_both()
        self.assertEqual(self.fresh(self.biljo).get_pending_balance, D('1000'))
        self.assertEqual(self.fresh(self.fluid).get_pending_balance, D('1000'))
        logs = DeletionLog.objects.order_by('entity_type')
        self.assertEqual([(l.entity_type, l.amount, l.reason) for l in logs], [
            (DeletionLog.ENTITY_SHOP_DISCOUNT, D('100'), 'wrong shop'),
            (DeletionLog.ENTITY_SUPPLIER_DISCOUNT, D('200'), 'wrong shop'),
        ])

    def test_office_may_delete_inside_24_hours(self):
        self.client.force_login(self.office)
        self.delete_both()
        self.assertFalse(SpareShopDiscount.objects.exists())
        self.assertFalse(SupplierDiscount.objects.exists())

    def test_office_is_refused_past_24_hours_and_an_owner_is_not(self):
        old = timezone.now() - timedelta(hours=30)
        SpareShopDiscount.objects.update(created_at=old)
        SupplierDiscount.objects.update(created_at=old)
        self.client.force_login(self.office)
        self.delete_both()
        self.assertEqual(SpareShopDiscount.objects.count() + SupplierDiscount.objects.count(), 2)
        self.client.force_login(self.owner)
        self.delete_both()
        self.assertEqual(SpareShopDiscount.objects.count() + SupplierDiscount.objects.count(), 0)

    def test_a_discount_is_deleted_only_from_its_own_shop(self):
        other = SpareShop.objects.create(name='Spare Club')
        response = self.client.post(
            reverse('spare_shop_discount_delete', args=[other.pk, self.sd.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(SpareShopDiscount.objects.exists())


class BackDatedAndPurgedTests(ShopDiscountBase):

    def test_a_back_dated_discount_is_found_in_change_history(self):
        self.bought(1000)
        self.billed(1000)
        two_back = (self.today - timedelta(days=2)).isoformat()
        self.spare_discount(100, date=two_back)
        self.supply_discount(100, date=two_back)
        start, end = _bounds(*self.month())
        kinds = sorted(r['source'] for r in backdated_rows(start, end))
        self.assertEqual(kinds, [DeletionLog.ENTITY_SHOP_DISCOUNT, DeletionLog.ENTITY_SUPPLIER_DISCOUNT])

    def test_the_go_live_purge_clears_both(self):
        self.bought(1000)
        self.billed(1000)
        self.spare_discount(100)
        self.supply_discount(100)
        call_command('purge_business_data', '--yes', stdout=StringIO())
        self.assertFalse(SpareShopDiscount.objects.exists())
        self.assertFalse(SupplierDiscount.objects.exists())


class TheScreensTests(ShopDiscountBase):

    def test_the_control_is_on_both_shop_pages_only_while_money_is_owed(self):
        pages = (reverse('spare_shop_detail', args=[self.biljo.pk]),
                 reverse('supplier_shop_detail', args=[self.fluid.pk]))
        for url in pages:
            with self.subTest(url=url, owed=False):
                html = self.client.get(url).content.decode()
                self.assertNotIn('id="rdiscForm"', html)
                self.assertNotIn('class="rdisc-sym', html)
        self.bought(1000)
        self.billed(1000)
        for url in pages:
            with self.subTest(url=url, owed=True):
                html = self.client.get(url).content.decode()
                self.assertIn('id="rdiscForm"', html)
                # A shop letting US off is profit: the green variant, not `-loss`.
                self.assertIn('class="rdisc-sym"', html)
                self.assertNotIn('rdisc-loss', html)

    def test_it_is_a_symbol_only_and_costs_the_page_nothing_else(self):
        """The owner's call (2026-09-30): a rare action is one tag symbol LEFT
        of the history buttons, with no caption, opening a small dialog — not a
        line of furniture inside the payment card."""
        self.bought(1000)
        self.billed(1000)
        for url, first_button in (
                (reverse('spare_shop_detail', args=[self.biljo.pk]), 'Payments ('),
                (reverse('supplier_shop_detail', args=[self.fluid.pk]), 'Restock Bills (')):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                symbol = html.index('class="rdisc-sym"')
                self.assertLess(symbol, html.index(first_button))
                button = html[symbol:html.index('</button>', symbol)]
                self.assertIn('bi-tag-fill', button)
                self.assertIn('aria-label="Record a discount"', button)
                self.assertNotIn('Discount<', button)          # no caption
                card = html[html.index('<div class="rpay-card">'):html.index('id="rdiscModal"')]
                self.assertNotIn('rdiscForm', card)            # not inside the payment card

    def test_an_archived_supplies_shop_offers_none(self):
        self.billed(1000)
        SupplierShop.objects.filter(pk=self.fluid.pk).update(is_active=False)
        html = self.client.get(reverse('supplier_shop_detail', args=[self.fluid.pk])).content.decode()
        self.assertNotIn('class="rdisc-sym', html)
        self.assertNotIn('id="rdiscForm"', html)

    def test_it_is_shown_under_total_paid_and_in_the_history_only_when_there_is_one(self):
        self.bought(1000)
        url = reverse('spare_shop_detail', args=[self.biljo.pk])
        html = self.client.get(url).content.decode()
        self.assertNotIn('class="stat-disc"', html)
        self.assertNotIn('class="rdisc-hist', html)
        self.spare_discount(150, note='Rounded off')
        html = self.client.get(url).content.decode()
        self.assertIn('+ ₹150 discount', html)
        self.assertIn('class="rdisc-hist', html)
        self.assertIn('Rounded off', html)
        self.assertIn(reverse('spare_shop_discount_delete',
                              args=[self.biljo.pk, SpareShopDiscount.objects.get().pk]), html)

    def test_the_supplies_shop_history_carries_it_too(self):
        self.billed(1000)
        self.supply_discount(300)
        html = self.client.get(reverse('supplier_shop_detail', args=[self.fluid.pk])).content.decode()
        self.assertIn('+ ₹300 discount', html)
        self.assertIn(reverse('delete_shop_discount',
                              args=[self.fluid.pk, SupplierDiscount.objects.get().pk]), html)

    def test_the_delete_asks_first_and_names_its_card(self):
        self.bought(1000)
        self.spare_discount(150)
        html = self.client.get(reverse('spare_shop_detail', args=[self.biljo.pk])).content.decode()
        self.assertIn('data-confirm-title="Delete this discount?"', html)
        self.assertIn('data-confirm-reason="deleting this discount"', html)
