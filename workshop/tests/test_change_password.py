"""
Change Password — the handover path, and the reason email is not load-bearing.

An owner is given a temporary password verbally, signs in, and replaces it here.
No email, no OTP, no mail provider. That keeps go-live independent of SMTP being
configured, and leaves the emailed reset code as what it should be: a backstop
for a genuinely forgotten password.

Owner-only on purpose. Office and Floor never change their own credentials —
owners manage those from Control Hub (/manage/?section=accounts).
"""

from django.contrib.auth.models import User, Group
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse

from workshop.models import UserSession

CURRENT = 'temp-handover-pw-1'
STRONG = 'Str0ngPass!2026'


class ChangePasswordTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='Sahad', password=CURRENT)
        self.owner.groups.add(Group.objects.get(name='Owner'))

        self.url = reverse('change_password')

    def _login_owner(self):
        self.assertTrue(self.client.login(username='Sahad', password=CURRENT))

    def _post(self, old=CURRENT, new1=STRONG, new2=STRONG):
        return self.client.post(self.url, {
            'old_password': old,
            'new_password1': new1,
            'new_password2': new2,
        })

    # -- happy path ---------------------------------------------------
    def test_owner_can_change_password(self):
        self._login_owner()

        response = self._post()

        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(STRONG))
        self.assertFalse(self.owner.check_password(CURRENT))

    def test_owner_stays_signed_in_afterwards(self):
        """Without update_session_auth_hash the owner logs themselves out."""
        self._login_owner()

        self._post()
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)

    # -- rejections ---------------------------------------------------
    def test_wrong_current_password_is_rejected(self):
        self._login_owner()

        response = self._post(old='not-the-current-password')

        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(CURRENT))

    def test_mismatched_confirmation_is_rejected(self):
        self._login_owner()

        response = self._post(new1=STRONG, new2='Different!2026')

        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(CURRENT))

    def test_weak_password_is_rejected_by_django_validators(self):
        """Same validators as everywhere else — short, numeric, and common all fail."""
        self._login_owner()

        for weak in ('short1', '12345678', 'password'):
            with self.subTest(weak=weak):
                self._post(new1=weak, new2=weak)
                self.owner.refresh_from_db()
                self.assertTrue(self.owner.check_password(CURRENT))

    # -- other devices ------------------------------------------------
    def test_other_devices_are_signed_out(self):
        """
        Django invalidates other sessions on a password change, but their rows
        would linger and keep Control Hub advertising dead devices.
        """
        other = SessionStore()
        other['_auth_user_id'] = str(self.owner.pk)
        other.create()
        UserSession.objects.create(user=self.owner, session_key=other.session_key)

        self._login_owner()
        self._post()

        self.assertFalse(Session.objects.filter(session_key=other.session_key).exists())
        self.assertFalse(UserSession.objects.filter(session_key=other.session_key).exists())

    # -- access control -----------------------------------------------
    def test_office_cannot_reach_it(self):
        office = User.objects.create_user(username='officestaff', password=CURRENT)
        office.groups.add(Group.objects.get(name='Office'))
        self.client.login(username='officestaff', password=CURRENT)

        response = self.client.post(self.url, {
            'old_password': CURRENT, 'new_password1': STRONG, 'new_password2': STRONG,
        })

        self.assertEqual(response.status_code, 403)
        office.refresh_from_db()
        self.assertTrue(office.check_password(CURRENT))

    def test_floor_cannot_reach_it(self):
        floor = User.objects.create_user(username='floorstaff', password=CURRENT)
        floor.groups.add(Group.objects.get(name='Floor'))
        self.client.login(username='floorstaff', password=CURRENT)

        response = self.client.post(self.url, {
            'old_password': CURRENT, 'new_password1': STRONG, 'new_password2': STRONG,
        })

        self.assertEqual(response.status_code, 403)
        floor.refresh_from_db()
        self.assertTrue(floor.check_password(CURRENT))

    def test_anonymous_is_redirected(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    # -- entry point --------------------------------------------------
    def test_drawer_shows_the_link_to_owners_only(self):
        self._login_owner()
        owner_view = self.client.get(reverse('home'))
        self.assertContains(owner_view, self.url)

        self.client.logout()
        office = User.objects.create_user(username='officestaff2', password=CURRENT)
        office.groups.add(Group.objects.get(name='Office'))
        self.client.login(username='officestaff2', password=CURRENT)

        office_view = self.client.get(reverse('home'))
        self.assertNotContains(office_view, self.url)
