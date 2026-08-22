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
from django.db.models import Q

from .models import Notification

logger = logging.getLogger(__name__)

AUDIENCE_OWNERS = 'OWNERS'

CRITICAL = Notification.SEVERITY_CRITICAL
INFO = Notification.SEVERITY_INFO

# event key -> (title, severity, audience)
EVENTS = {
    'LOGIN':            ("Someone signed in",        INFO,     AUDIENCE_OWNERS),
    # A STAFF sign-in — Office or Floor — is the same fact as LOGIN above at a
    # different tier, and the split exists so this file states the rule rather
    # than hiding it in a severity argument at the call site: *an owner signing
    # in is routine, somebody else signing in is not*. Owners already know when
    # they sign in themselves and the other owner is excluded as the actor, so
    # LOGIN buzzing a phone would only ever announce the co-owner's ordinary
    # working day. A staff account is different — it is used on shared devices
    # on the shop floor, and it is the one the owners cannot see being used.
    #
    # Volume is why this is safe to make CRITICAL: SESSION_COOKIE_AGE is 40
    # days, so a signed-in tablet stays signed in and this fires on a genuinely
    # new session, not on every shift.
    'STAFF_LOGIN':      ("Staff signed in",          CRITICAL, AUDIENCE_OWNERS),
    'ACCOUNT_LOCKED':   ("Account locked",           CRITICAL, AUDIENCE_OWNERS),
    # The system announced every routine sign-in and stayed silent for the one
    # event that means an account changed hands. A reset also terminates every
    # session, so without this the real owner is signed out on all their devices
    # with no message and no reason — which reads as "the app logged me out
    # again", the easiest thing in the world to shrug off. CRITICAL so it
    # reaches the other owner's phone, not just the bell.
    'PASSWORD_RESET':   ("Password was reset",       CRITICAL, AUDIENCE_OWNERS),
    # The two ways a reset can be ATTEMPTED and fail. Both were silent, which
    # left the system announcing every routine sign-in while saying nothing at
    # all about somebody working through an owner's account — the only accounts
    # that can reach the reset flow (`can_reset_password`).
    #
    # These are the one pair raised with **no actor**, so unlike every other
    # event here they reach BOTH owners, the targeted one included. That is
    # deliberate: there is no signed-in person to exclude, the account holder is
    # the one who can act, and the other owner is the corroboration. CRITICAL so
    # they reach a phone; de-duped to one per account per hour by
    # `_recently_raised`, because the form behind them needs no login and would
    # otherwise be a doorbell anyone could hold down.
    'RESET_CODE_LIMIT':
        ("Too many reset codes requested", CRITICAL, AUDIENCE_OWNERS),
    'RESET_CODE_ATTEMPTS_SPENT':
        ("Wrong reset code entered repeatedly", CRITICAL, AUDIENCE_OWNERS),
    'USER_CREATED':     ("New login created",        CRITICAL, AUDIENCE_OWNERS),
    # Creating a login was announced while deleting one and changing its
    # password were silent — the two actions in Control Hub that actually hand
    # over or revoke access. An owner could remove the Office account overnight
    # and the other owner's only clue would be the staff member failing to sign
    # in. CRITICAL for the same reason PASSWORD_RESET is: it means access
    # changed hands.
    'USER_DELETED':     ("Login deleted",            CRITICAL, AUDIENCE_OWNERS),
    'STAFF_PASSWORD_SET': ("Staff password changed", CRITICAL, AUDIENCE_OWNERS),
    'HIGH_DISCOUNT':    ("Large discount given",     CRITICAL, AUDIENCE_OWNERS),
    'RECORD_DELETED':   ("Record permanently deleted", CRITICAL, AUDIENCE_OWNERS),
    'ACCOUNT_ARCHIVED': ("Account archived",         INFO,     AUDIENCE_OWNERS),
    'SALARY_ADVANCE':   ("Salary advance given",     INFO,     AUDIENCE_OWNERS),
    'SALARY_SETTLED':   ("Salary month settled",     INFO,     AUDIENCE_OWNERS),
}


def _recipients(audience, exclude=None):
    """
    Resolve an audience to real accounts, minus whoever caused the event.

    An owner is `is_superuser=True` **or** in the `Owner` group — the same
    either-or every other RBAC check in this app already uses
    (`has_group`, `owner_required`'s `is_owner`). This used to be
    group-membership only, which is the *narrower* of the two and broke twice
    in practice: a fresh or reseeded database routinely leaves both owner
    accounts superuser with **empty** group membership until someone
    remembers to run `sync_owner_identity --yes`, and in that window this
    query returned nobody — every owner-addressed notification silently
    reached no one while appearing to work. `is_superuser` is the bit every
    decorator already trusts and nothing resets it, so checking it here too
    means the feed can't go dark just because the group sync was skipped.
    `sync_owner_identity` is still worth running — it's what closes
    `/admin/` and keeps the mobile number current — it just isn't a
    precondition for notifications to work.
    """
    if audience != AUDIENCE_OWNERS:
        return User.objects.none()

    people = User.objects.filter(
        Q(is_superuser=True) | Q(groups__name='Owner'),
        is_active=True,
    ).distinct()
    if exclude is not None and getattr(exclude, 'pk', None):
        people = people.exclude(pk=exclude.pk)
    return people


def recently_raised(event, object_id, within_minutes=60):
    """
    Has this exact event already been raised about this subject recently?

    A de-dupe for the events that can be triggered from OUTSIDE a login — the
    password-reset pair. Every other event in this file costs the sender a
    session and a role; those two cost a stranger a form submission, so without
    a limit anyone who knows an owner's username could buzz both phones on
    demand until the alert stopped being read. Which is the actual attack: not
    the reset itself, which the throttles already stop, but the alarm about it
    being made worthless.

    Reads the fanned-out rows, so one hit is enough — every recipient got theirs
    in the same `bulk_create`. Cheap: `event` and `created_at` are both indexed.
    """
    from django.utils import timezone
    from datetime import timedelta

    if object_id is None:
        return False

    return Notification.objects.filter(
        event=event,
        object_id=object_id,
        created_at__gte=timezone.now() - timedelta(minutes=within_minutes),
    ).exists()


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
