"""
The in-app notification feed.

Two things are being guarded. First the plumbing: rows fan out per recipient,
the actor is spared their own event, and a notification survives the deletion of
whatever it was about — most of them announce a deletion, so a ForeignKey would
have cascaded away exactly the record that mattered.

Second the audience. Owners are resolved by **group membership**, and that has
already gone wrong once: both owner accounts were superusers in no group, so the
query returned nobody and every notification would have silently reached no one
while appearing to work.
"""

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

    def test_owners_are_found_by_group_not_superuser(self):
        """
        The regression that started all this: a superuser in no group is not an
        owner as far as this query is concerned.
        """
        User.objects.create_user(username='lonesuper', password=PASSWORD, is_superuser=True)

        notify('LOGIN', 'something happened')

        self.assertFalse(Notification.objects.filter(recipient__username='lonesuper').exists())

    def test_inactive_owner_is_skipped(self):
        self.owner_b.is_active = False
        self.owner_b.save(update_fields=['is_active'])

        notify('LOGIN', 'something happened')

        self.assertEqual(Notification.objects.count(), 1)

    def test_severity_and_title_come_from_the_registry(self):
        notify('HIGH_DISCOUNT', 'big one')

        note = Notification.objects.first()
        title, severity, _ = EVENTS['HIGH_DISCOUNT']
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
        self.assertIn('1500.00', note.body)

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
        # The percentage is still in the body — no longer the threshold, but
        # still the context an owner reads ₹7,000 against.
        self.assertIn('12%', notes.first().body)

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
        url = reverse('admin_login')
        for _ in range(AccountLockout.MAX_FAILURES + 4):
            self.client.post(url, {'username': 'officestaff', 'password': 'wrong'})

        # One per owner, and no more however long the attempts continue.
        self.assertEqual(self._events().count('ACCOUNT_LOCKED'), 1)
        self.assertEqual(self._events(self.owner).count('ACCOUNT_LOCKED'), 1)

    def test_lockout_of_staff_links_where_it_can_be_unlocked(self):
        for _ in range(AccountLockout.MAX_FAILURES):
            self.client.post(reverse('admin_login'),
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
            self.client.post(reverse('admin_login'),
                             {'username': 'Sahad', 'password': 'wrong'})

        note = Notification.objects.filter(event='ACCOUNT_LOCKED').first()
        self.assertNotIn('section=accounts', note.url)
        self.assertIn('section=security', note.url)
        self.assertIn('cannot be unlocked', note.body)

    def test_successful_login_notifies_the_other_owner(self):
        self.client.post(reverse('admin_login'), {'username': 'Sahad', 'password': PASSWORD})

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
