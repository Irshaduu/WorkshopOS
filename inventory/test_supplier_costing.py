"""
Supplies Shop bills → warehouse cost.

Four defects found by audit on 2026-07-30, all in cost *attribution* rather than
in money moving. Each test below is named for the thing that was wrong:

  1. a bill-level discount never reached `avg_cost`
  2. a discount larger than its bill produced a NEGATIVE expense
  3. changing a bill's date left the stored average stale
  4. stock with no bill behind it was costed at ₹0 — i.e. reported as free
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from inventory.costing import average_cost_for
from inventory.models import (Category, Item, ShopCatalogItem, SupplierRestockBill,
                              SupplierRestockItem, SupplierShop)
from workshop import analysis_engine as engine
from workshop.models import JobCard, JobCardSpareItem

INVENTORY = JobCardSpareItem.SOURCE_INVENTORY


class SupplierCostingBase(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Oils')
        self.item = Item.objects.create(category=self.category, name='Castrol 5w30',
                                        average_stock=D('40'), current_stock=D('0'))
        self.shop = SupplierShop.objects.create(name='Supplies A')
        self.today = date.today()

    def bill(self, qty, total, discount='0', days_ago=0, item=None):
        b = SupplierRestockBill.objects.create(
            supplier=self.shop, bill_date=self.today - timedelta(days=days_ago),
            discount_amount=D(str(discount)))
        line = SupplierRestockItem.objects.create(
            bill=b, item=item or self.item,
            quantity=D(str(qty)), total_price=D(str(total)))
        b.update_totals()
        b.refresh_from_db()
        return b, line

    def draw(self, qty, days_ago=0, reg=None):
        jc = JobCard.objects.create(
            registration_number=reg or f'KL09XX{days_ago:04d}',
            admitted_date=self.today - timedelta(days=days_ago))
        return JobCardSpareItem.objects.create(job_card=jc, source=INVENTORY,
                                               item=self.item, quantity=D(str(qty)))

    def avg(self):
        self.item.refresh_from_db()
        return self.item.avg_cost


class DiscountReachesTheCostTests(SupplierCostingBase):
    """
    Costing used the gross line price while the Profit page expensed the
    discounted amount, so one purchase carried two different costs and every
    discounted part looked less profitable than it really was.
    """

    def test_the_discount_is_apportioned_into_the_unit_cost(self):
        # 10 L billed ₹12,000 with ₹2,000 off = ₹10,000 paid = ₹1,000/L
        _b, line = self.bill(10, 12000, discount=2000)
        self.assertEqual(line.per_unit_price, D('1200.00'))     # gross, display only
        self.assertEqual(line.effective_unit_price, D('1000'))  # what it actually cost
        self.assertEqual(self.avg(), D('1000.00'))

    def test_a_draw_is_costed_at_the_discounted_price(self):
        self.bill(10, 12000, discount=2000)
        self.assertEqual(self.draw(4).unit_price, D('1000.00'))

    def test_the_discount_is_shared_across_lines_by_value(self):
        other = Item.objects.create(category=self.category, name='Air Filter',
                                    average_stock=D('10'), current_stock=D('0'))
        b = SupplierRestockBill.objects.create(supplier=self.shop, bill_date=self.today,
                                               discount_amount=D('2000'))
        SupplierRestockItem.objects.create(bill=b, item=self.item,
                                           quantity=D('10'), total_price=D('12000'))
        SupplierRestockItem.objects.create(bill=b, item=other,
                                           quantity=D('10'), total_price=D('8000'))
        b.update_totals()

        # ₹20,000 billed, ₹2,000 off → every line is 10% cheaper
        other.refresh_from_db()
        self.assertEqual(self.avg(), D('1080.00'))    # 1200 − 10%
        self.assertEqual(other.avg_cost, D('720.00'))  # 800 − 10%

    def test_editing_one_line_recosts_its_discounted_siblings(self):
        """Changing a line moves the bill total, so every line's share moves."""
        other = Item.objects.create(category=self.category, name='Air Filter',
                                    average_stock=D('10'), current_stock=D('0'))
        b = SupplierRestockBill.objects.create(supplier=self.shop, bill_date=self.today,
                                               discount_amount=D('2000'))
        line_a = SupplierRestockItem.objects.create(bill=b, item=self.item,
                                                    quantity=D('10'), total_price=D('12000'))
        SupplierRestockItem.objects.create(bill=b, item=other,
                                           quantity=D('10'), total_price=D('8000'))
        b.update_totals()
        other.refresh_from_db()
        self.assertEqual(other.avg_cost, D('720.00'))

        line_a.total_price = D('28000')
        line_a.save()
        b.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(b.total_amount, D('36000.00'))
        self.assertNotEqual(other.avg_cost, D('720.00'))


class ImpossibleDiscountTests(SupplierCostingBase):
    """A discount above its bill total made the expense negative and raised profit."""

    def test_effective_amount_is_floored_at_zero(self):
        b = SupplierRestockBill.objects.create(
            supplier=self.shop, bill_date=self.today,
            total_amount=D('5000'), discount_amount=D('9000'))
        self.assertEqual(b.get_effective_amount, D('0'))

    def test_the_restock_view_drops_it_rather_than_applying_it(self):
        g, _ = Group.objects.get_or_create(name='Office')
        u = User.objects.create_user(username='off_disc', password='pw')
        u.groups.add(g)
        client = Client()
        client.login(username='off_disc', password='pw')

        ShopCatalogItem.objects.create(shop=self.shop, item=self.item)
        session = client.session
        session['restock_items'] = [str(self.item.id)]
        session.save()

        client.post(reverse('shop_restock_bill', args=[self.shop.id]), {
            'qty_%s' % self.item.id: '10',
            'price_%s' % self.item.id: '5000',
            'discount_amount': '9000',
        })

        b = SupplierRestockBill.objects.filter(supplier=self.shop).first()
        self.assertEqual(b.discount_amount, D('0'))
        self.assertEqual(b.get_effective_amount, D('5000.00'))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_billed_amount, D('5000.00'),
                         "the supplier ledger must not go negative either")


class BillTermsChangeRecostsTests(SupplierCostingBase):
    """
    A bill's date and discount both change what its stock cost, and neither lives
    on a line — so only a bill-level signal can notice. Measured stale by ₹818.18
    before that signal existed.
    """

    def test_backdating_a_bill_across_a_draw_recomputes(self):
        self.bill(10, 10000, days_ago=30)         # ₹1,000/L
        self.draw(9, days_ago=20)                 # draw sits between the two
        b2, _line = self.bill(10, 30000, days_ago=10)   # ₹3,000/L
        self.assertEqual(self.avg(), D('2818.18'))

        b2.bill_date = self.today - timedelta(days=25)   # now BEFORE the draw
        b2.save()

        self.assertEqual(self.avg(), average_cost_for(self.item))
        self.assertEqual(self.avg(), D('2000.00'))

    def test_changing_only_the_discount_recomputes(self):
        b, _line = self.bill(10, 10000, days_ago=10)
        self.assertEqual(self.avg(), D('1000.00'))
        b.discount_amount = D('2000')
        b.save()
        self.assertEqual(self.avg(), D('800.00'))    # (10000 − 2000) / 10


class UnknownCostIsNotZeroTests(SupplierCostingBase):
    """
    Stock with no bill behind it costs an UNKNOWN amount, not zero. Storing 0
    reported those parts as pure profit; NULL keeps "nobody knows" visible, and
    the analysis counts them so they can be found and priced.
    """

    def test_a_draw_from_uncosted_stock_records_no_cost(self):
        self.item.current_stock = D('50')      # opening stock, never billed
        self.item.save()
        self.assertIsNone(self.draw(5).unit_price)

    def test_the_analysis_counts_them_instead_of_hiding_them(self):
        self.item.current_stock = D('50')
        self.item.save()
        self.draw(5)
        s, e, _k, _l = engine.resolve_period('all_time')
        self.assertEqual(engine.warehouse_drawn_spare_cost(s, e), D('0'))
        self.assertEqual(engine.uncosted_draw_count(s, e), 1)
        self.assertEqual(engine.build_profit_report(s, e)['uncosted_draws'], 1)

    def test_a_normally_costed_draw_is_not_flagged(self):
        self.bill(10, 10000, days_ago=5)
        self.draw(4)
        s, e, _k, _l = engine.resolve_period('all_time')
        self.assertEqual(engine.uncosted_draw_count(s, e), 0)

    def test_deleting_the_only_bill_leaves_cost_unknown_not_free(self):
        b, _line = self.bill(10, 10000, days_ago=5)
        self.draw(4, days_ago=4)
        b.delete()
        # The earlier draw keeps its frozen cost; a NEW draw has nothing to go on.
        later = self.draw(1, days_ago=0, reg='KL09ZZ9999')
        self.assertIsNone(later.unit_price)


class DeferredBillingTests(SupplierCostingBase):
    """
    The workshop's actual rhythm: a Supplies Shop delivers and keeps its own book,
    and the bill is only entered when the collector comes at month end. Parts are
    therefore fitted for weeks before the system knows what they cost.

    Measured before this was handled: a month of draws stayed at NULL forever, so
    ₹36,000 of consumed oil was reported as free. This is the normal month here,
    not an edge case.
    """

    def test_a_late_bill_costs_the_draws_that_preceded_it(self):
        d1 = self.draw(10, days_ago=26, reg='KL11AA0001')
        d2 = self.draw(10, days_ago=19, reg='KL11AA0002')
        d3 = self.draw(10, days_ago=11, reg='KL11AA0003')
        for d in (d1, d2, d3):
            self.assertIsNone(d.unit_price)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, D('-30'))   # the outstanding-bill signal

        # Collector arrives; the bill is entered, backdated to the delivery.
        self.bill(40, 48000, days_ago=30)                     # ₹1,200/L

        for d in (d1, d2, d3):
            d.refresh_from_db()
            self.assertEqual(d.unit_price, D('1200.00'))

        s, e, _k, _l = engine.resolve_period('all_time')
        self.assertEqual(engine.warehouse_drawn_spare_cost(s, e), D('36000.00'))
        self.assertEqual(engine.uncosted_draw_count(s, e), 0)

    def test_each_draw_gets_the_average_as_at_its_own_date(self):
        """Not one blanket figure — the average that applied when each part was taken."""
        early = self.draw(5, days_ago=25, reg='KL11BB0001')
        later = self.draw(5, days_ago=5, reg='KL11BB0002')
        self.assertIsNone(early.unit_price)
        self.assertIsNone(later.unit_price)

        # Both bills keyed in one sitting at month end, dated to their deliveries.
        self.bill(10, 10000, days_ago=30)          # ₹1,000/L, before the first draw
        self.bill(10, 30000, days_ago=10)          # ₹3,000/L, between the two draws

        early.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(early.unit_price, D('1000.00'))
        # 5 left at ₹1,000 + 10 in at ₹3,000 → 35000/15
        self.assertEqual(later.unit_price, D('2333.33'),
                         "the second bill must reach the draw that followed it")

    def test_a_later_dated_bill_never_disturbs_an_earlier_draw(self):
        """
        The reason re-deriving is safe: the replay is date-ordered, so a bill
        entered afterwards and dated afterwards cannot reach back.
        """
        self.bill(10, 10000, days_ago=30)
        spare = self.draw(4, days_ago=25)
        self.assertEqual(spare.unit_price, D('1000.00'))

        self.bill(10, 50000, days_ago=20)          # prices jump, but AFTER that draw
        spare.refresh_from_db()
        self.assertEqual(spare.unit_price, D('1000.00'))

    def test_correcting_an_old_bill_does_move_the_draws_it_paid_for(self):
        """And when the workshop learns the real price, the margin should follow."""
        b, line = self.bill(10, 10000, days_ago=30)
        spare = self.draw(4, days_ago=25)
        self.assertEqual(spare.unit_price, D('1000.00'))

        line.total_price = D('14000')              # it was really ₹1,400/L
        line.save()
        spare.refresh_from_db()
        self.assertEqual(spare.unit_price, D('1400.00'),
                         "freezing here would preserve a figure known to be wrong")

    def test_a_draw_with_no_receipt_before_it_stays_uncosted(self):
        """Nothing had established a price by then, so there is nothing to fill in."""
        early = self.draw(5, days_ago=40, reg='KL11CC0001')
        self.bill(10, 10000, days_ago=10)          # bill dated AFTER that draw
        early.refresh_from_db()
        self.assertIsNone(early.unit_price)

        s, e, _k, _l = engine.resolve_period('all_time')
        self.assertEqual(engine.uncosted_draw_count(s, e), 1)
