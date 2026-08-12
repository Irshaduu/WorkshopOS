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
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User, Group
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.auth_views import resolve_user_by_identifier, can_reset_password
from workshop.models import (
    AccountLockout, PasswordResetOTP, UserProfile, UserSession, FailedAttempt,
)

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

    def test_throttled_request_sends_nothing_and_says_so(self):
        """
        A rate limit must be *told*, not performed silently.

        This test previously asserted the opposite — that a throttled request was
        indistinguishable from a successful one, redirect and all. That silence
        was the reported defect: an owner who re-requested inside the cooldown
        was shown "a code has been sent", got no email, and concluded the app was
        broken. The limit the visitor is now told about is their own browser's
        submission rate, which discloses nothing about any account; the
        account-keyed limit in `PasswordResetOTP.throttle_reason` is still silent
        and still the enforcement.
        """
        self._request_code()
        mail.outbox = []
        second = self._request_code()

        self.assertEqual(len(mail.outbox), 0)
        body = self.client.get(second.url).content.decode()
        self.assertIn('another in', body)

    def test_the_visible_throttle_is_not_an_existence_oracle(self):
        """
        The reason the throttle above can be disclosed at all: it fires on what
        *this browser* did, so a real account and an invented one are throttled
        identically. If this ever fails, the message has started leaking whether
        the account exists and must go back to being generic.
        """
        self._request_code('Sahad')
        real = self._request_code('Sahad')
        real_body = self.client.get(real.url).content.decode()

        self.client.session.flush()
        self._request_code('no-such-person')
        fake = self._request_code('no-such-person')
        fake_body = self.client.get(fake.url).content.decode()

        self.assertEqual(real.status_code, fake.status_code)
        self.assertEqual(real.url, fake.url)
        self.assertIn('another in', real_body)
        self.assertIn('another in', fake_body)

    def test_failed_delivery_does_not_spend_the_hourly_budget(self):
        """
        A code nobody received must not count against the three-an-hour cap.

        It used to: the row was retired but left in place, and `throttle_reason`
        counts by `created_at`, so three failed sends exhausted the budget and
        the honest "could not send" error silently became "a code has been sent".
        The app then reported two contradictory things about the same outage.
        """
        with patch('workshop.auth_views.send_reset_code_email', return_value=False):
            self._request_code()

        self.assertEqual(PasswordResetOTP.objects.filter(user=self.owner).count(), 0)
        self.assertIsNone(PasswordResetOTP.throttle_reason(self.owner))

    # -- step 2 -------------------------------------------------------
    def test_correct_code_sets_the_new_password(self):
        self._request_code()
        code = self._latest_code()

        response = self.client.post(self.reset_url, {
            'otp': code, 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

    def test_a_completed_reset_alerts_the_other_owner(self):
        """
        The takeover alarm. A reset also terminates every session, so without
        this the real owner is signed out everywhere with no message and no
        reason — indistinguishable from the app misbehaving. The person who
        performed the reset is excluded (`actor`), which is right in both
        readings: a genuine owner needs no telling, and an intruder should not
        be handed the warning about themselves.
        """
        from workshop.models import Notification

        other = User.objects.create_user(username='Rijas', password=OLD_PASSWORD,
                                         email='rijas@example.com')
        other.groups.add(Group.objects.get(name='Owner'))

        self._request_code()
        self.client.post(self.reset_url, {
            'otp': self._latest_code(),
            'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        alerts = Notification.objects.filter(event='PASSWORD_RESET')
        self.assertEqual(list(alerts.values_list('recipient__username', flat=True)), ['Rijas'])
        self.assertEqual(alerts.first().severity, Notification.SEVERITY_CRITICAL)
        self.assertIn('Sahad', alerts.first().body)

    def test_the_reset_email_uses_the_business_name_not_the_project_name(self):
        """
        The sender reads "Formula D Workshop" and the subject used to say
        "WorkshopOS" — two names in one message, which is what a phishing filter
        and a cautious owner both flag. "WorkshopOS" appears nowhere in the UI.
        """
        self._request_code()

        self.assertNotIn('WorkshopOS', mail.outbox[0].subject)
        self.assertNotIn('WorkshopOS', mail.outbox[0].body)
        self.assertIn(settings.BUSINESS_NAME, mail.outbox[0].subject)

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

    def test_a_locked_out_owner_can_sign_in_straight_after_resetting(self):
        """
        The reset is a locked-out owner's ONLY self-service route back —
        `manage_unlock_account` refuses Owner accounts by design. The lockout is
        keyed to the account rather than the password, so it used to survive the
        reset: the owner was told "Password changed. Please sign in with your new
        password", did exactly that, and was answered "This account is locked
        after too many failed attempts."

        That reads as the reset having failed, and the obvious next move — ask
        for another code — burns a budget of three an hour until
        `RESET_CODE_LIMIT` alarms both owners over somebody correctly recovering
        their own account.
        """
        login_url = reverse('login')
        # By email, not username: an owner is nameable only by their address at
        # the sign-in form (`resolve_login_identifier`). Posting the username
        # here would resolve to nothing, record no failures, and leave the
        # precondition below asserting a lockout that was never created.
        email = self.owner.email
        for _ in range(AccountLockout.MAX_FAILURES):
            self.client.post(login_url, {'username': email, 'password': 'wrong'})
        self.assertTrue(
            AccountLockout.minutes_remaining(self.owner),
            "precondition: the account should be locked before the reset",
        )

        self._request_code()
        self.client.post(self.reset_url, {
            'otp': self._latest_code(),
            'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        self.assertEqual(AccountLockout.minutes_remaining(self.owner), 0)

        self.client.post(login_url, {'username': email, 'password': NEW_PASSWORD})
        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.owner.pk))

    def test_the_reset_does_not_wipe_the_network_wide_failure_count(self):
        """
        The IP backstop is deliberately left alone. Its message names the
        network, not the account, so it never contradicts the reset the way the
        account lock did; it clears itself on the same timer; and wiping it would
        erase the record of a spray against every other account behind the same
        connection.
        """
        for _ in range(3):
            self.client.post(reverse('login'),
                             {'username': self.owner.email, 'password': 'wrong'})
        before = FailedAttempt.objects.get().failures

        self._request_code()
        self.client.post(self.reset_url, {
            'otp': self._latest_code(),
            'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
        })

        self.assertEqual(FailedAttempt.objects.get().failures, before)

    def test_step_two_cannot_be_opened_directly(self):
        response = self.client.get(self.reset_url)

        self.assertRedirects(response, self.forgot_url, fetch_redirect_response=False)

    def test_signed_in_user_is_sent_home(self):
        self.client.login(username='Sahad', password=OLD_PASSWORD)

        response = self.client.get(self.forgot_url)

        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)


class AbusingTheResetFormTellsTheOwnersTests(TestCase):
    """
    The two ways a reset can be ATTEMPTED and fail, both of which were silent.

    The system announced every routine sign-in and said nothing about somebody
    working through an owner's account — and only owner accounts can reach this
    flow at all (`can_reset_password`). Since the form needs no login, there is
    no actor to exclude, so both owners hear it: the account holder is the one
    who can act, and the other owner is the corroboration.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        Group.objects.get_or_create(name='Owner')

        self.owner = User.objects.create_user(
            username='Sahad', password=OLD_PASSWORD, email='sahad@example.com',
        )
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.other = User.objects.create_user(
            username='Rijas', password=OLD_PASSWORD, email='rijas@example.com',
        )
        self.other.groups.add(Group.objects.get(name='Owner'))

        self.forgot_url = reverse('owner_forgot_password')
        self.reset_url = reverse('owner_reset_password')
        mail.outbox = []

    def _events(self, event):
        from workshop.models import Notification
        return Notification.objects.filter(event=event)

    def _burn_the_hourly_budget(self):
        """
        Spend the account's whole hourly allowance, from a browser that keeps no
        session log — a cleared cookie jar, a private window, another machine.

        That distinction is the point: `_own_request_throttle` runs on the same
        two numbers and is checked FIRST, so an owner fumbling in one browser is
        stopped by their own session log and never reaches the account-keyed
        limit. Getting there means the requests arrived from somewhere with no
        history behind them.
        """
        for _ in range(PasswordResetOTP.MAX_REQUESTS_PER_HOUR):
            PasswordResetOTP.objects.create(
                user=self.owner,
                code_hash='x' * 64,
                expires_at=timezone.now() + timedelta(minutes=10),
            )
        # Backdated past the 60-second cooldown, because that check runs FIRST
        # and would otherwise be the one that answers. Three codes issued
        # seconds apart is impossible anyway — the cooldown is what spaces them
        # out, so a real budget is always spent over minutes, and this is what
        # the fourth request an attacker makes actually meets.
        # `.update()`, since `created_at` is auto_now_add.
        PasswordResetOTP.objects.filter(user=self.owner).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )

    # -- the hourly code limit ----------------------------------------
    def test_burning_the_hourly_budget_alerts_both_owners(self):
        self._burn_the_hourly_budget()

        self.client.post(self.forgot_url, {'username': 'Sahad'})

        alerts = self._events('RESET_CODE_LIMIT')
        self.assertEqual(alerts.count(), 2, "both owners should hear it")
        self.assertEqual(
            set(alerts.values_list('recipient__username', flat=True)),
            {'Sahad', 'Rijas'},
        )
        self.assertEqual(alerts.first().severity, 'CRITICAL')
        self.assertIn('Sahad', alerts.first().body)

    def test_a_normal_request_alerts_nobody(self):
        self.client.post(self.forgot_url, {'username': 'Sahad'})

        self.assertEqual(self._events('RESET_CODE_LIMIT').count(), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_sixty_second_cooldown_is_not_worth_an_alert(self):
        """
        A double-tapped button is not an attack. Only the hourly limit fires;
        an alert for the cooldown would be noise inside a week, and a critical
        alert nobody reads protects nothing.
        """
        PasswordResetOTP.objects.create(
            user=self.owner, code_hash='x' * 64,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        self.client.post(self.forgot_url, {'username': 'Sahad'})

        self.assertEqual(self._events('RESET_CODE_LIMIT').count(), 0)

    def test_the_alert_cannot_be_used_as_a_doorbell(self):
        """
        This form needs no login. Without a limit, anyone who knows an owner's
        username could buzz both phones on demand until the alert stopped being
        read — which is the real attack, not the reset itself.
        """
        self._burn_the_hourly_budget()

        for _ in range(6):
            self.client.post(self.forgot_url, {'username': 'Sahad'})

        self.assertEqual(self._events('RESET_CODE_LIMIT').count(), 2)  # one per owner, once

    # -- the five-attempt code budget ---------------------------------
    def test_guessing_a_code_to_death_alerts_both_owners(self):
        self.client.post(self.forgot_url, {'username': 'Sahad'})

        for _ in range(PasswordResetOTP.MAX_ATTEMPTS):
            self.client.post(self.reset_url, {
                'otp': '000000', 'new_password': NEW_PASSWORD,
                'confirm_password': NEW_PASSWORD,
            })

        alerts = self._events('RESET_CODE_ATTEMPTS_SPENT')
        self.assertEqual(alerts.count(), 2)
        self.assertIn('Sahad', alerts.first().body)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(OLD_PASSWORD), "password must be untouched")

    def test_one_wrong_digit_alerts_nobody(self):
        self.client.post(self.forgot_url, {'username': 'Sahad'})

        self.client.post(self.reset_url, {
            'otp': '000000', 'new_password': NEW_PASSWORD,
            'confirm_password': NEW_PASSWORD,
        })

        self.assertEqual(self._events('RESET_CODE_ATTEMPTS_SPENT').count(), 0)

    def test_an_expired_code_is_not_an_attack(self):
        """
        `_dead_end()` is reached by an expired or already-spent code too — an
        owner coming back to yesterday's email. The alert is raised at the exact
        fifth-wrong-guess transition instead, not from inside `_dead_end`.
        """
        self.client.post(self.forgot_url, {'username': 'Sahad'})
        PasswordResetOTP.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

        self.client.post(self.reset_url, {
            'otp': '123456', 'new_password': NEW_PASSWORD,
            'confirm_password': NEW_PASSWORD,
        })

        self.assertEqual(self._events('RESET_CODE_ATTEMPTS_SPENT').count(), 0)

    # -- the rule none of this may break ------------------------------
    def test_raising_an_alert_changes_nothing_the_visitor_can_see(self):
        """
        Step 1 replies identically whether or not the account exists — that is
        the whole reason it has one generic message. A notification raised
        behind it must not become a new way to ask the question: the status, the
        redirect and the rendered page all have to match an invented username
        byte for byte.
        """
        self._burn_the_hourly_budget()
        real = self.client.post(self.forgot_url, {'username': 'Sahad'}, follow=True)

        self.client.logout()
        self.client.cookies.clear()
        fake = self.client.post(self.forgot_url, {'username': 'not-a-person'}, follow=True)

        def _comparable(response):
            """
            The page minus the one thing that legitimately differs: the CSRF
            token is per-session and says nothing about the account. Everything
            else — every message, every hidden field, every byte — must match.
            """
            import re
            return re.sub(rb'value="[A-Za-z0-9]{32,}"', b'value="CSRF"', response.content)

        self.assertEqual(real.status_code, fake.status_code)
        self.assertEqual(real.redirect_chain, fake.redirect_chain)
        self.assertEqual(_comparable(real), _comparable(fake))
        # ...and the alert really did fire, so this is not passing by accident.
        self.assertEqual(self._events('RESET_CODE_LIMIT').count(), 2)
