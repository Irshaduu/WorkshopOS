"""
Login — one door, and the lockouts that protect it.

This arrived in three stages. Two full views drifted apart (one rejected a valid
owner password with a fake "Invalid credentials", a lie that bought nothing since
the owner door was one link away); they were collapsed into one view behind two
presentational *faces*, `/login/` and `/admin-login/`; and on 2026-08-12 the
faces went too. Since either accepted any role they gated nothing, while
announcing to anyone who typed the address that privileged accounts exist and
where their door is. `/admin-login/` survives only as a redirect, for bookmarks.

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
    """
    One door, for every role.

    There were two — a blue "Staff Sign In" at `/login/` and a red "Admin Sign
    In" at `/admin-login/` — on one view, with one identifier resolver and one
    set of lockouts. Since either accepted any role, the split gated nothing; all
    it did was tell anyone who typed the address that privileged accounts exist
    and where their door is.

    Several assertions here are the *inverse* of what they said before
    2026-08-12. They were not wrong then — they pinned a design that has since
    been replaced deliberately. See `LOGIN_TEMPLATE` in `auth_views.py`.
    """

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

        self.url = reverse('login')
        self.legacy_admin_url = reverse('admin_login')

    def _post(self, url, identifier, password=PASSWORD, **extra):
        return self.client.post(url, {'username': identifier, 'password': password, **extra})

    def test_there_is_one_door(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_the_old_admin_url_redirects_to_it(self):
        """
        Kept as a redirect, not deleted: the owners have it bookmarked, and the
        name is still reversed from `forgot_password.html` and old tests.
        """
        self.assertRedirects(self.client.get(self.legacy_admin_url), self.url)

    def test_the_redirect_carries_next_across(self):
        """
        A decorator appends `?next=` before bouncing an anonymous visitor. If the
        hop dropped it, an old bookmark would sign you in and then strand you on
        the dashboard instead of the page you asked for.
        """
        response = self.client.get(self.legacy_admin_url + '?next=/cashbook/')

        self.assertRedirects(response, self.url + '?next=/cashbook/')

    def test_the_door_names_no_roles(self):
        """
        The staff face's placeholder read "Office/Floor username" and the other
        face's heading read "Admin Sign In" — between them they published the
        whole tier structure to anyone who opened the page.

        Scoped to `.auth-shell`, never the whole document: `base.html` ships one
        global `<style>` block on every page, and the drawer rules inside it
        mention the role names in class names and comments. A page-wide search
        finds them on any render and the test would fail for a reason that has
        nothing to do with what a visitor can read — the same trap the invoice
        tests avoid by asserting against `_sheet()` rather than the full page.
        """
        html = self.client.get(self.url).content.decode()
        shell = html[html.index('<div class="auth-shell">'):]

        for leak in ('Admin', 'Office', 'Floor', 'Owner', 'Staff'):
            self.assertNotIn(leak, shell, f"the sign-in page names the {leak!r} role")

    def test_signed_out_pages_carry_no_app_chrome(self):
        """
        The auth screens own the whole viewport. A nav bar offering "Floor" and
        "Login" above a login form, plus an "install this app" prompt aimed at
        someone who has not proved they can get in yet, made the front door look
        like a fragment of the app.
        """
        for url in (self.url, reverse('owner_forgot_password')):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertNotIn('<nav ', html)
                self.assertNotIn('pwaInstallBanner', html)

    def test_signed_in_pages_still_have_the_nav(self):
        """The suppression is scoped to auth pages, not switched on globally."""
        self.client.login(username='Sahad', password=PASSWORD)

        html = self.client.get(reverse('home')).content.decode()

        self.assertIn('<nav ', html)

    def test_submit_button_is_guarded_against_double_posts(self):
        """
        The staff form had no guard, so the button could be pressed repeatedly
        while a sign-in was in flight — each press another POST, each wrong one
        spending part of the account's five-attempt lockout budget.
        """
        html = self.client.get(self.url).content.decode()

        self.assertIn('js-auth-form', html)
        self.assertIn('js-auth-submit', html)
        self.assertIn("dataset.submitting", html)

    def test_the_door_offers_no_other_door(self):
        """
        Inverted on 2026-08-12. Each face used to link to the other, which is
        exactly the signposting the merge removes — a cross-link is how a visitor
        learns the second door is there at all.
        """
        html = self.client.get(self.url).content.decode()

        self.assertNotIn(self.legacy_admin_url, html)

    def test_forgot_password_is_on_the_one_door(self):
        """
        Inverted on 2026-08-12. It used to appear only on the owner face, on the
        reasoning that Office and Floor carry no email — but the nav bar links to
        `/login/`, so an owner arriving the ordinary way had no recovery route on
        screen at all. It leaks nothing: step 1 of the reset answers identically
        whether or not an account exists.
        """
        self.assertContains(self.client.get(self.url), reverse('owner_forgot_password'))

    def test_every_role_signs_in_at_the_one_door(self):
        """
        This was two tests, one per face. The older of them existed because the
        staff view used to reject a valid owner password with a fake "Invalid
        credentials" — a lie that bought nothing, since the owner door was one
        link away. Whoever signs in lands on the dashboard their role renders.
        """
        # The owner is named by email, the floor account by username — see
        # `OwnersSignInByEmailOnlyTests` for why the two differ.
        for identifier, account in ((self.owner.email, self.owner),
                                    (self.floor.username, self.floor)):
            with self.subTest(identifier=identifier):
                self.client.logout()

                response = self._post(self.url, identifier)

                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    self.client.session.get('_auth_user_id'), str(account.pk),
                )

    def test_already_signed_in_is_sent_home(self):
        self.client.login(username='Sahad', password=PASSWORD)

        self.assertRedirects(
            self.client.get(self.url), reverse('home'), fetch_redirect_response=False,
        )

    # -- identifier resolution ----------------------------------------
    def test_staff_sign_in_by_username(self):
        """
        Office and Floor are unchanged, and carry no email by design
        (`can_reset_password`). Only owner accounts were narrowed.
        """
        self._post(self.url, 'floorstaff')

        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.floor.pk))

    def test_unknown_identifier_is_refused(self):
        response = self._post(self.url, 'nobody-at-all')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    # -- ?next= --------------------------------------------------------
    # Both sign in by EMAIL: `self.owner` is in the Owner group and carries an
    # address, so its username no longer resolves at this form
    # (`resolve_login_identifier`). Posting 'Sahad' here failed with 200 != 302,
    # which reads like a broken redirect rather than a refused identifier.
    def test_next_returns_the_user_to_where_they_were_headed(self):
        response = self._post(self.url, self.owner.email, next=reverse('cashbook'))

        self.assertRedirects(response, reverse('cashbook'), fetch_redirect_response=False)

    def test_next_to_another_host_is_ignored(self):
        """An unchecked next turns login into an open redirect."""
        response = self._post(self.url, self.owner.email, next='https://evil.example.com/harvest')

        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)


class OwnersSignInByEmailOnlyTests(TestCase):
    """
    An owner account can be named only by its email address at the sign-in form.

    `resolve_user_by_identifier` also accepts a username and the last ten digits
    of a mobile number, which meant the workshop's own published phone number was
    a valid owner identifier — it is on the website, on business cards and on
    Google Maps. Being nameable costs twice at this form: it is where guessing
    happens, and it is where five wrong tries lock the account, so anyone who
    could name an owner could lock that owner out on demand.

    Office and Floor are untouched, and the reset flow still accepts anything.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(
            username='Sahad', password=PASSWORD, email='sahad@example.com',
        )
        self.owner.groups.add(Group.objects.get(name='Owner'))
        UserProfile.objects.create(user=self.owner, mobile_number='+919567494933')

        self.url = reverse('login')

    def _post(self, identifier, password=PASSWORD):
        return self.client.post(self.url, {'username': identifier, 'password': password})

    def _signed_in_as(self):
        return self.client.session.get('_auth_user_id')

    def test_the_owners_email_signs_them_in(self):
        self._post('sahad@example.com')

        self.assertEqual(self._signed_in_as(), str(self.owner.pk))

    def test_the_email_match_ignores_case(self):
        """`resolve_user_by_identifier` matches `email__iexact`; so must this."""
        self._post('SAHAD@Example.COM')

        self.assertEqual(self._signed_in_as(), str(self.owner.pk))

    def test_the_owners_username_is_refused(self):
        self._post('Sahad')

        self.assertIsNone(self._signed_in_as())

    def test_the_owners_mobile_is_refused(self):
        for typed in ('9567494933', '+919567494933', '+91 95674 94933'):
            with self.subTest(typed=typed):
                self.client.logout()

                self._post(typed)

                self.assertIsNone(self._signed_in_as())

    def test_a_refused_identifier_cannot_authenticate_by_the_back_door(self):
        """
        The regression guard for the bug this change nearly shipped with.

        `login_view` used to fall back to `authenticate(username=identifier)`
        whenever nothing resolved — and Django's ModelBackend looks accounts up
        **by username**. So refusing the owner's username in
        `resolve_login_identifier` would have changed nothing at all: the raw
        text went to the backend anyway and signed them straight in. The view
        now passes an empty username when nothing resolved.

        Note this asserts against the CORRECT password. With a wrong one it
        passes whether or not the hole is open, which is exactly how a test like
        this gets written wrong and proves nothing.
        """
        self._post('Sahad', password=PASSWORD)

        self.assertIsNone(
            self._signed_in_as(),
            "the owner's username signed them in despite being refused",
        )

    def test_an_owner_with_no_email_keeps_their_username(self):
        """
        Deliberately exempt. With no address there is no email to sign in with
        *and* no `can_reset_password`, so applying the rule would be a permanent
        lockout with no self-service way back. Only an owner can clear an owner's
        email, so this is not a lever an attacker can pull.
        """
        rijas = User.objects.create_user(username='Rijas', password=PASSWORD)
        rijas.groups.add(Group.objects.get(name='Owner'))

        self._post('Rijas')

        self.assertEqual(self._signed_in_as(), str(rijas.pk))

    def test_an_office_account_still_signs_in_by_username(self):
        office = User.objects.create_user(username='officestaff', password=PASSWORD)
        office.groups.add(Group.objects.get(name='Office'))

        self._post('officestaff')

        self.assertEqual(self._signed_in_as(), str(office.pk))

    def test_a_superuser_outside_the_owner_group_is_still_narrowed(self):
        """
        `is_owner_account` checks the group **or** the superuser flag, the same
        pair every RBAC decorator uses. A stray superuser created by
        `createsuperuser` has full authority in this app and must not be
        reachable by a guessable username.
        """
        root = User.objects.create_superuser(
            username='root', password=PASSWORD, email='root@example.com',
        )

        self._post('root')
        self.assertIsNone(self._signed_in_as())

        self._post('root@example.com')
        self.assertEqual(self._signed_in_as(), str(root.pk))

    def test_a_refused_identifier_never_spends_the_lockout_budget(self):
        """
        It resolves to nothing, so there is no account to count against — which
        is the point. Otherwise the narrowing would have *created* the very
        attack it exists to stop: five posts of a guessable username, and the
        owner is locked out of the email login too.
        """
        for _ in range(AccountLockout.MAX_FAILURES + 2):
            self._post('Sahad', password=WRONG)

        self.assertEqual(AccountLockout.minutes_remaining(self.owner), 0)
        self._post('sahad@example.com')
        self.assertEqual(self._signed_in_as(), str(self.owner.pk))

    def test_the_reset_flow_still_accepts_a_username(self):
        """
        Not narrowed, on purpose. Step 1 answers identically whether or not an
        account exists, has its own two throttles, and delivers only to the
        address already on file — so a username there hands an attacker nothing,
        while refusing it would strand an owner who remembers their username but
        not which address is on the account.
        """
        from django.core import mail
        mail.outbox = []

        self.client.post(reverse('owner_forgot_password'), {'username': 'Sahad'})

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sahad@example.com'])


class AccountLockoutTests(TestCase):
    # These sign in by USERNAME despite `Sahad` being an owner, and that is
    # legitimate rather than an oversight: the fixture carries no email, and an
    # owner with no address is exempt from the email-only rule
    # (`resolve_login_identifier`) because applying it would make the account
    # unreachable and unrecoverable at once. Add `email=` to this fixture and
    # every test below starts failing — the identifier would stop resolving, so
    # no failures would be counted and the lockout would never fire.
    def setUp(self):
        FailedAttempt.objects.all().delete()
        Group.objects.get_or_create(name='Owner')

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.other = User.objects.create_user(username='Rijas', password=PASSWORD)

        self.url = reverse('login')

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
    # Emailless owner, signing in by username — see the note on
    # `AccountLockoutTests` above for why that is deliberate here.
    def setUp(self):
        FailedAttempt.objects.all().delete()
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.url = reverse('login')

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
        """
        Straight to `/login/`, in one hop. Owner and Office pages used to bounce
        to `/admin-login/`, which told anyone probing an owner URL that a
        separate admin door existed — and would now cost a second redirect to
        reach the same page anyway.
        """
        response = self.client.get(self.owner_only_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)
        self.assertNotIn('/admin-login/', response.url)

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
