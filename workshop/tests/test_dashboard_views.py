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

        # Office user for completed_list, live_report, toggle_hold etc.
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

    def test_mark_completed_and_undo(self):
        """mark_completed sets completed=True; undo_completed reverses it."""
        # mark_completed
        url_mark = reverse('mark_completed', args=[self.job.id])
        response = self.client.post(url_mark)
        self.assertRedirects(response, reverse('home'))
        self.job.refresh_from_db()
        self.assertTrue(self.job.completed)
        self.assertEqual(self.job.completed_date, date.today())

        # undo_completed
        url_undo = reverse('undo_completed', args=[self.job.id])
        response = self.client.post(url_undo)
        self.assertRedirects(response, reverse('completed_list'))
        self.job.refresh_from_db()
        self.assertFalse(self.job.completed)
        self.assertIsNone(self.job.completed_date)

    def test_undo_completed_blocked_when_active_conflict_exists(self):
        """
        undo_completed must refuse to reactivate an old job card if a different
        job card is already active for the same registration number — otherwise
        two active job cards would exist for the same vehicle simultaneously.
        """
        # self.job (KL01A1234) is completed, and will be the "old" job card.
        self.job.completed = True
        self.job.completed_date = date.today()
        self.job.save()

        # A different, currently-active job card for the same vehicle.
        live_job = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Toyota',
            model_name='Corolla',
            registration_number='KL01A1234',
            customer_name='Jane Doe',
        )

        url_undo = reverse('undo_completed', args=[self.job.id])
        response = self.client.post(url_undo)
        self.assertRedirects(response, reverse('completed_list'))

        # Old job card must remain completed — the undo was blocked.
        self.job.refresh_from_db()
        self.assertTrue(self.job.completed)
        self.assertIsNotNone(self.job.completed_date)

        # The live job card is untouched.
        live_job.refresh_from_db()
        self.assertFalse(live_job.completed)

    def test_mark_completed_get_ignored(self):
        """GET to mark_completed should not change completed status."""
        url_mark = reverse('mark_completed', args=[self.job.id])
        self.client.get(url_mark)
        self.job.refresh_from_db()
        self.assertFalse(self.job.completed)

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

    def test_completed_list_standard(self):
        """Standard GET to completed_list should show today's filter."""
        self.job.completed = True
        self.job.completed_date = date.today()
        self.job.save()

        url = reverse('completed_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/completed/completed_list.html')

    def test_completed_list_ajax_filters(self):
        """AJAX requests to completed_list should apply all date filters."""
        self.job.completed = True
        self.job.completed_date = date.today()
        self.job.save()

        url = reverse('completed_list')

        for f in ['today', 'week', 'month', 'year']:
            response = self.client.get(
                url, {'filter': f}, HTTP_X_REQUESTED_WITH='XMLHttpRequest'
            )
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(
                response, 'workshop/completed/completed_list_partial.html'
            )

    def test_completed_list_custom_date_filter(self):
        """Custom date range filter should work correctly."""
        self.job.completed = True
        self.job.completed_date = date.today()
        self.job.save()

        url = reverse('completed_list')
        response = self.client.get(url, {
            'filter': 'custom',
            'start_date': str(date.today() - timedelta(days=7)),
            'end_date': str(date.today()),
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)

    def test_completed_list_search_query(self):
        """AJAX search query on completed list should filter results."""
        url = reverse('completed_list')
        response = self.client.get(
            url, {'q': 'Honda'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
