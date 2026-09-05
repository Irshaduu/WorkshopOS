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

**Severity** is a tier, not decoration, and it is now wired to something:
CRITICAL sends a Web Push, INFO only lands in the feed. The shortlist is money
moved unexpectedly, something destroyed, or someone getting in. Everything else
is INFO — worth finding in the bell, not worth interrupting for.

**A notification is THREE strings.** `title` is the CATEGORY and lives here.
`body` is a COMPLETE STATEMENT ending in what happened — subject first, verb
last, understandable with nothing under it read at all. `detail` is the
supporting context, so that statement can stay one line: the device, the kind of
record, the remedy, the percentage behind a figure.

The feed row draws the glyph, then `body` loud, then `title · detail · actor`
quiet. A push puts `body` in its bold line and `title · detail` under it. Same
order on both, so the two cannot teach different habits.

The test for a new `body`: *if the reader saw only this line, would they know
what occurred?* And nothing that decides what the row MEANS may go in `detail`
— it is read second, or not at all.
"""

import logging
from typing import NamedTuple

from django.contrib.auth.models import User
from django.db.models import Q

from .models import Notification

logger = logging.getLogger(__name__)

AUDIENCE_OWNERS = 'OWNERS'

CRITICAL = Notification.SEVERITY_CRITICAL
INFO = Notification.SEVERITY_INFO


class Event(NamedTuple):
    """
    One row of the catalogue.

    A NamedTuple rather than a bare tuple so a fourth column could be added
    without every reader having to know its position — but still a tuple, so
    `EVENTS['LOGIN'][1]` keeps meaning severity for the tests that pin the tier.

    `title` is the CATEGORY, not the fact: two or three words naming what kind
    of thing happened. The specific fact — who, how much, which car — is the
    `body` passed at the call site. The two must never say the same word twice,
    because they are read together on both surfaces:

    * a **push** shows title as its bold line and body under it, which is
      exactly the right shape for a lock screen;
    * a **feed row** draws `glyph` in place of the title and gives the loud line
      to the body, because an icon says "deleted" faster than the word does and
      the loud line has to carry what DIFFERS between rows.

    `glyph` is a Bootstrap Icons class. It lives here rather than in a second
    dict in the templatetags so the catalogue stays the one place that answers
    "what does this thing notify about, and how loudly".
    """
    title: str
    severity: str
    audience: str
    glyph: str


# event key -> Event(title, severity, audience, glyph)
EVENTS = {
    # GETTING IN IS ALWAYS WORTH A PUSH — all three of these, on the owner's
    # decision (2026-08-29). LOGIN was INFO until then, on the reasoning that an
    # owner signing in is routine. What overruled it: an owner account is the
    # highest-privilege thing in this system, and a sign-in on one with a stolen
    # password raised NOTHING that reached a phone. PASSWORD_RESET pushes, but
    # only if the intruder went through the reset flow.
    #
    # Volume is what makes it safe, and it is the same argument that already
    # justified STAFF_LOGIN: SESSION_COOKIE_AGE is 40 days, so a signed-in phone
    # STAYS signed in and this fires on a genuinely new session — a new device,
    # a cleared cookie jar, 40 days elapsed — not on every shift. Roughly one or
    # two a month across two owners. `notify()` also excludes the actor, so an
    # owner never buzzes themselves; what arrives is always *somebody signed
    # into the other account*, which is exactly the thing worth knowing.
    #
    # The split between the two events survives the tier change, because the
    # tier was never the only thing it carried: the titles differ, and a staff
    # alert leads its detail with the ROLE, which is what says whether that
    # account can see money.
    'LOGIN':            Event("Owner signed in",     CRITICAL, AUDIENCE_OWNERS, 'bi-box-arrow-in-right'),
    # The two share a glyph on purpose. They are one act about two kinds of
    # account, and a second visual difference would invite reading them as two
    # unrelated events.
    'STAFF_LOGIN':      Event("Staff signed in",     CRITICAL, AUDIENCE_OWNERS, 'bi-box-arrow-in-right'),
    'ACCOUNT_LOCKED':   Event("Account locked",      CRITICAL, AUDIENCE_OWNERS, 'bi-person-fill-lock'),
    # The system announced every routine sign-in and stayed silent for the one
    # event that means an account changed hands. A reset also terminates every
    # session, so without this the real owner is signed out on all their devices
    # with no message and no reason — which reads as "the app logged me out
    # again", the easiest thing in the world to shrug off. CRITICAL so it
    # reaches the other owner's phone, not just the bell.
    'PASSWORD_RESET':   Event("Password reset",      CRITICAL, AUDIENCE_OWNERS, 'bi-key-fill'),
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
        Event("Too many reset codes", CRITICAL, AUDIENCE_OWNERS, 'bi-shield-exclamation'),
    'RESET_CODE_ATTEMPTS_SPENT':
        Event("Reset code guessed wrong", CRITICAL, AUDIENCE_OWNERS, 'bi-shield-exclamation'),
    'USER_CREATED':     Event("Login created",       CRITICAL, AUDIENCE_OWNERS, 'bi-person-plus-fill'),
    # Creating a login was announced while deleting one and changing its
    # password were silent — the two actions in Control Hub that actually hand
    # over or revoke access. An owner could remove the Office account overnight
    # and the other owner's only clue would be the staff member failing to sign
    # in. CRITICAL for the same reason PASSWORD_RESET is: it means access
    # changed hands.
    'USER_DELETED':     Event("Login deleted",       CRITICAL, AUDIENCE_OWNERS, 'bi-person-dash-fill'),
    'STAFF_PASSWORD_SET': Event("Staff password changed", CRITICAL, AUDIENCE_OWNERS, 'bi-key-fill'),
    'HIGH_DISCOUNT':    Event("Large discount",      CRITICAL, AUDIENCE_OWNERS, 'bi-percent'),
    'RECORD_DELETED':   Event("Record deleted",      CRITICAL, AUDIENCE_OWNERS, 'bi-trash3-fill'),
    # ⚠ THE TWO ACTS IN DEPOSIT & RENT THAT REWRITE HISTORY, AND BOTH ARE
    # OWNER-ONLY — which is precisely why they need announcing rather than
    # refusing. Every other guard in that section escalates to an owner, so an
    # owner is where the escalation STOPS: nothing above them can refuse a
    # mistake, and the only remaining control is that the act cannot happen
    # SILENTLY. `notify()` excludes the actor, so what arrives is always the
    # OTHER owner learning what was done — which with two owners is real
    # corroboration rather than somebody being told about themselves.
    #
    # Volume is what keeps them safe at CRITICAL, the same argument LOGIN
    # already rests on: a rent changes about once a YEAR, and a deposit filed
    # past the Office floor is a go-live opening entry or a rare correction.
    # Two pushes a year between them.
    #
    # They stay SPLIT rather than becoming one "rent history changed", because
    # the bodies are different facts with different remedies — one says what
    # the premises now cost, the other says money was filed into a closed
    # month — and a title covering both would have to be vague enough to say
    # nothing. Same reasoning that keeps LOGIN and STAFF_LOGIN apart.
    'RENT_RATE_SET':    Event("Rent changed",        CRITICAL, AUDIENCE_OWNERS, 'bi-building-gear'),
    'RENT_BACKDATED':   Event("Deposit back-dated",  CRITICAL, AUDIENCE_OWNERS, 'bi-calendar-x'),
    'ACCOUNT_ARCHIVED': Event("Account archived",    INFO,     AUDIENCE_OWNERS, 'bi-archive-fill'),
    'SALARY_ADVANCE':   Event("Salary advance",      INFO,     AUDIENCE_OWNERS, 'bi-cash-coin'),
    'SALARY_SETTLED':   Event("Salary settled",      INFO,     AUDIENCE_OWNERS, 'bi-cash-stack'),
}

# Fallback for a row written before its event was renamed, or by a key that has
# since been removed from the catalogue. A notification is kept for a fortnight
# and its `event` column is a plain CharField, so the feed must be able to draw
# a row whose key this file no longer knows — silently, rather than 500ing the
# one page an owner opens to find out what happened.
DEFAULT_GLYPH = 'bi-info-circle-fill'


def glyph_for(event):
    """The icon a feed row draws for this event key. Never raises."""
    spec = EVENTS.get(event)
    return spec.glyph if spec else DEFAULT_GLYPH


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

    # One implementation, in `decorators.owner_accounts` — the Owner
    # Withdrawals page draws a card per owner off the same list, and two copies
    # of a rule that has already gone dark twice would be two chances to fix
    # one and leave the other.
    from .decorators import owner_accounts
    people = owner_accounts()
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


def notify(event, body='', *, detail='', actor=None, url='', object_type='',
           object_id=None):
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

    # Attribute access, not tuple unpacking: `Event` gained a fourth column
    # (the glyph) and a positional unpack here would have had to change with it.
    title, severity, audience = spec.title, spec.severity, spec.audience

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
                # Coerced rather than trusted. NO call site passes None
                # today — this is one character of defence against a future one
                # that builds a body from a nullable column (`note`, `reason`
                # and `bill_number` are all blank-able), because the failure
                # mode is silent: a None raises inside the `try` below, which
                # logs and returns 0, so the business action succeeds and
                # nobody is ever told about it.
                body=(body or '')[:255],
                detail=(detail or '')[:255],
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
                # THE PUSH IS THE ROW, IN THE ROW'S OWN ORDER. A lock screen
                # gives two lines and no glyph, so the bold one takes the same
                # thing the feed's loud line takes — the complete statement —
                # and the quiet one takes the category plus the context. It
                # used to send the CATEGORY as its bold line, which put
                # "Record deleted" in bold on nine consecutive alerts with the
                # ₹1,00,000 in the small type underneath.
                queue_push(
                    people,
                    title=body or title,
                    body=' · '.join(part for part in (title, detail) if part),
                    url=url,
                )
            except Exception as exc:
                logger.error(f"queue_push for {event!r} failed: {exc}")

        return len(people)
    except Exception as exc:
        logger.error(f"notify({event!r}) failed: {exc}")
        return 0
