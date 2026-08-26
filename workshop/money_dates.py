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
