from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from workshop.models import JobCard
from datetime import date, timedelta


class DashboardViewsTestCase(TestCase):
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')

        # Owner can access everything (trash, restore, etc.)
        self.owner = User.objects.create_user(username='owner', password='password')
        self.owner.groups.add(self.owner_group)

        # Office user for delivered_list, live_report, toggle_hold etc.
        self.office = User.objects.create_user(username='officestaff', password='password')
        self.office.groups.add(self.office_group)

        self.client = Client()
        self.client.login(username='owner', password='password')

        self.job = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Toyota',
            model_name='Corolla',
            registration_number='KL01A1234',
            customer_name='John Doe'
        )

    def test_live_report_standard(self):
        """Standard GET to live_report should render full template."""
        url = reverse('live_report')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/jobcard/live_report.html')

    def test_live_report_with_search_and_status_filter(self):
        """Live report should support q and status query params."""
        url = reverse('live_report')
        response = self.client.get(url, {'q': 'Toyota', 'status': 'PENDING'})
        self.assertEqual(response.status_code, 200)

        response = self.client.get(url, {'q': 'Toyota', 'status': 'PAID'})
        self.assertEqual(response.status_code, 200)

    def test_jobcard_list_standard_and_ajax(self):
        """Jobcard list should work for both standard and AJAX requests."""
        url = reverse('jobcard_list')

        # Standard GET (q is ignored, smart reset)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # AJAX GET with search
        response = self.client.get(
            url, {'q': 'John'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/jobcard/job_list_partial.html')

    def test_trash_list_standard_and_ajax(self):
        """Trash list should show deleted jobs; AJAX returns partial."""
        self.job.is_deleted = True
        self.job.save()

        url = reverse('trash_list')

        # Standard GET
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/jobcard/trash_list.html')

        # AJAX Search
        response = self.client.get(
            url, {'q': 'KL01'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/jobcard/trash_list_partial.html')

    def test_restore_jobcard(self):
        """Restore should un-delete the job and redirect to trash list."""
        self.job.is_deleted = True
        self.job.save()

        url = reverse('restore_jobcard', args=[self.job.id])
        response = self.client.get(url)
        self.assertRedirects(response, '/trash/?tab=jobcards')

        self.job.refresh_from_db()
        self.assertFalse(self.job.is_deleted)

    def test_mark_delivered_and_undo(self):
        """mark_delivered sets delivered=True; undo_delivered reverses it."""
        # mark_delivered
        url_mark = reverse('mark_delivered', args=[self.job.id])
        response = self.client.post(url_mark)
        self.assertRedirects(response, reverse('home'))
        self.job.refresh_from_db()
        self.assertTrue(self.job.delivered)
        self.assertEqual(self.job.discharged_date, date.today())

        # undo_delivered
        url_undo = reverse('undo_delivered', args=[self.job.id])
        response = self.client.post(url_undo)
        self.assertRedirects(response, reverse('delivered_list'))
        self.job.refresh_from_db()
        self.assertFalse(self.job.delivered)
        self.assertIsNone(self.job.discharged_date)

    def test_mark_delivered_get_ignored(self):
        """GET to mark_delivered should not change delivered status."""
        url_mark = reverse('mark_delivered', args=[self.job.id])
        self.client.get(url_mark)
        self.job.refresh_from_db()
        self.assertFalse(self.job.delivered)

    def test_toggle_hold(self):
        """toggle_hold should flip the on_hold flag back and forth."""
        url = reverse('toggle_hold', args=[self.job.id])

        self.assertFalse(self.job.on_hold)

        # First toggle: True
        response = self.client.post(url)
        self.assertRedirects(response, reverse('home'))
        self.job.refresh_from_db()
        self.assertTrue(self.job.on_hold)

        # Second toggle: False
        self.client.post(url)
        self.job.refresh_from_db()
        self.assertFalse(self.job.on_hold)

    def test_delivered_list_standard(self):
        """Standard GET to delivered_list should show today's filter."""
        self.job.delivered = True
        self.job.discharged_date = date.today()
        self.job.save()

        url = reverse('delivered_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/delivered/delivered_list.html')

    def test_delivered_list_ajax_filters(self):
        """AJAX requests to delivered_list should apply all date filters."""
        self.job.delivered = True
        self.job.discharged_date = date.today()
        self.job.save()

        url = reverse('delivered_list')

        for f in ['today', 'week', 'month', 'year']:
            response = self.client.get(
                url, {'filter': f}, HTTP_X_REQUESTED_WITH='XMLHttpRequest'
            )
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(
                response, 'workshop/delivered/delivered_list_partial.html'
            )

    def test_delivered_list_custom_date_filter(self):
        """Custom date range filter should work correctly."""
        self.job.delivered = True
        self.job.discharged_date = date.today()
        self.job.save()

        url = reverse('delivered_list')
        response = self.client.get(url, {
            'filter': 'custom',
            'start_date': str(date.today() - timedelta(days=7)),
            'end_date': str(date.today()),
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)

    def test_delivered_list_search_query(self):
        """AJAX search query on delivered list should filter results."""
        url = reverse('delivered_list')
        response = self.client.get(
            url, {'q': 'Honda'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
