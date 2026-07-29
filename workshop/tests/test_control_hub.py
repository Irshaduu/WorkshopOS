"""
Control Hub — Owner-only, and the unlock that keeps the shop moving.

Every view here was `@office_required` while the drawer only ever offered the
hub to owners. Office users therefore could not *see* it but could reach `/manage/`
by URL and create logins, reset passwords, or read the session list. Owners hold
every login in this workshop, so the whole hub is owner-only now and the
decorator finally agrees with the navigation.

Owner accounts are not managed from this panel at all — reset, delete and unlock
each refuse them. Owner credentials are changed by the owner themselves
(`/change-password/`) or recovered by emailed code.
"""

from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse

from workshop.models import AccountLockout, Mechanic

PASSWORD = 'control-hub-test-pw-1'


class ControlHubAccessTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))

        self.office = User.objects.create_user(username='officestaff', password=PASSWORD)
        self.office.groups.add(Group.objects.get(name='Office'))

        self.floor = User.objects.create_user(username='floorstaff', password=PASSWORD)
        self.floor.groups.add(Group.objects.get(name='Floor'))

    def _hub_urls(self):
        return [
            reverse('manage_dashboard'),
            reverse('manage_dashboard') + '?section=accounts',
            reverse('manage_dashboard') + '?section=staff',
            reverse('manage_dashboard') + '?section=security',
        ]

    def test_owner_reaches_every_section(self):
        self.client.login(username='Sahad', password=PASSWORD)

        for url in self._hub_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_office_is_refused(self):
        """The gap this closes: Office could not see the hub but could open it."""
        self.client.login(username='officestaff', password=PASSWORD)

        for url in self._hub_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_floor_is_refused(self):
        self.client.login(username='floorstaff', password=PASSWORD)

        self.assertEqual(self.client.get(reverse('manage_dashboard')).status_code, 403)

    def test_anonymous_is_sent_to_sign_in(self):
        response = self.client.get(reverse('manage_dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin-login/', response.url)

    def test_office_cannot_create_an_account(self):
        self.client.login(username='officestaff', password=PASSWORD)

        response = self.client.post(reverse('manage_create_user'), {
            'username': 'sneaky', 'password': 'whatever-8-chars', 'role': 'Office',
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username='sneaky').exists())

    def test_office_cannot_reset_another_password(self):
        self.client.login(username='officestaff', password=PASSWORD)

        response = self.client.post(
            reverse('manage_reset_password', args=[self.floor.pk]),
            {'new_password': 'brand-new-password'},
        )

        self.assertEqual(response.status_code, 403)
        self.floor.refresh_from_db()
        self.assertTrue(self.floor.check_password(PASSWORD))

    def test_office_cannot_touch_the_staff_roster(self):
        self.client.login(username='officestaff', password=PASSWORD)

        response = self.client.post(reverse('manage_create_mechanic'), {
            'name': 'Ghost', 'role': Mechanic.ROLE_MECHANIC,
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Mechanic.objects.filter(name='Ghost').exists())


class AccountUnlockTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))

        self.floor = User.objects.create_user(username='floorstaff', password=PASSWORD)
        self.floor.groups.add(Group.objects.get(name='Floor'))

        self.accounts_url = reverse('manage_dashboard') + '?section=accounts'
        self.client.login(username='Sahad', password=PASSWORD)

    def _lock(self, user):
        for _ in range(AccountLockout.MAX_FAILURES):
            AccountLockout.record_failure(user)

    def test_owner_can_unlock_a_locked_account(self):
        self._lock(self.floor)
        self.assertGreater(AccountLockout.minutes_remaining(self.floor), 0)

        self.client.post(reverse('manage_unlock_account', args=[self.floor.pk]))

        self.assertEqual(AccountLockout.minutes_remaining(self.floor), 0)

    def test_unlocked_account_can_sign_in_again(self):
        """The point of the button — the shop shouldn't wait out 15 minutes."""
        self._lock(self.floor)
        self.client.post(reverse('manage_unlock_account', args=[self.floor.pk]))
        self.client.logout()

        self.client.post(
            reverse('login'), {'username': 'floorstaff', 'password': PASSWORD},
        )

        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.floor.pk))

    def test_get_does_not_unlock(self):
        """State changes belong behind POST."""
        self._lock(self.floor)

        self.client.get(reverse('manage_unlock_account', args=[self.floor.pk]))

        self.assertGreater(AccountLockout.minutes_remaining(self.floor), 0)

    def test_owner_accounts_are_refused(self):
        other_owner = User.objects.create_user(username='Rijas', password=PASSWORD)
        other_owner.groups.add(Group.objects.get(name='Owner'))
        self._lock(other_owner)

        response = self.client.post(
            reverse('manage_unlock_account', args=[other_owner.pk]), follow=True,
        )

        self.assertContains(response, "Cannot modify Owner accounts")
        self.assertGreater(AccountLockout.minutes_remaining(other_owner), 0)

    def test_non_owner_cannot_unlock(self):
        self.client.logout()
        office = User.objects.create_user(username='officestaff', password=PASSWORD)
        office.groups.add(Group.objects.get(name='Office'))
        self.client.login(username='officestaff', password=PASSWORD)
        self._lock(self.floor)

        response = self.client.post(reverse('manage_unlock_account', args=[self.floor.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertGreater(AccountLockout.minutes_remaining(self.floor), 0)

    # -- what the owner sees ------------------------------------------
    #
    # Asserted on the badge *icon* and the unlock URL, not the `lock-badge`
    # class: that class name also appears in the page's <style> block, so it is
    # present whether or not anyone is locked and proves nothing either way.
    def test_locked_account_shows_a_badge_and_an_unlock_button(self):
        self._lock(self.floor)

        response = self.client.get(self.accounts_url)

        self.assertContains(response, 'bi-lock-fill')
        self.assertContains(response, reverse('manage_unlock_account', args=[self.floor.pk]))

    def test_unlocked_account_shows_neither(self):
        response = self.client.get(self.accounts_url)

        self.assertNotContains(response, 'bi-lock-fill')
        self.assertNotContains(response, reverse('manage_unlock_account', args=[self.floor.pk]))

    def test_visiting_the_page_clears_an_elapsed_lock(self):
        """minutes_remaining() resets a spent window, same as the ghost-session sweep."""
        from datetime import timedelta
        from django.utils import timezone

        self._lock(self.floor)
        AccountLockout.objects.filter(user=self.floor).update(
            last_attempt=timezone.now() - timedelta(minutes=AccountLockout.LOCKOUT_MINUTES + 1)
        )

        self.client.get(self.accounts_url)

        self.assertEqual(AccountLockout.objects.get(user=self.floor).failures, 0)
