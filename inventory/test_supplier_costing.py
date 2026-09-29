"""
Supplies Shop bills → warehouse cost.

Defects found by audit on 2026-07-30, all in cost *attribution* rather than in
money moving. Each test below is named for the thing that was wrong:

  1. changing a bill's date left the stored average stale
  2. stock with no bill behind it was costed at ₹0 — i.e. reported as free

⚠ The first two classes of that audit were about a BILL DISCOUNT reaching the
cost. A bill carries no discount since 2026-09-29 — the owners' call, because a
discount shared into the lines made Cost / Unit disagree with the paper bill —
so those tests were rewritten, not deleted to make red go green: they now hold
the new rule, `ABillCostsItsOwnLinePricesTests`.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import FieldDoesNotExist
from django.test import Client, TestCase
from django.urls import NoReverseMatch, reverse

from inventory.costing import average_cost_for
from inventory.models import (Category, Item, ShopCatalogItem,
                              SupplierRestockBill, SupplierRestockItem, SupplierShop)
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

    def bill(self, qty, total, days_ago=0, item=None):
        b = SupplierRestockBill.objects.create(
            supplier=self.shop, bill_date=self.today - timedelta(days=days_ago))
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


class ABillCostsItsOwnLinePricesTests(SupplierCostingBase):
    """
    A SUPPLIES SHOP BILL HAS NO DISCOUNT OF ITS OWN (2026-09-29, the owners'
    call), so an item costs exactly what its line on the shop's bill says.

    It used to carry one, shared into every line pro-rata: ₹100 off Oil ₹1,000 +
    Coolant ₹1,000 costed each at ₹950. So Cost / Unit on the job card stopped
    matching the paper bill, the suggested customer price dropped with it, and a
    discount keyed late re-priced parts already fitted, moving past months'
    profit. A discount a shop gives is recorded on the shop's own page instead,
    and never reaches an item's cost.
    """

    def setUp(self):
        super().setUp()
        g, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='off_cost', password='pw')
        user.groups.add(g)
        self.client = Client()
        self.client.login(username='off_cost', password='pw')

    def test_an_item_costs_its_line_price(self):
        # 10 L billed ₹12,000 = ₹1,200/L, and that is what every litre costs.
        _b, line = self.bill(10, 12000)
        self.assertEqual(line.per_unit_price, D('1200.00'))
        self.assertEqual(self.avg(), D('1200.00'))
        self.assertEqual(self.draw(4).unit_price, D('1200.00'))

    def test_a_bill_has_no_discount_to_carry(self):
        with self.assertRaises(FieldDoesNotExist):
            SupplierRestockBill._meta.get_field('discount_amount')

    def test_the_quick_discount_door_is_gone(self):
        with self.assertRaises(NoReverseMatch):
            reverse('update_bill_discount', args=[self.shop.id, 1])
        b, _line = self.bill(10, 10000)
        resp = self.client.post(f'/inventory/shops/{self.shop.id}/bill/{b.id}/discount/',
                                {'discount_amount': '2000'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.avg(), D('1000.00'))

    def test_a_new_bill_ignores_a_posted_discount(self):
        """A page opened before the box went, submitted after: the bill is still
        the shop's own line prices, and so is the shop's balance."""
        ShopCatalogItem.objects.create(shop=self.shop, item=self.item)
        session = self.client.session
        session['restock_items'] = [str(self.item.id)]
        session.save()

        self.client.post(reverse('shop_restock_bill', args=[self.shop.id]), {
            f'qty_{self.item.id}': '10',
            f'price_{self.item.id}': '12000',
            'discount_amount': '2000',
        })

        b = SupplierRestockBill.objects.get(supplier=self.shop)
        self.assertEqual(b.total_amount, D('12000.00'))
        self.assertEqual(self.avg(), D('1200.00'))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_billed_amount, D('12000.00'))

    def test_an_edited_bill_ignores_a_posted_discount(self):
        b, line = self.bill(10, 10000, days_ago=5)
        draw = self.draw(4, days_ago=2)
        self.client.post(reverse('edit_restock_bill', args=[self.shop.id, b.id]), {
            'bill_date': b.bill_date.isoformat(), 'discount_amount': '2000',
            f'qty_{line.id}': '10', f'price_{line.id}': '10000'})

        b.refresh_from_db()
        draw.refresh_from_db()
        self.assertEqual(b.total_amount, D('10000.00'))
        self.assertEqual(self.avg(), D('1000.00'))
        self.assertEqual(draw.unit_price, D('1000.00'))

    def test_no_bill_screen_offers_a_discount_box(self):
        b, _line = self.bill(10, 10000)
        ShopCatalogItem.objects.create(shop=self.shop, item=self.item)
        session = self.client.session
        session['restock_items'] = [str(self.item.id)]
        session.save()
        pages = [
            reverse('shop_restock_bill', args=[self.shop.id]),
            reverse('edit_restock_bill', args=[self.shop.id, b.id]),
            reverse('supplier_shop_detail', args=[self.shop.id]),
            reverse('ajax_supplier_bills', args=[self.shop.id]),
        ]
        for url in pages:
            html = self.client.get(url).content.decode()
            self.assertNotIn('discount_amount', html, url)
            self.assertNotIn('Add Discount', html, url)

    def test_what_the_shops_billed_is_the_bill_total(self):
        self.bill(10, 12000)
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.supplier_billed(s, e), D('12000.00'))


class BillTermsChangeRecostsTests(SupplierCostingBase):
    """
    A bill's date changes what its stock cost, and does not live on a line — so
    only a bill-level signal can notice. Measured stale by ₹818.18 before that
    signal existed. (It re-costed on a discount change too, until a bill stopped
    carrying one on 2026-09-29.)
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
