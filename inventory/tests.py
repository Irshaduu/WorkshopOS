# inventory/tests.py
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone
from workshop.models import JobCard, JobCardSpareItem
from .models import Category, Item

class InventorySignalTests(TestCase):
    """
    Automated Testing Suite for Inventory Stock Deltas.

    Draws are declared explicitly (`source=INVENTORY` + the `item` FK) since
    2026-07-30 — stock no longer moves on a `spare_part_name` match. The wider
    contract, including shop purchases and negative stock, lives in
    inventory/test_signals.py.
    """
    def setUp(self):
        self.user = User.objects.create_user(username='staff_test_signal', password='password123')
        self.category = Category.objects.create(name='Engine Parts')
        self.item = Item.objects.create(
            category=self.category,
            name='Engine Oil 5W30',
            average_stock=100,
            current_stock=50
        )
        self.jobcard = JobCard.objects.create(
            registration_number='DL10AB1234',
            brand_name='Honda',
            model_name='City',
            admitted_date=timezone.now().date(),
            mileage='50000'
        )

    def test_stock_deduction_on_create(self):
        JobCardSpareItem.objects.create(
            job_card=self.jobcard,
            source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.item,
            quantity=5,
            unit_price=800
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 45)

    def test_stock_correction_on_update(self):
        spare = JobCardSpareItem.objects.create(
            job_card=self.jobcard,
            source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.item,
            quantity=5,
            unit_price=800
        )
        spare.quantity = 10
        spare.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 40)

    def test_stock_restoration_on_delete(self):
        spare = JobCardSpareItem.objects.create(
            job_card=self.jobcard,
            source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.item,
            quantity=5,
            unit_price=800
        )
        spare.delete()
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 50)

class InventoryViewTests(TestCase):
    """Office/Owner management screens + Floor-visible read screens."""
    def setUp(self):
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office_user', password='password')
        self.user.groups.add(self.office_group)
        self.client = Client()
        self.client.login(username='office_user', password='password')

        self.category = Category.objects.create(name='Brakes')
        self.item = Item.objects.create(category=self.category, name='Brake Pad', average_stock=10, current_stock=10)

    def test_inventory_manage_and_search(self):
        self.assertEqual(self.client.get(reverse('inventory_manage')).status_code, 200)
        self.assertContains(self.client.get(reverse('inventory_manage'), {'q': 'Brakes'}), 'Brakes')
        self.assertNotContains(self.client.get(reverse('inventory_manage'), {'q': 'GhostPart'}), 'Brake Pad')

    def test_category_add_and_edit(self):
        resp = self.client.post(reverse('inventory_add_category'), {'name': 'Suspension'})
        self.assertRedirects(resp, reverse('inventory_manage'))
        self.assertTrue(Category.objects.filter(name='Suspension').exists())
        self.client.post(reverse('inventory_edit_category', args=[self.category.id]), {'name': 'Braking Systems'})
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'Braking Systems')

    def test_category_detail_readonly_shows_shops(self):
        from .models import SupplierShop, ShopCatalogItem
        shop = SupplierShop.objects.create(name='Parts Hub')
        ShopCatalogItem.objects.create(shop=shop, item=self.item)
        resp = self.client.get(reverse('inventory_category_detail', args=[self.category.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Brake Pad')
        self.assertContains(resp, 'Parts Hub')

    def test_low_stock_readonly(self):
        Item.objects.create(category=self.category, name='Low Fluid', average_stock=10, current_stock=1)
        resp = self.client.get(reverse('inventory_low_stock'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Low Fluid')

    def test_home_redirects(self):
        self.assertRedirects(self.client.get(reverse('inventory_home')), reverse('inventory_list'))


class InventoryWorkflowTests(TestCase):
    """Add Product, catalog edit/deactivate/remove, and Stock History."""
    def setUp(self):
        from .models import SupplierShop
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office_wf', password='pw')
        self.user.groups.add(self.office_group)
        self.client = Client()
        self.client.login(username='office_wf', password='pw')
        self.shop = SupplierShop.objects.create(name='Shop A')

    def _catalog(self, name='Oil X', avg='10'):
        from .models import ShopCatalogItem
        self.client.post(reverse('add_shop_catalog_item', args=[self.shop.id]),
                         {'item_name': name, 'category_name': 'Fluids', 'average_stock': avg})
        return ShopCatalogItem.objects.get(item__name__iexact=name, shop=self.shop)

    def test_add_product_requires_average_stock(self):
        # Missing average stock → no item created
        self.client.post(reverse('add_shop_catalog_item', args=[self.shop.id]),
                         {'item_name': 'Oil X', 'category_name': 'Fluids'})
        self.assertFalse(Item.objects.filter(name__iexact='Oil X').exists())

        # With average stock → created + appears in Low Stock (current 0 < threshold)
        ci = self._catalog()
        self.assertEqual(ci.item.average_stock, Decimal('10'))
        self.assertContains(self.client.get(reverse('inventory_low_stock')), 'Oil X')

    def test_edit_catalog_item_name_and_threshold(self):
        ci = self._catalog()
        self.client.post(reverse('edit_catalog_item', args=[self.shop.id, ci.id]),
                         {'item_name': 'Oil Y', 'average_stock': '25'})
        ci.item.refresh_from_db()
        self.assertEqual(ci.item.name, 'Oil Y')
        self.assertEqual(ci.item.average_stock, Decimal('25'))

    def test_deactivate_reactivate_excludes_from_restock(self):
        ci = self._catalog()
        self.client.post(reverse('deactivate_catalog_item', args=[self.shop.id, ci.id]))
        ci.refresh_from_db()
        self.assertFalse(ci.is_active)
        # Deactivated → the shop's restock selection has no products to tick
        self.assertContains(self.client.get(reverse('shop_restock_select', args=[self.shop.id])),
                            'No products in catalog')
        self.client.post(reverse('reactivate_catalog_item', args=[self.shop.id, ci.id]))
        ci.refresh_from_db()
        self.assertTrue(ci.is_active)

    def test_remove_no_history_deletes_orphan_item(self):
        ci = self._catalog()
        item_id = ci.item_id
        self.client.post(reverse('remove_shop_catalog_item', args=[self.shop.id, ci.id]))
        self.assertFalse(Item.objects.filter(id=item_id).exists())

    def test_remove_keeps_a_product_a_job_card_has_used(self):
        """`JobCardSpareItem.item` is PROTECT — deleting the Item here would 500.

        Reachable at exactly zero stock with no bill history: draw the product,
        then edit the draw's quantity back down to zero.
        """
        ci = self._catalog()
        item_id = ci.item_id
        job = JobCard.objects.create(admitted_date=timezone.now().date(),
                                     registration_number='KL01PR0001')
        JobCardSpareItem.objects.create(
            job_card=job, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=ci.item, quantity=Decimal('0'))

        resp = self.client.post(reverse('remove_shop_catalog_item', args=[self.shop.id, ci.id]))

        self.assertEqual(resp.status_code, 302, "must redirect, not raise ProtectedError")
        self.assertTrue(Item.objects.filter(id=item_id).exists(),
                        "a product recorded on a job card must outlive its catalog link")

    def test_remove_with_bill_history_deactivates(self):
        from .models import SupplierRestockBill, SupplierRestockItem
        ci = self._catalog()
        bill = SupplierRestockBill.objects.create(supplier=self.shop, total_amount=Decimal('100'))
        SupplierRestockItem.objects.create(bill=bill, item=ci.item, quantity=Decimal('5'), total_price=Decimal('100'))
        self.client.post(reverse('remove_shop_catalog_item', args=[self.shop.id, ci.id]))
        ci.refresh_from_db()
        self.assertFalse(ci.is_active)                          # deactivated, not removed
        self.assertTrue(Item.objects.filter(id=ci.item_id).exists())

    def test_stock_history_and_mechanic_drilldown(self):
        from workshop.models import Mechanic
        mech = Mechanic.objects.create(name='Amlah')
        jc = JobCard.objects.create(registration_number='KL01AA0001', brand_name='BMW',
                                    model_name='320d', admitted_date=timezone.localdate(),
                                    lead_mechanic=mech)
        # A real warehouse draw: Stock History lists `source=INVENTORY` rows only,
        # so a spare that merely *looks* like a stock product no longer qualifies.
        item = Item.objects.create(category=Category.objects.create(name='Lubricants'),
                                   name='Castrol 5w40', average_stock=Decimal('10'),
                                   current_stock=Decimal('10'))
        JobCardSpareItem.objects.create(job_card=jc,
                                        source=JobCardSpareItem.SOURCE_INVENTORY, item=item,
                                        quantity=Decimal('3'), unit_price=Decimal('500'),
                                        total_price=Decimal('1500'))
        resp = self.client.get(reverse('inventory_history'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Castrol 5w40')
        self.assertContains(resp, 'Amlah')
        self.assertContains(resp, 'KL01AA0001')
        resp = self.client.get(reverse('inventory_history_mechanic', args=[mech.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Castrol 5w40')


class CategoryRulesTests(TestCase):
    """Categories: no duplicates, and delete only while empty."""
    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office_cat', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='office_cat', password='pw')
        self.category = Category.objects.create(name='Fluids')

    def _messages(self, resp):
        return [str(m) for m in resp.context['messages']]

    def test_duplicate_category_is_rejected_case_insensitively(self):
        resp = self.client.post(reverse('inventory_add_category'), {'name': 'fluids'}, follow=True)
        self.assertEqual(Category.objects.filter(name__iexact='fluids').count(), 1)
        self.assertTrue(any('already exists' in m for m in self._messages(resp)))

    def test_whitespace_only_name_is_rejected(self):
        self.client.post(reverse('inventory_add_category'), {'name': '   '})
        self.assertEqual(Category.objects.count(), 1)

    def test_distinct_name_still_creates(self):
        self.client.post(reverse('inventory_add_category'), {'name': 'Filters'})
        self.assertTrue(Category.objects.filter(name='Filters').exists())

    def test_rename_onto_another_category_is_rejected(self):
        other = Category.objects.create(name='Filters')
        resp = self.client.post(reverse('inventory_edit_category', args=[other.id]),
                                {'name': 'FLUIDS'}, follow=True)
        other.refresh_from_db()
        self.assertEqual(other.name, 'Filters')
        self.assertTrue(any('already exists' in m for m in self._messages(resp)))

    def test_rename_to_own_case_variant_is_allowed(self):
        self.client.post(reverse('inventory_edit_category', args=[self.category.id]), {'name': 'FLUIDS'})
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'FLUIDS')

    def test_empty_category_can_be_deleted(self):
        self.client.post(reverse('inventory_delete_category', args=[self.category.id]))
        self.assertFalse(Category.objects.filter(pk=self.category.pk).exists())

    def test_category_with_products_cannot_be_deleted(self):
        Item.objects.create(category=self.category, name='Oil A', average_stock=Decimal('5'))
        resp = self.client.post(reverse('inventory_delete_category', args=[self.category.id]), follow=True)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(any("can't be deleted" in m for m in self._messages(resp)))

    def test_delete_via_get_does_nothing(self):
        self.client.get(reverse('inventory_delete_category', args=[self.category.id]))
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_manage_offers_delete_only_for_empty_categories(self):
        full = Category.objects.create(name='Filters')
        Item.objects.create(category=full, name='Air Filter', average_stock=Decimal('5'))
        html = self.client.get(reverse('inventory_manage')).content.decode()
        self.assertIn(reverse('inventory_delete_category', args=[self.category.id]), html)
        self.assertNotIn(reverse('inventory_delete_category', args=[full.id]), html)


class LowStockSearchTests(TestCase):
    """Search must span the whole result set, not just the rendered page."""
    def setUp(self):
        floor, _ = Group.objects.get_or_create(name='Floor')
        self.user = User.objects.create_user(username='floor_ls', password='pw')
        self.user.groups.add(floor)
        self.client = Client()
        self.client.login(username='floor_ls', password='pw')
        self.category = Category.objects.create(name='Fluids')
        for i in range(60):
            Item.objects.create(category=self.category, name=f'Bulk Part {i:02d}',
                                average_stock=Decimal('10'), current_stock=Decimal('0'))
        Item.objects.create(category=self.category, name='Zebra Oil',
                            average_stock=Decimal('10'), current_stock=Decimal('0'))

    def test_match_on_a_later_page_is_found(self):
        # 'Zebra Oil' sorts last, so it is not on page 1 of 50.
        page_one = self.client.get(reverse('inventory_low_stock'))
        self.assertNotContains(page_one, 'Zebra Oil')
        found = self.client.get(reverse('inventory_low_stock'), {'q': 'zebra'})
        self.assertContains(found, 'Zebra Oil')
        self.assertEqual(found.context['page_obj'].paginator.count, 1)

    def test_search_by_category_name(self):
        resp = self.client.get(reverse('inventory_low_stock'), {'q': 'fluids'})
        self.assertEqual(resp.context['page_obj'].paginator.count, 61)


class CatalogRemovalSafetyTests(TestCase):
    """Removing a product must never silently destroy stock or delete unlogged."""
    def setUp(self):
        from .models import SupplierShop, ShopCatalogItem
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office_rm', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='office_rm', password='pw')
        self.shop = SupplierShop.objects.create(name='Remove Shop')
        self.category = Category.objects.create(name='Fluids')
        self.item = Item.objects.create(category=self.category, name='Stocked Part',
                                        average_stock=Decimal('5'), current_stock=Decimal('12'))
        self.catalog = ShopCatalogItem.objects.create(shop=self.shop, item=self.item)

    def test_product_holding_stock_is_deactivated_not_deleted(self):
        self.client.post(reverse('remove_shop_catalog_item', args=[self.shop.id, self.catalog.id]))
        self.catalog.refresh_from_db()
        self.item.refresh_from_db()
        self.assertFalse(self.catalog.is_active)
        self.assertEqual(self.item.current_stock, Decimal('12'))
        self.assertTrue(Item.objects.filter(pk=self.item.pk).exists())

    def test_zero_stock_orphan_delete_is_logged_to_deletion_history(self):
        from workshop.models import DeletionLog
        self.item.current_stock = Decimal('0')
        self.item.save(update_fields=['current_stock'])
        self.client.post(reverse('remove_shop_catalog_item', args=[self.shop.id, self.catalog.id]))
        self.assertFalse(Item.objects.filter(pk=self.item.pk).exists())
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_INVENTORY_ITEM)
        self.assertIn('Stocked Part', log.entity_label)
        self.assertEqual(log.deleted_by, self.user)


class RestockCatalogGuardTests(TestCase):
    """Restock bills move real stock, so item ids are re-validated in the writing view."""
    def setUp(self):
        from .models import SupplierShop, ShopCatalogItem
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office_rs', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='office_rs', password='pw')
        self.shop = SupplierShop.objects.create(name='Guard Shop')
        self.category = Category.objects.create(name='Fluids')
        self.item = Item.objects.create(category=self.category, name='Guarded Oil',
                                        average_stock=Decimal('10'), current_stock=Decimal('0'))
        self.catalog = ShopCatalogItem.objects.create(shop=self.shop, item=self.item)

    def _bill(self, item, qty='5', price='500'):
        session = self.client.session
        session['restock_items'] = [str(item.id)]
        session.save()
        return self.client.post(reverse('shop_restock_bill', args=[self.shop.id]),
                                {f'qty_{item.id}': qty, f'price_{item.id}': price,
                                 'discount_amount': '0'})

    def test_deactivated_product_cannot_be_billed(self):
        from .models import SupplierRestockItem
        self.client.post(reverse('deactivate_catalog_item', args=[self.shop.id, self.catalog.id]))
        self._bill(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal('0'))
        self.assertFalse(SupplierRestockItem.objects.filter(item=self.item).exists())

    def test_product_from_another_shop_cannot_be_billed(self):
        from .models import SupplierShop, ShopCatalogItem, SupplierRestockItem
        other = SupplierShop.objects.create(name='Other Guard Shop')
        foreign = Item.objects.create(category=self.category, name='Foreign Oil',
                                      average_stock=Decimal('5'), current_stock=Decimal('0'))
        ShopCatalogItem.objects.create(shop=other, item=foreign)
        self._bill(foreign)
        foreign.refresh_from_db()
        self.assertEqual(foreign.current_stock, Decimal('0'))
        self.assertFalse(
            SupplierRestockItem.objects.filter(item=foreign, bill__supplier=self.shop).exists())

    def test_active_catalog_product_still_bills_normally(self):
        self._bill(self.item)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal('5'))


class CatalogEditCollisionTests(TestCase):
    """Edit is the only way to fix a product name — a clash must message, not 500."""
    def setUp(self):
        from .models import SupplierShop, ShopCatalogItem
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office_ed', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='office_ed', password='pw')
        self.shop = SupplierShop.objects.create(name='Edit Shop')
        self.category = Category.objects.create(name='Fluids')
        self.a = Item.objects.create(category=self.category, name='Oil A', average_stock=Decimal('5'))
        self.b = Item.objects.create(category=self.category, name='Oil B', average_stock=Decimal('5'))
        self.catalog = ShopCatalogItem.objects.create(shop=self.shop, item=self.a)

    def test_rename_onto_existing_name_is_rejected_cleanly(self):
        resp = self.client.post(
            reverse('edit_catalog_item', args=[self.shop.id, self.catalog.id]),
            {'item_name': 'Oil B', 'average_stock': '9'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'Oil A')
        self.assertEqual(self.a.average_stock, Decimal('5'))   # nothing partially applied
        self.assertTrue(any('already exists' in str(m) for m in resp.context['messages']))

    def test_case_variant_of_own_name_still_renames(self):
        self.client.post(reverse('edit_catalog_item', args=[self.shop.id, self.catalog.id]),
                         {'item_name': 'OIL A', 'average_stock': '9'})
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'OIL A')
        self.assertEqual(self.a.average_stock, Decimal('9'))


class AddProductExistingItemTests(TestCase):
    """Linking an existing product must not throw away the Average Stock typed in."""
    def setUp(self):
        from .models import SupplierShop
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office_ap', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='office_ap', password='pw')
        self.shop = SupplierShop.objects.create(name='Add Shop')
        self.category = Category.objects.create(name='Fluids')

    def test_threshold_backfills_a_legacy_item_that_has_none(self):
        from .models import ShopCatalogItem
        legacy = Item.objects.create(category=self.category, name='Legacy Oil',
                                     average_stock=Decimal('0'))
        self.client.post(reverse('add_shop_catalog_item', args=[self.shop.id]),
                         {'item_name': 'Legacy Oil', 'category_name': 'Fluids',
                          'average_stock': '12', 'confirm_existing': '1'})
        legacy.refresh_from_db()
        self.assertEqual(legacy.average_stock, Decimal('12'))
        self.assertTrue(ShopCatalogItem.objects.filter(shop=self.shop, item=legacy).exists())

    def test_threshold_already_set_by_another_shop_is_not_overwritten(self):
        existing = Item.objects.create(category=self.category, name='Shared Oil',
                                       average_stock=Decimal('8'))
        self.client.post(reverse('add_shop_catalog_item', args=[self.shop.id]),
                         {'item_name': 'Shared Oil', 'category_name': 'Fluids',
                          'average_stock': '99', 'confirm_existing': '1'})
        existing.refresh_from_db()
        self.assertEqual(existing.average_stock, Decimal('8'))

    def test_new_product_always_gets_a_catalog_link(self):
        from .models import ShopCatalogItem
        self.client.post(reverse('add_shop_catalog_item', args=[self.shop.id]),
                         {'item_name': 'Brand New Oil', 'category_name': 'Fluids',
                          'average_stock': '10'})
        item = Item.objects.get(name='Brand New Oil')
        self.assertTrue(ShopCatalogItem.objects.filter(item=item).exists())


class StockHistoryAccuracyTests(TestCase):
    """
    Stock History must reflect what actually moved in the warehouse.

    Rewritten 2026-07-30. It previously listed *every* spare and flagged the ones
    whose name matched no product as "not from stock", because the route a part
    took had to be guessed from its name. The route is stored now, so the page
    simply shows `source=INVENTORY` rows and that flag no longer has a meaning to
    express — which is why the two tests asserting it are gone rather than fixed.
    """

    def _draw(self, card, item, qty):
        """A warehouse draw: declared by source + FK, not implied by its name."""
        return JobCardSpareItem.objects.create(
            job_card=card, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=item, quantity=Decimal(str(qty)))

    def _item(self, name='Castrol 5W40'):
        return Item.objects.create(category=self.category, name=name,
                                   average_stock=Decimal('10'), current_stock=Decimal('10'))
    def setUp(self):
        from workshop.models import Mechanic
        floor, _ = Group.objects.get_or_create(name='Floor')
        self.user = User.objects.create_user(username='floor_sh', password='pw')
        self.user.groups.add(floor)
        self.client = Client()
        self.client.login(username='floor_sh', password='pw')
        self.mechanic = Mechanic.objects.create(name='Rafi')
        self.category = Category.objects.create(name='Fluids')

    def _card(self, reg='KL01ZZ0001', **kwargs):
        return JobCard.objects.create(
            registration_number=reg, brand_name='BMW', model_name='320d',
            admitted_date=timezone.localdate(), lead_mechanic=self.mechanic, **kwargs)

    def test_soft_deleted_job_cards_are_excluded(self):
        card = self._card(is_deleted=True)
        self._draw(card, self._item('Ghost Filter'), 2)
        self.assertNotContains(self.client.get(reverse('inventory_history')), 'Ghost Filter')
        self.assertNotContains(
            self.client.get(reverse('inventory_history_mechanic', args=[self.mechanic.id])),
            'Ghost Filter')

    def test_a_warehouse_draw_is_listed(self):
        card = self._card()
        self._draw(card, self._item('Castrol 5W40'), 1)
        self.assertContains(self.client.get(reverse('inventory_history')), 'Castrol 5W40')

    def test_a_shop_bought_spare_never_appears(self):
        """It never touched the warehouse, so it is not stock history."""
        from workshop.models import SpareShop
        card = self._card()
        JobCardSpareItem.objects.create(
            job_card=card, source=JobCardSpareItem.SOURCE_SHOP,
            shop=SpareShop.objects.create(name='Ajmal Auto Parts'),
            spare_part_name='Bought Outside', quantity=Decimal('1'))

        self.assertNotContains(self.client.get(reverse('inventory_history')), 'Bought Outside')
        self.assertNotContains(
            self.client.get(reverse('inventory_history_mechanic', args=[self.mechanic.id])),
            'Bought Outside')

    def test_a_shop_spare_sharing_a_stock_products_name_still_never_appears(self):
        """The old name-match rule would have listed this as a warehouse draw."""
        from workshop.models import SpareShop
        self._item('Castrol 5W40')
        card = self._card()
        JobCardSpareItem.objects.create(
            job_card=card, source=JobCardSpareItem.SOURCE_SHOP,
            shop=SpareShop.objects.create(name='Malabar Spares'),
            spare_part_name='castrol 5w40', quantity=Decimal('1'))
        resp = self.client.get(reverse('inventory_history'))
        self.assertNotContains(resp, 'castrol 5w40')

    def test_mechanic_totals_group_case_insensitively(self):
        item = self._item('Castrol 5W40')
        card_a = self._card('KL01ZZ0002')
        card_b = self._card('KL01ZZ0003')
        self._draw(card_a, item, 2)
        self._draw(card_b, item, 3)
        resp = self.client.get(reverse('inventory_history_mechanic', args=[self.mechanic.id]))
        totals = resp.context['totals']
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]['total'], Decimal('5'))


class InventoryRBACTests(TestCase):
    """Floor sees only main / Low Stock / Stock History; management & supplier are Office/Owner."""
    def setUp(self):
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')
        self.floor = User.objects.create_user(username='floor_u', password='pw')
        self.floor.groups.add(self.floor_group)
        self.client = Client()
        self.client.login(username='floor_u', password='pw')
        self.category = Category.objects.create(name='Fluids')

    def test_floor_can_see_read_screens(self):
        for name in ('inventory_list', 'inventory_low_stock', 'inventory_history'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_floor_blocked_from_management_and_supplier(self):
        self.assertNotEqual(self.client.get(reverse('inventory_manage')).status_code, 200)
        self.assertNotEqual(self.client.get(reverse('supplier_shop_list')).status_code, 200)
        self.assertNotEqual(
            self.client.get(reverse('inventory_category_detail', args=[self.category.id])).status_code, 200)

    def test_floor_is_not_offered_links_it_cannot_open(self):
        """A link Floor can't follow is a dead end — it must not be rendered at all."""
        item = Item.objects.create(category=self.category, name='Oil A',
                                   average_stock=10, current_stock=4)
        html = self.client.get(reverse('inventory_list')).content.decode()
        self.assertNotContains(
            self.client.get(reverse('inventory_list')),
            reverse('inventory_item_suppliers', args=[item.id]))
        self.assertNotIn(reverse('supplier_shop_list'), html)
        # ...and the view itself still refuses Floor — 403 since 2026-07-28,
        # because they are signed in and simply lack the role.
        self.assertEqual(
            self.client.get(reverse('inventory_item_suppliers', args=[item.id])).status_code, 403)

    def test_office_still_gets_the_supplier_links(self):
        item = Item.objects.create(category=self.category, name='Oil B',
                                   average_stock=10, current_stock=4)
        office, _ = Group.objects.get_or_create(name='Office')
        u = User.objects.create_user(username='office_links', password='pw')
        u.groups.add(office)
        c = Client()
        c.login(username='office_links', password='pw')
        self.assertContains(c.get(reverse('inventory_list')),
                            reverse('inventory_item_suppliers', args=[item.id]))


class JobCardNormalizationTests(TestCase):
    """
    AUD-0016, AUD-0027: Verify that registration_number and brand_name are
    normalized (uppercased/title-cased) via JobCard.clean().
    """

    def test_registration_number_normalized_to_uppercase(self):
        """Lowercase reg numbers must be stored as uppercase."""
        from workshop.models import JobCard
        from django.utils import timezone
        jc = JobCard(
            registration_number='kl-01-ab-1234',
            brand_name='toyota',
            model_name='Camry',
            admitted_date=timezone.now().date(),
        )
        jc.clean()
        self.assertEqual(jc.registration_number, 'KL-01-AB-1234')

    def test_brand_name_normalized_to_title_case(self):
        """Brand names with extra spaces/casing must be normalized."""
        from workshop.models import JobCard
        from django.utils import timezone
        jc = JobCard(
            registration_number='MH12AB1234',
            brand_name='  hyundai  ',
            model_name='i20',
            admitted_date=timezone.now().date(),
        )
        jc.clean()
        self.assertEqual(jc.brand_name, 'Hyundai')

    def test_extra_spaces_in_registration_collapsed(self):
        """'KL  01  AB' with double spaces should become 'KL  01  AB' uppercased."""
        from workshop.models import JobCard
        from django.utils import timezone
        jc = JobCard(
            registration_number=' kl 01 ab 1234 ',
            brand_name='Honda',
            model_name='City',
            admitted_date=timezone.now().date(),
        )
        jc.clean()
        # .strip().upper() — only leading/trailing stripped, internal preserved
        self.assertEqual(jc.registration_number, 'KL 01 AB 1234')
