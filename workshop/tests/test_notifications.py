"""
The in-app notification feed.

Two things are being guarded. First the plumbing: rows fan out per recipient,
the actor is spared their own event, and a notification survives the deletion of
whatever it was about — most of them announce a deletion, so a ForeignKey would
have cascaded away exactly the record that mattered.

Second the audience. An owner is `is_superuser` **or** in the `Owner` group —
matching `has_group`/`owner_required` elsewhere — because group-membership-only
has already gone wrong once: a reseeded database routinely leaves both owner
accounts superuser with no group until `sync_owner_identity --yes` is re-run,
and in that window the old query returned nobody, so every notification
silently reached no one while appearing to work.
"""

import re
from decimal import Decimal
from datetime import date, timedelta

from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.models import (
    AccountLockout, DeletionLog, JobCard, Mechanic, Notification,
    SalaryAdvance, SpareShop,
)
from workshop.notifications import EVENTS, notify

PASSWORD = 'notification-test-pw-1'


class NotifyTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner_a = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner_a.groups.add(Group.objects.get(name='Owner'))
        self.owner_b = User.objects.create_user(username='Rijas', password=PASSWORD)
        self.owner_b.groups.add(Group.objects.get(name='Owner'))

        self.office = User.objects.create_user(username='officestaff', password=PASSWORD)
        self.office.groups.add(Group.objects.get(name='Office'))

    def test_one_row_per_owner(self):
        notify('LOGIN', 'something happened')

        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(
            set(Notification.objects.values_list('recipient__username', flat=True)),
            {'Sahad', 'Rijas'},
        )

    def test_actor_is_not_told_about_their_own_action(self):
        notify('LOGIN', 'Sahad signed in', actor=self.owner_a)

        recipients = list(Notification.objects.values_list('recipient__username', flat=True))
        self.assertEqual(recipients, ['Rijas'])

    def test_non_owners_receive_nothing(self):
        notify('LOGIN', 'something happened')

        self.assertFalse(Notification.objects.filter(recipient=self.office).exists())

    def test_a_lone_superuser_is_still_notified_without_group_membership(self):
        """
        The regression that started all this: a reseeded database leaves an
        owner superuser with no group until `sync_owner_identity --yes` catches
        up. The feed must not go dark for that entire window, so `is_superuser`
        alone is enough — the same either-or `has_group`/`owner_required` use.
        """
        User.objects.create_user(username='lonesuper', password=PASSWORD, is_superuser=True)

        notify('LOGIN', 'something happened')

        self.assertTrue(Notification.objects.filter(recipient__username='lonesuper').exists())

    def test_inactive_owner_is_skipped(self):
        self.owner_b.is_active = False
        self.owner_b.save(update_fields=['is_active'])

        notify('LOGIN', 'something happened')

        self.assertEqual(Notification.objects.count(), 1)

    def test_severity_and_title_come_from_the_registry(self):
        notify('HIGH_DISCOUNT', 'big one')

        note = Notification.objects.first()
        # Attribute access, not `title, severity, _ = ...`: `Event` gained a
        # fourth column (the glyph the feed row draws) and the point of the
        # test is that the registry is the source, not where a field sits.
        spec = EVENTS['HIGH_DISCOUNT']
        title, severity = spec.title, spec.severity
        self.assertEqual(note.title, title)
        self.assertEqual(note.severity, severity)
        self.assertEqual(note.severity, Notification.SEVERITY_CRITICAL)

    def test_unknown_event_is_ignored_not_raised(self):
        """A typo'd event key must never take down the business action."""
        written = notify('NO_SUCH_EVENT', 'whatever')

        self.assertEqual(written, 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_overlong_body_is_truncated_not_rejected(self):
        notify('LOGIN', 'x' * 900)

        self.assertEqual(len(Notification.objects.first().body), 255)

    def test_notification_outlives_its_subject(self):
        """
        A FK here would cascade the record away with the thing it announced —
        and most of these announce a deletion.
        """
        shop = SpareShop.objects.create(name='Doomed Motors')
        notify('ACCOUNT_ARCHIVED', 'Doomed Motors archived',
               object_type='SPARE_SHOP', object_id=shop.pk)

        shop.delete()

        note = Notification.objects.first()
        self.assertIsNotNone(note)
        self.assertIn('Doomed Motors', note.body)
        self.assertEqual(note.object_type, 'SPARE_SHOP')


class NotificationModelTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))

    def _make(self, **kwargs):
        defaults = dict(recipient=self.owner, event='LOGIN', title='t', body='b')
        defaults.update(kwargs)
        return Notification.objects.create(**defaults)

    def test_unread_count_ignores_read_rows(self):
        self._make()
        self._make()
        self._make(read_at=timezone.now())

        self.assertEqual(Notification.unread_count(self.owner), 2)

    def test_mark_all_read_clears_the_count(self):
        self._make()
        self._make()

        cleared = Notification.mark_all_read(self.owner)

        self.assertEqual(cleared, 2)
        self.assertEqual(Notification.unread_count(self.owner), 0)

    def test_purge_drops_old_read_notifications(self):
        old_read = self._make(read_at=timezone.now())
        Notification.objects.filter(pk=old_read.pk).update(
            created_at=timezone.now() - timedelta(days=Notification.RETENTION_DAYS + 1)
        )

        Notification.purge_old()

        self.assertFalse(Notification.objects.filter(pk=old_read.pk).exists())

    def test_purge_never_drops_unread_however_old(self):
        """Someone who hasn't looked in months should still find what they missed."""
        old_unread = self._make()
        Notification.objects.filter(pk=old_unread.pk).update(
            created_at=timezone.now() - timedelta(days=Notification.RETENTION_DAYS * 5)
        )

        Notification.purge_old()

        self.assertTrue(Notification.objects.filter(pk=old_unread.pk).exists())


class NotificationFeedTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.other_owner = User.objects.create_user(username='Rijas', password=PASSWORD)
        self.other_owner.groups.add(Group.objects.get(name='Owner'))

        self.office = User.objects.create_user(username='officestaff', password=PASSWORD)
        self.office.groups.add(Group.objects.get(name='Office'))

        self.url = reverse('notification_list')
        self.client.login(username='Sahad', password=PASSWORD)

    def test_feed_renders(self):
        notify('LOGIN', 'someone signed in')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'someone signed in')

    def test_owner_sees_only_their_own_copy(self):
        note_for_other = Notification.objects.create(
            recipient=self.other_owner, event='LOGIN', title='t', body='private to Rijas',
        )

        response = self.client.get(self.url)

        self.assertNotContains(response, 'private to Rijas')
        self.assertEqual(
            self.client.get(reverse('notification_open', args=[note_for_other.pk])).status_code,
            404,
        )

    def test_office_cannot_open_the_feed(self):
        self.client.logout()
        self.client.login(username='officestaff', password=PASSWORD)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_opening_marks_read_and_follows_the_link(self):
        note = Notification.objects.create(
            recipient=self.owner, event='LOGIN', title='t', body='b',
            url=reverse('manage_dashboard'),
        )

        response = self.client.get(reverse('notification_open', args=[note.pk]))

        note.refresh_from_db()
        self.assertIsNotNone(note.read_at)
        self.assertRedirects(response, reverse('manage_dashboard'), fetch_redirect_response=False)

    def test_opening_one_without_a_link_returns_to_the_feed(self):
        """Normal, not a bug — the subject of a deletion notice no longer exists."""
        note = Notification.objects.create(
            recipient=self.owner, event='RECORD_DELETED', title='t', body='b', url='',
        )

        response = self.client.get(reverse('notification_open', args=[note.pk]))

        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_mark_all_read_action(self):
        notify('LOGIN', 'one')
        notify('HIGH_DISCOUNT', 'two')

        self.client.post(reverse('notification_mark_all_read'))

        self.assertEqual(Notification.unread_count(self.owner), 0)

    def test_mark_all_read_ignores_get(self):
        notify('LOGIN', 'one')

        self.client.get(reverse('notification_mark_all_read'))

        self.assertEqual(Notification.unread_count(self.owner), 1)

    # -- the bell ------------------------------------------------------
    #
    # Asserted on `bi-bell-fill`, which the template only emits when something
    # is unread. The `nav-badge` class name would prove nothing either way: it
    # also appears in base.html's <style> block, so it is in every response
    # regardless. (Same trap as `lock-badge` in test_control_hub.)
    def test_badge_shows_the_unread_count(self):
        notify('LOGIN', 'one')
        notify('LOGIN', 'two')

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'bi-bell-fill')
        self.assertEqual(response.context['unread_notifications'], 2)

    def test_no_badge_when_nothing_unread(self):
        response = self.client.get(reverse('home'))

        self.assertNotContains(response, 'bi-bell-fill')
        self.assertEqual(response.context['unread_notifications'], 0)

    # -- floating panel -------------------------------------------------
    def test_panel_returns_only_the_recent_slice(self):
        """
        The panel is a glance, not a workspace. It must stay instant with
        thousands of rows behind it, so it never renders the whole feed.
        """
        from workshop.views.notifications import PANEL_SIZE

        for i in range(PANEL_SIZE + 15):
            Notification.objects.create(
                recipient=self.owner, event='LOGIN', title='t', body=f'note {i}',
            )

        response = self.client.get(reverse('notification_panel'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count('nf-row'), PANEL_SIZE)

    def test_panel_shows_only_this_owner(self):
        Notification.objects.create(
            recipient=self.other_owner, event='LOGIN', title='t', body='private to Rijas',
        )

        response = self.client.get(reverse('notification_panel'))

        self.assertNotContains(response, 'private to Rijas')

    def test_office_cannot_open_the_panel(self):
        self.client.logout()
        self.client.login(username='officestaff', password=PASSWORD)

        self.assertEqual(self.client.get(reverse('notification_panel')).status_code, 403)

    def test_mark_read_without_navigating(self):
        note = Notification.objects.create(
            recipient=self.owner, event='LOGIN', title='t', body='b',
        )

        response = self.client.post(reverse('notification_mark_read', args=[note.pk]))

        note.refresh_from_db()
        self.assertIsNotNone(note.read_at)
        self.assertEqual(response.json()['unread'], 0)

    def test_mark_read_cannot_touch_another_owners_copy(self):
        theirs = Notification.objects.create(
            recipient=self.other_owner, event='LOGIN', title='t', body='b',
        )

        self.client.post(reverse('notification_mark_read', args=[theirs.pk]))

        theirs.refresh_from_db()
        self.assertIsNone(theirs.read_at)

    def test_mark_all_read_answers_json_for_the_panel(self):
        notify('LOGIN', 'one')

        response = self.client.post(
            reverse('notification_mark_all_read'), HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.json()['unread'], 0)
        self.assertEqual(Notification.unread_count(self.owner), 0)

    def test_badge_caps_at_99_plus(self):
        """A four-digit count breaks the pill and tells an owner nothing extra."""
        Notification.objects.bulk_create([
            Notification(recipient=self.owner, event='LOGIN', title='t', body='b')
            for _ in range(105)
        ])

        response = self.client.get(reverse('home'))

        self.assertContains(response, '99+')
        self.assertNotContains(response, '>105<')

    def test_office_is_not_charged_for_the_count(self):
        """The bell is owner-only, so Office must not pay for a query it never sees."""
        self.client.logout()
        self.client.login(username='officestaff', password=PASSWORD)

        response = self.client.get(reverse('home'))

        self.assertNotIn('unread_notifications', response.context)


class EventHookTests(TestCase):
    """Each hook fires from the real code path, not from notify() directly."""

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))

        # A second owner is required, not decorative: the actor is excluded from
        # their own events, so with one owner the actions Sahad performs below
        # would correctly notify nobody and every assertion here would be
        # measuring the exclusion rule rather than the hook.
        self.other_owner = User.objects.create_user(username='Rijas', password=PASSWORD)
        self.other_owner.groups.add(Group.objects.get(name='Owner'))

        self.office = User.objects.create_user(username='officestaff', password=PASSWORD)
        self.office.groups.add(Group.objects.get(name='Office'))

    def _events(self, recipient=None):
        """
        Events as seen by **one** owner.

        Rows fan out per recipient, so counting the whole table counts owners as
        well as events — "did this fire once?" would read as 2. Defaults to
        Rijas, who never acts in these tests and therefore receives everything;
        Sahad is the actor for several and is correctly spared his own events.
        """
        return list(
            Notification.objects
            .filter(recipient=recipient or self.other_owner)
            .values_list('event', flat=True)
        )

    def test_deletion_log_raises_one_notification_for_any_entity(self):
        """
        The single choke point: every permanent delete already funnels through
        DeletionLog.record, so one hook covers all nine entity types.
        """
        shop = SpareShop.objects.create(name='Gone Motors')
        DeletionLog.record(
            DeletionLog.ENTITY_SHOP_PAYMENT, shop, user=self.office,
            amount=Decimal('1500.00'), label='Payment to Gone Motors',
        )

        note = Notification.objects.get(recipient=self.owner)
        self.assertEqual(note.event, 'RECORD_DELETED')
        self.assertEqual(note.severity, Notification.SEVERITY_CRITICAL)
        self.assertIn('Gone Motors', note.body)
        # `₹1,500`, not `1500.00`. The body used to append the raw Decimal in
        # brackets after a label that, at seven of the eighteen call sites,
        # already carried the same figure formatted the app's way — so one
        # amount was printed twice in two spellings. See
        # `TheDeletedRecordBodySaysEachFactOnceTests`.
        self.assertIn('₹1,500', note.body)

    def test_high_discount_notifies_but_a_normal_one_does_not(self):
        """
        The threshold is a flat ₹3,500, not a proportion (changed 2026-08-10).

        These two cards are what the change actually means. The first gives away
        40% of a ₹1,000 bill — the old rule shouted about it, and it is ₹400,
        which is a rounding-down at the counter. The second gives away 12% of
        ₹60,000 — the old rule was silent, and it is ₹7,000.
        """
        self.client.login(username='officestaff', password=PASSWORD)

        proportionally_steep = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01A0001', total_bill_amount=Decimal('1000.00'),
        )
        self.client.post(reverse('update_bill_status', args=[proportionally_steep.pk]),
                         {'received_amount': '600', 'payment_method': 'CASH'})
        self.assertNotIn('HIGH_DISCOUNT', self._events())

        genuinely_large = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01A0002', total_bill_amount=Decimal('60000.00'),
        )
        self.client.post(reverse('update_bill_status', args=[genuinely_large.pk]),
                         {'received_amount': '53000', 'payment_method': 'CASH'})

        self.assertIn('HIGH_DISCOUNT', self._events())
        # Office raised it, so both owners get a copy.
        notes = Notification.objects.filter(event='HIGH_DISCOUNT')
        self.assertEqual(notes.count(), 2)
        self.assertIn('KL01A0002', notes.first().body)
        # The percentage is still on the row — no longer the threshold, but
        # still the context an owner reads ₹7,000 against. It sits in `detail`
        # now, under the statement, because the loud line is for the car and
        # the amount.
        self.assertIn('12%', notes.first().detail)

    def test_the_discount_threshold_is_a_boundary_not_a_range(self):
        """
        Exactly ₹3,500 is not a large discount; ₹3,500.01 is. Pinned because
        `>` and `>=` are one keystroke apart and the difference is a phone
        buzzing on a settlement somebody makes every week.
        """
        self.client.login(username='officestaff', password=PASSWORD)

        for index, (received, expected) in enumerate(
            [(Decimal('6500.00'), False), (Decimal('6499.99'), True)]
        ):
            job = JobCard.objects.create(
                admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
                registration_number=f'KL01B{index:04d}', total_bill_amount=Decimal('10000.00'),
            )
            Notification.objects.all().delete()

            self.client.post(reverse('update_bill_status', args=[job.pk]),
                             {'received_amount': str(received), 'payment_method': 'CASH'})

            job.refresh_from_db()
            self.assertEqual(job.discount_amount, Decimal('10000.00') - received)
            self.assertEqual(
                Notification.objects.filter(event='HIGH_DISCOUNT').exists(), expected,
                f"₹{job.discount_amount} off should{'' if expected else ' not'} alert",
            )

    def test_lockout_notifies_once_on_crossing_only(self):
        """
        Otherwise an attacker could fill the owners' feed at will and bury
        anything real underneath it.
        """
        url = reverse('login')
        for _ in range(AccountLockout.MAX_FAILURES + 4):
            self.client.post(url, {'username': 'officestaff', 'password': 'wrong'})

        # One per owner, and no more however long the attempts continue.
        self.assertEqual(self._events().count('ACCOUNT_LOCKED'), 1)
        self.assertEqual(self._events(self.owner).count('ACCOUNT_LOCKED'), 1)

    def test_lockout_of_staff_links_where_it_can_be_unlocked(self):
        for _ in range(AccountLockout.MAX_FAILURES):
            self.client.post(reverse('login'),
                             {'username': 'officestaff', 'password': 'wrong'})

        note = Notification.objects.filter(event='ACCOUNT_LOCKED').first()
        self.assertIn('section=accounts', note.url)

    def test_lockout_of_an_owner_does_not_link_to_a_page_without_it(self):
        """
        Control Hub → Accounts lists Office and Floor only, and unlock refuses
        owner accounts by design. A locked owner sent there opened a page that
        did not contain the account, did not mention the lockout, and offered
        nothing to press — reported as "this notification leads to the wrong
        page". Anything but the Accounts section is an improvement; Security is
        the section that answers what an owner lockout actually raises.
        """
        for _ in range(AccountLockout.MAX_FAILURES):
            self.client.post(reverse('login'),
                             {'username': 'Sahad', 'password': 'wrong'})

        note = Notification.objects.filter(event='ACCOUNT_LOCKED').first()
        self.assertNotIn('section=accounts', note.url)
        self.assertIn('section=security', note.url)
        self.assertIn('cannot be unlocked', note.detail)

    def test_successful_login_notifies_the_other_owner(self):
        self.client.post(reverse('login'), {'username': 'Sahad', 'password': PASSWORD})

        logins = Notification.objects.filter(event='LOGIN')
        self.assertEqual(list(logins.values_list('recipient__username', flat=True)), ['Rijas'])

    def test_creating_a_login_notifies(self):
        self.client.login(username='Sahad', password=PASSWORD)

        self.client.post(reverse('manage_create_user'), {
            'username': 'newfloor', 'password': 'long-enough-pw', 'role': 'Floor',
        })

        self.assertIn('USER_CREATED', self._events())

    def test_deactivating_staff_notifies_but_reactivating_does_not(self):
        self.client.login(username='Sahad', password=PASSWORD)
        mech = Mechanic.objects.create(name='Ravi')

        self.client.post(reverse('manage_toggle_mechanic', args=[mech.pk]))
        self.assertEqual(self._events().count('ACCOUNT_ARCHIVED'), 1)

        self.client.post(reverse('manage_toggle_mechanic', args=[mech.pk]))
        self.assertEqual(self._events().count('ACCOUNT_ARCHIVED'), 1)

    def test_archiving_a_spare_shop_notifies(self):
        self.client.login(username='officestaff', password=PASSWORD)
        shop = SpareShop.objects.create(name='Old Motors')

        self.client.post(reverse('spare_shop_delete', args=[shop.pk]))

        self.assertIn('ACCOUNT_ARCHIVED', self._events())

    def test_salary_advance_notifies(self):
        self.client.login(username='officestaff', password=PASSWORD)
        mech = Mechanic.objects.create(name='Ravi')

        self.client.post(reverse('salary_advance_add'), {
            'staff_id': mech.pk, 'amount': '2500', 'date': date.today().isoformat(),
        })

        self.assertIn('SALARY_ADVANCE', self._events())
        self.assertTrue(SalaryAdvance.objects.exists())

    def test_the_salary_advance_link_opens_a_real_page(self):
        """
        A notification's url has to land somewhere that can act on its subject —
        the rule in CLAUDE.md, broken here for months.

        It pointed at `salary_advance_staff_detail`, which is the AJAX fragment
        the history modal fetches: a partial that extends no base template. An
        owner tapping the alert on their phone got a bare wall of unstyled rows,
        no nav, no heading, and no way back except the browser's own Back
        button. It now opens Salary & Advance with that person's history
        expanded.

        Asserted by FOLLOWING the stored url rather than comparing it to a
        `reverse()` — the failure was never a wrong string, it was a page that
        did not render as a page, and only fetching it can tell the difference.
        """
        self.client.login(username='officestaff', password=PASSWORD)
        mech = Mechanic.objects.create(name='Ravi')
        self.client.post(reverse('salary_advance_add'), {
            'staff_id': mech.pk, 'amount': '2500', 'date': date.today().isoformat(),
        })

        note = Notification.objects.filter(event='SALARY_ADVANCE').first()
        self.assertIsNotNone(note)
        self.assertTrue(note.url)

        self.client.login(username='Sahad', password=PASSWORD)
        landing = self.client.get(note.url)

        self.assertEqual(landing.status_code, 200)
        page = landing.content.decode()
        # The chrome base.html renders and a fragment cannot: if these are
        # missing, the link is pointing at a partial again.
        self.assertIn('<nav', page)
        self.assertIn('</html>', page)

        # It carries BOTH ids — the person, and the specific advance. Without
        # the second the page can only show the newest advance and has to label
        # it "Latest advance", which on an alert read days later would be a
        # different sum from the one the alert announced.
        advance = SalaryAdvance.objects.get(staff=mech)
        self.assertIn(str(mech.pk), note.url)
        self.assertIn(f'advance={advance.pk}', note.url)

        # And the page shows that advance as the subject rather than a guess.
        self.assertIn('Advance given', page)
        self.assertIn('2,500', page)
        self.assertIn('Ravi', page)


# =============================================================================
# The 2026-08-29 rewrite
# =============================================================================

class EveryEventCarriesAGlyphTests(TestCase):
    """
    The feed row draws the glyph where the title used to be, so an event without
    one arrives as a blank square on an owner's phone.

    The glyph lives in `EVENTS` rather than in a second table in the
    templatetags, so this only has to check one place — which is the point.
    """

    def test_every_event_has_a_bootstrap_icon_class(self):
        from workshop.notifications import EVENTS

        for key, spec in EVENTS.items():
            with self.subTest(event=key):
                self.assertTrue(spec.glyph.startswith('bi-'), f"{key}: {spec.glyph!r}")

    def test_every_glyph_is_a_class_the_vendored_icon_font_actually_declares(self):
        """
        A typo'd `bi-` class renders as nothing at all — no error, no console
        warning, just a hole where the row's whole identity should be. The font
        is vendored, so the answer is on disk and there is no excuse for
        guessing.
        """
        import io
        import os

        from django.conf import settings
        from workshop.notifications import DEFAULT_GLYPH, EVENTS

        css_path = os.path.join(
            settings.BASE_DIR, 'static', 'vendor', 'bootstrap-icons', 'bootstrap-icons.css'
        )
        css = io.open(css_path, encoding='utf-8').read()

        for glyph in {spec.glyph for spec in EVENTS.values()} | {DEFAULT_GLYPH}:
            with self.subTest(glyph=glyph):
                self.assertIn(".%s::before" % glyph, css)

    def test_an_unknown_event_key_still_draws_a_row(self):
        """
        A notification is kept for a fortnight and stores `event` as plain text,
        so a row written before an event was renamed — or by a key since
        removed — must still render. The feed is the one page an owner opens to
        find out what happened; 500ing it over a stale string is the worst
        failure mode available.
        """
        from workshop.notifications import DEFAULT_GLYPH, glyph_for

        self.assertEqual(glyph_for('NO_SUCH_EVENT_EVER'), DEFAULT_GLYPH)
        self.assertEqual(glyph_for(''), DEFAULT_GLYPH)
        self.assertEqual(glyph_for(None), DEFAULT_GLYPH)


class TheLoudLineCarriesTheFactTests(TestCase):
    """
    The row's headline used to be the event TITLE — a category, identical on
    every row of its kind ("Record permanently deleted" nine times running) —
    while the fact an owner actually needs sat under it in smaller, greyer type.
    The eye landed on the least useful line on the row.

    The glyph carries the category now and the body has the headline. Both
    strings are still on the row, so a swap would look almost right; these pin
    which is which.
    """

    def setUp(self):
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.client.login(username='Sahad', password=PASSWORD)

    def _feed(self):
        return self.client.get(reverse('notification_list')).content.decode()

    def test_the_body_is_in_the_headline_and_the_title_is_not(self):
        notify('HIGH_DISCOUNT', 'KL 10 AA 1038 discount given',
               detail='27% of the 20,500 bill')

        page = self._feed()
        self.assertIn('class="nf-fact">KL 10 AA 1038 discount given<', page)
        # The category and the context share the quiet second line, in that
        # order — category first, because it is what the glyph above it means.
        self.assertIn(
            'class="nf-sub">Large discount · 27% of the 20,500 bill', page)

    def test_the_detail_never_takes_the_loud_line(self):
        """
        The whole point of the column: context that used to be crammed into the
        statement now sits under it, so the statement stays one readable line.
        """
        notify('ACCOUNT_LOCKED', "amal's account locked",
               detail='5 wrong passwords from 1.2.3.4')

        page = self._feed()
        self.assertIn('class="nf-fact">amal&#x27;s account locked<', page)
        self.assertNotIn('1.2.3.4</span>\n            <time', page)

    def test_a_bodyless_notification_promotes_its_title_rather_than_printing_nothing(self):
        """
        Every call site passes a body today, but `notify()` does not require
        one — and a row whose loud line was empty would read as a broken feed.
        The title moves up, and the second line is then omitted rather than
        saying the same words twice.
        """
        notify('SALARY_SETTLED', '')

        page = self._feed()
        self.assertIn('class="nf-fact">Salary settled<', page)
        self.assertNotIn('class="nf-sub">Salary settled', page)

    def test_the_headline_class_does_not_collide_with_the_pages_own_header(self):
        """
        It did. `.nf-head` is the feed page's header block, declared in
        notification_list.html with `margin-bottom: 18px` — so while the row's
        headline shared that name, every row on the feed silently inherited
        18px of margin nothing intended, and none of the panel's rows did.
        Measured as a 40.3px headline sitting inside a 58.3px line.

        Nothing in this suite executes CSS, so the only available defence is
        that the name is not reused. Checked in both directions.
        """
        import io
        import os

        from django.conf import settings

        base = os.path.join(settings.BASE_DIR, 'workshop', 'templates', 'workshop')
        for name in (os.path.join(base, 'notifications', '_row.html'),
                     os.path.join(base, 'notifications', 'notification_list.html'),
                     os.path.join(base, 'base.html')):
            with self.subTest(template=os.path.basename(name)):
                # CSS comments are stripped first. The retired name is NAMED in
                # one, on purpose — it is the note explaining the trap, and a
                # test that forbids describing a bug forbids recording it.
                markup = re.sub(
                    r'/\*.*?\*/', '', io.open(name, encoding='utf-8').read(), flags=re.S)
                self.assertNotIn('nf-head', markup)


class TheDeletedRecordBodySaysEachFactOnceTests(TestCase):
    """
    `DeletionLog.record` builds the one notification body that is assembled from
    parts rather than written at a call site, and it printed two of them twice:

        "Restock Bill deleted: Restock Bill #669 - Fluid manjeri -
         31,500 (31500.00)"

    — the record type twice (the label opens with it) and the amount twice, in
    two different spellings of one number. Seven of the eighteen `record()`
    call sites put the amount in their own label, so that is the common case
    rather than an edge one.

    Both guards read what the LABEL already carries rather than a list of which
    call sites do what, so a nineteenth cannot reintroduce either.
    """

    def setUp(self):
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))

    def _note(self, **kwargs):
        Notification.objects.all().delete()
        DeletionLog.record(instance=SpareShop.objects.create(name='X Motors'), **kwargs)
        return Notification.objects.get(recipient=self.owner)

    def _body(self, **kwargs):
        return self._note(**kwargs).body

    def test_an_amount_already_in_the_label_is_not_repeated(self):
        body = self._body(
            entity_type=DeletionLog.ENTITY_SHOP_PAYMENT,
            amount=Decimal('31500.00'),
            label='₹31,500 → Fluid manjeri',
        )
        self.assertEqual(body.count('31,500'), 1)
        self.assertNotIn('31500.00', body)

    def test_an_amount_missing_from_the_label_is_added_in_the_apps_own_format(self):
        body = self._body(
            entity_type=DeletionLog.ENTITY_SHOP_PAYMENT,
            amount=Decimal('31500.00'), label='Payment to Fluid manjeri',
        )
        self.assertIn('₹31,500', body)
        self.assertNotIn('31500.00', body)

    def test_a_record_type_the_label_already_opens_with_is_not_repeated(self):
        note = self._note(
            entity_type=DeletionLog.ENTITY_RESTOCK_BILL,
            amount=Decimal('15000.00'),
            label='Restock Bill #669 · Fluid manjeri · ₹15,000',
        )
        self.assertEqual(note.body.lower().count('restock bill'), 1)
        # And the detail is dropped rather than repeating it a line below.
        self.assertEqual(note.detail, '')

    def test_a_record_type_the_label_omits_is_supplied(self):
        note = self._note(
            entity_type=DeletionLog.ENTITY_CASHBOOK,
            label='Money Out · Electricity · ₹4,200',
        )
        # In `detail`, under the statement — the loud line is what was deleted,
        # not what kind of thing it was.
        self.assertEqual(note.detail, 'Cashbook Entry')

    def test_the_headline_is_a_complete_statement_ending_in_the_action(self):
        """
        The loud line has to be understandable with nothing read under it. It
        used to open with the category ("Spare-Shop Payment deleted: ₹1 → …"),
        which put the identical word at the start of nine consecutive rows.
        """
        note = self._note(
            entity_type=DeletionLog.ENTITY_SHOP_PAYMENT, label='Calicut · ₹1 payment',
        )
        self.assertEqual(note.body, 'Calicut · ₹1 payment deleted')
        self.assertTrue(note.body.endswith('deleted'))
        self.assertEqual(note.title, 'Record deleted')
        self.assertEqual(note.detail, 'Spare-Shop Payment')

    def test_a_delete_with_no_amount_appends_nothing(self):
        note = self._note(
            entity_type=DeletionLog.ENTITY_MASTER_DATA, label="Spare part 'Oil filter'",
        )
        self.assertNotIn('₹', note.body)
        self.assertNotIn('₹', note.detail)


class TheAgeIsSaidInTheFeedsOwnWordsTests(TestCase):
    """
    `28 Aug, 11:59 p.m.` answered a question nobody asks of a notification.
    What an owner wants to know is whether this is from this morning or last
    week, and the absolute stamp made them work it out.

    It also has to be SHORT: it shares a flex line with the headline, so every
    character it spends comes off the line being read. That is why "Yesterday"
    was tried and reverted.
    """

    def _ago(self, **kwargs):
        from workshop.templatetags.custom_filters import short_ago
        return short_ago(timezone.now() - timedelta(**kwargs))

    def test_the_whole_scale(self):
        self.assertEqual(self._ago(seconds=5), 'now')
        self.assertEqual(self._ago(minutes=12), '12m')
        self.assertEqual(self._ago(hours=5), '5h')

    def test_hours_run_all_the_way_to_a_day(self):
        """
        Under 24 hours the hour figure is kept, because it says more: something
        21 hours old is "21h", not "1d".
        """
        self.assertEqual(self._ago(hours=21), '21h')
        self.assertEqual(self._ago(hours=23, minutes=50), '23h')

    def test_past_a_day_it_counts_CALENDAR_days_not_24_hour_blocks(self):
        """Two nights ago is "2d", never "45h"."""
        from workshop.templatetags.custom_filters import short_ago

        two_nights_ago = timezone.localtime(timezone.now()).replace(
            hour=23, minute=0, second=0, microsecond=0
        ) - timedelta(days=2)
        self.assertEqual(short_ago(two_nights_ago), '2d')

    def test_nothing_within_the_year_is_longer_than_six_characters(self):
        """
        Six is the budget, because this shares a flex line with the headline.
        The single exception is a row over a year old ("25 Aug 25"), which only
        an UNREAD notification can ever be — read ones are purged at 14 days.
        """
        from workshop.templatetags.custom_filters import short_ago

        for kwargs in ({'seconds': 5}, {'minutes': 12}, {'hours': 5}, {'hours': 21},
                       {'days': 1}, {'days': 3}, {'days': 20}, {'days': 200}):
            with self.subTest(**kwargs):
                self.assertLessEqual(
                    len(short_ago(timezone.now() - timedelta(**kwargs))), 6)

        self.assertLessEqual(len(short_ago(timezone.now() - timedelta(days=400))), 9)

    def test_a_missing_or_unusable_date_prints_nothing_rather_than_raising(self):
        from workshop.templatetags.custom_filters import short_ago

        self.assertEqual(short_ago(None), '')
        self.assertEqual(short_ago(''), '')
        self.assertEqual(short_ago('not a date'), '')

    def test_a_clock_skewed_future_row_reads_as_now_not_as_a_negative(self):
        from workshop.templatetags.custom_filters import short_ago

        self.assertEqual(short_ago(timezone.now() + timedelta(minutes=5)), 'now')


class EveryNotificationLandsOnItsSubjectTests(TestCase):
    """
    Tapping an alert has to reach the thing it is about.

    CLAUDE.md has stated this rule for a long time and nothing enforced it, which
    is how it was broken twice: `ACCOUNT_LOCKED` pointed every lockout at Control
    Hub → Accounts, a page that lists Office and Floor only, so a locked OWNER
    opened a page without the account on it and nothing to press; and a Supplies
    Shop `ACCOUNT_ARCHIVED` pointed at `supplier_shop_list`, which filters
    `is_active=True` — the one page guaranteed NOT to contain the shop the
    notification was about.

    Both were found by reading. `reverse()` was right in both cases, which is
    the trap: comparing a stored url against a `reverse()` proves the route
    exists and says nothing about whether the page SHOWS the subject. So these
    follow the link and look at the rendered page.

    Matching is case-INSENSITIVE: the Security section renders an owner as
    `{{ s.user.username|upper }}`, and a case-sensitive check reports a false
    miss on exactly the events this exists to protect.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.office = User.objects.create_user(username='officestaff', password=PASSWORD)
        self.office.groups.add(Group.objects.get(name='Office'))

        self.client.login(username='Sahad', password=PASSWORD)

    def _holds(self, url, needle):
        """GET the destination as an owner and say whether it names `needle`."""
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200, f"{url} did not render")
        return str(needle).lower() in response.content.decode().lower()

    # ---- the two that have already gone wrong ------------------------------

    def test_a_locked_OWNER_lands_where_owner_devices_are_listed(self):
        """
        Not Control Hub → Accounts: that section lists Office and Floor only,
        and `manage_unlock_account` refuses owners by design, so it is a page
        with neither the account nor anything to press.
        """
        url = reverse('manage_dashboard') + '?section=security'
        self.assertTrue(self._holds(url, self.owner.username))

    def test_a_locked_STAFF_account_lands_where_it_can_be_unlocked(self):
        url = reverse('manage_dashboard') + '?section=accounts'
        self.assertTrue(self._holds(url, self.office.username))

    def test_an_archived_supplies_shop_lands_on_the_list_that_still_shows_it(self):
        """
        `supplier_shop_list` filters `is_active=True`. Archiving is the only way
        this event fires, so that page is the one place the shop is guaranteed
        NOT to be.
        """
        from inventory.models import SupplierShop

        shop = SupplierShop.objects.create(name='Gone Fluids', is_active=False)
        self.assertTrue(self._holds(reverse('deactivated_supplier_shop_list'), shop.name))
        self.assertFalse(self._holds(reverse('supplier_shop_list'), shop.name))

    def test_an_archived_spare_shop_lands_on_the_list_that_still_shows_it(self):
        shop = SpareShop.objects.create(name='Gone Motors', is_trashed=True)
        self.assertTrue(self._holds(reverse('spare_shop_archived'), shop.name))

    # ---- an archived STAFF member is the one that is NOT on an archive list --

    def test_an_archived_staff_member_is_still_on_the_staff_roster(self):
        """
        There is no "archived staff" page, and there does not need to be:
        `manage_dashboard`'s staff section reads `Mechanic.objects.all()` and
        orders `-is_active`, so a retired person sits at the foot of their role
        rather than vanishing. That is what makes `?section=staff` a legitimate
        destination for this event where `supplier_shop_list` was not for its
        sibling.
        """
        retired = Mechanic.objects.create(name='Retired Ravi', is_active=False)
        url = reverse('manage_dashboard') + '?section=staff'
        self.assertTrue(self._holds(url, retired.name))

    # ---- the money ones ----------------------------------------------------

    def test_a_large_discount_lands_on_the_bill_it_was_given_on(self):
        card = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Audi', model_name='A4',
            registration_number='KL10AA1038', total_bill_amount=Decimal('20500.00'),
        )
        self.assertTrue(self._holds(
            reverse('invoice_view', args=[card.pk]), card.registration_number))

    def test_a_deleted_record_lands_on_its_own_deletion_history_entry(self):
        shop = SpareShop.objects.create(name='Calicut Spares')
        entry = DeletionLog.record(
            DeletionLog.ENTITY_SHOP_PAYMENT, shop, user=self.owner,
            amount=Decimal('1000.00'), label='Calicut Spares · ₹1,000 payment',
        )
        self.assertTrue(self._holds(
            reverse('deletion_history_detail', args=[entry.pk]), 'Calicut Spares'))

    def test_a_salary_advance_lands_on_the_person_who_got_it(self):
        staff = Mechanic.objects.create(name='Amlah', current_salary=Decimal('20000'))
        SalaryAdvance.objects.create(
            staff=staff, amount=Decimal('3000'), date=date.today(),
            created_by=self.owner,
        )
        self.assertTrue(self._holds(
            reverse('salary_advance_staff_detail', args=[staff.pk]), staff.name))

    # ---- and the one that deliberately cannot hold its subject --------------

    def test_a_deleted_login_lands_on_the_accounts_page_even_though_it_is_gone(self):
        """
        The exception, stated so nobody "fixes" it: the subject of USER_DELETED
        no longer exists by the time anyone taps. There is no DeletionLog row
        for a login either, so the accounts list — which shows who CAN still
        sign in — is the most useful page there is. The name is carried in the
        notification's own headline instead.
        """
        url = reverse('manage_dashboard') + '?section=accounts'
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.office.username, response.content.decode())
