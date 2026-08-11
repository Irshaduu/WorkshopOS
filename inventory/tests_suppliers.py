# inventory/tests_suppliers.py
"""
Full Test Suite for the Supplies Shops Section.
Covers: Shop CRUD, Catalog, Restock Bills, Stock Signals,
        Payments, Discounts, Bulk Pay Status, AJAX Pagination, Edge Cases.
"""
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.db import transaction

from .models import (
    Category, Item, SupplierShop, ShopCatalogItem,
    SupplierRestockBill, SupplierRestockItem, SupplierPayment,
)


class SupplierShopModelTests(TestCase):
    """Tests for SupplierShop model math and properties."""

    def setUp(self):
        self.shop = SupplierShop.objects.create(name='Test Supplier')
        self.category = Category.objects.create(name='Oils')
        self.item = Item.objects.create(
            category=self.category, name='Engine Oil', current_stock=100
        )

    def test_pending_balance_zero_on_creation(self):
        self.assertEqual(self.shop.get_pending_balance, Decimal('0'))

    def test_pending_balance_after_bill(self):
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=5000)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_billed_amount, Decimal('5000'))
        self.assertEqual(self.shop.get_pending_balance, Decimal('5000'))

    def test_pending_balance_after_payment(self):
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=5000)
        SupplierPayment.objects.create(supplier=self.shop, amount=3000)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.get_pending_balance, Decimal('2000'))

    def test_pending_balance_overpayment(self):
        """Paying more than owed should result in a negative (advance) balance."""
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=5000)
        SupplierPayment.objects.create(supplier=self.shop, amount=6000)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.get_pending_balance, Decimal('-1000'))

    def test_effective_amount_with_discount(self):
        bill = SupplierRestockBill.objects.create(
            supplier=self.shop, total_amount=10000, discount_amount=500
        )
        self.assertEqual(bill.get_effective_amount, Decimal('9500'))

    def test_update_totals_with_discount(self):
        """total_billed_amount should equal SUM(total_amount - discount_amount)."""
        SupplierRestockBill.objects.create(
            supplier=self.shop, total_amount=10000, discount_amount=500
        )
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_billed_amount, Decimal('9500'))

    def test_update_totals_excludes_trashed_payments(self):
        """Soft-deleted (trashed) payments must NOT count in total_paid_amount."""
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=5000)
        p = SupplierPayment.objects.create(supplier=self.shop, amount=3000)
        p.is_trashed = True
        p.save()
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_paid_amount, Decimal('0'))

    def test_multiple_bills_sum_correctly(self):
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=5000)
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=3000)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_billed_amount, Decimal('8000'))

    def test_multiple_payments_sum_correctly(self):
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=10000)
        SupplierPayment.objects.create(supplier=self.shop, amount=3000)
        SupplierPayment.objects.create(supplier=self.shop, amount=2000)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_paid_amount, Decimal('5000'))
        self.assertEqual(self.shop.get_pending_balance, Decimal('5000'))


class SupplierRestockSignalTests(TestCase):
    """Tests for stock synchronization signals on restock items."""

    def setUp(self):
        self.shop = SupplierShop.objects.create(name='Signal Test Shop')
        self.category = Category.objects.create(name='Filters')
        self.item = Item.objects.create(
            category=self.category, name='Oil Filter', current_stock=20
        )
        self.bill = SupplierRestockBill.objects.create(supplier=self.shop)

    def test_stock_increase_on_restock_create(self):
        SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=10, total_price=500
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 30)  # 20 + 10

    def test_stock_delta_on_restock_edit_increase(self):
        ri = SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=10, total_price=500
        )
        ri.quantity = 15
        ri.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 35)  # 20 + 15

    def test_stock_delta_on_restock_edit_decrease(self):
        ri = SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=10, total_price=500
        )
        ri.quantity = 3
        ri.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 23)  # 20 + 3

    def test_stock_reversal_on_restock_item_delete(self):
        ri = SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=10, total_price=500
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 30)
        ri.delete()
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 20)  # Fully reversed

    def test_stock_reversal_on_bill_cascade_delete(self):
        """Deleting a bill cascades to items and reverses ALL stock changes."""
        SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=10, total_price=500
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 30)
        self.bill.delete()
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 20)

    def test_bill_total_auto_updates_on_item_save(self):
        SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=5, total_price=1000
        )
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.total_amount, Decimal('1000'))

    def test_bill_total_updates_on_item_delete(self):
        ri = SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=5, total_price=1000
        )
        ri.delete()
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.total_amount, Decimal('0'))

    def test_shop_totals_cascade_from_bill_item(self):
        """Creating a restock item should cascade: item → bill → shop totals."""
        SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=5, total_price=2500
        )
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_billed_amount, Decimal('2500'))


class SupplierShopViewTests(TestCase):
    """Tests for all Supplier Shop views and UI flows."""

    def setUp(self):
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(
            username='supplier_tester', password='pass123'
        )
        self.user.groups.add(self.office_group)
        self.client = Client()
        self.client.login(username='supplier_tester', password='pass123')

        self.category = Category.objects.create(name='Oils')
        self.item1 = Item.objects.create(
            category=self.category, name='Engine Oil 5W30', current_stock=50
        )
        self.item2 = Item.objects.create(
            category=self.category, name='Brake Fluid', current_stock=30
        )

    # ── Shop CRUD ──

    def test_shop_list_page(self):
        SupplierShop.objects.create(name='Castrol Depot')
        response = self.client.get(reverse('supplier_shop_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Castrol Depot')

    def test_add_shop(self):
        response = self.client.post(reverse('add_supplier_shop'), {
            'name': 'Shell Center', 'phone': '9876543210', 'address': 'MG Road'
        })
        self.assertRedirects(response, reverse('supplier_shop_list'))
        self.assertTrue(SupplierShop.objects.filter(name='Shell Center').exists())

    def test_add_duplicate_shop_does_not_create(self):
        SupplierShop.objects.create(name='UniqueShop')
        response = self.client.post(
            reverse('add_supplier_shop'), {'name': 'UniqueShop'}
        )
        self.assertEqual(response.status_code, 200)
        # Should still be exactly 1 shop with that name
        self.assertEqual(SupplierShop.objects.filter(name='UniqueShop').count(), 1)

    def test_edit_shop(self):
        shop = SupplierShop.objects.create(name='Old Name')
        self.client.post(reverse('edit_supplier_shop', args=[shop.id]), {
            'name': 'New Name', 'phone': '', 'address': ''
        })
        shop.refresh_from_db()
        self.assertEqual(shop.name, 'New Name')

    def test_deactivate_shop(self):
        shop = SupplierShop.objects.create(name='Toggle Shop')
        self.client.post(reverse('deactivate_supplier_shop', args=[shop.id]))
        shop.refresh_from_db()
        self.assertFalse(shop.is_active)

    def test_activate_shop(self):
        shop = SupplierShop.objects.create(name='Restore Shop', is_active=False)
        self.client.post(reverse('activate_supplier_shop', args=[shop.id]))
        shop.refresh_from_db()
        self.assertTrue(shop.is_active)

    def test_deactivated_shop_list(self):
        SupplierShop.objects.create(name='Inactive Shop', is_active=False)
        response = self.client.get(reverse('deactivated_supplier_shop_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Inactive Shop')

    # ── Catalog ──

    def test_add_existing_item_requires_confirmation(self):
        shop = SupplierShop.objects.create(name='Cat Test Shop')
        response = self.client.post(
            reverse('add_shop_catalog_item', args=[shop.id]),
            {'item_name': 'Engine Oil 5W30', 'category_name': 'Oils'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Already Exists')

    def test_add_existing_item_with_confirmation(self):
        shop = SupplierShop.objects.create(name='Confirm Shop')
        self.client.post(
            reverse('add_shop_catalog_item', args=[shop.id]),
            {'item_name': 'Engine Oil 5W30', 'category_name': 'Oils',
             'confirm_existing': '1'}
        )
        self.assertTrue(
            ShopCatalogItem.objects.filter(shop=shop, item=self.item1).exists()
        )

    def test_add_brand_new_item_to_catalog(self):
        shop = SupplierShop.objects.create(name='NewItem Shop')
        self.client.post(
            reverse('add_shop_catalog_item', args=[shop.id]),
            {'item_name': 'Transmission Fluid', 'category_name': 'Oils', 'average_stock': '10'}
        )
        self.assertTrue(Item.objects.filter(name='Transmission Fluid').exists())
        new_item = Item.objects.get(name='Transmission Fluid')
        self.assertTrue(
            ShopCatalogItem.objects.filter(shop=shop, item=new_item).exists()
        )

    def test_add_duplicate_catalog_item_rejected(self):
        shop = SupplierShop.objects.create(name='Dup Cat Shop')
        ShopCatalogItem.objects.create(shop=shop, item=self.item1)
        self.client.post(
            reverse('add_shop_catalog_item', args=[shop.id]),
            {'item_name': 'Engine Oil 5W30', 'category_name': 'Oils',
             'confirm_existing': '1'}
        )
        # Should still be only 1 catalog entry
        self.assertEqual(
            ShopCatalogItem.objects.filter(shop=shop, item=self.item1).count(), 1
        )

    def test_remove_catalog_item(self):
        """A product with no stock and no bill history is unlinked outright."""
        shop = SupplierShop.objects.create(name='Remove Shop')
        item = Item.objects.create(category=self.category, name='Zero Stock Part',
                                   average_stock=Decimal('5'), current_stock=Decimal('0'))
        ci = ShopCatalogItem.objects.create(shop=shop, item=item)
        self.client.post(
            reverse('remove_shop_catalog_item', args=[shop.id, ci.id])
        )
        self.assertFalse(ShopCatalogItem.objects.filter(id=ci.id).exists())

    def test_remove_catalog_item_holding_stock_deactivates(self):
        """item1 holds stock — removing it would destroy a countable quantity,
        so the entry is deactivated and both item and stock survive."""
        shop = SupplierShop.objects.create(name='Remove Stocked Shop')
        ci = ShopCatalogItem.objects.create(shop=shop, item=self.item1)
        before = self.item1.current_stock
        self.client.post(
            reverse('remove_shop_catalog_item', args=[shop.id, ci.id])
        )
        ci.refresh_from_db()
        self.item1.refresh_from_db()
        self.assertFalse(ci.is_active)
        self.assertEqual(self.item1.current_stock, before)

    def test_edit_catalog_item_name(self):
        shop = SupplierShop.objects.create(name='Rename Shop')
        ci = ShopCatalogItem.objects.create(shop=shop, item=self.item1)
        self.client.post(
            reverse('edit_catalog_item', args=[shop.id, ci.id]),
            {'item_name': 'Engine Oil 10W40'}
        )
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.name, 'Engine Oil 10W40')

    # ── Restock Bill Creation ──

    def test_create_restock_bill_full_flow(self):
        shop = SupplierShop.objects.create(name='Restock Shop')
        ShopCatalogItem.objects.create(shop=shop, item=self.item1)
        session = self.client.session
        session['restock_items'] = [str(self.item1.id)]
        session.save()

        response = self.client.post(
            reverse('shop_restock_bill', args=[shop.id]),
            {f'qty_{self.item1.id}': '10', f'price_{self.item1.id}': '5000',
             'discount_amount': '0'}
        )
        self.assertRedirects(
            response, reverse('supplier_shop_detail', args=[shop.id])
        )
        # Bill created
        bill = SupplierRestockBill.objects.filter(supplier=shop).first()
        self.assertIsNotNone(bill)
        self.assertEqual(bill.items.count(), 1)
        # Stock increased
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.current_stock, 60)  # 50 + 10
        # Shop totals updated
        shop.refresh_from_db()
        self.assertEqual(shop.total_billed_amount, Decimal('5000'))

    def test_create_bill_with_discount(self):
        shop = SupplierShop.objects.create(name='Disc Bill Shop')
        # A bill may only contain products this shop actively stocks — the picker
        # enforces it and so does shop_restock_bill.
        ShopCatalogItem.objects.create(shop=shop, item=self.item1)
        session = self.client.session
        session['restock_items'] = [str(self.item1.id)]
        session.save()

        self.client.post(
            reverse('shop_restock_bill', args=[shop.id]),
            {f'qty_{self.item1.id}': '10', f'price_{self.item1.id}': '5000',
             'discount_amount': '250'}
        )
        shop.refresh_from_db()
        self.assertEqual(shop.total_billed_amount, Decimal('4750'))  # 5000 - 250

    def test_delete_restock_bill_reverses_stock(self):
        shop = SupplierShop.objects.create(name='Delete Bill Shop')
        bill = SupplierRestockBill.objects.create(supplier=shop)
        SupplierRestockItem.objects.create(
            bill=bill, item=self.item1, quantity=10, total_price=5000
        )
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.current_stock, 60)

        self.client.post(
            reverse('delete_restock_bill', args=[shop.id, bill.id])
        )
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.current_stock, 50)  # Fully reversed
        shop.refresh_from_db()
        self.assertEqual(shop.total_billed_amount, Decimal('0'))

    # ── Edit Bill ──

    def test_edit_bill_increase_qty(self):
        shop = SupplierShop.objects.create(name='Edit+ Shop')
        bill = SupplierRestockBill.objects.create(supplier=shop)
        ri = SupplierRestockItem.objects.create(
            bill=bill, item=self.item1, quantity=10, total_price=5000
        )
        self.client.post(
            reverse('edit_restock_bill', args=[shop.id, bill.id]),
            {f'qty_{ri.id}': '15', f'price_{ri.id}': '7500',
             'discount_amount': '0'}
        )
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.current_stock, 65)  # 50 + 15

    def test_edit_bill_decrease_qty(self):
        shop = SupplierShop.objects.create(name='Edit- Shop')
        bill = SupplierRestockBill.objects.create(supplier=shop)
        ri = SupplierRestockItem.objects.create(
            bill=bill, item=self.item1, quantity=10, total_price=5000
        )
        self.client.post(
            reverse('edit_restock_bill', args=[shop.id, bill.id]),
            {f'qty_{ri.id}': '3', f'price_{ri.id}': '1500',
             'discount_amount': '0'}
        )
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.current_stock, 53)  # 50 + 3

    def test_edit_bill_remove_item_by_zero_qty(self):
        shop = SupplierShop.objects.create(name='Edit0 Shop')
        bill = SupplierRestockBill.objects.create(supplier=shop)
        ri = SupplierRestockItem.objects.create(
            bill=bill, item=self.item1, quantity=10, total_price=5000
        )
        self.client.post(
            reverse('edit_restock_bill', args=[shop.id, bill.id]),
            {f'qty_{ri.id}': '0', f'price_{ri.id}': '0',
             'discount_amount': '0'}
        )
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.current_stock, 50)  # Stock fully reversed
        self.assertEqual(bill.items.count(), 0)

    # ── Payments ──

    def test_bulk_payment_updates_totals(self):
        shop = SupplierShop.objects.create(name='Bulk Pay Shop')
        SupplierRestockBill.objects.create(supplier=shop, total_amount=10000)
        self.client.post(
            reverse('add_shop_payment', args=[shop.id]),
            {'amount': '6000', 'payment_method': 'UPI', 'note': 'June'}
        )
        shop.refresh_from_db()
        self.assertEqual(shop.total_paid_amount, Decimal('6000'))
        self.assertEqual(shop.get_pending_balance, Decimal('4000'))



    def test_delete_payment_hard_deletes_and_logs(self):
        from workshop.models import DeletionLog
        shop = SupplierShop.objects.create(name='Del Pay Shop')
        SupplierRestockBill.objects.create(supplier=shop, total_amount=5000)
        payment = SupplierPayment.objects.create(supplier=shop, amount=3000)
        shop.refresh_from_db()
        self.assertEqual(shop.total_paid_amount, Decimal('3000'))

        self.client.post(
            reverse('delete_shop_payment', args=[shop.id, payment.id])
        )
        # Permanently deleted (not soft-trashed) + logged; shop balance recomputed.
        self.assertFalse(SupplierPayment.objects.filter(pk=payment.id).exists())
        self.assertTrue(
            DeletionLog.objects.filter(entity_type=DeletionLog.ENTITY_SUPPLIER_PAYMENT).exists()
        )
        shop.refresh_from_db()
        self.assertEqual(shop.total_paid_amount, Decimal('0'))

    # ── Discount ──

    def test_update_bill_discount(self):
        shop = SupplierShop.objects.create(name='Discount Shop')
        bill = SupplierRestockBill.objects.create(
            supplier=shop, total_amount=10000
        )
        self.client.post(
            reverse('update_bill_discount', args=[shop.id, bill.id]),
            {'discount_amount': '500'}
        )
        bill.refresh_from_db()
        self.assertEqual(bill.discount_amount, Decimal('500'))
        self.assertEqual(bill.get_effective_amount, Decimal('9500'))
        shop.refresh_from_db()
        self.assertEqual(shop.total_billed_amount, Decimal('9500'))

    # ── Bulk Pay Status Badges ──

    def test_status_fully_covered(self):
        shop = SupplierShop.objects.create(name='Covered Shop')
        SupplierRestockBill.objects.create(supplier=shop, total_amount=5000)
        SupplierPayment.objects.create(supplier=shop, amount=5000)
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id])
        )
        self.assertContains(response, 'Fully Covered')

    def test_status_unpaid(self):
        shop = SupplierShop.objects.create(name='Unpaid Shop')
        SupplierRestockBill.objects.create(supplier=shop, total_amount=5000)
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id])
        )
        self.assertContains(response, 'Unpaid')

    def test_status_partial(self):
        shop = SupplierShop.objects.create(name='Partial Shop')
        SupplierRestockBill.objects.create(supplier=shop, total_amount=5000)
        SupplierPayment.objects.create(supplier=shop, amount=3000)
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id])
        )
        self.assertContains(response, 'Partially:')

    def test_status_multiple_bills_cascade(self):
        """3 bills: oldest covered, middle partial, newest unpaid."""
        shop = SupplierShop.objects.create(name='Cascade Shop')
        SupplierRestockBill.objects.create(
            supplier=shop, total_amount=3000, bill_date='2025-01-01'
        )
        SupplierRestockBill.objects.create(
            supplier=shop, total_amount=3000, bill_date='2025-02-01'
        )
        SupplierRestockBill.objects.create(
            supplier=shop, total_amount=3000, bill_date='2025-03-01'
        )
        # Pay 5000 of 9000 total
        SupplierPayment.objects.create(supplier=shop, amount=5000)
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id]),
            {'filter': 'all'}   # bypass date filter — bills are from 2025
        )
        # Should contain at least one of each status
        self.assertContains(response, 'Fully Covered')
        self.assertContains(response, 'Unpaid')

    # ── AJAX Pagination ──

    def test_ajax_bills_returns_200(self):
        shop = SupplierShop.objects.create(name='AJAX Bills Shop')
        SupplierRestockBill.objects.create(supplier=shop, total_amount=1000)
        response = self.client.get(
            reverse('ajax_supplier_bills', args=[shop.id]), {'page': 1}
        )
        self.assertEqual(response.status_code, 200)

    def test_ajax_payments_returns_200(self):
        shop = SupplierShop.objects.create(name='AJAX Pay Shop')
        SupplierPayment.objects.create(supplier=shop, amount=1000)
        response = self.client.get(
            reverse('ajax_supplier_payments', args=[shop.id]), {'page': 1}
        )
        self.assertEqual(response.status_code, 200)

    def test_ajax_bills_empty_page_returns_empty(self):
        shop = SupplierShop.objects.create(name='Empty AJAX Shop')
        response = self.client.get(
            reverse('ajax_supplier_bills', args=[shop.id]), {'page': 99}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().strip(), '')

    def test_ajax_payments_empty_page_returns_empty(self):
        shop = SupplierShop.objects.create(name='Empty Pay AJAX Shop')
        response = self.client.get(
            reverse('ajax_supplier_payments', args=[shop.id]), {'page': 99}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().strip(), '')

    def test_ajax_bills_with_filter(self):
        shop = SupplierShop.objects.create(name='Filter AJAX Shop')
        response = self.client.get(
            reverse('ajax_supplier_bills', args=[shop.id]),
            {'page': 1, 'filter': 'month'}
        )
        self.assertEqual(response.status_code, 200)

    # ── Detail Page ──

    def test_shop_detail_page_loads(self):
        shop = SupplierShop.objects.create(name='Detail Shop')
        ShopCatalogItem.objects.create(shop=shop, item=self.item1)
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Detail Shop')
        self.assertContains(response, 'Engine Oil 5W30')

    def test_shop_detail_with_month_filter(self):
        shop = SupplierShop.objects.create(name='Filter Shop')
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id]),
            {'filter': 'month'}
        )
        self.assertEqual(response.status_code, 200)

    def test_shop_detail_with_year_filter(self):
        shop = SupplierShop.objects.create(name='Year Shop')
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id]),
            {'filter': 'year'}
        )
        self.assertEqual(response.status_code, 200)

    def test_shop_detail_with_custom_filter(self):
        shop = SupplierShop.objects.create(name='Custom Shop')
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id]),
            {'filter': 'custom', 'start_date': '2025-01-01',
             'end_date': '2025-12-31'}
        )
        self.assertEqual(response.status_code, 200)

    # ── Edge Cases ──

    def test_zero_balance_shows_all_clear(self):
        shop = SupplierShop.objects.create(name='Zero Shop')
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id])
        )
        self.assertContains(response, 'All Clear')

    def test_payment_hides_when_balance_zero(self):
        """Quick payment form section should NOT render when balance is 0."""
        shop = SupplierShop.objects.create(name='NoPay Shop')
        response = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id])
        )
        # The visible form heading should not appear (quickPayAmount appears in JS)
        self.assertNotContains(response, 'Record Payment')

    def test_invalid_payment_amount_rejected(self):
        shop = SupplierShop.objects.create(name='Invalid Pay Shop')

        self.client.post(
            reverse('add_shop_payment', args=[shop.id]),
            {'amount': 'abc', 'payment_method': 'CASH'}
        )
        self.assertEqual(
            SupplierPayment.objects.filter(supplier=shop).count(), 0
        )

    def test_zero_payment_rejected(self):
        shop = SupplierShop.objects.create(name='Zero Pay Shop')

        self.client.post(
            reverse('add_shop_payment', args=[shop.id]),
            {'amount': '0', 'payment_method': 'CASH'}
        )
        self.assertEqual(
            SupplierPayment.objects.filter(supplier=shop).count(), 0
        )

    def test_negative_payment_rejected(self):
        shop = SupplierShop.objects.create(name='Neg Pay Shop')

        self.client.post(
            reverse('add_shop_payment', args=[shop.id]),
            {'amount': '-500', 'payment_method': 'CASH'}
        )
        self.assertEqual(
            SupplierPayment.objects.filter(supplier=shop).count(), 0
        )

    def test_unauthenticated_redirects_to_login(self):
        unauthenticated_client = Client()
        response = unauthenticated_client.get(reverse('supplier_shop_list'))
        self.assertEqual(response.status_code, 302)
        # Supplier module is Office/Owner now → admin login gate
        self.assertIn('/admin-login/', response.url)

    def test_restock_select_no_session_redirects(self):
        shop = SupplierShop.objects.create(name='NoSession Shop')
        response = self.client.get(
            reverse('shop_restock_bill', args=[shop.id])
        )
        # Should redirect because no items selected in session
        self.assertEqual(response.status_code, 302)

    def test_item_suppliers_view(self):
        shop = SupplierShop.objects.create(name='ItemSupp Shop')
        ShopCatalogItem.objects.create(shop=shop, item=self.item1)
        response = self.client.get(
            reverse('inventory_item_suppliers', args=[self.item1.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ItemSupp Shop')


class ADuplicateShopNameIsRefusedNotCrashedTests(TestCase):
    """
    AUD-0089 — adding a Supplies Shop under a name already in use returned a
    500 instead of the "already exists" message the view was clearly written to
    show. It happened 40 times before anyone connected the crash to the cause.

    The mechanism is worth stating, because `try/except IntegrityError` around a
    write reads as correct and is the natural thing to write again. A failed
    statement leaves the database transaction unusable: the exception is caught,
    but every query after it — `messages.error`, which writes to the session,
    and the `render()` that follows — raises `TransactionManagementError`. So
    the error was handled and the request died anyway, downstream of the
    handling.

    These tests run inside `TestCase`'s own atomic block, which is that same
    condition, so a return to `create()`-inside-`except` fails here rather than
    in production.
    """

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='dup_tester', password='pass123')
        user.groups.add(office)
        self.client = Client()
        self.client.login(username='dup_tester', password='pass123')

    def _add(self, name, **extra):
        payload = {'name': name}
        payload.update(extra)
        return self.client.post(reverse('add_supplier_shop'), payload)

    def _messages(self, response):
        return ' '.join(str(m) for m in response.context['messages'])

    def test_an_exact_duplicate_is_reported_not_a_500(self):
        SupplierShop.objects.create(name='Kochi Auto Spares')

        response = self._add('Kochi Auto Spares')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SupplierShop.objects.filter(name='Kochi Auto Spares').count(), 1
        )
        self.assertIn('already exists', self._messages(response))

    def test_a_case_variant_is_the_same_shop(self):
        """
        `unique=True` is case-sensitive on both databases, so this one never
        raised at all — it quietly created a SECOND shop. Two shops mean two
        ledgers, and the supplier's real balance is then split across a name
        nobody is looking at.
        """
        SupplierShop.objects.create(name='Kochi Auto Spares')

        response = self._add('kochi AUTO spares')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SupplierShop.objects.count(), 1)

    def test_a_surrounding_space_is_the_same_shop(self):
        SupplierShop.objects.create(name='Kochi Auto Spares')

        self._add('  Kochi Auto Spares  ')

        self.assertEqual(SupplierShop.objects.count(), 1)

    def test_a_clash_with_an_ARCHIVED_shop_says_it_is_archived(self):
        """
        The plain message would name a shop that appears on no page the reader
        can open — `supplier_shop_list` filters `is_active=True`. Reactivating
        is also the right remedy, since the archived shop still holds the bills
        and payments already recorded against that supplier.
        """
        SupplierShop.objects.create(name='Old Depot', is_active=False)

        response = self._add('Old Depot')

        self.assertIn('archived', self._messages(response).lower())
        self.assertEqual(SupplierShop.objects.count(), 1)

    def test_a_rejected_name_does_not_cost_the_phone_and_address(self):
        SupplierShop.objects.create(name='Kochi Auto Spares')

        response = self._add(
            'Kochi Auto Spares', phone='9876543210', address='MG Road'
        )

        self.assertContains(response, '9876543210')
        self.assertContains(response, 'MG Road')

    def test_the_page_still_works_after_a_refusal(self):
        """
        The real symptom. The message was set and then the response that would
        have carried it never rendered — so this asserts the whole request
        completes, not merely that the write was refused.
        """
        SupplierShop.objects.create(name='Kochi Auto Spares')
        self._add('Kochi Auto Spares')

        following = self.client.get(reverse('supplier_shop_list'))
        self.assertEqual(following.status_code, 200)
        self.assertContains(following, 'Kochi Auto Spares')

    def test_a_first_shop_is_still_created_normally(self):
        response = self._add('Brand New Depot', phone='9', address='Road')

        self.assertRedirects(response, reverse('supplier_shop_list'))
        shop = SupplierShop.objects.get(name='Brand New Depot')
        self.assertEqual(shop.phone, '9')

    def test_a_blank_name_is_refused_without_writing_None(self):
        """`name` is NOT NULL; the view used to write whatever POST held."""
        response = self._add('   ')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SupplierShop.objects.count(), 0)


class RenamingASupplierShopOntoAnotherIsRefusedTests(TestCase):
    """
    The same AUD-0089 crash reached through Edit, plus the two holes beside it:
    a POST with no `name` key wrote `None` into a NOT NULL column, and an
    untrimmed value made " Depot " a different shop from "Depot".

    A collision is REFUSED here rather than merged, which is the opposite of
    what the master lists do — and deliberately. Two spellings of "Oil Filter"
    are one part; two Supplies Shops are two balances and two payment
    histories, and merging them would move money between suppliers with no way
    back.
    """

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='rename_tester', password='pass123')
        user.groups.add(office)
        self.client = Client()
        self.client.login(username='rename_tester', password='pass123')
        self.a = SupplierShop.objects.create(name='Depot A')
        self.b = SupplierShop.objects.create(name='Depot B')

    def _edit(self, shop, name):
        return self.client.post(
            reverse('edit_supplier_shop', args=[shop.id]),
            {'name': name, 'phone': '', 'address': ''},
        )

    def test_renaming_onto_another_shop_is_reported_not_a_500(self):
        response = self._edit(self.a, 'Depot B')

        self.assertEqual(response.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'Depot A')

    def test_a_case_variant_of_another_shop_is_refused_too(self):
        self._edit(self.a, 'depot b')

        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'Depot A')

    def test_resaving_a_shop_under_its_own_name_is_not_a_collision(self):
        """The exclude(pk=…) — otherwise a shop could never be edited at all."""
        response = self._edit(self.a, 'Depot A')

        self.assertRedirects(
            response, reverse('supplier_shop_detail', args=[self.a.id])
        )

    def test_recasing_a_shops_own_name_is_allowed(self):
        self._edit(self.a, 'DEPOT A')

        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'DEPOT A')

    def test_a_name_is_trimmed_on_the_way_in(self):
        self._edit(self.a, '  Depot Z  ')

        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'Depot Z')

    def test_a_post_with_no_name_leaves_the_shop_alone(self):
        response = self.client.post(
            reverse('edit_supplier_shop', args=[self.a.id]), {'phone': '123'}
        )

        self.assertEqual(response.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'Depot A')
