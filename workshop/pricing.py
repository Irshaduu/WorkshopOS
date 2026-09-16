"""
Suggested customer prices — the numbers, and the one rule for reading a markup.

WHAT THIS MODULE IS NOT
-----------------------
**It never works out a price.** The job card suggests a customer price in the
BROWSER (`static/js/pricing-core.js`) and the server stores only what was posted.
That split is the whole safety of the feature, and it is deliberate:

  * Floor opens most job cards and adds parts without prices. A price computed
    in `JobCardSpareItem.save()` would bill those parts before Office ever
    looked at them.
  * `settlement.py` chases "no customer price". A price the server invented
    would silence the one check that catches an unpriced part.
  * An unlocked edit of a settled card would reprice parts the customer has
    already paid for.
  * `inventory/costing.py` rewrites a draw's cost when a late supplier bill
    lands. A price that followed cost would move old bills with it.

So a suggestion reaches a bill only when a person presses Save with it on
screen. `test_the_server_never_prices_a_part` pins that down, so this module
cannot quietly grow a `price_for()` later.

MARKUP, NEVER MARGIN
--------------------
40% here means ₹1,000 of cost becomes ₹1,400 — profit ÷ COST. As a margin
(profit ÷ price) the same part is 28.6%, which is the figure Deep Analysis
prints as "Margin %". Both are true; the badge on the job card says "Markup"
so the two screens are not read as disagreeing.
"""

import re

#: Every spare part (bought from a spare shop) is suggested at this markup, and
#: a new stock product starts at it. Fixed here on the owners' decision — one
#: line to change, and there is deliberately no settings screen for it.
DEFAULT_MARKUP_PERCENT = 40

#: Below this the job card's markup badge turns yellow. Below zero it is red.
LOW_MARKUP_PERCENT = 20

#: The ceiling on a product's markup. Three digits: beyond that is a slip of the
#: keyboard rather than a price policy, and it keeps the badge narrow.
MAX_MARKUP_PERCENT = 999

# ASCII digits only. Python's `\d` also matches Arabic-Indic and Devanagari
# digits, and `int()` reads those happily — a pasted "४०" would be stored as 40
# with nothing on screen saying where the number came from.
_WHOLE = re.compile(r'[0-9]{1,3}')


def parse_markup(raw):
    """
    A posted markup as a whole number from 0 to MAX_MARKUP_PERCENT, or None.

    REFUSED, NEVER CLAMPED OR DEFAULTED — the rule everywhere a value is typed in
    this app. "40.5", "-5", "1000", "40%", "abc", blank and None all come back as
    None, and the caller says so. A fallback would save a markup nobody typed.

    Whole numbers only, on purpose: the browser works a suggested price out in
    whole paise with integer arithmetic, and a whole percent keeps that exact.
    """
    text = '' if raw is None else str(raw).strip()
    if not _WHOLE.fullmatch(text):
        return None
    value = int(text)
    if value > MAX_MARKUP_PERCENT:
        return None
    return value
