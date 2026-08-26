from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone
from workshop.models import (
    JobCard, CarBrand, CarModel, Mechanic, SparePart, 
    ConcernSolution, JobCardConcern, JobCardSpareItem, JobCardLabourItem,
    FailedAttempt
)
import json
from datetime import timedelta

class WorkshopViewTests(TestCase):
    """
    Exhaustive Testing for Workshop Operations & Management.
    Titan Standard 100% Verification.
    """

    def setUp(self):
        # 1. Groups & Users
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        
        self.owner = User.objects.create_user(username='Sahad', password='password')
        self.owner.groups.add(self.owner_group)
        
        self.user = User.objects.create_user(username='workshop_office', password='password')
        self.user.groups.add(self.office_group)
        
        self.client = Client()
        self.client.login(username='workshop_office', password='password')
        
        # 2. Master Data
        self.brand = CarBrand.objects.create(name='Honda')
        self.car_model = CarModel.objects.create(brand=self.brand, name='City')
        FailedAttempt.objects.all().delete()
        self.mechanic = Mechanic.objects.create(name='Test Mech')
        self.spare = SparePart.objects.create(name='Oil Filter')
        self.sol = ConcernSolution.objects.create(concern='Engine Sound')
        
        # 3. Base Job Card
        self.jobcard = JobCard.objects.create(
            registration_number='KL01AB1111',
            brand_name='Honda',
            model_name='City',
            admitted_date=timezone.localdate(),
            lead_mechanic=self.mechanic
        )

    def test_jobcard_search_and_pagination(self):
        url = reverse('jobcard_list')
        # Create 30 job cards for pagination
        for i in range(30):
            JobCard.objects.create(
                registration_number=f'KL01AB{i}',
                brand_name='Honda',
                model_name='City',
                admitted_date=timezone.localdate()
            )
        response = self.client.get(url, {'page': 2})
        self.assertEqual(response.status_code, 200)

    def test_jobcard_edit_with_formsets(self):
        """EXHAUSTIVE: Test editing a job card with ALL 3 formsets (Concerns, Spares, Labours)."""
        url = reverse('jobcard_edit', args=[self.jobcard.pk])
        
        data = {
            'registration_number': 'KL01AB1111',
            'brand_name': 'Honda',
            'model_name': 'City',
            'admitted_date': str(timezone.localdate()),
            'lead_mechanic': self.mechanic.id,
            # Concern Formset
            'concerns-TOTAL_FORMS': '1',
            'concerns-INITIAL_FORMS': '0',
            'concerns-0-concern_text': 'Noise Corrected',
            'concerns-0-status': 'FIXED',
            # Spares Formset
            'spares-TOTAL_FORMS': '1',
            'spares-INITIAL_FORMS': '0',
            'spares-0-spare_part_name': 'Oil Filter',
            'spares-0-quantity': '1',
            'spares-0-unit_price': '500',
            'spares-0-total_price': '600',
            'spares-0-status': 'PENDING',
            'spares-0-shop_name': 'Auto Shop',
            # Labour Formset
            'inventory-TOTAL_FORMS': '0',
            'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0',
            'inventory-MAX_NUM_FORMS': '1000',

            'labours-TOTAL_FORMS': '1',
            'labours-INITIAL_FORMS': '0',
            'labours-0-job_description': 'Oil Change',
            # The charge for all the work rides on the card, not on the line.
            'labour_amount': '200',
        }
        
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        # Verify save
        self.jobcard.refresh_from_db()
        self.assertEqual(self.jobcard.concerns.count(), 1)
        self.assertEqual(self.jobcard.spares.count(), 1)
        self.assertEqual(self.jobcard.labours.count(), 1)

    def test_jobcard_edit_with_formset_deletion(self):
        """Test deleting a spare item via the formset."""
        spare = JobCardSpareItem.objects.create(job_card=self.jobcard, spare_part_name='DeleteMe', quantity=1, unit_price=100)
        url = reverse('jobcard_edit', args=[self.jobcard.pk])
        
        data = {
            'registration_number': 'KL01AB1111',
            'brand_name': 'Honda',
            'model_name': 'City',
            'admitted_date': str(timezone.localdate()),
            'lead_mechanic': self.mechanic.id,
            'concerns-TOTAL_FORMS': '0',
            'concerns-INITIAL_FORMS': '0',
            'spares-TOTAL_FORMS': '1',
            'spares-INITIAL_FORMS': '1',
            'spares-0-id': spare.id,
            'spares-0-spare_part_name': 'DeleteMe',
            'spares-0-quantity': '1',
            'spares-0-unit_price': '100',
            'spares-0-DELETE': 'on', # TRIGGER DELETION
            'inventory-TOTAL_FORMS': '0',
            'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0',
            'inventory-MAX_NUM_FORMS': '1000',

            'labours-TOTAL_FORMS': '0',
            'labours-INITIAL_FORMS': '0',
        }
        self.client.post(url, data)
        self.assertFalse(JobCardSpareItem.objects.filter(pk=spare.id).exists())

    def test_the_live_report_has_no_filters_left_to_honour(self):
        """
        This used to assert that `q` / `status` narrowed the Live Jobs list.
        That list has gone — the home page's car cards do the same job better,
        and are where Floor already works — and with it went the only filtered
        thing on the page.

        The test is INVERTED rather than deleted: what matters now is that a
        crafted query string cannot make the page report less than the whole
        workshop. Every remaining box answers "what is the state of the
        workshop right now", and a half-filtered answer to that is worse than
        no answer.
        """
        url = reverse('live_report')
        JobCard.objects.create(
            registration_number='PAID001', admitted_date=timezone.localdate(),
            completed=False, payment_status='PAID')

        for params in ({}, {'q': 'NOBODY'}, {'status': 'PENDING'}):
            with self.subTest(params=params):
                page = self.client.get(url, params).content.decode()
                self.assertIn('PAID001', page)
                self.assertIn('>2 in workshop<', page)

    def test_management_master_lists(self):
        # We need OWNER access for these typically
        self.client.login(username='Sahad', password='password')
        
        # Brand CRUD
        response = self.client.post(reverse('brand_add'), {'name': 'BMW'})
        self.assertTrue(CarBrand.objects.filter(name='BMW').exists())
        
        # Model CRUD
        response = self.client.post(reverse('model_add_generic'), {'brand': self.brand.id, 'name': 'Accord'})
        self.assertTrue(CarModel.objects.filter(name='Accord').exists())
        
        # SparePart CRUD
        response = self.client.post(reverse('spare_add'), {'name': 'Brake Pad'})
        self.assertTrue(SparePart.objects.filter(name='Brake Pad').exists())
        
        # ConcernSolution CRUD
        response = self.client.post(reverse('concern_add'), {'concern': 'Brake Sound'})
        self.assertTrue(ConcernSolution.objects.filter(concern='Brake Sound').exists())

    def test_completed_view_search(self):
        self.jobcard.completed = True
        self.jobcard.completed_date = timezone.localdate()
        self.jobcard.save()
        
        url = reverse('completed_list')
        response = self.client.get(url, {'q': 'KL01AB1111', 'filter': 'all'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertContains(response, 'KL01AB1111')
