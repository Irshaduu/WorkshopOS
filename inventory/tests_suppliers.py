# inventory/tests_suppliers.py
"""
Full Test Suite for the Supplies Shops Section.
Covers: Shop CRUD, Catalog, Restock Bills, Stock Signals,
        Payments, Discounts, Bulk Pay Status, AJAX Pagination, Edge Cases.
"""
from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.db import transaction
from django.utils import timezone

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

    def test_the_name_and_the_actions_are_separately_addressable(self):
        """
        Same fix, same reason as spare_shop_detail (CLAUDE.md: "a shop header
        gives up its actions before it gives up its name"). Restock Bills,
        Payments and the ⋮ menu used to be a bare `flex-wrap` group, so on a
        phone they wrapped one at a time and the ⋮ stranded on its own row
        below the other two. Both halves need their own hook or the phone
        rule below has nothing to target and silently does nothing.
        """
        shop = SupplierShop.objects.create(name='Layout Shop')
        page = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id])
        ).content.decode()

        self.assertIn('shop-headrow', page)
        self.assertIn('shop-titleblock', page)
        self.assertIn('shop-actions', page)

    def test_on_a_phone_the_actions_take_their_own_row_aligned_right(self):
        shop = SupplierShop.objects.create(name='Layout Shop')
        page = self.client.get(
            reverse('supplier_shop_detail', args=[shop.id])
        ).content.decode()

        self.assertIn('max-width: 767.98px', page)
        # 100% basis is what FORCES the break — without it the two boxes
        # share the line again the moment they happen to fit.
        self.assertIn('flex: 1 1 100%', page)
        self.assertIn('justify-content: flex-end', page)

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
        # One sign-in page for every role. This asserted '/admin-login/' until
        # 2026-08-12: Office/Owner pages used to bounce to a separate admin door,
        # which is what told anyone probing a protected URL that one existed.
        self.assertIn('/login/', response.url)

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


class TheShopPageOwnPayFormCarriesTheDateTests(TestCase):
    """
    THE OTHER DOOR, and the one people actually use.

    `SupplierPayment` gained its date input on `add_payment.html` — a separate
    stacked page — while the INLINE "Record a Payment" form on the shop's own
    page was missed. It posts through a hidden form carrying amount, method and
    note, so no date ever reached the view and every payment made from there
    fell straight back to `default=timezone.now`: the keystroke, which is the
    exact thing the column exists to stop trusting.

    The view has read `posted_date()` all along, so nothing about the rule was
    wrong — only that this form never handed it anything. That is why it
    survived the pass that fixed the rule: a view test would pass either way.
    So this asserts the FORM, through the page, not the view.
    """

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='sup_inline', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='sup_inline', password='pw')

        self.shop = SupplierShop.objects.create(name='Inline Depot')
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=50000)
        self.today = timezone.localdate()

    def test_the_page_renders_a_date_box_wired_into_the_posted_form(self):
        html = self.client.get(
            reverse('supplier_shop_detail', args=[self.shop.id])).content.decode()

        self.assertIn('id="quickPayDate"', html,
                      'the inline form needs a visible date control')
        self.assertIn('name="date" id="hfDate"', html,
                      'and a hidden field, or the value never reaches the view')
        self.assertIn(self.today.isoformat(), html,
                      'the box defaults to today and is capped at it')

    def test_a_back_dated_payment_from_this_form_is_stored_on_that_day(self):
        moved = self.today - timedelta(days=11)

        self.client.post(reverse('add_shop_payment', args=[self.shop.id]),
                         {'amount': '1000', 'payment_method': 'CASH',
                          'date': moved.isoformat()})

        self.assertEqual(SupplierPayment.objects.get().date, moved)

    def test_the_heading_matches_the_spare_shop_word_for_word(self):
        """
        Two screens an owner opens in one sitting called the same act two
        things — "Record Payment" here, "Make a Bulk Payment" there.
        """
        html = self.client.get(
            reverse('supplier_shop_detail', args=[self.shop.id])).content.decode()
        self.assertIn('Record a Payment', html)
        self.assertNotIn('Make a Bulk Payment', html)


class ASupplierPaymentIsDatedByTheDayTheMoneyMovedTests(TestCase):
    """
    `SupplierPayment.date` has existed since day one and nothing ever wrote to
    it: the form rendered no date input and the view read none, so every
    payment fell back to `default=timezone.now` and was stamped with the
    KEYSTROKE.

    The collector on this side comes round weekly or monthly, so a bill settled
    at month end and keyed the following week landed in the wrong month on this
    shop's own Last Month filter, with no route to correct it — the same defect
    `CashbookEntry.date` exists to stop, and the one its spare-shop sibling was
    fixed for. Nothing here reaches the Profit page: a payment settles a debt
    the restock bill already expensed.

    The rule itself lives in `workshop/money_dates.py` and is tested there.
    What is asserted here is that this view actually CALLS it — an import can
    be right while the caller adds a clause of its own.
    """

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='sup_dates', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='sup_dates', password='pw')

        self.shop = SupplierShop.objects.create(name='Depot Dates')
        SupplierRestockBill.objects.create(supplier=self.shop, total_amount=50000)
        self.today = timezone.localdate()

    def _pay(self, **extra):
        data = {'amount': '1000', 'payment_method': 'CASH'}
        data.update(extra)
        return self.client.post(
            reverse('add_shop_payment', args=[self.shop.id]), data)

    def test_a_back_dated_payment_is_stored_on_the_day_it_was_typed_for(self):
        """The whole point: the office keys last month's settlement this week."""
        moved = self.today - timedelta(days=12)
        self._pay(date=moved.isoformat())

        payment = SupplierPayment.objects.get(supplier=self.shop)
        self.assertEqual(payment.date, moved)
        # created_at stays as the audit trail — it records the keystroke, and
        # the two now answer different questions rather than one badly.
        self.assertEqual(timezone.localdate(payment.created_at), self.today)

    def test_a_payment_with_no_date_posted_still_lands_on_today(self):
        """
        Every existing caller and test posts no date. Falling back rather than
        400ing keeps them right, and today is the same answer the column gave
        when it was not editable at all.
        """
        self._pay()

        self.assertEqual(
            SupplierPayment.objects.get(supplier=self.shop).date, self.today)

    def test_an_unreadable_date_falls_back_instead_of_500ing(self):
        self._pay(date='not-a-date')

        self.assertEqual(
            SupplierPayment.objects.get(supplier=self.shop).date, self.today)

    def test_a_future_dated_payment_is_refused_outright(self):
        """
        Money dated forward is a mistyped year far more often than a plan, and
        this workshop pays at the counter. Refused BEFORE the row is written —
        nothing is clamped, because a clamp saves a date nobody typed.
        """
        response = self._pay(date=(self.today + timedelta(days=1)).isoformat())

        self.assertEqual(SupplierPayment.objects.filter(supplier=self.shop).count(), 0)
        self.assertContains(response, 'cannot be dated in the future')

    def test_the_form_offers_a_date_box_capped_at_today(self):
        """
        The control is this page's own idiom — a stacked full-width input, not
        the spare shop's 46px glyph, which is compact because it sits in an
        inline row. `max` is the browser half of the refusal above.
        """
        html = self.client.get(
            reverse('add_shop_payment', args=[self.shop.id])).content.decode()

        self.assertIn('name="date"', html)
        self.assertIn(f'max="{self.today.isoformat()}"', html)
        self.assertIn(f'value="{self.today.isoformat()}"', html)

    def test_the_amber_state_can_actually_beat_bootstrap(self):
        """
        The back-dated cue is a CLASS carrying `!important`, never an inline
        style — and that is not a preference, it is the only thing that works.
        The input wears Bootstrap's `bg-light` and `border-0`, both `!important`
        utilities, and an `!important` stylesheet rule beats a normal inline
        declaration. Driving the colours from `el.style.*` therefore set the
        inline properties and rendered NOTHING: measured computed `0px none`
        on the border and the unchanged grey behind it, while reading back
        amber from `el.style.borderColor` exactly as intended.

        Nothing in this suite executes CSS, so what is asserted is the shape
        that made it work: a real rule, marked important, and a script that
        toggles the class instead of painting the element directly.
        """
        html = self.client.get(
            reverse('add_shop_payment', args=[self.shop.id])).content.decode()

        self.assertIn('.pay-date-custom', html)
        rule = html.split('.pay-date-custom', 1)[1].split('}', 1)[0]
        self.assertIn('!important', rule)
        self.assertIn('#f59e0b', rule)          # the border
        self.assertIn('#fffbeb', rule)          # the ground

        self.assertIn("classList.toggle('pay-date-custom'", html)
        # The inline route is the one that silently does nothing here.
        self.assertNotIn('style.borderColor', html)
        self.assertNotIn('style.background', html)

    def test_the_shop_page_windows_payments_by_that_date(self):
        """
        The column is only worth having if what reads it agrees. A payment
        back-dated out of the window must drop out of the shop's own page.
        """
        # ⚠ THE OLD ROW IS BUILT ON THE MODEL, NOT POSTED THROUGH THE FORM.
        # This test is about what the shop PAGE reads, and it wants a payment
        # far outside every window — 400 days — to prove one drops out of it.
        # Office may no longer FILE one that old (`money_dates.too_far_back`),
        # but one can certainly exist: an owner may file it, and go-live data
        # carries older rows still. Posting it would turn this into a test of
        # the form's policy, which is pinned in `test_backdate_floor.py`.
        SupplierPayment.objects.create(
            supplier=self.shop, amount=Decimal('4000'), payment_method='CASH',
            date=self.today - timedelta(days=400))
        self._pay(amount='1500')

        self.shop.refresh_from_db()
        # The BALANCE is never windowed - a debt is not a period.
        self.assertEqual(self.shop.total_paid_amount, Decimal('5500'))

        payments = list(SupplierPayment.objects.filter(
            supplier=self.shop, date__range=(self.today.replace(day=1), self.today)))
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0].amount, Decimal('1500'))


class TheCatalogActionWrapsUPWARDNotDownTests(TestCase):
    """
    On a narrow screen "New Stock Entry" sits ABOVE the Shop Catalog heading and
    hard against the right edge (2026-08-28).

    Left as it wrapped, it landed UNDER the heading and hard against the LEFT
    margin — the loudest control on the page, below the thing it acts on, in the
    column the page uses for headings.

    Done with `flex-wrap: wrap-reverse` rather than a media query, and that is
    the point: the row is untouched while it fits, and the moment it does not
    the second item lands on the line above the first. No breakpoint to pick and
    nothing to keep in step with the content's own width. Measured: one row at
    1280 and 520, wrapped and flush right at 375 and 320.
    """

    TEMPLATE = 'inventory/templates/inventory/suppliers/shop_detail.html'

    def source(self):
        with open(self.TEMPLATE, encoding='utf-8') as fh:
            return fh.read()

    def test_the_header_wraps_upward(self):
        rule = self.source().split('.sect-hdr {', 1)[1].split('}', 1)[0]
        self.assertIn('flex-wrap: wrap-reverse', rule)

    def test_the_bootstrap_wrap_utility_is_off_the_element(self):
        """
        ⚠ THE LOAD-BEARING HALF. Bootstrap's utilities are `!important`, so
        `class="... flex-wrap ..."` on the element sets `flex-wrap: wrap` and
        beats the `wrap-reverse` declared in the stylesheet — the rule would be
        matched, computed and ignored, which looks exactly like a rule that is
        not being applied at all. The class had to come off the element; the
        same trap CLAUDE.md records for an important utility outranking a normal
        inline style.
        """
        source = self.source()
        self.assertIn('class="sect-hdr gap-3"', source)
        self.assertNotIn('class="sect-hdr flex-wrap', source)

    def test_the_action_is_pushed_to_the_right_edge(self):
        """
        `justify-content: space-between` does nothing for a line holding ONE
        item, so the wrapped line would put it at flex-start. The auto margin
        eats the free space to its left; on the single-row layout it changes
        nothing, because space-between has already pushed it there.
        """
        source = self.source()
        rule = source.split('.sect-hdr-action {', 1)[1].split('}', 1)[0]
        self.assertIn('margin-left: auto', rule)
        # …and the button actually carries the class.
        self.assertIn('sect-hdr-action btn btn-primary', source)
        self.assertIn('New Stock Entry', source)
