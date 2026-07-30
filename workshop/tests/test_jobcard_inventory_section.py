"""
The Job Card's Inventory Items section (added 2026-07-30).

Warehouse draws and spare-shop purchases are edited as two separate sections over
one model, told apart by `JobCardSpareItem.source`. These tests cover the seam:
that each formset only ever touches its own route's rows, that a draw is linked by
FK rather than by a typed name, and that the Financial Lock and the Floor
price-hiding rule cover the new section as well as the old one.
"""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Category, Item
from workshop.models import JobCard, JobCardSpareItem, Mechanic, SpareShop

INVENTORY = JobCardSpareItem.SOURCE_INVENTORY
SHOP = JobCardSpareItem.SOURCE_SHOP


class InventorySectionBase(TestCase):
    def setUp(self):
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')

        self.user = User.objects.create_user(username='off', password='pw')
        self.user.groups.add(self.office_group)
        self.client = Client()
        self.client.login(username='off', password='pw')

        self.mechanic = Mechanic.objects.create(name='Lead Tech')
        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.category = Category.objects.create(name='Oils')
        self.item = Item.objects.create(
            category=self.category, name='Engine Oil 5W30',
            average_stock=D('20'), current_stock=D('20'), avg_cost=D('400'))

        self.job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01A1234', customer_name='John',
            customer_contact='1234567890')

    def payload(self, reg='KL01A1234', **overrides):
        data = {
            'registration_number': reg,
            'admitted_date': str(date.today()),
            'customer_name': 'Alice',
            'customer_contact': '9876543210',
            'brand_name': 'Honda',
            'model_name': 'City',
            'mileage': '10k',
            'lead_mechanic': self.mechanic.id,
            'car_color': 'Black',

            'concerns-TOTAL_FORMS': '0',
            'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0',
            'concerns-MAX_NUM_FORMS': '1000',

            'spares-TOTAL_FORMS': '0',
            'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0',
            'spares-MAX_NUM_FORMS': '1000',

            'inventory-TOTAL_FORMS': '0',
            'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0',
            'inventory-MAX_NUM_FORMS': '1000',

            'labours-TOTAL_FORMS': '0',
            'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0',
            'labours-MAX_NUM_FORMS': '1000',
        }
        data.update(overrides)
        return data

    def edit(self, **overrides):
        return self.client.post(
            reverse('jobcard_edit', args=[self.job.pk]), self.payload(**overrides))


class SavingADrawTests(InventorySectionBase):
    def test_posting_the_section_creates_an_inventory_row_and_moves_stock(self):
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '3',
            'inventory-0-total_price': '1800',
        })
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.job.pk]))

        draw = JobCardSpareItem.objects.get(job_card=self.job)
        self.assertEqual(draw.source, INVENTORY)
        self.assertEqual(draw.item_id, self.item.pk)
        self.assertIsNone(draw.shop_id)
        self.assertEqual(draw.spare_part_name, 'Engine Oil 5W30')
        self.assertEqual(draw.unit_price, D('400.00'))   # snapshot of avg_cost

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, D('17.00'))

    def test_a_rate_drives_the_customer_total(self):
        self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '7',
            'inventory-0-customer_rate': '1200',
        })
        draw = JobCardSpareItem.objects.get(job_card=self.job)
        self.assertEqual(draw.total_price, D('8400.00'))

    def test_a_row_with_content_but_no_product_is_rejected(self):
        """Someone typed a quantity but never picked from the suggestions."""
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': '',
            'inventory-0-quantity': '3',
            'inventory-0-total_price': '900',
        })
        self.assertEqual(resp.status_code, 200)     # redisplayed, not saved
        self.assertFalse(JobCardSpareItem.objects.filter(job_card=self.job).exists())
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, D('20.00'))

    def test_a_product_with_no_quantity_is_rejected(self):
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(JobCardSpareItem.objects.filter(job_card=self.job).exists())

    def test_a_wholly_empty_row_is_ignored_not_rejected(self):
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': '',
            'inventory-0-quantity': '',
        })
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.job.pk]))
        self.assertFalse(JobCardSpareItem.objects.filter(job_card=self.job).exists())

    def test_overdrawing_is_allowed_and_goes_negative(self):
        """Recording a part already taken must never be blocked by the form."""
        self.item.current_stock = D('2')
        self.item.save()
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '5',
            'inventory-0-total_price': '3000',
        })
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.job.pk]))
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, D('-3.00'))


class TheTwoSectionsStaySeparateTests(InventorySectionBase):
    def setUp(self):
        super().setUp()
        self.draw = JobCardSpareItem.objects.create(
            job_card=self.job, source=INVENTORY, item=self.item,
            quantity=D('2'), total_price=D('1000'))
        self.purchase = JobCardSpareItem.objects.create(
            job_card=self.job, source=SHOP, shop=self.shop,
            spare_part_name='Brake Pad', quantity=D('1'),
            unit_price=D('500'), total_price=D('700'))

    def test_each_formset_shows_only_its_own_route(self):
        resp = self.client.get(reverse('jobcard_edit', args=[self.job.pk]))
        inv = resp.context['inventory_formset']
        spa = resp.context['spare_formset']

        self.assertEqual([f.instance.pk for f in inv.forms], [self.draw.pk])
        self.assertEqual([f.instance.pk for f in spa.forms], [self.purchase.pk])

    def test_editing_one_section_leaves_the_other_untouched(self):
        """The shop pass reads shop_name as a posted pk — it must skip draws."""
        resp = self.client.post(reverse('jobcard_edit', args=[self.job.pk]), self.payload(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-INITIAL_FORMS': '1',
            'inventory-0-id': str(self.draw.pk),
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '4',
            'inventory-0-total_price': '2000',

            'spares-TOTAL_FORMS': '1',
            'spares-INITIAL_FORMS': '1',
            'spares-0-id': str(self.purchase.pk),
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '1',
            'spares-0-unit_price': '500',
            'spares-0-total_price': '700',
            'spares-0-status': 'RECEIVED',
            'spares-0-shop_name': str(self.shop.pk),
            'spares-0-ordered_date': '',
            'spares-0-received_date': '',
        }))
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.job.pk]))

        self.draw.refresh_from_db()
        self.purchase.refresh_from_db()
        self.assertEqual(self.draw.quantity, D('4.00'))
        self.assertEqual(self.draw.source, INVENTORY)
        self.assertIsNone(self.draw.shop_id, "the shop pass must not touch a draw")
        self.assertEqual(self.purchase.source, SHOP)
        self.assertEqual(self.purchase.shop_id, self.shop.pk)

    def test_the_detail_page_splits_them(self):
        resp = self.client.get(reverse('jobcard_detail', args=[self.job.pk]))
        self.assertEqual([s.pk for s in resp.context['inventory_draws']], [self.draw.pk])
        self.assertEqual([s.pk for s in resp.context['shop_spares']], [self.purchase.pk])
        self.assertContains(resp, 'Inventory Items Used')

    def test_the_bill_counts_both_routes(self):
        self.job.refresh_from_db()
        self.assertEqual(self.job.total_bill_amount, D('1700.00'))   # 1000 + 700


class FinancialLockCoversTheNewSectionTests(InventorySectionBase):
    def test_a_paid_card_rejects_an_inventory_edit_without_unlocking(self):
        self.job.payment_status = 'PAID'
        self.job.save()

        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '3',
            'inventory-0-total_price': '1800',
        })
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.job.pk]))
        self.assertFalse(JobCardSpareItem.objects.filter(job_card=self.job).exists(),
                         "a settled bill must not gain parts through the new section")
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, D('20.00'))

    def test_unlocking_lets_the_edit_through(self):
        self.job.payment_status = 'PAID'
        self.job.save()

        resp = self.edit(**{
            'financial_unlock': 'true',
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '3',
            'inventory-0-total_price': '1800',
        })
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.job.pk]))
        self.assertTrue(JobCardSpareItem.objects.filter(job_card=self.job).exists())


class FloorSeesNoPricesTests(InventorySectionBase):
    def setUp(self):
        super().setUp()
        self.floor_user = User.objects.create_user(username='mech', password='pw')
        self.floor_user.groups.add(self.floor_group)
        self.floor_client = Client()
        self.floor_client.login(username='mech', password='pw')
        # An existing draw, so the formset actually renders a row to inspect —
        # with extra=0 an empty section has no numbered fields at all.
        JobCardSpareItem.objects.create(
            job_card=self.job, source=INVENTORY, item=self.item,
            quantity=D('2'), customer_rate=D('600'))

    def test_floor_gets_the_section_but_the_price_cells_are_hidden(self):
        resp = self.floor_client.get(reverse('jobcard_edit', args=[self.job.pk]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()

        self.assertIn('id="inventory-list"', body)
        self.assertIn('inventory-item-search', body)
        # The inputs still have to be present, or a mechanic saving the card would
        # wipe the bill Office entered — they are just inside a d-none cell.
        self.assertIn('inventory-0-customer_rate', body)
        # Asserted against the column HEADER, not the bare label: the hidden
        # inputs carry the same text as a placeholder, so a looser check would
        # pass or fail for the wrong reason.
        self.assertNotIn('>Unit Price (₹)</th>', body)
        self.assertNotIn('>Customer Price (₹)</th>', body)

    def test_the_hidden_price_cell_is_actually_hidden(self):
        body = self.floor_client.get(reverse('jobcard_edit', args=[self.job.pk])).content.decode()
        cell_start = body.index('inventory-0-customer_rate')
        preceding = body[:cell_start]
        self.assertIn('d-none', preceding[-400:],
                      "Floor's price inputs must sit inside a d-none cell")

    def test_office_does_see_the_price_headers(self):
        resp = self.client.get(reverse('jobcard_edit', args=[self.job.pk]))
        self.assertContains(resp, '>Unit Price (₹)</th>')
        self.assertContains(resp, '>Customer Price (₹)</th>')


class PickerEndpointTests(InventorySectionBase):
    def test_it_returns_the_id_so_the_draw_can_be_linked_by_fk(self):
        resp = self.client.get(reverse('autocomplete_inventory_items'), {'q': 'engine'})
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], self.item.pk)
        self.assertEqual(rows[0]['name'], 'Engine Oil 5W30')
        self.assertEqual(rows[0]['stock'], '20.00')

    def test_an_overdrawn_product_is_still_offered(self):
        """Hiding it would block recording a part already physically taken."""
        self.item.current_stock = D('-3')
        self.item.save()
        rows = self.client.get(reverse('autocomplete_inventory_items'), {'q': 'engine'}).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['stock'], '-3.00')

    def test_the_spare_autocomplete_no_longer_offers_stock_products(self):
        """Warehouse products have their own picker; mixing them was the old bug."""
        rows = self.client.get(reverse('autocomplete_spares'), {'q': 'engine'}).json()
        self.assertEqual(rows, [])
