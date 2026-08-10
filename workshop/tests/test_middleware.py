from django.test import TestCase, Client, RequestFactory
from django.contrib.auth.models import User
from workshop.models import UserSession
from workshop.middleware import SessionTrackingMiddleware
from django.utils import timezone

class MiddlewareSecurityTests(TestCase):
    """
    Tests the SessionTrackingMiddleware (The All-Seeing Eye).
    Verifies that every request from an owner is tracked and audited.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='sahad_owner', password='password123')
        self.factory = RequestFactory()
        self.client = Client()

    def test_session_tracking_logic(self):
        """Verify that the middleware creates/updates UserSession records."""
        # 1. Login
        self.client.login(username='sahad_owner', password='password123')
        
        # 2. Trigger the middleware via a request
        # In a real integration test, the client handles this.
        response = self.client.get('/')
        
        # 3. Verify UserSession exists
        session_key = self.client.session.session_key
        session_record = UserSession.objects.filter(session_key=session_key).first()
        self.assertIsNotNone(session_record)
        self.assertEqual(session_record.user, self.user)
        
        # 4. Verify IP tracking (Direct)
        # The test client defaults to 127.0.0.1
        self.assertEqual(session_record.ip_address, '127.0.0.1')

    def test_proxy_ip_identification(self):
        """Verify that the middleware correctly extracts IPs from X-Forwarded-For."""
        # Manual middleware call for edge case testing
        def get_response(req): return None
        middleware = SessionTrackingMiddleware(get_response)
        
        request = self.factory.get('/')
        request.user = self.user
        # Simulate session
        from django.contrib.sessions.middleware import SessionMiddleware
        SessionMiddleware(get_response).process_request(request)
        request.session.save()
        
        # Add Proxy Header
        request.META['HTTP_X_FORWARDED_FOR'] = '203.0.113.1, 192.168.1.1'
        
        middleware(request)
        
        session_record = UserSession.objects.get(session_key=request.session.session_key)
        self.assertEqual(session_record.ip_address, '203.0.113.1')


class SignedInPagesAreNotKeptByTheBrowserTests(TestCase):
    """
    Pressing Back after signing out used to redisplay the dashboard.

    Logging out flushes the session, so the *next request* is bounced to the
    sign-in page — but Back never makes a request. It restores the page from the
    back/forward cache, fully rendered, on a laptop that may now be in somebody
    else's hands. Nothing server-side can undo that after the page has been
    sent; the only lever is telling the browser at the time not to keep it.

    A test client has no bfcache, so what is asserted here is the instruction —
    which is exactly the thing that regressed by being absent.
    """

    def setUp(self):
        from django.contrib.auth.models import Group
        Group.objects.get_or_create(name='Owner')
        Group.objects.get_or_create(name='Floor')
        self.user = User.objects.create_user(username='floorhand', password='password123')
        self.user.groups.add(Group.objects.get(name='Floor'))
        self.client = Client()

    def test_a_signed_in_page_says_no_store(self):
        self.client.login(username='floorhand', password='password123')

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])

    def test_the_sign_in_page_is_left_alone(self):
        """
        Scoped to authenticated responses on purpose. A signed-out page holds
        nothing worth withholding, and widening this would make every asset and
        error page uncacheable for no gain.
        """
        response = self.client.get('/login/')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('no-store', response.get('Cache-Control', ''))

    def test_a_bill_is_covered_too(self):
        """
        The pages that matter most here are the ones carrying money, and they do
        not extend base.html — the printed invoice is a standalone template. A
        header covers it with nothing to remember, which is the same reasoning
        that made NoIndexMiddleware a header rather than a meta tag.
        """
        from datetime import date
        from django.contrib.auth.models import Group
        from django.urls import reverse
        from workshop.models import JobCard

        office, _ = Group.objects.get_or_create(name='Office')
        clerk = User.objects.create_user(username='clerk', password='password123')
        clerk.groups.add(office)
        self.client.login(username='clerk', password='password123')

        job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01A9911', customer_name='X',
        )
        response = self.client.get(reverse('invoice_view', args=[job.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
