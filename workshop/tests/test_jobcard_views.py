from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from workshop.models import (
    JobCard, Mechanic, CarBrand, CarModel,
    SparePart, ConcernSolution
)
from datetime import date


class JobCardViewsTestCase(TestCase):
    def setUp(self):
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')
        self.office_group, _ = Group.objects.get_or_create(name='Office')

        self.user = User.objects.create_user(username='staff', password='password')
        self.user.groups.add(self.floor_group)
        self.client = Client()
        self.client.login(username='staff', password='password')

        self.mechanic = Mechanic.objects.create(name='Lead Tech')
        self.job = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Toyota',
            model_name='Corolla',
            registration_number='KL01A1234',
            customer_name='John',
            customer_contact='1234567890'
        )

    def _base_formset_data(self, reg='MH123456'):
        """Helper: returns valid POST data for the create/edit view."""
        return {
            'registration_number': reg,
            'admitted_date': str(date.today()),
            'customer_name': 'Alice',
            'customer_contact': '9876543210',
            'brand_name': 'Honda',
            'model_name': 'City',
            'mileage': '10k',
            'lead_mechanic': self.mechanic.id,
            'car_color': 'Black',

            'concerns-TOTAL_FORMS': '1',
            'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0',
            'concerns-MAX_NUM_FORMS': '1000',
            'concerns-0-concern_text': 'Oil change',
            'concerns-0-status': 'PENDING',

            'spares-TOTAL_FORMS': '1',
            'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0',
            'spares-MAX_NUM_FORMS': '1000',
            'spares-0-spare_part_name': 'Engine Oil',
            'spares-0-quantity': '1',
            'spares-0-unit_price': '500',
            'spares-0-total_price': '600',
            'spares-0-status': 'PENDING',
            'spares-0-shop_name': '',
            'spares-0-ordered_date': '',
            'spares-0-received_date': '',

            'labours-TOTAL_FORMS': '1',
            'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0',
            'labours-MAX_NUM_FORMS': '1000',
            'labours-0-job_description': 'Service',
            'labours-0-amount': '400',
        }

    def test_jobcard_create_get(self):
        """GET request should render the blank create form."""
        url = reverse('jobcard_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/jobcard/jobcard_form.html')

    def test_jobcard_create_post_success(self):
        """Successful POST should create job card and redirect to edit page."""
        url = reverse('jobcard_create')
        data = self._base_formset_data(reg='MH123456')

        response = self.client.post(url, data)

        # Should have created the job
        job_new = JobCard.objects.filter(registration_number__iexact='MH123456').first()
        self.assertIsNotNone(job_new, "Job card was not created")

        # Should redirect to edit page
        self.assertRedirects(response, reverse('jobcard_edit', args=[job_new.pk]))

        # Verify inline data was saved
        self.assertEqual(job_new.concerns.count(), 1)
        self.assertEqual(job_new.spares.count(), 1)
        self.assertEqual(job_new.labours.count(), 1)
        self.assertEqual(job_new.concerns.first().concern_text, 'Oil change')
        self.assertEqual(job_new.spares.first().spare_part_name, 'Engine Oil')
        self.assertEqual(job_new.labours.first().job_description, 'Service')

    def test_jobcard_create_post_autolearning(self):
        """Auto-learning should save new brands, models, concerns, and spares."""
        url = reverse('jobcard_create')
        data = self._base_formset_data(reg='NEW999')
        self.client.post(url, data)

        self.assertTrue(ConcernSolution.objects.filter(concern='Oil change').exists())
        self.assertTrue(SparePart.objects.filter(name='Engine Oil').exists())

    def test_jobcard_create_duplicate_blocked(self):
        """
        Creating a job for a plate that already has an active job card must be
        hard-blocked — no bypass. Two active job cards for the same registration
        number is the exact state this check exists to prevent.
        """
        url = reverse('jobcard_create')
        # Use the existing job's plate (KL01A1234 — active, not completed)
        data = self._base_formset_data(reg='KL01A1234')

        # Repeated attempts must all be blocked — there is no N-th-attempt bypass.
        for _ in range(3):
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 200)  # Re-renders with error, not saved
            self.assertEqual(JobCard.objects.filter(
                registration_number__iexact='KL01A1234'
            ).count(), 1)

    def test_jobcard_create_allowed_once_existing_is_completed(self):
        """Creating a job for a plate is allowed once the prior job card is completed."""
        self.job.completed = True
        self.job.save()

        url = reverse('jobcard_create')
        data = self._base_formset_data(reg='KL01A1234')
        response = self.client.post(url, data)

        new_job = JobCard.objects.filter(
            registration_number__iexact='KL01A1234', completed=False
        ).first()
        self.assertIsNotNone(new_job, "New job card should be created once the old one is completed")
        self.assertRedirects(response, reverse('jobcard_edit', args=[new_job.pk]))

    def test_jobcard_edit_get(self):
        """GET to edit view should render pre-filled form."""
        url = reverse('jobcard_edit', args=[self.job.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/jobcard/jobcard_form.html')

    def test_jobcard_edit_post_success(self):
        """Valid POST to edit should save changes and redirect to same edit page."""
        url = reverse('jobcard_edit', args=[self.job.pk])

        data = {
            'registration_number': 'KL01A1234',
            'admitted_date': str(date.today()),
            'customer_name': 'John Edited',
            'customer_contact': '1234567890',
            'brand_name': 'Toyota',
            'model_name': 'Corolla',
            'car_color': 'White',

            'concerns-TOTAL_FORMS': '1',
            'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0',
            'concerns-MAX_NUM_FORMS': '1000',
            'concerns-0-concern_text': 'New Brake Issue',
            'concerns-0-status': 'PENDING',

            'spares-TOTAL_FORMS': '0',
            'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0',
            'spares-MAX_NUM_FORMS': '1000',

            'labours-TOTAL_FORMS': '0',
            'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0',
            'labours-MAX_NUM_FORMS': '1000',
        }

        response = self.client.post(url, data)
        # Edit redirects back to the same edit page
        self.assertRedirects(response, reverse('jobcard_edit', args=[self.job.pk]))

        self.job.refresh_from_db()
        self.assertEqual(self.job.customer_name, 'John Edited')
        # Auto-learning: new concern should appear in master list
        self.assertTrue(ConcernSolution.objects.filter(concern='New Brake Issue').exists())

    def test_jobcard_edit_registration_conflict_blocked(self):
        """
        Editing a job card's registration number to match a DIFFERENT active job
        card must be hard-blocked — that's the third door to the same "two active
        job cards, one vehicle" bug (alongside create and undo_completed).
        """
        # self.job is active with reg 'KL01A1234'. Create a second, unrelated
        # active job card, then try to edit it to steal self.job's plate.
        other_job = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Honda',
            model_name='City',
            registration_number='MH999999',
            customer_name='Bob',
        )

        url = reverse('jobcard_edit', args=[other_job.pk])
        data = self._base_formset_data(reg='KL01A1234')  # collides with self.job

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)  # Re-renders with error, not saved

        other_job.refresh_from_db()
        self.assertEqual(other_job.registration_number, 'MH999999', "Edit must not have saved the colliding plate")

    def test_jobcard_edit_unchanged_registration_not_a_conflict(self):
        """Saving an edit with the SAME registration number must never conflict with itself."""
        url = reverse('jobcard_edit', args=[self.job.pk])
        data = self._base_formset_data(reg='KL01A1234')  # same as self.job's own plate

        response = self.client.post(url, data)
        self.assertRedirects(response, reverse('jobcard_edit', args=[self.job.pk]))

    def test_invoice_view_access_control(self):
        """Floor-only user should be redirected away from invoice view."""
        url = reverse('invoice_view', args=[self.job.pk])

        # Floor user — no office_required permission → redirected to login
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)  # Any redirect is correct

        # Add Office group — should now be able to view
        self.user.groups.add(self.office_group)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_jobcard_detail_view(self):
        """Detail view should be accessible to all staff."""
        url = reverse('jobcard_detail', args=[self.job.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/jobcard/jobcard_detail.html')

    def test_car_profile_detail(self):
        """Car profile detail shows all job history for a plate."""
        self.user.groups.add(self.office_group)
        url = reverse('car_profile_detail', args=['KL01A1234'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_update_bill_status(self):
        """update_bill_status should save payment info, auto-set status and discount."""
        from workshop.models import JobCardSpareItem
        self.user.groups.add(self.office_group)
        url = reverse('update_bill_status', args=[self.job.pk])
        
        # Add a spare so total bill is 600
        JobCardSpareItem.objects.create(job_card=self.job, total_price=600, quantity=1)

        response = self.client.post(url, {
            'received_amount': '500',
            'payment_method': 'Cash',
        })

        self.assertRedirects(response, reverse('invoice_view', args=[self.job.pk]))
        self.job.refresh_from_db()
        self.assertEqual(float(self.job.received_amount), 500.0)
        self.assertEqual(self.job.payment_status, 'PAID')
        self.assertEqual(float(self.job.discount_amount), 100.0)
        
        # Test 0 amount -> PENDING
        self.client.post(url, {
            'received_amount': '0',
            'payment_method': 'Cash',
        })
        self.job.refresh_from_db()
        self.assertEqual(self.job.payment_status, 'PENDING')
        self.assertEqual(float(self.job.discount_amount), 0.0)

    def test_bulk_payment_cascade(self):
        """Test the cascade algorithm for fleet/bulk payments."""
        from decimal import Decimal
        from workshop.models import JobCardSpareItem, BulkPayer
        from datetime import timedelta
        
        self.user.groups.add(self.office_group)

        # Make self.job the oldest
        self.job.admitted_date = date.today() - timedelta(days=2)
        self.job.save()

        # Create two more pending jobs for 'John'
        job2 = JobCard.objects.create(
            admitted_date=date.today() - timedelta(days=1), brand_name='Toyota', model_name='Camry',
            registration_number='KL01A9999', customer_name='John', customer_contact='1234567890'
        )
        job3 = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Yaris',
            registration_number='KL01A8888', customer_name='John', customer_contact='1234567890'
        )

        # Add spares so they have balances
        JobCardSpareItem.objects.create(job_card=self.job, total_price=1000, quantity=1)
        JobCardSpareItem.objects.create(job_card=job2, total_price=2000, quantity=1)
        JobCardSpareItem.objects.create(job_card=job3, total_price=3000, quantity=1)

        # Create a BulkPayer and link the cards
        bulk_payer = BulkPayer.objects.create(customer_name='John')
        bulk_payer.job_cards.add(self.job, job2, job3)

        # Total balance is 6000. Customer pays a lump sum of 2500
        url = reverse('bulk_payer_pay', args=[bulk_payer.pk])
        response = self.client.post(url, {
            'lump_sum': '2500',
            'payment_method': 'CASH'
        })
        
        self.assertRedirects(response, reverse('bulk_payer_detail', args=[bulk_payer.pk]))

        self.job.refresh_from_db()
        job2.refresh_from_db()
        job3.refresh_from_db()

        # Job 1 (oldest): Should be fully PAID (1000)
        self.assertEqual(self.job.payment_status, 'BULK_PAID')
        self.assertEqual(self.job.received_amount, Decimal('1000'))

        # Job 2: Should be PARTIAL (1500 received out of 2000)
        self.assertEqual(job2.payment_status, 'PARTIAL')
        self.assertEqual(job2.received_amount, Decimal('1500'))

        # Job 3: Should be untouched (PENDING, 0 received)
        self.assertEqual(job3.payment_status, 'PENDING')
        self.assertEqual(job3.received_amount, Decimal('0'))
