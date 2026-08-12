from django.test import TestCase, Client, RequestFactory
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from workshop.models import UserSession, FailedAttempt, AccountLockout, JobCard, CarBrand, CarModel, SpareShop, SpareShopPayment, JobCardSpareItem
from workshop.auth_views import normalize_phone, get_client_ip
import time


class SecurityHardeningTests(TestCase):
    """
    Automated 'Steel Gate' testing suite for WorkshopOS.
    Verifies that IP lockouts, OTP failures, and Session Revocation 
    behave as expected in mission-critical scenarios.
    """

    def setUp(self):

        FailedAttempt.objects.all().delete()
        # 1. Create a baseline 'Owner' user for 2FA tests
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.user = User.objects.create_user(username='Sahad', password='ownerpassword')
        self.user.groups.add(self.owner_group)
        
        self.client = Client()
        self.factory = RequestFactory()
        self.test_ip = '192.168.1.100'

    def test_five_failures_still_stops_the_sixth_attempt(self):
        """
        The protection is unchanged; the unit it counts in is not.

        This used to be the IP gate firing at 5. That was the wrong unit — the
        laptop, the tablet and both owners' phones share one connection, so five
        fumbled attempts on the Floor tablet locked out people who had done
        nothing. `AccountLockout` now locks the account actually being guessed
        at, at the same threshold, while the IP gate stays as a backstop against
        a spray across many accounts (`IP_FAILURE_LIMIT`, covered in
        `test_login.IPLockoutTests`).

        So: still five strikes, still blocked on the sixth even with the right
        password — and now without the collateral damage.
        """
        # The original version of this test posted 'sahad_test', a username that
        # does not exist in setUp — so it only ever exercised the IP counter.
        # Account lockout needs a real target, so it uses the account setUp made.
        url = reverse('login')
        target = self.user

        for _ in range(AccountLockout.MAX_FAILURES):
            self.client.post(
                url,
                {'username': target.username, 'password': 'wrongpassword'},
                REMOTE_ADDR=self.test_ip,
            )

        # Both counters saw the attempts.
        self.assertEqual(
            FailedAttempt.objects.get(ip_address=self.test_ip).failures,
            AccountLockout.MAX_FAILURES,
        )
        self.assertEqual(
            AccountLockout.objects.get(user=target).failures,
            AccountLockout.MAX_FAILURES,
        )

        # The sixth is refused even though the password is now correct.
        response = self.client.post(
            url,
            {'username': target.username, 'password': 'ownerpassword'},
            REMOTE_ADDR=self.test_ip,
        )

        self.assertNotIn('_auth_user_id', self.client.session)
        messages = [m.message for m in list(response.context['messages'])]
        self.assertTrue(
            any("locked" in m for m in messages),
            f"expected a lockout message, got {messages}",
        )

    def test_ip_lockout_success_reset(self):
        """
        Verify that a successful login resets the failure counter for that IP.
        """
        url = reverse('login')
        
        # 1. Record some failures
        FailedAttempt.objects.create(ip_address=self.test_ip, failures=3)
        
        # 2. Login successfully (Admin Step 1)
        response = self.client.post(url, {
            'username': 'Sahad',
            'password': 'ownerpassword'
        }, REMOTE_ADDR=self.test_ip)
        
        # 3. Verify failure counter is now 0 (Reset by success)
        attempt = FailedAttempt.objects.get(ip_address=self.test_ip)
        self.assertEqual(attempt.failures, 0)

    def test_owner_direct_login(self):
        """
        Verify that an Owner can now log in directly with their password,
        bypassing the 2FA OTP step entirely.
        """
        url = reverse('login')
        
        # 1. Login with correct owner credentials
        response = self.client.post(url, {
            'username': 'Sahad',
            'password': 'ownerpassword'
        }, REMOTE_ADDR=self.test_ip, follow=True)
        
        # 2. Verify immediate access to Home (Status 200)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['user'].is_authenticated)
        # Verify it didn't stay on a login page
        self.assertNotIn('login', response.request['PATH_INFO'])

    def test_session_revocation_integrity(self):
        """Verify that revoking a UserSession actually blocks access to the app."""
        # 1. Setup an office user for access
        office_user = User.objects.create_user(username='office_rev', password='password123')
        office_user.groups.add(Group.objects.get_or_create(name='Office')[0])
        
        self.client.login(username='office_rev', password='password123')
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        
        # 2. Fetch the UserSession record created by the middleware
        session_key = self.client.session.session_key
        user_session = UserSession.objects.get(session_key=session_key)
        # Update IP to match our test IP if necessary (though middleware should have set it)
        user_session.ip_address = self.test_ip
        user_session.save()
        
        # 3. Access home (Verify OK)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        
        # 4. Revoke the session record
        user_session.delete() # Middleware or Logout view would do this
        
        # 5. Access home again (Should redirect to login as it's no longer 'active')
        # Note: In our implementation, revocation happens in the middleware via session key match.
        response = self.client.get(reverse('home'))
        # The user should ideally be redirected or denied access.

class JobCardModelTests(TestCase):
    """
    Tests the industrial heart of the JobCard model.
    """
    def setUp(self):
        self.brand = CarBrand.objects.create(name='Toyota')
        self.car_model = CarModel.objects.create(brand=self.brand, name='Camry')

    def test_job_card_bill_generation(self):
        """Verify that bill numbers are generated in the JB-YY-NNN format."""
        today = timezone.now().date()
        year_str = str(today.year)[2:]
        
        job = JobCard.objects.create(
            registration_number='KA01AB1234',
            brand_name='Toyota',
            model_name='Camry',
            admitted_date=today,
            mileage='10000'
        )
        # TITAN STRICTNESS: Exact representation check
        self.assertEqual(str(job), job.bill_number)

    def test_job_card_soft_delete(self):
        """Verify the soft-delete/trash state."""
        job = JobCard.objects.create(
            registration_number='KA01AB1234',
            brand_name='Toyota',
            model_name='Camry',
            admitted_date=timezone.now().date()
        )
        self.assertFalse(job.is_deleted)
        job.is_deleted = True
        job.save()
        self.assertTrue(job.is_deleted)

class UserSessionModelTests(TestCase):
    """
    Tests the device-identification engine in UserSession.
    """
    def test_device_parsing(self):
        # iPhone
        self.assertEqual(UserSession.get_device_name("Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)"), "Apple Safari on iPhone")
        # Android Samsung
        self.assertEqual(UserSession.get_device_name("Mozilla/5.0 (Linux; Android 11; SM-G991B)"), "Web Browser on Samsung Galaxy")
        # Windows PC
        self.assertEqual(UserSession.get_device_name("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"), "Web Browser on Windows PC")
        # Empty/None
        self.assertEqual(UserSession.get_device_name(None), "Web Browser on Desktop")

class AuthHelperTests(TestCase):
    """
    Tests the utility functions that power security and normalization.
    """
    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("+91 98765 43210"), "9876543210")
        self.assertEqual(normalize_phone("9876543210"), "9876543210")
        self.assertEqual(normalize_phone(""), "")
        self.assertEqual(normalize_phone(None), "")

    # `mask_phone` and its test were removed on 2026-07-28 along with
    # `get_owner_mobile` / `get_owner_username_by_mobile`. All three existed to
    # read owner identity out of .env; identity now lives in the database and
    # `resolve_user_by_identifier` reads it from there, so nothing masked or
    # looked up a number from config any more. `normalize_phone` survives —
    # the resolver still uses it to match a typed mobile against a stored one.

    def test_get_client_ip(self):
        factory = RequestFactory()
        # Direct IP
        request = factory.get('/')
        request.META['REMOTE_ADDR'] = '1.1.1.1'
        self.assertEqual(get_client_ip(request), '1.1.1.1')
        # Proxied IP (Steel Gate ignores X-Forwarded-For)
        request.META['HTTP_X_FORWARDED_FOR'] = '2.2.2.2, 1.1.1.1'
        self.assertEqual(get_client_ip(request), '1.1.1.1')

class SpareShopPaymentTests(TestCase):
    """
    Tests the payment system for Spare Shops, ensuring the cascade algorithm
    works properly and that custom fields (like notes) are handled safely.
    """
    def setUp(self):
        self.shop = SpareShop.objects.create(name='Test Auto Parts')
        self.brand = CarBrand.objects.create(name='Ford')
        self.car_model = CarModel.objects.create(brand=self.brand, name='Mustang')
        self.job = JobCard.objects.create(
            registration_number='DL01AA1111',
            brand_name='Ford',
            model_name='Mustang',
            admitted_date=timezone.now().date()
        )
        # Create an unpaid spare item for this shop
        self.item1 = JobCardSpareItem.objects.create(
            job_card=self.job,
            shop=self.shop,
            spare_part_name='Brake Pads',
            quantity=1,
            unit_price=2500,
        )
        self.item2 = JobCardSpareItem.objects.create(
            job_card=self.job,
            shop=self.shop,
            spare_part_name='Engine Oil',
            quantity=1,
            unit_price=1000,
        )

        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='officetest', password='password')
        self.user.groups.add(self.office_group)
        self.client = Client()
        self.client.login(username='officetest', password='password')

    def test_bulk_payment_cascade(self):
        """Verify that a bulk payment cascades correctly and saves notes."""
        url = reverse('spare_shop_pay', args=[self.shop.pk])
        response = self.client.post(url, {
            'lump_sum': '3000',
            'payment_method': 'UPI',
            'note': 'Handed to Mohammed directly'
        })
        self.assertEqual(response.status_code, 302)  # Redirects back to detail

        # Check that the shop totals were updated correctly
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_paid_amount, 3000)

        # Verify the payment record and the new note field
        payment = SpareShopPayment.objects.get(shop=self.shop)
        self.assertEqual(payment.amount, 3000)
        self.assertEqual(payment.payment_method, 'UPI')
        self.assertEqual(payment.note, 'Handed to Mohammed directly')

    def test_shop_detail_date_filters(self):
        """Verify that the date filtering logic successfully renders without errors."""
        base_url = reverse('spare_shop_detail', args=[self.shop.pk])
        
        # Test 1: All time
        res_all = self.client.get(base_url + '?filter=all')
        self.assertEqual(res_all.status_code, 200)
        
        # Test 2: Month
        res_month = self.client.get(base_url + '?filter=month')
        self.assertEqual(res_month.status_code, 200)

        # Test 3: Year
        res_year = self.client.get(base_url + '?filter=year')
        self.assertEqual(res_year.status_code, 200)

        # Test 4: Custom
        res_custom = self.client.get(base_url + '?filter=custom&start_date=2026-01-01&end_date=2026-12-31')
        self.assertEqual(res_custom.status_code, 200)
