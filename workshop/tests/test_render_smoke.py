"""
Render smoke tests — exercise every screen touched by the delete/deactivate
restructure (and its neighbours) end-to-end, so a template/URL/Decimal regression
surfaces as a failing test instead of a broken page in production.

Each page is GET-rendered with realistic data and must return HTTP 200.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse

from workshop.models import (
    JobCard, JobCardSpareItem, JobCardLabourItem,
    SpareShop, SpareShopPayment, BulkPayer, BulkPaymentHistory,
    Mechanic, CashbookEntry, DeletionLog,
)
from inventory.models import (
    Category, Item, SupplierShop, SupplierRestockBill, SupplierRestockItem, SupplierPayment,
)


class RenderSmokeTests(TestCase):
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='owner', password='pw')
        self.owner.groups.add(self.owner_group)
        self.client = Client()
        self.client.login(username='owner', password='pw')

        self.mech = Mechanic.objects.create(name='Mech')

        # --- Accounts: active + archived ---
        self.shop = SpareShop.objects.create(name='Active Shop', phone='111')
        self.shop_archived = SpareShop.objects.create(name='Archived Shop', is_trashed=True)
        self.payer = BulkPayer.objects.create(customer_name='Active Fleet')
        self.payer_archived = BulkPayer.objects.create(customer_name='Archived Fleet', is_trashed=True)

        # --- Job cards: one clean (deletable), one heavy (guard blocks) ---
        self.clean_job = JobCard.objects.create(
            registration_number='KL01AA0001', brand_name='Honda', model_name='City',
            admitted_date=date.today(), lead_mechanic=self.mech,
        )
        self.heavy_job = JobCard.objects.create(
            registration_number='KL01BB0002', brand_name='Toyota', model_name='Innova',
            admitted_date=date.today() - timedelta(days=3), lead_mechanic=self.mech,
            bulk_payer=self.payer,
        )
        JobCardSpareItem.objects.create(
            job_card=self.heavy_job, shop=self.shop, spare_part_name='Oil',
            quantity=Decimal('1.50'), unit_price=Decimal('300'), total_price=Decimal('450'),
        )
        JobCardLabourItem.objects.create(
            job_card=self.heavy_job, job_description='Service', amount=Decimal('800'),
        )

        # --- Financial records ---
        self.shop_payment = SpareShopPayment.objects.create(
            shop=self.shop, amount=Decimal('200'), payment_method='CASH',
        )
        self.history = BulkPaymentHistory.objects.create(
            bulk_payer=self.payer, amount=Decimal('500'), payment_method='CASH',
            jobs_affected=1, details=json.dumps({'jobs': [], 'advance_used': '0', 'advance_stored': '0'}),
        )
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Tea', amount=Decimal('50'))

        # --- A deletion-history record to view ---
        self.log = DeletionLog.record(
            DeletionLog.ENTITY_CASHBOOK,
            CashbookEntry(entry_type='INCOME', category='Misc', amount=Decimal('10'), date=date.today()),
            user=self.owner, reason='test', amount=Decimal('10'), label='Test entry',
        )

        # --- Inventory + supplier side (Decimal quantities) ---
        self.cat = Category.objects.create(name='Fluids')
        self.item = Item.objects.create(
            category=self.cat, name='Engine Oil',
            current_stock=Decimal('1.50'), average_stock=Decimal('10'),
        )
        self.supplier = SupplierShop.objects.create(name='Supplier A')
        self.bill = SupplierRestockBill.objects.create(supplier=self.supplier, total_amount=Decimal('0'))
        SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=Decimal('2.50'), total_price=Decimal('500'),
        )
        SupplierPayment.objects.create(supplier=self.supplier, amount=Decimal('300'))

    def _get_ok(self, name, *args, **kwargs):
        url = reverse(name, args=args, kwargs=kwargs)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, f"{name} -> {resp.status_code}")
        return resp

    # ---- Core job-card / dashboard screens ----
    def test_core_screens(self):
        self._get_ok('home')
        self._get_ok('jobcard_list')
        self._get_ok('jobcard_detail', self.heavy_job.pk)   # renders clean_qty on spares
        self._get_ok('invoice_view', self.heavy_job.pk)     # renders clean_qty
        self._get_ok('completed_list')
        self._get_ok('paid_bills_list')
        self._get_ok('pending_payments_list')
        self._get_ok('cashbook')

    # ---- New delete/deactivate screens ----
    def test_deletion_and_archive_screens(self):
        self._get_ok('deletion_history')
        self._get_ok('deletion_history_detail', self.log.pk)
        self._get_ok('spare_shop_archived')
        r = self._get_ok('bulk_payer_archived')
        self.assertContains(r, 'Archived Fleet')

    # ---- Job-card delete confirm: clean vs guarded ----
    def test_jobcard_delete_confirm_states(self):
        clean = self.client.get(reverse('jobcard_delete', args=[self.clean_job.pk]))
        self.assertEqual(clean.status_code, 200)
        self.assertContains(clean, 'Delete Permanently')

        guarded = self.client.get(reverse('jobcard_delete', args=[self.heavy_job.pk]))
        self.assertEqual(guarded.status_code, 200)
        self.assertContains(guarded, "can't be deleted")

    # ---- Account detail screens (relabeled buttons) ----
    def test_account_detail_screens(self):
        self._get_ok('spare_shop_list')
        self._get_ok('spare_shop_detail', self.shop.pk)
        self._get_ok('bulk_payer_detail', self.payer.pk)

    # ---- Inventory + supplier screens (Float->Decimal) ----
    def test_inventory_supplier_screens(self):
        self._get_ok('inventory_list')
        self._get_ok('inventory_low_stock')
        self._get_ok('inventory_manage')
        self._get_ok('supplier_shop_list')
        self._get_ok('deactivated_supplier_shop_list')
        self._get_ok('supplier_shop_detail', self.supplier.id)
        self._get_ok('inventory_history')
        self._get_ok('inventory_history_mechanic', self.mech.pk)
        self._get_ok('inventory_category_detail', self.cat.id)
        self._get_ok('add_shop_catalog_item', self.supplier.id)
        self._get_ok('edit_restock_bill', self.supplier.id, self.bill.id)

    # ---- Restock bill entry screen (needs the session set by the picker) ----
    def test_restock_bill_screen(self):
        from inventory.models import ShopCatalogItem
        ShopCatalogItem.objects.create(shop=self.supplier, item=self.item)
        session = self.client.session
        session['restock_items'] = [str(self.item.id)]
        session.save()
        resp = self._get_ok('shop_restock_bill', self.supplier.id)
        self.assertContains(resp, 'Engine Oil')
        self._get_ok('shop_restock_select', self.supplier.id)

    # ---- Related owner sections that query the changed models ----
    def test_related_owner_sections(self):
        self._get_ok('manage_dashboard')
        self._get_ok('master_lists_home')
        self._get_ok('car_profile_list')
        self._get_ok('live_report')
        self._get_ok('analysis_dashboard')  # queries is_deleted / is_active (both kept)
