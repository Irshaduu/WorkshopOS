"""
The whole notification catalogue, in one file.

Every event the system can raise is declared in `EVENTS` below and sent through
`notify()`. That is the point of this module: with a dozen call sites scattered
across fourteen view modules, nobody could answer "what does this thing notify
about?" without grepping. Here it is one screen.

**Adding an event**: add a row to `EVENTS`, then call `notify()` from the one
place it happens. Do not call `Notification.objects.create()` directly.

**Audience**: owners only, for now. Floor deliberately receives nothing — a
notification a mechanic cannot act on is noise that trains everyone to ignore
the bell. The actor is excluded from their own events; nobody needs telling
about what they just did, and with two owners that halves the volume.

**Severity** is a tier, not decoration. CRITICAL is the shortlist that would
justify a phone buzzing once push exists (roadmap §VI): money moved
unexpectedly, something was destroyed, or someone got in. Everything else is
INFO — worth finding in the feed, not worth interrupting for.
"""

import logging

from django.contrib.auth.models import User

from .models import Notification

logger = logging.getLogger(__name__)

AUDIENCE_OWNERS = 'OWNERS'

CRITICAL = Notification.SEVERITY_CRITICAL
INFO = Notification.SEVERITY_INFO

# event key -> (title, severity, audience)
EVENTS = {
    'LOGIN':            ("Someone signed in",        INFO,     AUDIENCE_OWNERS),
    'ACCOUNT_LOCKED':   ("Account locked",           CRITICAL, AUDIENCE_OWNERS),
    # The system announced every routine sign-in and stayed silent for the one
    # event that means an account changed hands. A reset also terminates every
    # session, so without this the real owner is signed out on all their devices
    # with no message and no reason — which reads as "the app logged me out
    # again", the easiest thing in the world to shrug off. CRITICAL so it
    # reaches the other owner's phone, not just the bell.
    'PASSWORD_RESET':   ("Password was reset",       CRITICAL, AUDIENCE_OWNERS),
    'USER_CREATED':     ("New login created",        CRITICAL, AUDIENCE_OWNERS),
    'HIGH_DISCOUNT':    ("Large discount given",     CRITICAL, AUDIENCE_OWNERS),
    'RECORD_DELETED':   ("Record permanently deleted", CRITICAL, AUDIENCE_OWNERS),
    'ACCOUNT_ARCHIVED': ("Account archived",         INFO,     AUDIENCE_OWNERS),
    'SALARY_ADVANCE':   ("Salary advance given",     INFO,     AUDIENCE_OWNERS),
    'SALARY_SETTLED':   ("Salary month settled",     INFO,     AUDIENCE_OWNERS),
}


def _recipients(audience, exclude=None):
    """
    Resolve an audience to real accounts, minus whoever caused the event.

    Owners are found by **group membership**, not `is_superuser`. That
    distinction bit once already: both owner accounts were superusers in no
    group, so this query returned an empty list and every owner-addressed
    notification would have reached nobody while appearing to work.
    `sync_owner_identity` keeps the group populated.
    """
    if audience != AUDIENCE_OWNERS:
        return User.objects.none()

    people = User.objects.filter(groups__name='Owner', is_active=True).distinct()
    if exclude is not None and getattr(exclude, 'pk', None):
        people = people.exclude(pk=exclude.pk)
    return people


def notify(event, body='', *, actor=None, url='', object_type='', object_id=None):
    """
    Raise one event to its audience. Returns how many rows were written.

    Call this inside whatever transaction the business action already uses — the
    row belongs with the thing it describes, and a rolled-back payment should not
    leave an announcement behind.

    **Never breaks the caller.** A malformed body or a missing account must not
    fail a payment, so anything unexpected is logged and swallowed. Note the
    limit of that promise: if the failure is a *database* error inside an atomic
    block, the surrounding transaction is already doomed and swallowing here
    cannot rescue it — which is correct, because at that point the business data
    itself is in doubt.
    """
    spec = EVENTS.get(event)
    if spec is None:
        logger.error(f"notify() called with unknown event {event!r}")
        return 0

    title, severity, audience = spec

    try:
        people = list(_recipients(audience, exclude=actor))
        if not people:
            return 0

        Notification.objects.bulk_create([
            Notification(
                recipient=person,
                event=event,
                severity=severity,
                title=title,
                body=body[:255],
                url=url[:200],
                actor=actor if (actor is not None and getattr(actor, 'pk', None)) else None,
                object_type=object_type[:40],
                object_id=object_id,
            )
            for person in people
        ])

        # Push is a *delivery layer* over the rows just written, not a second
        # system: the feed is already correct whether or not this does anything.
        # Only CRITICAL events buzz a phone — an INFO event waits in the bell.
        #
        # Guarded separately from the block above so that a push problem cannot
        # change what this function *reports*. The rows are the real outcome;
        # letting a dead push service make notify() return 0 would be a lie.
        if severity == CRITICAL:
            try:
                from .push import queue_push
                queue_push(people, title=title, body=body, url=url)
            except Exception as exc:
                logger.error(f"queue_push for {event!r} failed: {exc}")

        return len(people)
    except Exception as exc:
        logger.error(f"notify({event!r}) failed: {exc}")
        return 0
