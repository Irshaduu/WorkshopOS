"""
`setup_groups` creates the roles this app's RBAC actually reads.

It used to create `Workers` and `Admins` — two groups nothing here has ever
looked at — while GO_LIVE_RUNBOOK.md's checklist claimed it created
Owner / Office / Floor and Control Hub told anyone with a missing role to run
it. Both remedies pointed at a command that reported success and fixed nothing.

That is only reproducible on an EMPTY database, which is exactly what go-live
day is and what no development database ever is. Hence these tests.
"""
from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from workshop.decorators import owner_accounts  # noqa: F401  (import guard)


class SetupGroupsCreatesTheRolesRbacReadsTests(TestCase):

    def test_it_creates_owner_office_and_floor(self):
        Group.objects.all().delete()

        call_command('setup_groups', stdout=StringIO())

        self.assertEqual(
            sorted(Group.objects.values_list('name', flat=True)),
            ['Floor', 'Office', 'Owner'],
        )

    def test_it_creates_no_group_this_app_does_not_read(self):
        """
        The specific regression. `Workers` and `Admins` are read by no view, no
        decorator and no template, so creating them looked like success and left
        every RBAC role missing.
        """
        Group.objects.all().delete()

        call_command('setup_groups', stdout=StringIO())

        names = set(Group.objects.values_list('name', flat=True))
        self.assertNotIn('Workers', names)
        self.assertNotIn('Admins', names)

    def test_running_it_twice_changes_nothing(self):
        Group.objects.all().delete()

        call_command('setup_groups', stdout=StringIO())
        first = set(Group.objects.values_list('name', flat=True))
        call_command('setup_groups', stdout=StringIO())

        self.assertEqual(first, set(Group.objects.values_list('name', flat=True)))
        self.assertEqual(Group.objects.count(), 3)

    def test_it_does_not_send_anyone_to_the_django_admin(self):
        """
        Owner accounts are `is_staff=False` by design, so /admin/ is unenterable
        by anybody. The old command's closing advice was to go there and assign
        groups by hand.
        """
        out = StringIO()
        call_command('setup_groups', stdout=out)

        self.assertNotIn('/admin/', out.getvalue())


class AMissingRoleIsReportedWithAdviceThatWorksTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(
            'sahad', password='pw-for-tests-1234', is_superuser=True)
        self.client.force_login(self.owner)

    def test_creating_a_login_without_the_role_refuses_and_creates_nothing(self):
        """
        The guard itself, which was already right: the group is resolved BEFORE
        the account exists, so a missing role cannot leave a login with no group.
        """
        Group.objects.filter(name='Office').delete()

        self.client.post(reverse('manage_create_user'), {
            'username': 'office', 'password': 'pw-for-tests-1234',
            'role': 'Office',
        })

        self.assertFalse(User.objects.filter(username='office').exists())

    def test_the_advice_names_a_command_that_actually_fixes_it(self):
        """
        `setup_groups` is named on screen, so it has to be the command that
        works. It is now — this pins the two together, since the message was
        correct-sounding and useless for as long as the command was wrong.
        """
        Group.objects.all().delete()

        response = self.client.post(reverse('manage_create_user'), {
            'username': 'office', 'password': 'pw-for-tests-1234',
            'role': 'Office',
        }, follow=True)

        message = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('setup_groups', message)

        # Follow the advice; it must leave the role in place.
        call_command('setup_groups', stdout=StringIO())
        self.assertTrue(Group.objects.filter(name='Office').exists())

        # And the same POST now succeeds.
        self.client.post(reverse('manage_create_user'), {
            'username': 'office', 'password': 'pw-for-tests-1234',
            'role': 'Office',
        })
        self.assertTrue(User.objects.filter(username='office').exists())
