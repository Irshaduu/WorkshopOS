"""
Weighted-average warehouse costing (inventory/costing.py).

The reference case throughout is the owner's own example: one product supplied
by two shops at different prices — 2 L at ₹1200 and 5 L at ₹1000 — giving 7 L
on the shelf worth ₹7,400.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from inventory.costing import average_cost_for, new_average_cost, recompute_average_cost
from inventory.models import (Category, Item, SupplierShop,
                              SupplierRestockBill, SupplierRestockItem)
from workshop.models import JobCard, JobCardSpareItem

INVENTORY = JobCardSpareItem.SOURCE_INVENTORY


class CostingBase(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Oils')
        self.item = Item.objects.create(
            category=self.category, name='Engine Oil 5W30',
            current_stock=D('0'), average_stock=D('20'))
        self.shop_a = SupplierShop.objects.create(name='Shop A')
        self.shop_b = SupplierShop.objects.create(name='Shop B')
        self.today = date.today()

    def receive(self, supplier, qty, unit_cost, days_ago=0):
        """Book a restock receipt of `qty` units at `unit_cost` each."""
        bill = SupplierRestockBill.objects.create(
            supplier=supplier, bill_date=self.today - timedelta(days=days_ago))
        return SupplierRestockItem.objects.create(
            bill=bill, item=self.item, quantity=D(str(qty)),
            total_price=D(str(qty)) * D(str(unit_cost)))

    def draw(self, qty, days_ago=0, reg='KL01AA1111'):
        job = JobCard.objects.create(
            admitted_date=self.today - timedelta(days=days_ago),
            registration_number=reg)
        return JobCardSpareItem.objects.create(
            job_card=job, source=INVENTORY, item=self.item, quantity=D(str(qty)))

    def avg(self):
        self.item.refresh_from_db()
        return self.item.avg_cost


class PureFormulaTests(TestCase):
    """`new_average_cost` in isolation — no database."""

    def test_textbook_blend(self):
        # 2 on hand at 1200, receive 5 at 1000 -> 7400 / 7
        self.assertEqual(
            new_average_cost(D('2'), D('1200'), D('5'), D('1000')).quantize(D('0.01')),
            D('1057.14'))

    def test_empty_shelf_adopts_the_incoming_price(self):
        self.assertEqual(new_average_cost(D('0'), D('0'), D('5'), D('1000')), D('1000'))

    def test_negative_shelf_adopts_the_incoming_price_not_a_blend(self):
        """A blend against -3 would return a figure above every price ever paid."""
        result = new_average_cost(D('-3'), D('1000'), D('10'), D('1200'))
        self.assertEqual(result, D('1200'))
        self.assertLessEqual(result, D('1200'), "average cannot exceed the highest price paid")

    def test_uncosted_stock_adopts_the_first_known_price(self):
        """Opening stock entered before any bill has no cost to weight against."""
        self.assertEqual(new_average_cost(D('5'), D('0'), D('10'), D('1200')), D('1200'))

    def test_receiving_nothing_leaves_the_average_alone(self):
        self.assertEqual(new_average_cost(D('5'), D('1000'), D('0'), D('9999')), D('1000'))


class OwnersExampleTests(CostingBase):
    """2 L @ ₹1200 from Shop A, then 5 L @ ₹1000 from Shop B."""

    def test_two_shops_blend_to_the_weighted_average(self):
        self.receive(self.shop_a, 2, 1200, days_ago=2)
        self.assertEqual(self.avg(), D('1200'))

        self.receive(self.shop_b, 5, 1000, days_ago=1)
        self.assertEqual(self.avg(), D('1057.14'))

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, D('7'))

    def test_a_draw_is_costed_at_the_average_as_at_its_own_date(self):
        self.receive(self.shop_a, 2, 1200, days_ago=10)
        self.receive(self.shop_b, 5, 1000, days_ago=9)

        spare = self.draw(4, days_ago=5)
        # Cost per unit as at the draw's date; 4 x 1057.14 = 4228.56
        self.assertEqual(spare.unit_price, D('1057.14'))

        # A genuinely LATER, pricier receipt must not reach back to it. Note the
        # date matters, not the entry order: a receipt sharing the draw's date
        # counts first ("received before used"), so this is dated afterwards.
        self.receive(self.shop_a, 10, 2000, days_ago=0)
        spare.refresh_from_db()
        self.assertEqual(spare.unit_price, D('1057.14'),
                         "the replay is date-ordered — later receipts cannot move it")

    def test_drawing_stock_does_not_move_the_average(self):
        self.receive(self.shop_a, 2, 1200, days_ago=2)
        self.receive(self.shop_b, 5, 1000, days_ago=1)
        before = self.avg()

        self.draw(4)
        self.assertEqual(self.avg(), before,
                         "issuing at the average leaves the average unchanged")

    def test_total_cost_is_conserved_however_it_is_split(self):
        """The 7 L cost ₹7,400 — consumed plus remaining must still be ₹7,400."""
        self.receive(self.shop_a, 2, 1200, days_ago=2)
        self.receive(self.shop_b, 5, 1000, days_ago=1)

        spare = self.draw(4)
        consumed = spare.unit_price * spare.quantity

        self.item.refresh_from_db()
        remaining = self.item.avg_cost * self.item.current_stock

        # 4228.56 + 3171.42, within rounding of the 7400 actually paid.
        self.assertAlmostEqual(consumed + remaining, D('7400'), delta=D('0.10'))


class ReplayTests(CostingBase):
    """Corrections go through a full replay, so nothing is path-dependent."""

    def test_editing_a_receipts_price_recomputes_the_average(self):
        self.receive(self.shop_a, 2, 1200, days_ago=2)
        line = self.receive(self.shop_b, 5, 1000, days_ago=1)
        self.assertEqual(self.avg(), D('1057.14'))

        # Shop B actually charged 1100, not 1000.
        line.total_price = D('5500')
        line.save()
        # (2*1200 + 5*1100) / 7 = 7900 / 7
        self.assertEqual(self.avg(), D('1128.57'))

    def test_deleting_a_receipt_recomputes_the_average(self):
        self.receive(self.shop_a, 2, 1200, days_ago=2)
        line = self.receive(self.shop_b, 5, 1000, days_ago=1)
        self.assertEqual(self.avg(), D('1057.14'))

        line.delete()
        self.assertEqual(self.avg(), D('1200'), "only Shop A's receipt remains")

    def test_a_backdated_bill_is_replayed_in_date_order_not_entry_order(self):
        """Entered second, but dated first — the replay must respect the date."""
        self.receive(self.shop_b, 5, 1000, days_ago=1)
        self.receive(self.shop_a, 2, 1200, days_ago=5)

        # Chronologically: 2 @ 1200 then 5 @ 1000 -> 7400 / 7
        self.assertEqual(self.avg(), D('1057.14'))

    def test_replay_is_idempotent(self):
        self.receive(self.shop_a, 2, 1200, days_ago=2)
        self.receive(self.shop_b, 5, 1000, days_ago=1)
        self.draw(4)

        first = average_cost_for(self.item)
        self.assertEqual(average_cost_for(self.item), first)
        self.assertEqual(recompute_average_cost(self.item), first)

    def test_an_overdrawn_shelf_then_a_receipt_takes_the_new_price(self):
        """The negative-stock path, end to end through the replay."""
        self.receive(self.shop_a, 2, 1200, days_ago=3)
        self.draw(5, days_ago=2)              # shelf goes to -3

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, D('-3'))

        self.receive(self.shop_b, 10, 1000, days_ago=1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, D('7'))
        self.assertEqual(self.item.avg_cost, D('1000'),
                         "all 7 units on hand came from the ₹1000 receipt")


class CustomerRateTests(CostingBase):
    """
    The optional "Unit Price" box: customer price per unit. Input only — never
    derived from the total, so the two figures cannot silently disagree.
    """

    def test_rate_drives_the_customer_total(self):
        spare = JobCardSpareItem.objects.create(
            job_card=JobCard.objects.create(admitted_date=self.today,
                                            registration_number='KL01BB2222'),
            source=INVENTORY, item=self.item,
            quantity=D('7'), customer_rate=D('1200'))
        self.assertEqual(spare.total_price, D('8400'))

    def test_editing_the_quantity_recomputes_the_total(self):
        spare = JobCardSpareItem.objects.create(
            job_card=JobCard.objects.create(admitted_date=self.today,
                                            registration_number='KL01CC3333'),
            source=INVENTORY, item=self.item,
            quantity=D('7'), customer_rate=D('1200'))
        spare.quantity = D('4')
        spare.save()
        self.assertEqual(spare.total_price, D('4800'),
                         "a stale 8400 would over-bill the customer")

    def test_a_typed_total_stands_alone_when_no_rate_is_given(self):
        """The common case — staff skip the rate box entirely."""
        spare = JobCardSpareItem.objects.create(
            job_card=JobCard.objects.create(admitted_date=self.today,
                                            registration_number='KL01DD4444'),
            source=INVENTORY, item=self.item,
            quantity=D('7'), total_price=D('8400'))
        self.assertIsNone(spare.customer_rate)
        self.assertEqual(spare.total_price, D('8400'))

    def test_the_rate_is_never_back_filled_from_the_total(self):
        spare = JobCardSpareItem.objects.create(
            job_card=JobCard.objects.create(admitted_date=self.today,
                                            registration_number='KL01EE5555'),
            source=INVENTORY, item=self.item,
            quantity=D('7'), total_price=D('8400'))
        spare.refresh_from_db()
        self.assertIsNone(spare.customer_rate,
                          "null must keep meaning 'nobody entered a rate'")


class NegativeStockIsNotLowStockTests(TestCase):
    """
    A negative balance means a Supplies Shop bill is missing, not that anything
    should be reordered — so Low Stock must surface it as its own thing. Showing
    them alike would have someone raising a purchase order for a part physically
    sitting on the shelf.
    """

    def setUp(self):
        from django.contrib.auth.models import Group, User
        from django.test import Client
        floor, _ = Group.objects.get_or_create(name='Floor')
        self.user = User.objects.create_user(username='floor_ns', password='pw')
        self.user.groups.add(floor)
        self.client = Client()
        self.client.login(username='floor_ns', password='pw')
        self.category = Category.objects.create(name='Oils')

    def _item(self, name, stock):
        return Item.objects.create(category=self.category, name=name,
                                   average_stock=D('20'), current_stock=D(str(stock)))

    def test_a_negative_item_is_reported_as_a_discrepancy(self):
        self._item('Overdrawn Oil', -3)
        resp = self.client.get(reverse('inventory_low_stock'))
        self.assertEqual(resp.context['discrepancy_count'], 1)
        self.assertContains(resp, 'stock discrepanc')
        self.assertContains(resp, 'Supplies Shop bill has not been entered')
        self.assertContains(resp, 'check bill')

    def test_merely_low_or_empty_stock_is_not_a_discrepancy(self):
        self._item('Low Oil', 2)       # below 25% of 20
        self._item('Empty Oil', 0)     # out, but not overdrawn
        resp = self.client.get(reverse('inventory_low_stock'))
        self.assertEqual(resp.context['discrepancy_count'], 0)
        self.assertNotContains(resp, 'stock discrepanc')
        self.assertContains(resp, 'out')   # the ordinary out-of-stock badge

    def test_the_overflow_count_is_computed_not_guessed(self):
        for i in range(13):
            self._item(f'Oil {i}', -(i + 1))
        resp = self.client.get(reverse('inventory_low_stock'))
        self.assertEqual(resp.context['discrepancy_count'], 13)
        self.assertEqual(len(resp.context['discrepancy_items']), 10)
        self.assertEqual(resp.context['discrepancy_more'], 3)
        self.assertContains(resp, '+3 more')

    def test_the_two_counts_are_disjoint(self):
        """One overdrawn product is one problem, not both 'out of stock' and 'discrepancy'."""
        self._item('Overdrawn Oil', -3)
        self._item('Empty Oil', 0)
        resp = self.client.get(reverse('inventory_low_stock'))
        self.assertEqual(resp.context['discrepancy_count'], 1)   # the negative one
        self.assertEqual(resp.context['out_of_stock'], 1)        # the zero one only
