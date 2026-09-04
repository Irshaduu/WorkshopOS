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

from datetime import date as _date

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
# ⚠ IT IS A CALENDAR MONTH, NEVER A DAY COUNT, and that is the whole design.
# A fixed "14 days" breaks at exactly the moment the feature exists for: the
# office reconciles LAST month against the collector's book in the first days
# of this one, so a gap found on 3 September may belong to 5 August. A day
# count refuses that correction; the month boundary is the rhythm the work
# actually follows. Same lesson `delete_window` records for measuring on
# `created_at` rather than the money date — a rule that cuts across the month
# end fights the workflow it is meant to protect.
#
# ⚠ IT BINDS OFFICE, NOT OWNERS — the escalation `delete_window` already uses,
# not a wall. Owners need the exception for real reasons: a go-live opening
# position is a deposit dated before the ledger even starts, and an audit
# finding can be older still. What stops an owner's mistake is not a refusal,
# it is that the act cannot happen SILENTLY: the caller raises a CRITICAL
# notification to the other owner using this same floor as its trigger, so one
# constant decides both who is refused and what is announced.

#: How many whole calendar months back Office may file money. 1 means "the 1st
#: of last month onward", so the window is the whole of this month plus the
#: whole of the last — generous during the days a month is being reconciled and
#: closed everywhere else.
BACKDATE_MONTHS = 1


def backdate_floor(today=None):
    """The earliest money date Office may file: the 1st of `BACKDATE_MONTHS` months ago."""
    today = today or timezone.localdate()
    total = today.year * 12 + (today.month - 1) - BACKDATE_MONTHS
    return _date(total // 12, total % 12 + 1, 1)


def is_too_far_back(value, today=None):
    """Is this money date older than Office may file? — the raw predicate.

    Separate from the message below because two callers need the ANSWER
    without the refusal: the view that decides whether to raise the alert on an
    owner, and the template that sets the date box's `min`.
    """
    return value < backdate_floor(today)


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
    return (f"{what} can only be dated back to "
            f"{floor.day} {floor:%B %Y}. "
            f"Ask an owner to record one older than that.")
