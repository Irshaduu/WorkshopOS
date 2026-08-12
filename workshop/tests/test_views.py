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
            admitted_date=timezone.now().date(),
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
                admitted_date=timezone.now().date()
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
            'admitted_date': str(timezone.now().date()),
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
            'admitted_date': str(timezone.now().date()),
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

    def test_financial_report_exhaustive_filters(self):
        """
        `q` / `status` narrow the LIVE JOBS list.

        Every assertion here is scoped to that list rather than to the whole
        page, because the operations board above it deliberately ignores both
        parameters: it reports the state of the workshop right now, and a
        half-filtered answer to that question is worse than no answer. Asserting
        against the whole page would silently stop testing the filter — the
        board names every active car whatever is searched for.
        """
        url = reverse('live_report')
        # live_report only shows completed=False (active) jobs, so create one that is active
        paid_job = JobCard.objects.create(registration_number='PAID001', admitted_date=timezone.now().date(), completed=False, payment_status='PAID')

        def live_jobs(response):
            # The heading markup, not the bare class name — that also appears
            # in the page's own stylesheet, which would split too early.
            return response.content.decode().split('<div class="lr-jobs-head">', 1)[1]

        # 1. Search filter
        self.assertIn('PAID001', live_jobs(self.client.get(url, {'q': 'PAID001'})))

        # 2. Payment Status filter
        self.assertIn('PAID001', live_jobs(self.client.get(url, {'status': 'PAID'})))

        # 3. Empty filter
        self.assertNotIn('PAID001', live_jobs(self.client.get(url, {'q': 'NOBODY'})))

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
        self.jobcard.completed_date = timezone.now().date()
        self.jobcard.save()
        
        url = reverse('completed_list')
        response = self.client.get(url, {'q': 'KL01AB1111', 'filter': 'all'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertContains(response, 'KL01AB1111')
