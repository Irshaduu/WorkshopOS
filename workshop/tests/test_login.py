"""
Login — one engine behind two faces, and the lockout that protects it.

`/login/` and `/admin-login/` are the same view with a different heading. The
separation is presentational, because owners think of them as two doors; the
authentication path, the identifier resolver and both lockouts are shared. Two
full views drifted before — one of them rejected a valid owner password with a
fake "Invalid credentials", a lie that bought nothing since the owner door was
one link away.

The lockout rules these tests hold:
  - 5 failures locks the *account* for 15 minutes
  - 20 failures locks the *IP*, as a backstop only
The unit matters. The whole workshop shares one connection, so counting only by
IP meant five fumbled attempts on the Floor tablet locked the owners out of
their own phones.
"""

from datetime import timedelta

from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.auth_views import IP_FAILURE_LIMIT
from workshop.models import AccountLockout, FailedAttempt, UserProfile

PASSWORD = 'correct-horse-battery-1'
WRONG = 'not-the-password'


# A `NoOutboundAlerts` base class used to live here, stubbing the Twilio/Telegram
# broadcast that fired on every successful sign-in. Without it these tests made a
# live call to the real Twilio API and collected a 401 apiece — slow, flaky, and
# traffic a test suite has no business generating. The channel was deleted on
# 2026-07-29 and the login alert is now an in-app notification, which is a plain
# database write, so there is nothing left to stub.


class LoginFacesTests(TestCase):
    """Either door opens for any role — the face is a heading, not a gate."""

    def setUp(self):
        FailedAttempt.objects.all().delete()
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(
            username='Sahad', password=PASSWORD, email='sahad@example.com',
        )
        self.owner.groups.add(Group.objects.get(name='Owner'))
        UserProfile.objects.create(user=self.owner, mobile_number='+919567494933')

        self.floor = User.objects.create_user(username='floorstaff', password=PASSWORD)
        self.floor.groups.add(Group.objects.get(name='Floor'))

        self.staff_url = reverse('login')
        self.owner_url = reverse('admin_login')

    def _post(self, url, identifier, password=PASSWORD, **extra):
        return self.client.post(url, {'username': identifier, 'password': password, **extra})

    def test_both_faces_render(self):
        self.assertEqual(self.client.get(self.staff_url).status_code, 200)
        self.assertEqual(self.client.get(self.owner_url).status_code, 200)

    def test_each_face_offers_the_other(self):
        self.assertContains(self.client.get(self.staff_url), self.owner_url)
        self.assertContains(self.client.get(self.owner_url), self.staff_url)

    def test_forgot_password_appears_only_on_the_owner_face(self):
        """Office and Floor carry no email, so the link would go nowhere."""
        forgot = reverse('owner_forgot_password')
        self.assertContains(self.client.get(self.owner_url), forgot)
        self.assertNotContains(self.client.get(self.staff_url), forgot)

    def test_owner_can_sign_in_on_the_staff_face(self):
        """The old fake rejection is gone — it protected nothing."""
        response = self._post(self.staff_url, 'Sahad')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.owner.pk))

    def test_floor_can_sign_in_on_the_owner_face(self):
        response = self._post(self.owner_url, 'floorstaff')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.floor.pk))

    def test_already_signed_in_is_sent_home(self):
        self.client.login(username='Sahad', password=PASSWORD)

        for url in (self.staff_url, self.owner_url):
            with self.subTest(url=url):
                self.assertRedirects(
                    self.client.get(url), reverse('home'), fetch_redirect_response=False,
                )

    # -- identifier resolution ----------------------------------------
    def test_sign_in_by_username_email_or_mobile(self):
        for identifier in ('Sahad', 'sahad@example.com', 'SAHAD@EXAMPLE.COM',
                           '9567494933', '+919567494933'):
            with self.subTest(identifier=identifier):
                self.client.logout()
                self._post(self.owner_url, identifier)
                self.assertEqual(
                    self.client.session.get('_auth_user_id'), str(self.owner.pk),
                    f"{identifier} should have resolved to Sahad",
                )

    def test_unknown_identifier_is_refused(self):
        response = self._post(self.staff_url, 'nobody-at-all')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    # -- ?next= --------------------------------------------------------
    def test_next_returns_the_user_to_where_they_were_headed(self):
        response = self._post(self.owner_url, 'Sahad', next=reverse('cashbook'))

        self.assertRedirects(response, reverse('cashbook'), fetch_redirect_response=False)

    def test_next_to_another_host_is_ignored(self):
        """An unchecked next turns login into an open redirect."""
        response = self._post(self.owner_url, 'Sahad', next='https://evil.example.com/harvest')

        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)


class AccountLockoutTests(TestCase):
    def setUp(self):
        FailedAttempt.objects.all().delete()
        Group.objects.get_or_create(name='Owner')

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.other = User.objects.create_user(username='Rijas', password=PASSWORD)

        self.url = reverse('admin_login')

    def _fail(self, identifier='Sahad', times=1):
        for _ in range(times):
            self.client.post(self.url, {'username': identifier, 'password': WRONG})

    def test_failures_accumulate_against_the_account(self):
        self._fail(times=3)

        self.assertEqual(AccountLockout.objects.get(user=self.owner).failures, 3)

    def test_account_locks_after_the_limit(self):
        self._fail(times=AccountLockout.MAX_FAILURES)

        self.assertGreater(AccountLockout.minutes_remaining(self.owner), 0)

    def test_locked_account_is_refused_even_with_the_right_password(self):
        self._fail(times=AccountLockout.MAX_FAILURES)

        self.client.post(self.url, {'username': 'Sahad', 'password': PASSWORD})

        self.assertNotIn('_auth_user_id', self.client.session)

    def test_lockout_message_states_the_wait(self):
        self._fail(times=AccountLockout.MAX_FAILURES)

        response = self.client.post(
            self.url, {'username': 'Sahad', 'password': PASSWORD}, follow=True,
        )

        self.assertContains(response, 'locked')
        self.assertContains(response, 'minute')

    def test_lockout_expires(self):
        self._fail(times=AccountLockout.MAX_FAILURES)
        AccountLockout.objects.filter(user=self.owner).update(
            last_attempt=timezone.now() - timedelta(minutes=AccountLockout.LOCKOUT_MINUTES + 1)
        )

        self.assertEqual(AccountLockout.minutes_remaining(self.owner), 0)

    def test_expiry_resets_the_budget(self):
        """An old bad day must not shorten the allowance on a good one."""
        self._fail(times=AccountLockout.MAX_FAILURES)
        AccountLockout.objects.filter(user=self.owner).update(
            last_attempt=timezone.now() - timedelta(minutes=AccountLockout.LOCKOUT_MINUTES + 1)
        )
        AccountLockout.minutes_remaining(self.owner)

        self.assertEqual(AccountLockout.objects.get(user=self.owner).failures, 0)

    def test_success_clears_the_count(self):
        self._fail(times=AccountLockout.MAX_FAILURES - 1)

        self.client.post(self.url, {'username': 'Sahad', 'password': PASSWORD})

        self.assertEqual(AccountLockout.objects.get(user=self.owner).failures, 0)

    def test_one_account_locking_does_not_lock_another(self):
        """The whole point: the Floor tablet must not lock the owners out."""
        self._fail(times=AccountLockout.MAX_FAILURES)

        self.client.post(self.url, {'username': 'Rijas', 'password': PASSWORD})

        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.other.pk))


class IPLockoutTests(TestCase):
    def setUp(self):
        FailedAttempt.objects.all().delete()
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.url = reverse('admin_login')

    def test_ip_gate_tolerates_a_shared_workshop_connection(self):
        """
        Five failures used to lock the whole building. The account gate handles
        that case now, so the IP gate must not fire at ordinary volumes.
        """
        for _ in range(AccountLockout.MAX_FAILURES + 1):
            self.client.post(self.url, {'username': 'ghost', 'password': WRONG})

        response = self.client.post(self.url, {'username': 'Sahad', 'password': PASSWORD})

        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.owner.pk))
        self.assertEqual(response.status_code, 302)

    def test_ip_gate_still_closes_on_a_spray(self):
        FailedAttempt.objects.create(ip_address='127.0.0.1', failures=IP_FAILURE_LIMIT)

        response = self.client.post(
            self.url, {'username': 'Sahad', 'password': PASSWORD}, follow=True,
        )

        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, 'this network')


class RoleGateTests(TestCase):
    """
    Anonymous -> login page. Signed in but wrong role -> 403.

    Both used to redirect to a login form, so an Office user opening an Owner
    page saw a sign-in screen while already signed in.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.office = User.objects.create_user(username='officestaff', password=PASSWORD)
        self.office.groups.add(Group.objects.get(name='Office'))

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))

        self.owner_only_url = reverse('deletion_history')

    def test_anonymous_is_redirected_to_sign_in(self):
        response = self.client.get(self.owner_only_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin-login/', response.url)

    def test_redirect_carries_the_intended_destination(self):
        response = self.client.get(self.owner_only_url)

        self.assertIn('next=', response.url)
        self.assertIn(self.owner_only_url, response.url)

    def test_signed_in_wrong_role_gets_403(self):
        self.client.login(username='officestaff', password=PASSWORD)

        response = self.client.get(self.owner_only_url)

        self.assertEqual(response.status_code, 403)

    def test_right_role_passes(self):
        self.client.login(username='Sahad', password=PASSWORD)

        self.assertEqual(self.client.get(self.owner_only_url).status_code, 200)
