"""
Web Push delivery — a layer on top of `Notification` rows, never a source of truth.

Everything here is best-effort by design. If the VAPID keys are missing, if the
push service is down, if every subscription is stale — the in-app feed is
completely unaffected, because the row was already written before any of this
runs. That is the whole reason push was built last.

**Sent off the request thread.** A push is an HTTPS call to Google's or
Mozilla's servers, ~200 ms each, and an owner with a phone and a laptop is two
of them per event. Doing that inline would add most of a second to saving a
payment. `queue_push()` hands the work to a background thread on
`transaction.on_commit`, so nothing is sent for a transaction that rolls back
and nobody waits for the network.

**Only CRITICAL events push.** INFO events land in the feed and wait to be read.
A phone that buzzes for a routine salary advance stops being read for the things
that matter — see the severity note in `notifications.py`.
"""

import json
import logging
import threading

from django.conf import settings
from django.db import close_old_connections, transaction

logger = logging.getLogger(__name__)


def push_is_configured():
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY)


def _vapid_claims():
    """
    Contact address the push service may use to reach the sender.

    RFC 8292 wants a `mailto:` URI here. Some services reject a bare address, so
    the scheme is added if it is missing rather than trusting the .env value to
    carry it.
    """
    email = (settings.VAPID_ADMIN_EMAIL or '').strip()
    if not email:
        return {}
    if not email.startswith('mailto:'):
        email = f"mailto:{email}"
    return {'sub': email}


def _deliver(subscription_ids, payload):
    """
    Runs on a background thread. Never raises into the caller.

    Opens and closes its own database connection: a thread started outside
    Django's request cycle does not inherit one, and leaving it open would leak a
    connection per push on a server with a small Postgres connection budget.
    """
    from pywebpush import WebPushException, webpush

    from .models import PushSubscription

    try:
        close_old_connections()
        subscriptions = list(PushSubscription.objects.filter(pk__in=subscription_ids))

        for sub in subscriptions:
            try:
                webpush(
                    subscription_info=sub.as_dict(),
                    data=json.dumps(payload),
                    vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_claims=dict(_vapid_claims()),  # copied: pywebpush mutates this
                    timeout=10,
                )
            except WebPushException as exc:
                _handle_failure(sub, exc)
            except Exception as exc:
                logger.error(f"Push to {sub.pk} failed unexpectedly: {exc}")
            else:
                from django.utils import timezone
                PushSubscription.objects.filter(pk=sub.pk).update(
                    last_success=timezone.now(), failure_count=0,
                )
    except Exception as exc:
        logger.error(f"Push delivery thread failed: {exc}")
    finally:
        close_old_connections()


def _handle_failure(sub, exc):
    """
    Reap subscriptions the push service says are dead.

    404/410 mean the browser revoked or replaced this endpoint — it will never
    work again, so keeping it only wastes a request on every future event.
    Anything else (a timeout, a 5xx) is treated as transient and only counted;
    the row is dropped once it has failed `MAX_FAILURES` times in a row.
    """
    from .models import PushSubscription

    status = getattr(getattr(exc, 'response', None), 'status_code', None)

    if status in (404, 410):
        logger.info(f"Push endpoint gone ({status}); dropping subscription {sub.pk}")
        PushSubscription.objects.filter(pk=sub.pk).delete()
        return

    failures = sub.failure_count + 1
    if failures >= PushSubscription.MAX_FAILURES:
        logger.info(f"Push subscription {sub.pk} failed {failures}x; dropping")
        PushSubscription.objects.filter(pk=sub.pk).delete()
    else:
        PushSubscription.objects.filter(pk=sub.pk).update(failure_count=failures)
        logger.warning(f"Push to {sub.pk} failed ({status}); attempt {failures}")


def queue_push(recipients, *, title, body, url=''):
    """
    Schedule a push to every device belonging to `recipients`.

    Does nothing — quietly, and without touching the caller — when push is not
    configured, when nobody has subscribed, or when called during tests.
    Returns the number of devices queued, for tests and logging.
    """
    if not push_is_configured():
        return 0

    from .models import PushSubscription

    subscription_ids = list(
        PushSubscription.objects.filter(user__in=recipients).values_list('pk', flat=True)
    )
    if not subscription_ids:
        return 0

    payload = {'title': title, 'body': body, 'url': url or '/notifications/'}

    def _start():
        thread = threading.Thread(
            target=_deliver, args=(subscription_ids, payload), daemon=True,
        )
        thread.start()

    # on_commit so a rolled-back action never announces itself, and so the
    # thread cannot read rows the caller has not committed yet.
    transaction.on_commit(_start)
    return len(subscription_ids)
