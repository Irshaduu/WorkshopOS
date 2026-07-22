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
