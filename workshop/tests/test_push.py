"""
Web Push — the delivery layer, and the guarantee that it can never break the feed.

Push sits on top of `Notification` rows that are already written. Every test
below that exercises a failure path is really asserting the same thing: the
in-app notification survives it. That is why push was built last and why nothing
in the request path waits on it.

The service worker route matters more than it looks. A worker can only control
pages at or below its own path, so serving it from /static/ would silently limit
its scope and it would never receive a push for the app itself.
"""

import json
from unittest.mock import patch

from django.contrib.auth.models import User, Group
from django.test import TestCase, override_settings
from django.urls import reverse

from workshop.models import Notification, PushSubscription
from workshop.notifications import notify

PASSWORD = 'push-test-pw-1'

# Deliberately not a real keypair. `pywebpush.webpush` is mocked in every test
# that would sign anything, so these are only ever compared as strings — and a
# genuine private key committed to the repository would be a live credential in
# version control forever, whatever the variable is named.
FAKE_PUBLIC = 'test-public-key-not-a-real-vapid-value'
FAKE_PRIVATE = 'test-private-key-not-a-real-vapid-value'

CONFIGURED = override_settings(
    VAPID_PUBLIC_KEY=FAKE_PUBLIC,
    VAPID_PRIVATE_KEY=FAKE_PRIVATE,
    VAPID_ADMIN_EMAIL='ops@example.com',
)


def _sub_payload(endpoint='https://push.example.com/abc'):
    return {'endpoint': endpoint, 'keys': {'p256dh': 'fake-p256dh', 'auth': 'fake-auth'}}


class ServiceWorkerRouteTests(TestCase):
    def test_served_from_the_origin_root(self):
        """
        Not /static/sw.js. A worker's scope is its own directory, so one served
        from /static/ could only ever control pages under /static/.
        """
        self.assertEqual(reverse('service_worker'), '/sw.js')

    def test_served_as_javascript_with_root_scope_and_no_caching(self):
        response = self.client.get('/sw.js')

        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response['Content-Type'])
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn('no-store', response['Cache-Control'])

    def test_reachable_without_signing_in(self):
        """The browser fetches this before anyone has logged in."""
        self.assertEqual(self.client.get('/sw.js').status_code, 200)

    def test_handles_push_and_click(self):
        body = self.client.get('/sw.js').content.decode()

        self.assertIn("addEventListener('push'", body)
        self.assertIn("addEventListener('notificationclick'", body)


class SubscriptionEndpointTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.other_owner = User.objects.create_user(username='Rijas', password=PASSWORD)
        self.other_owner.groups.add(Group.objects.get(name='Owner'))

        self.office = User.objects.create_user(username='officestaff', password=PASSWORD)
        self.office.groups.add(Group.objects.get(name='Office'))

        self.subscribe_url = reverse('push_subscribe')
        self.unsubscribe_url = reverse('push_unsubscribe')
        self.client.login(username='Sahad', password=PASSWORD)

    def _subscribe(self, payload=None):
        return self.client.post(
            self.subscribe_url,
            data=json.dumps(payload or _sub_payload()),
            content_type='application/json',
        )

    def test_owner_can_subscribe(self):
        response = self._subscribe()

        self.assertEqual(response.status_code, 200)
        sub = PushSubscription.objects.get()
        self.assertEqual(sub.user, self.owner)
        self.assertEqual(sub.p256dh, 'fake-p256dh')

    def test_resubscribing_updates_rather_than_duplicates(self):
        """A permission reset hands back the same endpoint — don't stack rows."""
        self._subscribe()
        PushSubscription.objects.update(failure_count=2)

        self._subscribe()

        self.assertEqual(PushSubscription.objects.count(), 1)
        self.assertEqual(PushSubscription.objects.get().failure_count, 0)

    def test_one_row_per_device(self):
        self._subscribe(_sub_payload('https://push.example.com/phone'))
        self._subscribe(_sub_payload('https://push.example.com/laptop'))

        self.assertEqual(PushSubscription.objects.filter(user=self.owner).count(), 2)

    def test_malformed_payload_is_refused(self):
        for bad in ({}, {'endpoint': 'x'}, {'keys': {}}):
            with self.subTest(bad=bad):
                response = self.client.post(
                    self.subscribe_url, data=json.dumps(bad), content_type='application/json',
                )
                self.assertEqual(response.status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(self.subscribe_url).status_code, 405)

    def test_office_cannot_subscribe(self):
        self.client.logout()
        self.client.login(username='officestaff', password=PASSWORD)

        self.assertEqual(self._subscribe().status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())

    def test_unsubscribe_removes_the_device(self):
        self._subscribe()

        self.client.post(
            self.unsubscribe_url,
            data=json.dumps({'endpoint': 'https://push.example.com/abc'}),
            content_type='application/json',
        )

        self.assertFalse(PushSubscription.objects.exists())

    def test_one_owner_cannot_unsubscribe_the_other(self):
        PushSubscription.objects.create(
            user=self.other_owner, endpoint='https://push.example.com/rijas',
            p256dh='k', auth='a',
        )

        self.client.post(
            self.unsubscribe_url,
            data=json.dumps({'endpoint': 'https://push.example.com/rijas'}),
            content_type='application/json',
        )

        self.assertTrue(PushSubscription.objects.filter(user=self.other_owner).exists())


class PushDispatchTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.other = User.objects.create_user(username='Rijas', password=PASSWORD)
        self.other.groups.add(Group.objects.get(name='Owner'))

        PushSubscription.objects.create(
            user=self.other, endpoint='https://push.example.com/rijas', p256dh='k', auth='a',
        )

    @CONFIGURED
    def test_critical_event_queues_a_push(self):
        with patch('workshop.push.transaction.on_commit', lambda fn: fn()), \
             patch('workshop.push._deliver') as deliver:
            notify('HIGH_DISCOUNT', 'big discount', actor=self.owner)

        self.assertTrue(deliver.called)
        _, payload = deliver.call_args[0]
        self.assertEqual(payload['body'], 'big discount')

    @CONFIGURED
    def test_info_event_does_not_push(self):
        """An INFO event waits in the bell. A phone that buzzes for routine
        activity stops being read for the things that matter."""
        with patch('workshop.push.transaction.on_commit', lambda fn: fn()), \
             patch('workshop.push._deliver') as deliver:
            notify('SALARY_ADVANCE', 'routine advance', actor=self.owner)

        self.assertFalse(deliver.called)
        self.assertTrue(Notification.objects.filter(event='SALARY_ADVANCE').exists())

    @CONFIGURED
    def test_actor_is_not_pushed_their_own_event(self):
        PushSubscription.objects.create(
            user=self.owner, endpoint='https://push.example.com/sahad', p256dh='k', auth='a',
        )

        with patch('workshop.push.transaction.on_commit', lambda fn: fn()), \
             patch('workshop.push._deliver') as deliver:
            notify('HIGH_DISCOUNT', 'big discount', actor=self.owner)

        queued_ids, _ = deliver.call_args[0]
        endpoints = set(
            PushSubscription.objects.filter(pk__in=queued_ids).values_list('endpoint', flat=True)
        )
        self.assertEqual(endpoints, {'https://push.example.com/rijas'})

    def test_unconfigured_server_still_writes_the_notification(self):
        """No VAPID keys is a valid deploy — push is optional, the feed is not."""
        with override_settings(VAPID_PUBLIC_KEY='', VAPID_PRIVATE_KEY=''), \
             patch('workshop.push._deliver') as deliver:
            written = notify('HIGH_DISCOUNT', 'big discount', actor=self.owner)

        self.assertFalse(deliver.called)
        self.assertEqual(written, 1)
        self.assertTrue(Notification.objects.filter(event='HIGH_DISCOUNT').exists())

    @CONFIGURED
    def test_no_subscribers_is_not_an_error(self):
        PushSubscription.objects.all().delete()

        with patch('workshop.push.transaction.on_commit', lambda fn: fn()), \
             patch('workshop.push._deliver') as deliver:
            notify('HIGH_DISCOUNT', 'big discount', actor=self.owner)

        self.assertFalse(deliver.called)
        self.assertTrue(Notification.objects.exists())

    @CONFIGURED
    def test_a_failing_push_layer_never_breaks_the_notification(self):
        """
        The guarantee the whole design rests on, and it must hold for the
        *reported* result too — a dead push service making notify() return 0
        would be a lie about work that actually succeeded.
        """
        with patch('workshop.push.queue_push', side_effect=RuntimeError('push service down')):
            written = notify('HIGH_DISCOUNT', 'big discount', actor=self.owner)

        self.assertEqual(written, 1)
        self.assertTrue(Notification.objects.filter(event='HIGH_DISCOUNT').exists())

    @CONFIGURED
    def test_a_failing_send_is_swallowed_inside_the_delivery_worker(self):
        """
        Exercised through the real `_deliver`, called directly rather than on a
        thread: patching `_deliver` itself would only prove that Python does not
        propagate exceptions out of threads, and would leave the error handling
        that actually matters untested.
        """
        from workshop.push import _deliver

        ids = list(PushSubscription.objects.values_list('pk', flat=True))
        with patch('pywebpush.webpush', side_effect=RuntimeError('push service down')):
            _deliver(ids, {'title': 't', 'body': 'b', 'url': '/'})

        # Survived, and an unrecognised error is not treated as "endpoint gone".
        self.assertTrue(PushSubscription.objects.filter(pk__in=ids).exists())

    @CONFIGURED
    def test_a_successful_send_records_the_time_and_clears_failures(self):
        from workshop.push import _deliver

        PushSubscription.objects.update(failure_count=2)
        ids = list(PushSubscription.objects.values_list('pk', flat=True))

        with patch('pywebpush.webpush', return_value=None):
            _deliver(ids, {'title': 't', 'body': 'b', 'url': '/'})

        sub = PushSubscription.objects.get(pk=ids[0])
        self.assertIsNotNone(sub.last_success)
        self.assertEqual(sub.failure_count, 0)


class DeadSubscriptionTests(TestCase):
    """404/410 mean the browser threw the endpoint away — it will never work again."""

    def setUp(self):
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.sub = PushSubscription.objects.create(
            user=self.owner, endpoint='https://push.example.com/dead', p256dh='k', auth='a',
        )

    def _fail_with(self, status):
        from workshop.push import _handle_failure

        class FakeResponse:
            status_code = status

        class FakeExc(Exception):
            response = FakeResponse()

        _handle_failure(self.sub, FakeExc())

    def test_gone_endpoints_are_dropped_immediately(self):
        for status in (404, 410):
            with self.subTest(status=status):
                PushSubscription.objects.update_or_create(
                    endpoint='https://push.example.com/dead',
                    defaults={'user': self.owner, 'p256dh': 'k', 'auth': 'a'},
                )
                self.sub = PushSubscription.objects.get()
                self._fail_with(status)
                self.assertFalse(PushSubscription.objects.filter(pk=self.sub.pk).exists())

    def test_transient_failures_are_counted_not_dropped(self):
        self._fail_with(503)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.failure_count, 1)

    def test_repeated_failure_eventually_drops_it(self):
        self.sub.failure_count = PushSubscription.MAX_FAILURES - 1
        self.sub.save(update_fields=['failure_count'])

        self._fail_with(503)

        self.assertFalse(PushSubscription.objects.filter(pk=self.sub.pk).exists())


class PushSetupUITests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='Sahad', password=PASSWORD)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.client.login(username='Sahad', password=PASSWORD)

    @CONFIGURED
    def test_feed_ships_the_public_key_only(self):
        response = self.client.get(reverse('notification_list'))

        self.assertContains(response, FAKE_PUBLIC)
        self.assertNotContains(response, FAKE_PRIVATE)

    @CONFIGURED
    def test_feed_offers_the_enable_button(self):
        response = self.client.get(reverse('notification_list'))

        self.assertContains(response, 'push-toggle')
        self.assertContains(response, reverse('push_subscribe'))
