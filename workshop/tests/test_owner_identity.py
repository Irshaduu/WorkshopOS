"""
Owner identity lives in the database, not in .env.

Two things are guarded here:

1. `UserProfile.mobile_number` is unique. Login resolves an identifier
   (username / email / mobile) to exactly one account; two profiles sharing a
   number would make that resolution ambiguous, so the constraint is what keeps
   the resolver honest rather than a code convention that can drift.

2. `sync_owner_identity` migrates the historical OWNER_n_* .env entries into the
   DB. The bug it exists to fix: both owner accounts were superusers in **no**
   group, so `filter(groups__name='Owner')` returned nothing and any
   owner-addressed query reached no one. Membership is what makes owners
   findable; it must survive being re-run and must refuse to write a clashing
   mobile.
"""

from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User, Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from workshop.models import UserProfile, JobCard
from workshop.management.commands.sync_owner_identity import Command


class MobileNumberUniquenessTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user(username='owner_a', password='pw-for-tests-1')
        self.b = User.objects.create_user(username='owner_b', password='pw-for-tests-2')

    def test_duplicate_mobile_is_rejected(self):
        UserProfile.objects.create(user=self.a, mobile_number='+919567494933')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserProfile.objects.create(user=self.b, mobile_number='+919567494933')

    def test_multiple_profiles_may_have_no_mobile(self):
        """Office/Floor accounts carry no number — NULL must not collide."""
        UserProfile.objects.create(user=self.a, mobile_number=None)
        UserProfile.objects.create(user=self.b, mobile_number=None)
        self.assertEqual(UserProfile.objects.filter(mobile_number__isnull=True).count(), 2)


class SyncOwnerIdentityTests(TestCase):
    """The command is driven by .env; the tests drive it by patching that read."""

    def setUp(self):
        Group.objects.get_or_create(name='Owner')
        self.sahad = User.objects.create_user(
            username='Sahad', password='pw-for-tests-3', is_superuser=True,
        )

    def _run(self, owners, apply_changes=False):
        out = StringIO()
        with patch.object(Command, '_env_owners', return_value=owners):
            call_command('sync_owner_identity', yes=apply_changes, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        output = self._run([(1, 'Sahad', '+919567494933')])

        self.assertIn('DRY RUN', output)
        self.assertFalse(self.sahad.groups.filter(name='Owner').exists())
        self.assertFalse(UserProfile.objects.filter(user=self.sahad).exists())

    def test_apply_adds_group_and_creates_profile(self):
        self._run([(1, 'Sahad', '+919567494933')], apply_changes=True)

        self.assertTrue(self.sahad.groups.filter(name='Owner').exists())
        profile = UserProfile.objects.get(user=self.sahad)
        self.assertEqual(profile.mobile_number, '+919567494933')

    def test_owner_becomes_findable_by_group_query(self):
        """The actual regression: owners must be reachable without is_superuser."""
        self.assertEqual(User.objects.filter(groups__name='Owner').count(), 0)

        self._run([(1, 'Sahad', '+919567494933')], apply_changes=True)

        self.assertEqual(
            list(User.objects.filter(groups__name='Owner')), [self.sahad],
        )

    def test_rerunning_is_idempotent(self):
        owners = [(1, 'Sahad', '+919567494933')]
        self._run(owners, apply_changes=True)
        output = self._run(owners, apply_changes=True)

        self.assertIn('Nothing to change', output)
        self.assertEqual(UserProfile.objects.filter(user=self.sahad).count(), 1)

    def test_mobile_belonging_to_another_account_is_refused(self):
        other = User.objects.create_user(username='rijas', password='pw-for-tests-4')
        UserProfile.objects.create(user=other, mobile_number='+919567494933')

        output = self._run([(1, 'Sahad', '+919567494933')], apply_changes=True)

        self.assertIn('CLASHES', output)
        self.assertFalse(
            UserProfile.objects.filter(user=self.sahad, mobile_number='+919567494933').exists()
        )

    def test_missing_account_is_reported_not_crashed(self):
        output = self._run([(1, 'ghost', '+910000000000')], apply_changes=True)

        self.assertIn('account not found', output)

    def test_missing_email_is_flagged(self):
        output = self._run([(1, 'Sahad', '+919567494933')])

        self.assertIn('NOT SET', output)

    def test_shared_email_is_flagged(self):
        self.sahad.email = 'shared@example.com'
        self.sahad.save(update_fields=['email'])
        User.objects.create_user(
            username='Rijas', password='pw-for-tests-5', email='shared@example.com',
        )

        output = self._run([(1, 'Sahad', '+919567494933')])

        self.assertIn('SHARED', output)


class AdminAccessPolicyTests(SyncOwnerIdentityTests):
    """
    Owners hold every in-app privilege but no Django admin access: /admin/
    bypasses DeletionLog, the Financial Lock, and archive-don't-delete.
    `is_staff` is the flag that gates the admin site.
    """

    def test_is_staff_is_revoked(self):
        User.objects.filter(pk=self.sahad.pk).update(is_staff=True)

        self._run([(1, 'Sahad', '+919567494933')], apply_changes=True)

        self.assertFalse(User.objects.get(pk=self.sahad.pk).is_staff)

    def test_revoking_admin_does_not_touch_superuser(self):
        """In-app authority must survive: every decorator checks is_superuser."""
        User.objects.filter(pk=self.sahad.pk).update(is_staff=True)

        self._run([(1, 'Sahad', '+919567494933')], apply_changes=True)

        refreshed = User.objects.get(pk=self.sahad.pk)
        self.assertTrue(refreshed.is_superuser)
        self.assertFalse(refreshed.is_staff)

    def test_non_owner_admin_account_is_left_alone(self):
        """A deliberate break-glass superuser isn't an OWNER_n entry — don't touch it."""
        breakglass = User.objects.create_user(
            username='devadmin', password='pw-for-tests-6',
            is_superuser=True, is_staff=True,
        )

        self._run([(1, 'Sahad', '+919567494933')], apply_changes=True)

        self.assertTrue(User.objects.get(pk=breakglass.pk).is_staff)


class SetOwnerEmailTests(TestCase):
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.sahad = User.objects.create_user(
            username='Sahad', password='pw-for-tests-7', email='old@example.com',
        )
        self.sahad.groups.add(self.owner_group)

    def _run(self, username, email, apply_changes=False):
        out = StringIO()
        call_command('set_owner_email', username, email, yes=apply_changes, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        output = self._run('Sahad', 'new@example.com')

        self.assertIn('DRY RUN', output)
        self.assertEqual(User.objects.get(pk=self.sahad.pk).email, 'old@example.com')

    def test_apply_sets_the_address(self):
        self._run('Sahad', 'new@example.com', apply_changes=True)

        self.assertEqual(User.objects.get(pk=self.sahad.pk).email, 'new@example.com')

    def test_address_is_stored_lowercased(self):
        """Login-by-email matches on a plain lookup, so case must not vary."""
        self._run('Sahad', 'MiXeD@Example.COM', apply_changes=True)

        self.assertEqual(User.objects.get(pk=self.sahad.pk).email, 'mixed@example.com')

    def test_duplicate_address_is_refused(self):
        User.objects.create_user(
            username='Rijas', password='pw-for-tests-8', email='taken@example.com',
        )

        with self.assertRaises(CommandError):
            self._run('Sahad', 'taken@example.com', apply_changes=True)

        self.assertEqual(User.objects.get(pk=self.sahad.pk).email, 'old@example.com')

    def test_duplicate_check_ignores_case(self):
        User.objects.create_user(
            username='Rijas', password='pw-for-tests-9', email='taken@example.com',
        )

        with self.assertRaises(CommandError):
            self._run('Sahad', 'TAKEN@example.com', apply_changes=True)

    def test_invalid_address_is_refused(self):
        with self.assertRaises(CommandError):
            self._run('Sahad', 'not-an-email', apply_changes=True)

    def test_unknown_account_is_refused(self):
        with self.assertRaises(CommandError):
            self._run('ghost', 'new@example.com', apply_changes=True)


class InvoiceLinkVisibilityTests(TestCase):
    """
    The Invoice link used to be gated on `user.is_staff` — Django's
    admin-access flag, not a workshop role. That hid the link from Office, whose
    job is billing, while `invoice_view` itself is @office_required. The gate now
    mirrors the decorator.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.office = User.objects.create_user(username='officestaff', password='pw-for-tests-10')
        self.office.groups.add(Group.objects.get(name='Office'))

        self.floor = User.objects.create_user(username='floorstaff', password='pw-for-tests-11')
        self.floor.groups.add(Group.objects.get(name='Floor'))

        self.job = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Toyota',
            model_name='Corolla',
            registration_number='KL01A1234',
            customer_name='John Doe',
        )
        self.invoice_url = reverse('invoice_view', args=[self.job.pk])

    def test_office_sees_the_invoice_link(self):
        self.client.login(username='officestaff', password='pw-for-tests-10')

        response = self.client.get(reverse('home'))

        self.assertContains(response, self.invoice_url)

    def test_floor_does_not_see_the_invoice_link(self):
        self.client.login(username='floorstaff', password='pw-for-tests-11')

        response = self.client.get(reverse('home'))

        self.assertNotContains(response, self.invoice_url)
