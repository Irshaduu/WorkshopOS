"""
How long a money row stays deletable by OFFICE.

One question, one implementation: *has this record been in the books long
enough that removing it should be an owner's decision?*

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

  * deleting something recorded an hour ago is a CORRECTION — frequent, cheap,
    and the money is still fresh in everybody's head;
  * deleting something recorded six weeks ago is ANOMALOUS — that period has
    been reported on, an owner has read the Profit page against it, and a
    shop's balance was settled on it.

So Office keeps the first and an owner takes the second. It is an ESCALATION,
never a wall: no new mechanism, no approval queue, and the owners are already
the people the CRITICAL alert goes to.

⚠ **THE WINDOW IS MEASURED ON `created_at`, NEVER ON THE MONEY DATE, and that
is what stops this feature breaking the workflow it protects.** Every model it
covers carries both columns, and the two answer different questions.
`date` / `bill_date` is when the money moved; `created_at` is when somebody
keyed the row. Back-dating is NORMAL here — a Supplies Shop delivers and keeps
its own book, and the bill is only entered when the collector comes at month
end; the Cashbook, the spare-shop and the fleet payment forms all have a date
box for exactly this. Measured on the money date, Office would key a bill
back-dated six weeks, mistype it, and be refused permission to delete their own
typo thirty seconds later — the precise case this exists to keep easy.
`created_at` asks the right question: how long has this been sitting in the
books. It is also why those columns were kept when the money dates landed.

⚠ **A REFUSAL NAMES THE ROUTE, and that is why the button is still offered.**
The rule this codebase already follows for a frozen salary advance is that "a
lock says *you cannot* without saying why, and why is the only part anybody can
act on". Hiding the control would say something false as well — that the record
cannot be deleted at all, when an owner can delete it. So the control stays,
the POST is refused, and the message says how old the row is and who to ask.

Deliberately NOT covered:

  * **`jobcard_delete`** — already guarded: a card carrying spares, labour or a
    received payment cannot be deleted at all, so a deletable card holds no
    money and there is nothing here to protect. A window would be friction
    buying nothing.
  * **`salary_payment_delete`** — `@owner_required` already.
  * **Housekeeping deletes** (master data, unassigned spares) — no money moves,
    and auto-learn restores a master-list name the next time somebody types it.
"""

from django.utils import timezone

from .decorators import is_owner

#: How many days back Office may still delete a money row it recorded.
#: Recorded today is 0 days old, so a row stays deletable through its 7th day
#: and is refused on the 8th. Generous for the "I keyed it wrong" case, which
#: is caught in minutes or the next morning; a correction found at month-end
#: reconciliation lands past it, and that is the case worth an owner's eyes,
#: because it changes a period they have already read.
#:
#: One constant, read by the guard and by every message it writes, so the
#: number on screen can never disagree with the number enforced.
OFFICE_DELETE_WINDOW_DAYS = 7


def age_in_days(created_at):
    """
    How many CALENDAR days ago this row was recorded, in the workshop's own
    timezone.

    `timezone.localtime()` rather than a bare `.date()`: the server can run in
    UTC while the business runs in IST, so a row keyed at 01:00 on a Kerala
    morning is stored under the previous UTC day and would read a day older
    than it is.
    """
    return (timezone.localdate() - timezone.localtime(created_at).date()).days


def refusal(user, created_at, what):
    """
    The reason this user may not delete this row, or **None** if they may.

    `what` names the record in the message ("this ₹15,000 payment"), so one
    sentence shape serves every call site and no view writes its own.

    An owner is never refused. A missing `created_at` is never refused either —
    every column this covers is `auto_now_add`, so it cannot legitimately be
    None, and guessing "too old" about a row whose age is unknowable would
    block a delete on no evidence.
    """
    if is_owner(user) or created_at is None:
        return None

    days = age_in_days(created_at)
    if days <= OFFICE_DELETE_WINDOW_DAYS:
        return None

    when = "yesterday" if days == 1 else f"{days} days ago"
    return (
        f"{what} was recorded {when}. Office can delete something recorded in "
        f"the last {OFFICE_DELETE_WINDOW_DAYS} days — ask an owner to remove "
        f"this one."
    )
