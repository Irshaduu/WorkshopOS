"""
A STAFF sign-in reaches the owners' phones; an owner's stays in the bell.

Every routine sign-in used to be announced at INFO — which means the feed only,
never a push — so the system buzzed for a discount and said nothing on a phone
about somebody signing in as Office at eleven at night. Office and Floor
accounts live on shared shop-floor devices and are the ones the owners cannot
see being used, so that is the sign-in worth interrupting for.

Two things are asserted beyond "it fires": the OWNER path is unchanged (or the
critical list grows by two owners announcing their own working days to each
other, and a critical list nobody trusts is a critical list nobody reads), and
the actor is still excluded.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from workshop.models import FailedAttempt, Notification
from workshop.notifications import EVENTS

PASSWORD = 'staff-login-test-pw-1'


class StaffSignInReachesThePhoneTests(TestCase):
    def setUp(self):
        # The IP backstop counts by REMOTE_ADDR across the whole test database;
        # without clearing it a neighbouring test's failures can lock this one
        # out of the login form entirely.
        FailedAttempt.objects.all().delete()

        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner_a = User.objects.create_user(
            username='Sahad', password=PASSWORD, email='sahad@example.com')
        self.owner_a.groups.add(Group.objects.get(name='Owner'))
        self.owner_b = User.objects.create_user(
            username='Rijas', password=PASSWORD, email='rijas@example.com')
        self.owner_b.groups.add(Group.objects.get(name='Owner'))

        self.office = User.objects.create_user(username='officestaff', password=PASSWORD)
        self.office.groups.add(Group.objects.get(name='Office'))

        self.floor = User.objects.create_user(username='floorstaff', password=PASSWORD)
        self.floor.groups.add(Group.objects.get(name='Floor'))

    def sign_in(self, identifier):
        return self.client.post(
            reverse('login'), {'username': identifier, 'password': PASSWORD})

    # ---- the tier ----------------------------------------------------

    def test_the_staff_event_is_critical_so_it_pushes(self):
        """
        CRITICAL is the whole point: `notify()` only calls `queue_push` for
        CRITICAL, so an INFO event here would land in the bell and buzz nothing.
        """
        self.assertEqual(EVENTS['STAFF_LOGIN'][1], Notification.SEVERITY_CRITICAL)

    def test_an_owner_sign_in_pushes_too(self):
        """
        Changed 2026-08-29 on the owner's decision: it was INFO, on the
        reasoning that an owner signing in is routine. An owner account is the
        highest-privilege thing in the system and a sign-in on one with a stolen
        password reached no phone at all.

        Safe because of the volume, not despite it: `SESSION_COOKIE_AGE` is 40
        days, so this fires on a genuinely new session rather than every shift.
        """
        self.assertEqual(EVENTS['LOGIN'][1], Notification.SEVERITY_CRITICAL)

    def test_getting_in_always_pushes(self):
        """All three ways a session or an account changes hands reach a phone."""
        for key in ('LOGIN', 'STAFF_LOGIN', 'ACCOUNT_LOCKED'):
            with self.subTest(event=key):
                self.assertEqual(EVENTS[key].severity, Notification.SEVERITY_CRITICAL)

    # ---- routing -----------------------------------------------------

    def test_office_signing_in_raises_the_staff_event(self):
        self.sign_in('officestaff')
        events = set(Notification.objects.values_list('event', flat=True))
        self.assertEqual(events, {'STAFF_LOGIN'})

    def test_floor_signing_in_raises_the_staff_event(self):
        self.sign_in('floorstaff')
        events = set(Notification.objects.values_list('event', flat=True))
        self.assertEqual(events, {'STAFF_LOGIN'})

    def test_an_owner_signing_in_still_raises_the_plain_one(self):
        self.sign_in('sahad@example.com')
        events = set(Notification.objects.values_list('event', flat=True))
        self.assertEqual(events, {'LOGIN'})

    # ---- audience ----------------------------------------------------

    def test_both_owners_hear_about_a_staff_sign_in(self):
        self.sign_in('officestaff')
        recipients = set(
            Notification.objects.filter(event='STAFF_LOGIN')
            .values_list('recipient__username', flat=True)
        )
        self.assertEqual(recipients, {'Sahad', 'Rijas'})

    def test_staff_receive_nothing_themselves(self):
        """
        Floor gets no notifications at all — a bell a mechanic cannot act on is
        what trains everyone to stop looking at theirs.
        """
        self.sign_in('officestaff')
        self.assertFalse(
            Notification.objects.filter(recipient__in=[self.office, self.floor]).exists())

    def test_an_owner_is_not_told_about_their_own_sign_in(self):
        self.sign_in('sahad@example.com')
        self.assertFalse(Notification.objects.filter(recipient=self.owner_a).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.owner_b).exists())

    # ---- what it says ------------------------------------------------

    def test_the_alert_names_the_role(self):
        """
        It arrives on a lock screen as a statement plus a line of context.
        "amal signed in" does not say whether that account can see money, so the
        role LEADS the context line — it is the fact that decides that.

        Asserted across both strings rather than on `body` alone: which of the
        two carries the role is a presentation choice, that it is on the alert
        at all is the rule.
        """
        self.sign_in('officestaff')
        note = Notification.objects.filter(event='STAFF_LOGIN').first()
        self.assertEqual(note.body, 'officestaff signed in')
        self.assertTrue(note.detail.startswith('Office · '), note.detail)

    def test_an_account_named_after_its_role_still_reports_that_role(self):
        """
        Both staff logins in this workshop are literally named after their role
        — "Office" and "Floor". The role used to be suppressed for exactly those
        accounts, because the body was one string and "Floor (Floor) signed in"
        read as a bug. The role now sits in its own field, so the suppression
        stopped preventing a duplication and started reporting the only two
        staff accounts that exist as having no role at all.
        """
        floor = User.objects.create_user(username='Floor', password=PASSWORD)
        floor.groups.add(Group.objects.get(name='Floor'))

        self.sign_in('Floor')

        note = Notification.objects.filter(event='STAFF_LOGIN').first()
        self.assertEqual(note.body, 'Floor signed in')
        self.assertTrue(note.detail.startswith('Floor · '), note.detail)
        self.assertNotIn('No role', note.detail)

    def test_a_failed_sign_in_announces_nothing(self):
        self.client.post(reverse('login'),
                         {'username': 'officestaff', 'password': 'wrong'})
        self.assertFalse(Notification.objects.filter(event='STAFF_LOGIN').exists())
