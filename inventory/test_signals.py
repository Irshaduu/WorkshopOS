"""
Stock synchronisation tests (inventory/signals.py).

These were rewritten on 2026-07-30 when warehouse draws stopped being *inferred*
from a name match and became explicit (`JobCardSpareItem.source` + `item` FK).
The old versions asserted that any spare whose name matched an `Item` moved
stock — which is precisely the defect being removed, so they were asserting the
bug. `test_shop_bought_part_never_touches_the_warehouse` below is the inverse of
what `test_stock_deduction_on_create` used to prove; do not "restore" the old
behaviour.

Note this is NOT the usual "fix the code, not the tests" case: the
specification changed by decision, not the implementation by accident.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import TestCase

from inventory.models import (Category, Item, SupplierShop,
                              SupplierRestockBill, SupplierRestockItem)
from workshop.models import JobCard, JobCardSpareItem, SpareShop

INVENTORY = JobCardSpareItem.SOURCE_INVENTORY
SHOP = JobCardSpareItem.SOURCE_SHOP


class StockSyncBase(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Consumables')
        self.item_oil = Item.objects.create(
            category=self.category, name='Oil Filter',
            current_stock=D('10'), average_stock=D('10'))
        self.item_air = Item.objects.create(
            category=self.category, name='Air Filter',
            current_stock=D('10'), average_stock=D('10'))
        self.job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota',
            model_name='Corolla', registration_number='KL01SIG1234')

    def draw(self, item, qty, **kw):
        """Record a warehouse draw on the job card."""
        return JobCardSpareItem.objects.create(
            job_card=self.job, source=INVENTORY, item=item,
            quantity=D(str(qty)), **kw)

    def stock(self, item):
        item.refresh_from_db()
        return item.current_stock


class WarehouseDrawTests(StockSyncBase):
    def test_draw_deducts_stock(self):
        self.draw(self.item_oil, 2)
        self.assertEqual(self.stock(self.item_oil), D('8'))

    def test_quantity_edit_applies_only_the_delta(self):
        spare = self.draw(self.item_oil, 2)
        self.assertEqual(self.stock(self.item_oil), D('8'))

        spare.quantity = D('5')
        spare.save()
        self.assertEqual(self.stock(self.item_oil), D('5'))   # 8 - 3

    def test_correcting_the_product_returns_one_and_takes_the_other(self):
        """The mechanic picked the wrong filter; fixing it must move both counts."""
        spare = self.draw(self.item_oil, 2)
        self.assertEqual(self.stock(self.item_oil), D('8'))

        spare.item = self.item_air
        spare.spare_part_name = self.item_air.name
        spare.save()

        self.assertEqual(self.stock(self.item_oil), D('10'))  # returned
        self.assertEqual(self.stock(self.item_air), D('8'))   # taken

    def test_delete_returns_stock(self):
        spare = self.draw(self.item_oil, 3)
        self.assertEqual(self.stock(self.item_oil), D('7'))

        spare.delete()
        self.assertEqual(self.stock(self.item_oil), D('10'))

    def test_the_draw_names_itself_from_the_product(self):
        spare = self.draw(self.item_oil, 1)
        self.assertEqual(spare.spare_part_name, 'Oil Filter')


class ShopPurchaseNeverMovesStockTests(StockSyncBase):
    """
    The defect that motivated `source`. A part bought from a spare shop for one
    job never came off the shelf, however its name is spelled.
    """

    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')

    def test_shop_bought_part_never_touches_the_warehouse(self):
        """Engine oil is out of stock, so it is bought from a shop for this job."""
        JobCardSpareItem.objects.create(
            job_card=self.job, source=SHOP, shop=self.shop,
            spare_part_name='Oil Filter',          # same name as a stock product
            quantity=D('3'), unit_price=D('400'), total_price=D('1800'))

        self.assertEqual(self.stock(self.item_oil), D('10'),
                         "a shop purchase must not deduct warehouse stock")

    def test_deleting_a_shop_bought_part_does_not_inflate_the_warehouse(self):
        spare = JobCardSpareItem.objects.create(
            job_card=self.job, source=SHOP, shop=self.shop,
            spare_part_name='Oil Filter', quantity=D('3'),
            unit_price=D('400'), total_price=D('1800'))
        spare.delete()

        self.assertEqual(self.stock(self.item_oil), D('10'),
                         "deleting a shop purchase must not invent stock")

    def test_a_shop_row_with_no_item_is_inert_even_when_renamed(self):
        spare = JobCardSpareItem.objects.create(
            job_card=self.job, source=SHOP, shop=self.shop,
            spare_part_name='Oil Filter', quantity=D('2'))
        spare.spare_part_name = 'Air Filter'
        spare.save()

        self.assertEqual(self.stock(self.item_oil), D('10'))
        self.assertEqual(self.stock(self.item_air), D('10'))


class NegativeStockTests(StockSyncBase):
    """
    Stock is a record of what happened, not a gate on it. See the clamp note in
    inventory/signals.py — these are the tests that keep the clamp out.
    """

    def test_overdrawing_goes_negative_rather_than_clamping(self):
        self.item_oil.current_stock = D('2')
        self.item_oil.save()

        self.draw(self.item_oil, 5)   # mechanic physically took 5

        self.assertEqual(self.stock(self.item_oil), D('-3'),
                         "clamping at zero would destroy the evidence of an overdraw")

    def test_a_late_supplier_bill_heals_the_negative_exactly(self):
        """The scenario the clamp corrupted: -3, then the missing +10 bill = 7."""
        self.item_oil.current_stock = D('2')
        self.item_oil.save()
        self.draw(self.item_oil, 5)
        self.assertEqual(self.stock(self.item_oil), D('-3'))

        supplier = SupplierShop.objects.create(name='Titan Supplies')
        bill = SupplierRestockBill.objects.create(supplier=supplier, bill_date=date.today())
        SupplierRestockItem.objects.create(
            bill=bill, item=self.item_oil, quantity=D('10'), total_price=D('3800'))

        self.assertEqual(self.stock(self.item_oil), D('7'),
                         "the old zero-clamp landed on 10 here, inventing 3 units")


class RestockReceiptTests(StockSyncBase):
    def setUp(self):
        super().setUp()
        self.supplier = SupplierShop.objects.create(name='Titan Supplies')
        self.bill = SupplierRestockBill.objects.create(
            supplier=self.supplier, bill_date=date.today())

    def test_receipt_adds_stock(self):
        SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item_oil, quantity=D('5'), total_price=D('1000'))
        self.assertEqual(self.stock(self.item_oil), D('15'))

    def test_editing_a_receipt_applies_only_the_delta(self):
        line = SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item_oil, quantity=D('5'), total_price=D('1000'))
        line.quantity = D('8')
        line.total_price = D('1600')
        line.save()
        self.assertEqual(self.stock(self.item_oil), D('18'))

    def test_deleting_a_receipt_removes_its_stock_unclamped(self):
        line = SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item_oil, quantity=D('5'), total_price=D('1000'))
        self.item_oil.current_stock = D('2')     # most of it already consumed
        self.item_oil.save()

        line.delete()
        self.assertEqual(self.stock(self.item_oil), D('-3'),
                         "an unclamped deletion shows the shortfall instead of hiding it")


class PreSaveSnapshotTests(StockSyncBase):
    def test_snapshot_is_zeroed_when_the_row_does_not_exist(self):
        from inventory.signals import track_old_draw

        spare = JobCardSpareItem(job_card=self.job, source=INVENTORY,
                                 item=self.item_oil, quantity=D('2'))
        spare.pk = 999999          # fake PK — no such row
        track_old_draw(sender=JobCardSpareItem, instance=spare)

        self.assertEqual(spare._old_quantity, D('0'))
        self.assertIsNone(spare._old_item_id)
        self.assertIsNone(spare._old_source)
