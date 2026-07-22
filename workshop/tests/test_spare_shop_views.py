from decimal import Decimal
from datetime import date

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse

from workshop.models import SpareShop, SpareShopPayment, JobCard, Mechanic, JobCardSpareItem

class SpareShopViewsExhaustiveTests(TestCase):
    """
    100% Coverage Test Suite specifically for all endpoints in workshop/views/spare_shop.py.
    """

    def setUp(self):
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')

        self.office_user = User.objects.create_user(username='officetest', password='password')
        self.office_user.groups.add(self.office_group)

        self.owner_user = User.objects.create_user(username='ownertest', password='password')
        self.owner_user.groups.add(self.owner_group)

        self.client = Client()
        self.client.login(username='officetest', password='password')

        self.shop = SpareShop.objects.create(name='Auto Parts Center', phone='1234567890')
        self.mechanic = Mechanic.objects.create(name='Test Mech')

    def _create_jobcard(self):
        return JobCard.objects.create(
            registration_number='KL01AB1111',
            brand_name='Honda',
            model_name='City',
            admitted_date=date.today(),
            lead_mechanic=self.mechanic
        )

    # -------------------------------------------------------------------------
    # 1. Shop List View
    # -------------------------------------------------------------------------
    def test_spare_shop_list_view(self):
        """Verify the shop list view loads and accurately calculates annotations."""
        # Add some items to ensure annotations hit
        jc = self._create_jobcard()
        JobCardSpareItem.objects.create(
            job_card=jc, shop=self.shop, spare_part_name='Filter',
            unit_price=Decimal('500'), quantity=Decimal('2')
        )
        self.shop.update_totals()

        url = reverse('spare_shop_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Auto Parts Center')
        
        # Verify the shop is passed in the context
        shops = response.context['shops']
        self.assertEqual(len(shops), 1)
        self.assertEqual(shops[0].item_count, 1)

    # -------------------------------------------------------------------------
    # 2. Shop Create View
    # -------------------------------------------------------------------------
    def test_spare_shop_create_success(self):
        url = reverse('spare_shop_create')
        response = self.client.post(url, {
            'name': 'New Shop',
            'phone': '9876543210',
            'address': 'Test Road'
        })
        self.assertTrue(SpareShop.objects.filter(name='New Shop').exists())
        self.assertEqual(response.status_code, 302)

    def test_spare_shop_create_empty_name(self):
        url = reverse('spare_shop_create')
        response = self.client.post(url, {'name': '   ', 'phone': '9876543210'})
        self.assertFalse(SpareShop.objects.filter(phone='9876543210').exists())
        self.assertEqual(response.status_code, 302)

    def test_spare_shop_create_duplicate(self):
        url = reverse('spare_shop_create')
        response = self.client.post(url, {'name': 'Auto Parts Center'})
        # Should reject duplicate name
        self.assertEqual(SpareShop.objects.count(), 1)
        self.assertEqual(response.status_code, 302)

    # -------------------------------------------------------------------------
    # 3. Shop Edit View
    # -------------------------------------------------------------------------
    def test_spare_shop_edit_success(self):
        url = reverse('spare_shop_edit', args=[self.shop.pk])
        response = self.client.post(url, {
            'name': 'Auto Parts Center Updated',
            'phone': '0000000000'
        })
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.name, 'Auto Parts Center Updated')
        self.assertEqual(self.shop.phone, '0000000000')
        self.assertEqual(response.status_code, 302)

    def test_spare_shop_edit_empty_name(self):
        url = reverse('spare_shop_edit', args=[self.shop.pk])
        response = self.client.post(url, {'name': ''})
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.name, 'Auto Parts Center')

    def test_spare_shop_edit_duplicate(self):
        SpareShop.objects.create(name='Other Shop')
        url = reverse('spare_shop_edit', args=[self.shop.pk])
        response = self.client.post(url, {'name': 'Other Shop'})
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.name, 'Auto Parts Center') # Should not change

    # -------------------------------------------------------------------------
    # 4. Shop Detail View Filters
    # -------------------------------------------------------------------------
    def test_spare_shop_detail_filters(self):
        base_url = reverse('spare_shop_detail', args=[self.shop.pk])
        # Test various combinations of filters to hit all code paths
        res1 = self.client.get(base_url + '?filter=month&sort_by=ordered')
        self.assertEqual(res1.status_code, 200)

        res2 = self.client.get(base_url + '?filter=year&sort_by=received')
        self.assertEqual(res2.status_code, 200)

        res3 = self.client.get(base_url + '?filter=custom&start_date=2026-01-01&end_date=2026-12-31')
        self.assertEqual(res3.status_code, 200)

    # -------------------------------------------------------------------------
    # 5. Shop Trashing & Restoring
    # -------------------------------------------------------------------------
    def test_spare_shop_soft_delete_and_restore(self):
        self.client.login(username='ownertest', password='password') # Owner required for deletion
        
        # Delete 
        del_url = reverse('spare_shop_delete', args=[self.shop.pk])
        response = self.client.post(del_url)
        self.assertEqual(response.status_code, 302)
        
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_trashed)

        # Restore
        res_url = reverse('spare_shop_restore', args=[self.shop.pk])
        response = self.client.post(res_url)
        self.assertEqual(response.status_code, 302)

        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_trashed)

    # -------------------------------------------------------------------------
    # 6. Shop Permanent Delete
    # -------------------------------------------------------------------------
    def test_spare_shop_permanent_delete(self):
        self.client.login(username='ownertest', password='password') # Owner required
        self.shop.is_trashed = True
        self.shop.save()
        
        url = reverse('spare_shop_permanent_delete', args=[self.shop.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SpareShop.objects.filter(pk=self.shop.pk).exists())

    # -------------------------------------------------------------------------
    # 7. Payment Permanent Delete
    # -------------------------------------------------------------------------
    def test_spare_shop_payment_permanent_delete(self):
        self.client.login(username='ownertest', password='password')
        payment = SpareShopPayment.objects.create(
            shop=self.shop, amount=1000, payment_method='CASH', is_trashed=True
        )
        url = reverse('spare_shop_payment_permanent_delete', args=[payment.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SpareShopPayment.objects.filter(pk=payment.pk).exists())

    # -------------------------------------------------------------------------
    # 8. Shop Print View
    # -------------------------------------------------------------------------
    def test_spare_shop_print_view(self):
        url = reverse('spare_shop_print', args=[self.shop.pk])
        response = self.client.get(url + '?filter=all')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Auto Parts Center')

    # -------------------------------------------------------------------------
    # 9. Add Unassigned Spares
    # -------------------------------------------------------------------------
    def test_spare_shop_add_unassigned_success(self):
        url = reverse('spare_shop_add_unassigned', args=[self.shop.pk])
        response = self.client.post(url, {
            'spare_part_name': 'Loose Nut',
            'unit_price': '15.50',
            'quantity': '10'
        })
        self.assertEqual(response.status_code, 302)
        item = JobCardSpareItem.objects.filter(spare_part_name='Loose Nut').first()
        self.assertIsNotNone(item)
        self.assertIsNone(item.job_card)
        self.assertEqual(item.shop, self.shop)
        self.assertEqual(item.unit_price, Decimal('15.50'))
        self.assertEqual(item.quantity, Decimal('10'))

    def test_spare_shop_add_unassigned_empty_name(self):
        url = reverse('spare_shop_add_unassigned', args=[self.shop.pk])
        response = self.client.post(url, {
            'spare_part_name': '   ',
            'unit_price': '10',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(JobCardSpareItem.objects.count(), 0)

    def test_spare_shop_add_unassigned_invalid_price(self):
        url = reverse('spare_shop_add_unassigned', args=[self.shop.pk])
        response = self.client.post(url, {
            'spare_part_name': 'Loose Nut',
            'unit_price': 'abc',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(JobCardSpareItem.objects.count(), 0)

    # -------------------------------------------------------------------------
    # 10. Unassign Item
    # -------------------------------------------------------------------------
    def test_spare_shop_unassign_item(self):
        jc = self._create_jobcard()
        item = JobCardSpareItem.objects.create(
            job_card=jc, shop=self.shop, spare_part_name='Filter',
            unit_price=Decimal('500'), quantity=Decimal('2')
        )
        url = reverse('spare_shop_unassign_item', args=[item.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertIsNone(item.job_card)
        self.assertIn('Honda City (KL01AB1111)', item.original_vehicle_info)

    # -------------------------------------------------------------------------
    # 11. Update Item Price
    # -------------------------------------------------------------------------
    def test_spare_shop_update_item_price_success(self):
        item = JobCardSpareItem.objects.create(
            job_card=None, shop=self.shop, spare_part_name='Filter',
            unit_price=Decimal('500'), quantity=Decimal('2')
        )
        url = reverse('spare_shop_update_item_price', args=[item.pk])
        response = self.client.post(url, {
            'unit_price': '600',
            'quantity': '3'
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.unit_price, Decimal('600'))
        self.assertEqual(item.quantity, Decimal('3'))

    def test_spare_shop_update_item_price_invalid(self):
        item = JobCardSpareItem.objects.create(
            job_card=None, shop=self.shop, spare_part_name='Filter',
            unit_price=Decimal('500'), quantity=Decimal('2')
        )
        url = reverse('spare_shop_update_item_price', args=[item.pk])
        response = self.client.post(url, {
            'unit_price': 'xyz',
            'quantity': 'abc'
        })
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.unit_price, Decimal('500'))
        self.assertEqual(item.quantity, Decimal('2'))
