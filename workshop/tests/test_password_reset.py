"""
Emailed 6-digit password reset — Owners only.

Chosen over Django's built-in reset *link* because on iOS an installed PWA has
its own cookie jar: a link tapped in the mail app opens in Safari/Chrome and
completes the reset in a different session, leaving the app still signed out. A
code is plain text, so the reset finishes in the session that asked for it.

The rules these tests exist to hold:
  - the code is never stored, only its SHA-256 hash
  - 10-minute expiry, single use, 5 attempts, then dead
  - throttling is counted per account in the DB, not in the session (a session
    counter is defeated by clearing cookies, which would let someone burn the
    mail provider's quota)
  - every response is identical whether or not the account exists
  - a weak password does not spend the code
  - a completed reset signs the account out everywhere
"""

from datetime import timedelta

from django.contrib.auth.models import User, Group
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.auth_views import resolve_user_by_identifier, can_reset_password
from workshop.models import PasswordResetOTP, UserProfile, UserSession, FailedAttempt

OLD_PASSWORD = 'old-owner-password-1'
NEW_PASSWORD = 'Str0ngPass!2026'


class ResetOTPModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='Sahad', password=OLD_PASSWORD, email='sahad@example.com',
        )

    def test_code_is_stored_only_as_a_hash(self):
        otp, code = PasswordResetOTP.issue(self.user)

        self.assertNotIn(code, otp.code_hash)
        self.assertEqual(len(otp.code_hash), 64)
        self.assertEqual(len(code), PasswordResetOTP.CODE_LENGTH)
        self.assertTrue(code.isdigit())

    def test_correct_code_verifies_once_only(self):
        otp, code = PasswordResetOTP.issue(self.user)

        self.assertTrue(otp.verify(code))
        self.assertIsNotNone(otp.used_at)
        self.assertFalse(otp.is_usable)
        self.assertFalse(otp.verify(code))

    def test_wrong_code_spends_an_attempt(self):
        otp, code = PasswordResetOTP.issue(self.user)

        self.assertFalse(otp.verify('000000'))
        self.assertEqual(otp.attempts, 1)
        self.assertEqual(otp.attempts_remaining, PasswordResetOTP.MAX_ATTEMPTS - 1)

    def test_code_dies_after_the_attempt_budget(self):
        otp, code = PasswordResetOTP.issue(self.user)

        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            otp.verify('000000')

        self.assertFalse(otp.is_usable)
        self.assertFalse(otp.verify(code), "a burned code must not accept the right value")

    def test_expired_code_is_unusable(self):
        otp, code = PasswordResetOTP.issue(self.user)
        otp.expires_at = timezone.now() - timedelta(seconds=1)
        otp.save(update_fields=['expires_at'])

        self.assertFalse(otp.is_usable)
        self.assertFalse(otp.verify(code))

    def test_issuing_retires_the_previous_code(self):
        first, first_code = PasswordResetOTP.issue(self.user)
        PasswordResetOTP.issue(self.user)

        first.refresh_from_db()
        self.assertIsNotNone(first.used_at)
        self.assertFalse(first.verify(first_code))

    # -- throttling ---------------------------------------------------
    def test_resend_cooldown_blocks_a_rapid_second_request(self):
        PasswordResetOTP.issue(self.user)

        self.assertIsNotNone(PasswordResetOTP.throttle_reason(self.user))

    def test_cooldown_clears_once_elapsed(self):
        otp, _ = PasswordResetOTP.issue(self.user)
        PasswordResetOTP.objects.filter(pk=otp.pk).update(
            created_at=timezone.now() - timedelta(seconds=PasswordResetOTP.RESEND_COOLDOWN_SECONDS + 5)
        )

        self.assertIsNone(PasswordResetOTP.throttle_reason(self.user))

    def test_hourly_cap_blocks_further_requests(self):
        for _ in range(PasswordResetOTP.MAX_REQUESTS_PER_HOUR):
            otp, _ = PasswordResetOTP.issue(self.user)
            PasswordResetOTP.objects.filter(pk=otp.pk).update(
                created_at=timezone.now() - timedelta(minutes=2)
            )

        self.assertIn('hour', PasswordResetOTP.throttle_reason(self.user))

    def test_throttle_is_per_account_not_global(self):
        other = User.objects.create_user(
            username='Rijas', password=OLD_PASSWORD, email='rijas@example.com',
        )
        PasswordResetOTP.issue(self.user)

        self.assertIsNone(PasswordResetOTP.throttle_reason(other))


class IdentifierResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='Sahad', password=OLD_PASSWORD, email='sahad@example.com',
        )
        UserProfile.objects.create(user=self.user, mobile_number='+919567494933')

    def test_resolves_by_username(self):
        self.assertEqual(resolve_user_by_identifier('Sahad'), self.user)

    def test_resolves_by_email_any_case(self):
        self.assertEqual(resolve_user_by_identifier('SAHAD@example.com'), self.user)

    def test_resolves_by_mobile_in_any_format(self):
        for typed in ('9567494933', '+919567494933', '+91 95674 94933'):
            with self.subTest(typed=typed):
                self.assertEqual(resolve_user_by_identifier(typed), self.user)

    def test_unknown_identifier_resolves_to_nothing(self):
        self.assertIsNone(resolve_user_by_identifier('nobody'))
        self.assertIsNone(resolve_user_by_identifier(''))

    def test_ambiguous_email_fails_closed(self):
        """User.email has no DB constraint — never guess between two matches."""
        User.objects.create_user(
            username='Rijas', password=OLD_PASSWORD, email='sahad@example.com',
        )

        self.assertIsNone(resolve_user_by_identifier('sahad@example.com'))


class ResetEligibilityTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office'):
            Group.objects.get_or_create(name=name)

    def test_owner_with_email_may_reset(self):
        owner = User.objects.create_user(
            username='Sahad', password=OLD_PASSWORD, email='sahad@example.com',
        )
        owner.groups.add(Group.objects.get(name='Owner'))

        self.assertTrue(can_reset_password(owner))

    def test_office_account_may_not(self):
        """Office/Floor logins are managed by owners — no self-service path."""
        office = User.objects.create_user(username='officestaff', password=OLD_PASSWORD)
        office.groups.add(Group.objects.get(name='Office'))

        self.assertFalse(can_reset_password(office))

    def test_owner_without_an_email_may_not(self):
        owner = User.objects.create_user(username='Rijas', password=OLD_PASSWORD)
        owner.groups.add(Group.objects.get(name='Owner'))

        self.assertFalse(can_reset_password(owner))

    def test_inactive_owner_may_not(self):
        owner = User.objects.create_user(
            username='Gone', password=OLD_PASSWORD, email='gone@example.com', is_active=False,
        )
        owner.groups.add(Group.objects.get(name='Owner'))

        self.assertFalse(can_reset_password(owner))


class ResetFlowTests(TestCase):
    def setUp(self):
        FailedAttempt.objects.all().delete()
        Group.objects.get_or_create(name='Owner')
        Group.objects.get_or_create(name='Office')

        self.owner = User.objects.create_user(
            username='Sahad', password=OLD_PASSWORD, email='sahad@example.com',
        )
        self.owner.groups.add(Group.objects.get(name='Owner'))

        self.forgot_url = reverse('owner_forgot_password')
        self.reset_url = reverse('owner_reset_password')
        mail.outbox = []

    def _request_code(self, identifier='Sahad'):
        return self.client.post(self.forgot_url, {'username': identifier})

    def _latest_code(self):
        """Recover the plain code from the email — it is never stored."""
        return mail.outbox[-1].subject.split()[0]

    # -- step 1 -------------------------------------------------------
    def test_code_is_emailed_and_appears_in_the_subject(self):
        response = self._request_code()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sahad@example.com'])
        code = self._latest_code()
        self.assertTrue(code.isdigit())
        self.assertEqual(len(code), 6)
        self.assertIn(code, mail.outbox[0].body)
        self.assertEqual(response.status_code, 302)

    def test_unknown_account_is_indistinguishable(self):
        """Same redirect, same message, no email — no existence oracle."""
        real = self._request_code('Sahad')
        mail.outbox = []
        self.client.session.flush()
        fake = self._request_code('does-not-exist')

        self.assertEqual(real.status_code, fake.status_code)
        self.assertEqual(real.url, fake.url)
        self.assertEqual(len(mail.outbox), 0)

    def test_office_account_gets_no_code(self):
        office = User.objects.create_user(username='officestaff', password=OLD_PASSWORD)
        office.groups.add(Group.objects.get(name='Office'))

        self._request_code('officestaff')

        self.assertEqual(len(mail.outbox), 0)

    def test_throttled_request_sends_nothing_but_looks_the_same(self):
        first = self._request_code()
        mail.outbox = []
        second = self._request_code()

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(first.url, second.url)

    # -- step 2 -------------------------------------------------------
    def test_correct_code_sets_the_new_password(self):
        self._request_code()
        code = self._latest_code()

        response = self.client.post(self.reset_url, {
            'otp': code, 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD))
        self.assertRedirects(response, reverse('admin_login'), fetch_redirect_response=False)

    def test_wrong_code_spends_an_attempt_and_keeps_the_password(self):
        self._request_code()

        self.client.post(self.reset_url, {
            'otp': '000000', 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(OLD_PASSWORD))
        self.assertEqual(PasswordResetOTP.objects.get(user=self.owner).attempts, 1)

    def test_weak_password_does_not_spend_the_code(self):
        """A typo in the password must not cost the owner a fresh email."""
        self._request_code()
        code = self._latest_code()

        self.client.post(self.reset_url, {
            'otp': code, 'new_password': 'short', 'confirm_password': 'short',
        })
        self.assertEqual(PasswordResetOTP.objects.get(user=self.owner).attempts, 0)

        self.client.post(self.reset_url, {
            'otp': code, 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD))

    def test_mismatched_confirmation_does_not_spend_the_code(self):
        self._request_code()
        code = self._latest_code()

        self.client.post(self.reset_url, {
            'otp': code, 'new_password': NEW_PASSWORD, 'confirm_password': 'Different!2026',
        })

        self.assertEqual(PasswordResetOTP.objects.get(user=self.owner).attempts, 0)

    def test_common_password_is_rejected_by_django_validators(self):
        self._request_code()
        code = self._latest_code()

        self.client.post(self.reset_url, {
            'otp': code, 'new_password': 'password123', 'confirm_password': 'password123',
        })

        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(OLD_PASSWORD))

    def test_expired_code_is_refused(self):
        self._request_code()
        code = self._latest_code()
        PasswordResetOTP.objects.filter(user=self.owner).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.client.post(self.reset_url, {
            'otp': code, 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(OLD_PASSWORD))
        self.assertRedirects(response, self.forgot_url, fetch_redirect_response=False)

    def test_code_cannot_be_replayed(self):
        self._request_code()
        code = self._latest_code()
        self.client.post(self.reset_url, {
            'otp': code, 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        self.client.post(self.forgot_url, {'username': 'Sahad'})
        response = self.client.post(self.reset_url, {
            'otp': code, 'new_password': 'Another!Pass2026', 'confirm_password': 'Another!Pass2026',
        })

        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD), "the spent code must not work again")
        self.assertEqual(response.status_code, 302)

    def test_reset_signs_the_account_out_everywhere(self):
        """A reset is how a locked-out owner recovers — a stolen session must not survive it."""
        stolen = SessionStore()
        stolen['_auth_user_id'] = str(self.owner.pk)
        stolen.create()
        UserSession.objects.create(user=self.owner, session_key=stolen.session_key)

        self._request_code()
        self.client.post(self.reset_url, {
            'otp': self._latest_code(),
            'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        self.assertFalse(Session.objects.filter(session_key=stolen.session_key).exists())
        self.assertFalse(UserSession.objects.filter(user=self.owner).exists())

    def test_step_two_cannot_be_opened_directly(self):
        response = self.client.get(self.reset_url)

        self.assertRedirects(response, self.forgot_url, fetch_redirect_response=False)

    def test_signed_in_user_is_sent_home(self):
        self.client.login(username='Sahad', password=OLD_PASSWORD)

        response = self.client.get(self.forgot_url)

        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
