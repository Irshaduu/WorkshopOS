"""
How long a money row stays changeable by OFFICE — edited or deleted.

One question, one implementation: *has this record been in the books long
enough that changing or removing it should be an owner's decision?*

Every permanent delete in this app already funnels through
`DeletionLog.record()`, which stores who, when, what and a full snapshot and
raises `RECORD_DELETED` at CRITICAL — a push to both owners' phones within
seconds, linking straight to the record. That is DETECTION, and it is strong.
What there was none of is PREVENTION: `bulk_payment_history_delete`,
`spare_shop_payment_reverse`, `delete_shop_payment`, `delete_restock_bill`,
`delete_cashbook_entry` and `salary_advance_delete` are all `@office_required`,
so Office could remove a six-month-old fleet payment exactly as easily as one
keyed this morning.

Those are two different acts and the system treated them identically:

  * changing something recorded an hour ago is a CORRECTION — frequent, cheap,
    and the money is still fresh in everybody's head;
  * changing something recorded last week is ANOMALOUS — that period may have
    been reported on, and a shop's balance may have been settled on it.

So Office keeps the first and an owner takes the second. It is an ESCALATION,
never a wall: no new mechanism, no approval queue, and the owners are already
the people the alerts go to.

⚠ **24 HOURS, AND IT COVERS EDITS AS WELL AS DELETES — both reverse what this
file said until 2026-09-22** (the owner's decision). It was 7 calendar days, and
for deletes only. That left the Cashbook's EDIT as a quiet delete: Office could
not remove a three-week-old ₹50,000 entry, but could retype it as ₹500, which
only reached the bell. One window for both doors closes that, and 24 hours is
long enough for "I typed it wrong" and short enough that nothing older moves
without an owner.

⚠ **THE WINDOW IS MEASURED ON WHEN THE ROW WAS TYPED (`created_at`), NEVER ON
THE MONEY DATE**, and that is what stops this breaking the workflow it
protects. `date` / `bill_date` is when the money moved; `created_at` is when
somebody keyed the row. A Supplies Shop bill is keyed when the collector comes
and dated to the delivery day; measured on that date, Office would type it,
mistype it, and be refused their own typo thirty seconds later. The one caller
that passes something else is the settled job card, which passes `paid_date` —
the moment the bill was settled is when it entered the books as money.

⚠ **A REFUSAL NAMES THE ROUTE.** The rule this codebase already follows for a
frozen salary advance is that "a lock says *you cannot* without saying why, and
why is the only part anybody can act on". So the message says how old the row
is and who to ask, and a list that can tell in advance (`is_past_window`) says
so in the row's own menu rather than offering a button that will be refused.

Deliberately NOT covered:

  * **`jobcard_delete`** — already guarded: a card carrying spares, labour or a
    received payment cannot be deleted at all, so a deletable card holds no
    money and there is nothing here to protect.
  * **An unsettled job card** — work in progress is edited over days.
  * **Filling in the price of a part Floor recorded without one** — Office does
    that days later by design; it is the hand-off, not a correction.
  * **`salary_payment_delete`** — `@owner_required` already.
  * **Housekeeping deletes** (master data, unassigned spares) — no money moves,
    and auto-learn restores a master-list name the next time somebody types it.
"""

from datetime import timedelta

from django.utils import timezone

from .decorators import is_owner

#: How many hours after a money row is recorded Office may still change or
#: delete it. One constant, read by the guard and by every message it writes,
#: so the number on screen can never disagree with the number enforced.
OFFICE_WINDOW_HOURS = 24


def is_past_window(stamp):
    """
    Is this row older than Office may change? — the age rule, with no user.

    Split out so a list can ask it per row without holding a user: the age of a
    row is not a question about a person. A missing stamp is never "too old" —
    every column this covers is `auto_now_add` or set on settlement, and
    guessing about a row whose age is unknowable would refuse on no evidence.
    """
    return (stamp is not None
            and timezone.now() - stamp > timedelta(hours=OFFICE_WINDOW_HOURS))


def _how_long_ago(stamp):
    """'30 hours ago' inside two days, '5 days ago' after — never '0 days'."""
    hours = int((timezone.now() - stamp).total_seconds() // 3600)
    if hours < 48:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"


def refusal(user, stamp, what, action='delete', happened='recorded'):
    """
    The reason this user may not change this row, or **None** if they may.

    `what` names the record in the message ("This ₹15,000 payment"), `action`
    is what they tried ('delete' or 'change'), and `happened` is what the stamp
    measures ('recorded', or 'settled' for a job card) — so one sentence shape
    serves every call site and no view writes its own.

    An owner is never refused.
    """
    if is_owner(user) or not is_past_window(stamp):
        return None

    return (
        f"{what} was {happened} {_how_long_ago(stamp)}. Office can change or "
        f"delete money only within {OFFICE_WINDOW_HOURS} hours — ask an owner "
        f"to {action} this one."
    )
