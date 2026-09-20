"""
AUD-0008 — "what role is this user?" is ONE rule, asked ONCE per request.

Two things are pinned here and they fail in completely different ways:

* **One rule.** `owner_required` and the `has_group` template filter are the two
  halves of RBAC — the decorator decides whether a page opens, the filter
  decides what it draws. They were two separate implementations of one rule, so
  a page could enforce one thing and show another. The scan at the foot of this
  file is what stops a third growing back.

* **Asked once.** Both implementations asked the database on **every call**, and
  the comment on the filter said the opposite ("cached on the user instance
  after the first call"), which is why it survived so long. Measured before the
  fix: the job card form issued **32 role queries out of 48**, because that one
  template calls `has_group` 19 times.

Nothing here asserts a page's total query count — that belongs to AUD-0096 and
would go stale on the next query added. What is asserted is the INVARIANT: the
answer costs the same whether it is wanted once or twenty times.
"""

import ast
import io

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, Group, User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workshop.decorators import (
    is_floor_office_owner, is_office_or_owner, is_owner, role_names,
)
from workshop.templatetags.custom_filters import has_group


def _role_queries(ctx):
    """The queries that asked the database what somebody's role is."""
    return [q['sql'] for q in ctx.captured_queries if 'auth_user_groups' in q['sql']]


class OneRoleQuestionCostsOneQueryTests(TestCase):
    """However many times a request asks, the database is asked once."""

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.office = User.objects.create_user('office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.boss = User.objects.create_superuser(
            'sahad', password='pw', email='s@example.com')

    def test_asking_twenty_times_costs_what_asking_once_costs(self):
        """The invariant, stated as a comparison rather than a magic number."""
        once = User.objects.get(pk=self.office.pk)
        with CaptureQueriesContext(connection) as ctx:
            is_owner(once)
        first = len(_role_queries(ctx))

        many = User.objects.get(pk=self.office.pk)
        with CaptureQueriesContext(connection) as ctx:
            for _ in range(20):
                is_owner(many)
                is_office_or_owner(many)
                is_floor_office_owner(many)
                has_group(many, 'Office')
        self.assertEqual(
            len(_role_queries(ctx)), first,
            'eighty role checks cost more than one - the per-instance cache in '
            '`role_names` has stopped working, and every template that draws a '
            'role is paying per call again')
        self.assertEqual(first, 1, 'a role should be looked up exactly once')

    def test_a_superuser_is_never_looked_up_at_all(self):
        """Both owner accounts in this workshop are superusers.

        Every caller tests `is_superuser` before it reaches `role_names`, so the
        role question costs them nothing. That is not a micro-optimisation - it
        is the commonest case on the pages that ask most.
        """
        boss = User.objects.get(pk=self.boss.pk)
        with CaptureQueriesContext(connection) as ctx:
            self.assertTrue(is_owner(boss))
            self.assertTrue(is_office_or_owner(boss))
            self.assertTrue(is_floor_office_owner(boss))
            self.assertTrue(has_group(boss, 'Owner'))
            self.assertTrue(has_group(boss, 'Floor'))
        self.assertEqual(_role_queries(ctx), [],
                         'a superuser triggered a group lookup')

    def test_an_anonymous_visitor_costs_nothing_and_is_nobody(self):
        anon = AnonymousUser()
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(role_names(anon), frozenset())
            self.assertFalse(is_owner(anon))
            self.assertFalse(has_group(anon, 'Owner'))
        self.assertEqual(_role_queries(ctx), [])

    def test_a_whole_page_asks_once_however_often_it_draws_a_role(self):
        """`jobcard_form.html` calls `has_group` 19 times on 19 separate lines.

        Rendering it used to issue a query for each, plus one per decorator and
        one from the context processor - 32 in all, measured, two thirds of the
        page's entire database traffic.
        """
        self.client.force_login(self.office)
        with CaptureQueriesContext(connection) as ctx:
            page = self.client.get(reverse('jobcard_create'))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(
            len(_role_queries(ctx)), 1,
            'the job card form asked the database for a role more than once')


class TheCacheIsDroppedWhenMembershipMovesTests(TestCase):
    """
    An instance cache is only safe if it notices the thing it caches changing.

    `WorkshopConfig.ready()` wires `_forget_roles` to `User.groups`, so adding
    or removing a group throws the cached answer away. Without it, code that
    promotes somebody and then re-asks on the SAME object reads the old answer -
    and `test_has_group_filter` in test_extras.py does exactly that, which is
    what turned this from a theoretical worry into a required receiver.
    """

    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user('staff', password='pw')

    def test_adding_a_group_is_seen_immediately(self):
        self.assertFalse(is_owner(self.user))          # caches "no roles"
        self.user.groups.add(self.owner_group)
        self.assertTrue(is_owner(self.user),
                        'the cached answer survived a group being added')
        self.assertTrue(has_group(self.user, 'Owner'))

    def test_removing_a_group_is_seen_immediately(self):
        self.user.groups.add(self.owner_group)
        self.assertTrue(is_owner(self.user))           # caches "Owner"
        self.user.groups.remove(self.owner_group)
        self.assertFalse(is_owner(self.user),
                         'the cached answer survived a group being removed')

    def test_clearing_every_group_is_seen_immediately(self):
        self.user.groups.add(self.owner_group)
        self.assertTrue(is_owner(self.user))
        self.user.groups.clear()
        self.assertFalse(is_owner(self.user),
                         'the cached answer survived groups being cleared')

    def test_a_second_instance_of_the_same_person_reads_the_database(self):
        """The cache is per OBJECT, which is what makes it safe across requests.

        Every request builds `request.user` afresh out of the session, so a role
        changed between two requests is picked up with nothing to invalidate.
        """
        self.user.groups.add(self.owner_group)
        self.assertTrue(is_owner(User.objects.get(pk=self.user.pk)))


class TheDecoratorAndTheTemplateFilterAreOneRuleTests(TestCase):
    """What a page ENFORCES and what it DRAWS must be the same answer."""

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

    def test_every_role_gets_the_same_answer_from_both_halves(self):
        for role in ('Owner', 'Office', 'Floor'):
            user = User.objects.create_user('u_' + role.lower(), password='pw')
            user.groups.add(Group.objects.get(name=role))
            fresh = User.objects.get(pk=user.pk)
            self.assertEqual(
                is_owner(fresh), has_group(fresh, 'Owner'),
                '%s: owner_required and has_group disagree' % role)
            self.assertEqual(
                is_office_or_owner(fresh),
                has_group(fresh, 'Office') or has_group(fresh, 'Owner'),
                '%s: office_required and has_group disagree' % role)
            self.assertEqual(
                is_floor_office_owner(fresh),
                any(has_group(fresh, g) for g in ('Floor', 'Office', 'Owner')),
                '%s: staff_required and has_group disagree' % role)

    def test_a_user_in_no_group_is_refused_by_both(self):
        nobody = User.objects.create_user('nobody', password='pw')
        self.assertFalse(is_owner(nobody))
        self.assertFalse(is_floor_office_owner(nobody))
        self.assertFalse(has_group(nobody, 'Owner'))


class NobodyReimplementsTheRoleRuleTests(TestCase):
    """
    A scan, because a fifth copy of this rule is invisible to every other test.

    There were NINE hand-rolled copies of "is this user an Owner?" before this -
    in the decorators, the template filter, the context processor, the salary
    views, twice in auth, and three times in Control Hub, where it is the guard
    that stops one owner's account being reset or deleted from the panel. Every
    one was correct on the day it was written, which is exactly the problem: the
    rule that decides who sees money should not be typed out nine times.

    ⚠ MANAGEMENT COMMANDS ARE ALLOWED THEIR OWN. They run once, outside any
    request, against an account they have just loaded - there is nothing to
    amortise, and `sync_owner_identity` is go-live tooling that should not be
    coupled to a request-scoped cache. That is a decision, not an oversight.
    """

    # The one place the rule is allowed to be written, plus the command-line
    # tools above. Read as paths relative to BASE_DIR.
    ALLOWED = {
        'workshop/decorators.py',
        'workshop/management/commands/sync_owner_identity.py',
        'workshop/management/commands/set_owner_email.py',
    }

    def _app_files(self):
        base = settings.BASE_DIR
        for app in ('workshop', 'inventory'):
            for path in (base / app).rglob('*.py'):
                rel = path.relative_to(base).as_posix()
                if '/tests' in rel or rel.rsplit('/', 1)[-1].startswith('test'):
                    continue
                if '/migrations/' in rel:
                    continue
                yield rel, io.open(path, encoding='utf-8').read()

    def test_the_scan_actually_reads_the_codebase(self):
        """A floor, so a scan that finds nothing cannot pass for being clean.

        The failure this guards against is the one that bit last time: a scan
        walking a RELATIVE path finds no files from any other directory, reports
        no offenders and goes green for the wrong reason.
        """
        files = list(self._app_files())
        self.assertGreater(len(files), 40,
                           'the scan read almost nothing - check BASE_DIR')
        self.assertIn('workshop/decorators.py', dict(files))

    def test_only_decorators_asks_the_database_what_role_somebody_is(self):
        offenders = []
        for rel, src in self._app_files():
            if rel in self.ALLOWED:
                continue
            for node in ast.walk(ast.parse(src)):
                # `<something>.groups.filter(...)` / `.groups.all()` - the two
                # shapes the nine copies were written in.
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if not isinstance(fn, ast.Attribute):
                    continue
                if fn.attr not in ('filter', 'all', 'exists', 'values_list'):
                    continue
                inner = fn.value
                if isinstance(inner, ast.Attribute) and inner.attr == 'groups':
                    offenders.append('%s:%d' % (rel, node.lineno))
        self.assertEqual(
            sorted(set(offenders)), [],
            'the role rule has been written out again instead of calling '
            '`decorators.role_names` / `is_owner` - one of these will drift '
            'from the other, and it is the rule that decides who sees money')
