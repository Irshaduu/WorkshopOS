"""
What day did this money move? — one module, no views.

Every ledger in this app files a figure under a date, and for a *typed* entry
that date is the one thing nobody can derive: a bill paid on the 30th is often
keyed on the 3rd, and `auto_now_add` records the keystroke rather than the
payment. That is how a month-end expense lands in the following month on the
Profit page, permanently, and it is what `CashbookEntry.date` exists to stop.

The rule was written once for the Cashbook and lived inside
`cashbook_views._entry_date`, so the moment a second ledger needed it — the
spare-shop payment form — there was a copy waiting to be made. Two
implementations of "which day is this money filed under" would be two answers
free to disagree, and they would disagree at a month boundary, which is exactly
where an owner reads the difference.

Pure functions over strings and dates. The callers hold the messages, because
what a refused date should *say* depends on the ledger it was typed into.
"""

from datetime import date as _date, timedelta

from django.utils import timezone


def posted_date(raw):
    """
    'YYYY-MM-DD' from a form → date, falling back to today.

    Bad input falls back rather than 400ing: every caller renders the box as a
    `required` `<input type="date">`, so anything unparseable arriving here is
    a crafted POST, and today is the same answer the field gave before it was
    editable at all.

    A FUTURE date parses perfectly and is not refused here — see
    `is_future()`. The two are separate because the fallback is about input
    that cannot be read, and the future check is about input that reads fine
    and is wrong; keeping them apart is what lets a caller say which of the two
    just happened.
    """
    parsed = None
    if raw:
        try:
            parsed = _date.fromisoformat(raw.strip())
        except (ValueError, AttributeError):
            parsed = None
    return parsed or timezone.localdate()


def is_future(value):
    """
    Is this date after today? — `timezone.localdate()`, never `date.today()`.

    The server can run in UTC while the workshop operates in IST, so
    `date.today()` calls the small hours of an IST morning "tomorrow" and
    refuses a date that is simply now.

    Money dated forward is a mistyped year far more often than a plan — 2027
    for 2026 — and this workshop settles at the counter, so nothing is paid in
    advance of the day it is recorded. Same reasoning as `spare_dates`.
    """
    return value > timezone.localdate()


# =============================================================================
# HOW FAR BACK A MONEY DATE MAY REACH
# =============================================================================
# `is_future()` closes one end. This closes the other, and it is the end where
# the damage is quiet: a figure dated forward is caught the moment somebody
# reads the period it lands in, while one dated three years back rewrites a
# month nobody scrolls to and reports nothing.
#
# ⚠ IT IS THREE DAYS, AND THAT REVERSES WHAT THIS FILE SAID UNTIL 2026-09-22
# (the owner's decision). It read "a calendar month, never a day count": the
# floor was the 1st of LAST month, on the reasoning that the office reconciles
# last month in the first days of this one and a day count would refuse that
# correction. True, and the price was that Office could quietly file money
# into last month for the whole of this one — after the owners had read that
# month's profit and decided on it. The owners' answer is that the office
# enters money on the day it moves, and the rare catch-up is theirs to do:
# "this limitation stops users from moving entries to another time".
#
# So a late correction is not refused, it is ESCALATED — an owner records it,
# and the other owner is told. Three days covers yesterday's receipt typed this
# morning and a Saturday found on Monday.
#
# ⚠ IT BINDS OFFICE, NOT OWNERS — the escalation `delete_window` already uses,
# not a wall. Owners need the exception for real reasons: a go-live opening
# position is a deposit dated before the ledger even starts, and an audit
# finding can be older still. What stops an owner's mistake is not a refusal,
# it is that the act cannot happen SILENTLY: `notifications.notify_dated_back`
# raises a CRITICAL notification to the other owner using this same floor as
# its trigger, so one constant decides both who is refused and what is
# announced.

#: How many days back Office may date money. 3 means today and the three days
#: before it: on the 22nd, the 19th is the earliest.
BACKDATE_DAYS = 3


def backdate_floor(today=None):
    """The earliest money date Office may file: `BACKDATE_DAYS` days before today."""
    today = today or timezone.localdate()
    return today - timedelta(days=BACKDATE_DAYS)


def is_too_far_back(value, today=None):
    """Is this money date older than Office may file? — the raw predicate.

    Separate from the message below because two callers need the ANSWER
    without the refusal: the view that decides whether to raise the alert on an
    owner, and the template that sets the date box's `min`.
    """
    return value < backdate_floor(today)


def keyed_on(created_at):
    """The calendar day a row was TYPED IN — the IST day, never the UTC date,
    which reads yesterday for the whole of an IST morning."""
    return timezone.localtime(created_at).date()


def filed_past_limit(value, created_at):
    """
    Was money dated `value` filed PAST THE LIMIT, judged as at the day it was
    KEYED? — so it answers what the rule said at that moment, and a row never
    becomes "past the limit" just because weeks have gone by since.

    Read by the Back-dated tab of the history page, for every money table. It
    is `is_too_far_back`, the predicate that refused Office and tiered the
    alert at that same moment, so the red mark shows exactly the rows that
    rang the other owner's phone.
    """
    if created_at is None:
        return False
    return is_too_far_back(value, today=keyed_on(created_at))


def too_far_back(value, user, what, today=None):
    """
    The reason this user may not file money on this date, or **None** if they may.

    Shaped exactly like `delete_window.refusal()`: an owner is never refused,
    and the message names the rule AND the route, because a refusal that says
    "you cannot" without saying who can is the half nobody can act on.
    """
    from .decorators import is_owner              # avoids a circular import

    if is_owner(user) or not is_too_far_back(value, today):
        return None

    # `{floor.day}` rather than a `%-d` / `%#d` strftime code: those are
    # platform-specific (glibc vs MSVC) and this codebase is developed on
    # Windows and deployed on Linux, so one of the two would print "01 August".
    floor = backdate_floor(today)
    return (f"{what} can only be dated up to {BACKDATE_DAYS} days back — "
            f"{floor.day} {floor:%B %Y} at the earliest. "
            f"Ask an owner to record one older than that.")
