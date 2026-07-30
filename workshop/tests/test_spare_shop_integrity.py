"""
Two defects in the Spare Shop side of a Job Card, found by audit 2026-07-30 and
fixed 2026-07-31. Both PRE-DATED the inventory-split work.

  AUD-0080  moving a spare between shops double-counted what the workshop owed
  AUD-0081  a Floor user could rewrite any bill with a crafted POST
"""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Category, Item
from workshop.models import JobCard, JobCardSpareItem, Mechanic, SpareShop

SHOP = JobCardSpareItem.SOURCE_SHOP
INVENTORY = JobCardSpareItem.SOURCE_INVENTORY


class SpareShopBase(TestCase):
    def setUp(self):
        self.office_g, _ = Group.objects.get_or_create(name='Office')
        self.floor_g, _ = Group.objects.get_or_create(name='Floor')

        self.office = User.objects.create_user(username='off_ss', password='pw')
        self.office.groups.add(self.office_g)
        self.client = Client()
        self.client.login(username='off_ss', password='pw')

        self.mechanic = Mechanic.objects.create(name='Tech')
        self.A = SpareShop.objects.create(name='Shop A')
        self.B = SpareShop.objects.create(name='Shop B')
        self.category = Category.objects.create(name='Oils')
        self.item = Item.objects.create(category=self.category, name='Engine Oil',
                                        average_stock=D('20'), current_stock=D('50'),
                                        avg_cost=D('1000'))
        self.jc = JobCard.objects.create(registration_number='KL12AA0001',
                                         admitted_date=date(2026, 1, 5))

    def owed(self):
        self.A.refresh_from_db()
        self.B.refresh_from_db()
        return self.A.get_pending_balance, self.B.get_pending_balance

    def payload(self, **overrides):
        data = {
            'registration_number': 'KL12AA0001', 'admitted_date': '2026-01-05',
            'customer_name': 'X', 'customer_contact': '9', 'brand_name': 'B',
            'model_name': 'M', 'mileage': '1', 'lead_mechanic': self.mechanic.id,
            'car_color': 'Black',
            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'spares-TOTAL_FORMS': '0', 'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '0', 'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',
        }
        data.update(overrides)
        return data


class MovingASpareBetweenShopsTests(SpareShopBase):
    """
    AUD-0080. One ₹1,000 purchase used to show as ₹1,000 owed to Shop A *and*
    ₹1,000 owed to Shop B — ₹2,000 of debt against ₹1,000 spent, and it never
    self-corrected. The old shop's cached total was simply never recomputed.
    """

    def spare(self, shop):
        return JobCardSpareItem.objects.create(
            job_card=self.jc, source=SHOP, shop=shop, shop_name=shop.name,
            spare_part_name='Brake Pad', quantity=D('2'),
            unit_price=D('500'), total_price=D('1400'))

    def test_the_old_shops_ledger_is_cleared_on_save(self):
        row = self.spare(self.A)
        self.assertEqual(self.owed(), (D('1000.00'), D('0')))

        row.shop = self.B
        row.shop_name = self.B.name
        row.save()

        self.assertEqual(self.owed(), (D('0.00'), D('1000.00')),
                         "the debt must move, not be duplicated")

    def test_clearing_the_shop_strands_no_debt(self):
        row = self.spare(self.A)
        row.shop = None
        row.save()
        self.assertEqual(self.owed(), (D('0.00'), D('0')))

    def test_moving_it_through_the_job_card_form(self):
        """The reachable path: Office changes the Shop dropdown and saves."""
        row = self.spare(self.A)
        self.assertEqual(self.owed(), (D('1000.00'), D('0')))

        resp = self.client.post(reverse('jobcard_edit', args=[self.jc.pk]), self.payload(**{
            'spares-TOTAL_FORMS': '1', 'spares-INITIAL_FORMS': '1',
            'spares-0-id': str(row.pk),
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '2', 'spares-0-unit_price': '500',
            'spares-0-total_price': '1400', 'spares-0-status': 'RECEIVED',
            'spares-0-shop_name': str(self.B.pk),          # moved A -> B
            'spares-0-ordered_date': '', 'spares-0-received_date': '',
        }))
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.jc.pk]))

        a_owed, b_owed = self.owed()
        self.assertEqual(a_owed, D('0.00'))
        self.assertEqual(b_owed, D('1000.00'))
        self.assertEqual(a_owed + b_owed, D('1000.00'),
                         "only ₹1,000 was ever spent")

    def test_clearing_the_dropdown_through_the_form(self):
        row = self.spare(self.A)
        self.client.post(reverse('jobcard_edit', args=[self.jc.pk]), self.payload(**{
            'spares-TOTAL_FORMS': '1', 'spares-INITIAL_FORMS': '1',
            'spares-0-id': str(row.pk),
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '2', 'spares-0-unit_price': '500',
            'spares-0-total_price': '1400', 'spares-0-status': 'RECEIVED',
            'spares-0-shop_name': '',                      # no shop at all
            'spares-0-ordered_date': '', 'spares-0-received_date': '',
        }))
        self.assertEqual(self.owed(), (D('0.00'), D('0')))


class FloorCannotSetPricesTests(SpareShopBase):
    """
    AUD-0081. Prices are hidden from Floor in the template but the inputs still
    submit, and nothing on the server checked. A Floor login POSTing
    `total_price=1` turned a ₹5,000 bill into ₹1.
    """

    def setUp(self):
        super().setUp()
        self.mech = User.objects.create_user(username='mech_ss', password='pw')
        self.mech.groups.add(self.floor_g)
        self.floor = Client()
        self.floor.login(username='mech_ss', password='pw')

        self.shop_row = JobCardSpareItem.objects.create(
            job_card=self.jc, source=SHOP, shop=self.A, shop_name='Shop A',
            spare_part_name='Brake Pad', quantity=D('2'),
            unit_price=D('500'), total_price=D('1400'))
        self.draw = JobCardSpareItem.objects.create(
            job_card=self.jc, source=INVENTORY, item=self.item,
            quantity=D('2'), total_price=D('5000'))

    def _floor_post(self, **overrides):
        return self.floor.post(reverse('jobcard_edit', args=[self.jc.pk]),
                               self.payload(**overrides))

    def test_floor_cannot_cut_an_inventory_line_price(self):
        self._floor_post(**{
            'inventory-TOTAL_FORMS': '1', 'inventory-INITIAL_FORMS': '1',
            'inventory-0-id': str(self.draw.pk),
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '2',
            'inventory-0-total_price': '1',        # the attack
            'inventory-0-customer_rate': '1',
        })
        self.draw.refresh_from_db()
        self.jc.refresh_from_db()
        self.assertEqual(self.draw.total_price, D('5000.00'))
        self.assertIsNone(self.draw.customer_rate)

    def test_floor_cannot_cut_a_spare_shop_line_price(self):
        self._floor_post(**{
            'spares-TOTAL_FORMS': '1', 'spares-INITIAL_FORMS': '1',
            'spares-0-id': str(self.shop_row.pk),
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '2',
            'spares-0-unit_price': '1',            # the attack
            'spares-0-total_price': '1',
            'spares-0-status': 'RECEIVED',
            'spares-0-shop_name': str(self.A.pk),
            'spares-0-ordered_date': '', 'spares-0-received_date': '',
        })
        self.shop_row.refresh_from_db()
        self.assertEqual(self.shop_row.unit_price, D('500.00'))
        self.assertEqual(self.shop_row.total_price, D('1400.00'))
        self.A.refresh_from_db()
        self.assertEqual(self.A.get_pending_balance, D('1000.00'),
                         "the shop ledger must not move either")

    def test_floor_can_still_do_its_own_job(self):
        """The lock covers prices only — quantity is the mechanic's to record."""
        self._floor_post(**{
            'inventory-TOTAL_FORMS': '1', 'inventory-INITIAL_FORMS': '1',
            'inventory-0-id': str(self.draw.pk),
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '5',
            'inventory-0-total_price': '5000',
        })
        self.draw.refresh_from_db()
        self.assertEqual(self.draw.quantity, D('5.00'))
        self.assertEqual(self.draw.total_price, D('5000.00'))

    def test_office_is_unaffected(self):
        self.client.post(reverse('jobcard_edit', args=[self.jc.pk]), self.payload(**{
            'inventory-TOTAL_FORMS': '1', 'inventory-INITIAL_FORMS': '1',
            'inventory-0-id': str(self.draw.pk),
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '2',
            'inventory-0-total_price': '6200',
        }))
        self.draw.refresh_from_db()
        self.assertEqual(self.draw.total_price, D('6200.00'))
