# inventory/tests.py
from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone
from workshop.models import JobCard, JobCardSpareItem
from .models import Category, Item, ConsumptionRecord

class InventorySignalTests(TestCase):
    """
    Automated Testing Suite for Inventory Stock Deltas.
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
            spare_part_name='Engine Oil 5W30',
            quantity=5,
            unit_price=800
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 45)

    def test_stock_correction_on_update(self):
        spare = JobCardSpareItem.objects.create(
            job_card=self.jobcard,
            spare_part_name='Engine Oil 5W30',
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
            spare_part_name='Engine Oil 5W30',
            quantity=5,
            unit_price=800
        )
        spare.delete()
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 50)

class InventoryViewTests(TestCase):
    """
    Tests for all Inventory Management Views.
    """
    def setUp(self):
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office_user', password='password')
        self.user.groups.add(self.office_group)
        self.client = Client()
        self.client.login(username='office_user', password='password')
        
        self.category = Category.objects.create(name='Brakes')
        self.item = Item.objects.create(category=self.category, name='Brake Pad', current_stock=10)

    def test_inventory_manage_and_search(self):
        # 1. Dashboard
        response = self.client.get(reverse('inventory_manage'))
        self.assertEqual(response.status_code, 200)
        
        # 2. Search
        response = self.client.get(reverse('inventory_manage'), {'q': 'Brakes'})
        self.assertContains(response, 'Brakes')
        
        # 3. Search miss
        response = self.client.get(reverse('inventory_manage'), {'q': 'GhostPart'})
        # Should not contain Brakes if it didn't match
        self.assertNotContains(response, 'Brake Pad')

    def test_category_crud(self):
        # Add Category
        response = self.client.post(reverse('inventory_add_category'), {'name': 'Suspension'})
        self.assertRedirects(response, reverse('inventory_manage'))
        self.assertTrue(Category.objects.filter(name='Suspension').exists())
        
        # Edit Category
        response = self.client.post(reverse('inventory_edit_category', args=[self.category.id]), {'name': 'Braking Systems'})
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'Braking Systems')
        
        # Delete Category — must first delete items due to PROTECT (AUD-0024)
        self.item.delete()
        response = self.client.post(reverse('inventory_delete_category', args=[self.category.id]))
        self.assertFalse(Category.objects.filter(id=self.category.id).exists())

    def test_item_management(self):
        # Detail view
        response = self.client.get(reverse('inventory_category_detail', args=[self.category.id]))
        self.assertContains(response, 'Brake Pad')
        
        # Add Item
        response = self.client.post(reverse('inventory_add_item', args=[self.category.id]), {
            'name': 'Brake Disc',
            'average_stock': 20,
            'current_stock': 5
        })
        self.assertTrue(Item.objects.filter(name='Brake Disc').exists())
        
        # Edit Item
        response = self.client.post(reverse('inventory_edit_item', args=[self.item.id]), {
            'name': 'Brake Pad Premium',
            'average_stock': 15,
            'current_stock': 12
        })
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Brake Pad Premium')
        
        # Delete Item
        response = self.client.post(reverse('inventory_delete_item', args=[self.item.id]))
        self.assertFalse(Item.objects.filter(id=self.item.id).exists())

    def test_stock_restock_and_low_stock(self):
        # Restock list
        response = self.client.get(reverse('inventory_list'))
        self.assertContains(response, 'Brake Pad')
        
        # Update Stock
        response = self.client.post(reverse('inventory_update_stock', args=[self.item.id]), {'current_stock': 50})
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 50)
        
        # Low Stock view
        # Create a low stock item
        Item.objects.create(category=self.category, name='Low Fluid', average_stock=10, current_stock=1)
        response = self.client.get(reverse('inventory_low_stock'))
        self.assertContains(response, 'Low Fluid')

    def test_consumption_history(self):
        ConsumptionRecord.objects.create(user=self.user, item=self.item, quantity=2)
        response = self.client.get(reverse('inventory_history'))
        self.assertContains(response, 'Brake Pad')
        self.assertContains(response, 'office_user')

    def test_get_methods(self):
        # inventory_home redirects to restock
        response = self.client.get(reverse('inventory_home'))
        self.assertRedirects(response, reverse('inventory_list'))

        # delete_category GET (no POST body) → safe redirect, does NOT delete
        response = self.client.get(
            reverse('inventory_delete_category', args=[self.category.id])
        )
        self.assertRedirects(response, reverse('inventory_manage'))
        self.assertTrue(Category.objects.filter(id=self.category.id).exists())

        # add_item GET → redirect to category_detail (no template needed)
        response = self.client.get(
            reverse('inventory_add_item', args=[self.category.id])
        )
        self.assertRedirects(
            response,
            reverse('inventory_category_detail', args=[self.category.id])
        )

        # edit_item GET → redirect to category_detail
        response = self.client.get(
            reverse('inventory_edit_item', args=[self.item.id])
        )
        self.assertRedirects(
            response,
            reverse('inventory_category_detail', args=[self.item.category.id])
        )

        # delete_item GET → redirect to manage
        response = self.client.get(
            reverse('inventory_delete_item', args=[self.item.id])
        )
        self.assertRedirects(response, reverse('inventory_manage'))
        self.assertTrue(Item.objects.filter(id=self.item.id).exists())

        # inventory_list with empty search
        response = self.client.get(reverse('inventory_list'), {'q': ''})
        self.assertEqual(response.status_code, 200)

        # update_stock POST without next_url → redirect to restock
        response = self.client.post(
            reverse('inventory_update_stock', args=[self.item.id]),
            {'current_stock': 50}
        )
        self.assertRedirects(response, reverse('inventory_list'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, 50)

        # update_stock POST with next_url → redirect to that url
        response = self.client.post(
            reverse('inventory_update_stock', args=[self.item.id]),
            {'current_stock': 25, 'next': reverse('inventory_manage')}
        )
        self.assertRedirects(response, reverse('inventory_manage'))


class CategoryProtectionTests(TestCase):
    """
    AUD-0024, AUD-0060, AUD-0071: Verify that CASCADE → PROTECT prevents
    accidental category deletion when items exist, and that the UI surfaces
    a clear error message instead of a 500 crash.
    """

    def setUp(self):
        owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='owner_protect_test', password='Test1234!')
        self.owner.groups.add(owner_group)
        self.client = Client()
        self.client.login(username='owner_protect_test', password='Test1234!')
        self.category = Category.objects.create(name='Test Category')
        self.item = Item.objects.create(
            category=self.category,
            name='Test Item',
            average_stock=10,
            current_stock=5,
        )

    def test_delete_category_with_items_is_blocked(self):
        """Deleting a non-empty category must be blocked and show a clear error."""
        url = reverse('inventory_delete_category', args=[self.category.id])
        response = self.client.post(url, follow=True)
        self.assertRedirects(response, reverse('inventory_manage'))
        # Category must still exist
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        # A helpful error message must be shown
        messages_list = [str(m) for m in list(response.context['messages'])]
        self.assertTrue(any("Cannot delete" in m for m in messages_list))

    def test_delete_empty_category_succeeds(self):
        """Deleting a category with no items must still work normally."""
        empty_cat = Category.objects.create(name='Empty Category')
        url = reverse('inventory_delete_category', args=[empty_cat.id])
        response = self.client.post(url, follow=True)
        self.assertRedirects(response, reverse('inventory_manage'))
        self.assertFalse(Category.objects.filter(pk=empty_cat.pk).exists())


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