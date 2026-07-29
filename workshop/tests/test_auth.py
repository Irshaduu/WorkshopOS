from django.test import TestCase, Client, RequestFactory
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from workshop.models import FailedAttempt, UserProfile
from workshop.auth_views import IP_FAILURE_LIMIT, IP_LOCKOUT_MINUTES


class AuthFlowTests(TestCase):
    """
    Sign-in gates: the IP backstop and identifier resolution.

    This file used to patch `workshop.auth_views.config` to blank the Twilio SID
    and force the SMS sender into terminal mock mode. Both the sender and that
    `config` import were deleted on 2026-07-29 — nothing here reaches the network
    any more, so there is nothing left to stub.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        # Groups
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')

        self.owner = User.objects.create_user(username='Sahad', password='ownerpassword')
        self.owner.groups.add(self.owner_group)
        
        self.staff_office = User.objects.create_user(username='office_test', password='staffpassword')
        self.staff_office.groups.add(self.office_group)
        
        self.client = Client()
        self.test_ip = '192.168.1.50'

    def test_ip_lockout_gate(self):
        """
        The IP gate is a *backstop* now, not the primary control.

        It used to fire at 5 failures, which was the wrong unit: the whole
        workshop leaves through one connection, so one person fumbling on the
        Floor tablet locked the owners out of their own phones. Per-account
        lockout took over that job (`AccountLockout`, covered in
        `test_login.py`); this threshold was raised so ordinary shared use never
        reaches it, and only a spray across many accounts does.
        """
        url = reverse('login')

        # Already signed in — straight home, no form.
        self.client.login(username='office_test', password='staffpassword')
        self.assertRedirects(self.client.get(url), reverse('home'))
        self.client.logout()

        FailedAttempt.objects.create(ip_address=self.test_ip, failures=IP_FAILURE_LIMIT)
        response = self.client.get(url, REMOTE_ADDR=self.test_ip)
        self.assertContains(response, "this network")

        # Window elapsed — the gate reopens and the count resets.
        FailedAttempt.objects.filter(ip_address=self.test_ip).update(
            last_attempt=timezone.now() - timedelta(minutes=IP_LOCKOUT_MINUTES + 1)
        )
        response = self.client.get(url, REMOTE_ADDR=self.test_ip)
        self.assertNotContains(response, "this network")

        response = self.client.post(
            url, {'username': 'office_test', 'password': 'wrong'}, follow=True,
        )
        self.assertContains(response, "Invalid credentials")

    # The old step 4 of this test asserted that a *valid* owner password was
    # rejected on the staff face with a fake "Invalid credentials". That was
    # removed on 2026-07-28: the owner door sat one link below the form, so the
    # lie protected nothing while guaranteeing a baffling support call the first
    # time an owner typed correct details on the wrong page. Either face now
    # accepts any role — see `test_login.LoginFacesTests`.

    # The SMS/Telegram broadcast test was removed on 2026-07-29 along with the
    # channel itself. A successful sign-in still alerts the owners — it just does
    # it through the in-app feed now, covered by
    # `test_notifications.EventHookTests.test_successful_login_notifies_the_other_owner`.

    def test_sign_in_by_mobile_reads_the_database(self):
        """
        Mobile resolution moved from .env (`OWNER_n_MOBILE`) to
        `UserProfile.mobile_number` — identity lives in the database now, so a
        third owner needs no redeploy.
        """
        UserProfile.objects.create(user=self.owner, mobile_number='+919567494933')

        self.client.post(
            reverse('admin_login'),
            {'username': '9567494933', 'password': 'ownerpassword'},
        )

        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.owner.pk))

    # Password-reset coverage moved to `test_password_reset.py` on 2026-07-28.
    #
    # The old `test_password_reset_flow_edge_cases` was deleted rather than
    # updated: it drove a mechanism that no longer exists (the OTP hash and its
    # expiry stored in `request.session`, delivery over SMS/Telegram) and it
    # asserted a behaviour that was removed *on purpose* — a distinct
    # "Please wait ..." reply on the cooldown path. That reply only appeared for
    # accounts that actually exist, which made the forgot-password form an
    # account-existence oracle. The cooldown is now folded into the one generic
    # response, and the limits are counted per account in the database.
    #
    # Every case it covered has a successor, with the new semantics:
    #   non-existent user  -> ResetFlowTests.test_unknown_account_is_indistinguishable
    #   cooldown           -> ResetFlowTests.test_throttled_request_sends_nothing_and_says_so
    #   short password     -> ResetFlowTests.test_weak_password_does_not_spend_the_code
    #   mismatch           -> ResetFlowTests.test_mismatched_confirmation_does_not_spend_the_code
    #   success + redirect -> ResetFlowTests.test_correct_code_sets_the_new_password
    #
    # The cooldown case changed again on 2026-07-29: the throttle *is* now
    # reported, but only the per-browser one, which is not an existence oracle.
    # See test_the_visible_throttle_is_not_an_existence_oracle.


class LogoutConfirmationTests(TestCase):
    """
    Signing out must take two deliberate actions.

    The drawer's Logout button used to be a bare submit: one tap on a phone or
    the Floor tablet ended the session, and a mechanic mid-job card then needed
    an owner to type the password back in.
    """

    def setUp(self):
        Group.objects.get_or_create(name='Floor')
        self.user = User.objects.create_user(username='floorhand', password='pw-long-enough')
        self.user.groups.add(Group.objects.get(name='Floor'))
        self.client.login(username='floorhand', password='pw-long-enough')

    def test_the_drawer_button_opens_a_confirmation_instead_of_signing_out(self):
        body = self.client.get(reverse('home')).content.decode()

        self.assertIn('logoutConfirmModal', body)
        self.assertIn('data-bs-target="#logoutConfirmModal"', body)

    def test_there_is_exactly_one_way_to_sign_out_and_it_is_a_post(self):
        """
        A second, unguarded logout control anywhere would quietly reinstate the
        one-tap sign-out this modal exists to prevent.
        """
        body = self.client.get(reverse('home')).content.decode()

        self.assertEqual(body.count(f'action="{reverse("logout")}"'), 1)
        self.assertIn('method="post"', body)

    def test_logout_still_works_when_confirmed(self):
        self.client.post(reverse('logout'))

        self.assertNotIn('_auth_user_id', self.client.session)
