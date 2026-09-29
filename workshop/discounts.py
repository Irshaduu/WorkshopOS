"""
workshop/discounts.py — the rules for RECORDING A DISCOUNT, one implementation
for every ledger that takes one.

A DISCOUNT IS A PAYMENT WITH NO CASH (2026-09-29, the owners' call). It settles
what an account owes exactly the way a payment does, and moves no money:

    spare shop / Supplies Shop   the shop let us off   → profit UP, on its date
    Fleet Account                we let the fleet off  → on the cards it settles

So it is read the way a payment is read — the same money rules, the same
date rules — plus the one rule a payment does not need: it can never be more
than what is still owed. A payment larger than the balance is an overpayment
somebody really made; a discount larger than the balance is a typo that would
turn a debt into a credit nobody gave.

The views keep their own locking, writing and alerts; this module only decides
whether what was typed may be written.
"""
from .money import fit_text, parse_money
from .money_dates import is_future, posted_date, too_far_back


def read_discount(request, model, owed):
    """
    Read a posted discount against `model` (its `amount` and `note` columns
    bound the input) and what is still `owed`.

    Returns `(amount, date, note, None)` when it may be written, or
    `(None, None, None, message)` naming what is wrong.
    """
    # `<= 0` as well as None: `parse_money` refuses a zero BEFORE it quantises,
    # so `0.004` comes back as `0.00` — the rule every money view here follows.
    amount = parse_money(request.POST.get('amount') or '0', model, 'amount')
    if amount is None or amount <= 0:
        return None, None, None, "Enter a valid discount amount."

    if owed <= 0:
        return None, None, None, "Nothing is owed, so there is nothing to discount."
    if amount > owed:
        return None, None, None, (
            f"A discount can't be more than the ₹{owed:,.0f} still owed.")

    # The day it was given, through the one rule for every money date: never
    # forward, and Office no further back than `money_dates.BACKDATE_DAYS`.
    on = posted_date(request.POST.get('date'))
    if is_future(on):
        return None, None, None, "A discount can't be dated in the future."
    blocked = too_far_back(on, request.user, "A discount")
    if blocked:
        return None, None, None, blocked

    # Blank stores NULL: nobody wrote a note is a different fact from an empty
    # one. Trimmed to the column rather than 500ing on Postgres.
    note = fit_text((request.POST.get('note') or '').strip(), model, 'note') or None
    return amount, on, note, None
