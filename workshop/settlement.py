"""
What is still missing before a bill should be settled — one module, no views.

Settling is the last thing that happens to a job card and the only irreversible
one: a walk-in has exactly one payment event, so the moment Office types a
figure the card is marked PAID and whatever was not handed over becomes a
permanent discount (see CLAUDE.md). Every field that was going to be filled in
has to be filled in *before* that, because afterwards the Financial Lock stands
between the card and anyone correcting it.

Nothing here blocks. It is a list of things a person would want to know they are
about to skip, handed to the settle dialog so it can say them out loud. The
owner may well settle anyway — a customer standing at the counter does not wait
for a mechanic to tick a box — and the decision stays theirs.

Same split as `invoice.py`: pure functions over a job card, so the rules are
testable without a request and a second settlement surface cannot grow its own
slightly different idea of "ready".

**The two routes are checked differently, and that is the `source` rule again.**
A shop spare carries an ordering workflow — a shop, a price, an ordered date, a
received date, a status — and each of those is a real thing that can be
forgotten. A warehouse draw carries none of them: it came off the shelf already
fitted, its cost is derived from the supplier bills, and its `status` column is
meaningless. Checking a draw for a received date would report a problem that
cannot exist and cannot be fixed, which is how a checklist teaches people to
click past it.
"""

from dataclasses import dataclass
from decimal import Decimal

from .models import JobCardSpareItem


ZERO = Decimal('0')


@dataclass(frozen=True)
class Gap:
    """
    One thing that is not filled in — as a LABEL, not a sentence.

    This is read in the two seconds between agreeing a price and taking the
    money, so it is built to be scanned rather than read: `label` is the short
    phrase ("No mechanic", "Spare parts"), and `tags` are the chips beside it
    naming exactly which parts of it are missing.

    The first version wrote a full explanatory sentence per gap. Every sentence
    was true and the whole list was a wall of prose four paragraphs deep, which
    on the one screen where somebody is standing at a counter with a customer is
    the same as saying nothing.
    """
    key: str
    label: str
    tags: tuple = ()


def settlement_gaps(jobcard):
    """
    Everything unfilled on this card, in the order someone would fix it.

    Reads `jobcard.spares`, `jobcard.concerns` and `jobcard.labours` through the
    relation, so the caller should have prefetched all three — the invoice view
    does.

    Ordered vehicle-first, then work, then parts, because that is the order the
    job card itself is laid out in and the list is read as a route down it.

    **The parts checks are GROUPED into one row per section, not one row per
    check.** Four separate lines all beginning "Spare parts" is four times the
    height for one fact — that the parts section is unfinished — and it buries
    the two lines above it that are about something else entirely.
    """
    gaps = []

    # ---- The card's own header -------------------------------------------
    if not jobcard.lead_mechanic_id:
        gaps.append(Gap('mechanic', 'No mechanic assigned'))

    if not (jobcard.mileage or '').strip():
        gaps.append(Gap('mileage', 'No mileage'))

    # ---- The work --------------------------------------------------------
    # Named by STATUS rather than by wording. `concern_text` is a TextField and
    # staff write sentences into it; quoting one costs three lines and still
    # only describes one of them. The status is the thing being asked about.
    concerns = list(jobcard.concerns.all())
    unfixed = [c for c in concerns if c.status != 'FIXED']
    if unfixed:
        counts = {}
        for concern in unfixed:
            counts[concern.get_status_display()] = counts.get(concern.get_status_display(), 0) + 1
        gaps.append(Gap(
            'concerns',
            'Customer concerns not fixed',
            tuple(f"{number} {name}" for name, number in sorted(counts.items())),
        ))

    # The labour charge. Reported only when work was RECORDED and not priced —
    # a card with no job lines is a parts-only bill, where ₹0 labour is the
    # correct answer and saying so on every one of them is how this list would
    # come to be clicked past without being read.
    #
    # `.all()` rather than `.exists()`: the caller prefetches this relation, and
    # exists() ignores the prefetch cache and issues a fresh query.
    if list(jobcard.labours.all()) and (jobcard.labour_amount or ZERO) <= ZERO:
        gaps.append(Gap('labour', 'No labour amount'))

    # ---- The parts -------------------------------------------------------
    spares = list(jobcard.spares.all())
    shop_spares = [s for s in spares if s.source == JobCardSpareItem.SOURCE_SHOP]
    draws = [s for s in spares if s.source == JobCardSpareItem.SOURCE_INVENTORY]

    # Shop parts carry the whole ordering workflow, so they can be missing five
    # different things; each becomes a chip on one row.
    shop_tags = []
    if any(not s.shop_id for s in shop_spares):
        shop_tags.append('No shop')
    if any(not s.ordered_date for s in shop_spares):
        shop_tags.append('No order date')
    if any(s.status != 'RECEIVED' for s in shop_spares):
        shop_tags.append('Not received')
    if any(not s.received_date for s in shop_spares):
        shop_tags.append('No received date')
    if any(s.unit_price is None for s in shop_spares):
        shop_tags.append('No shop price')
    if any(s.total_price is None for s in shop_spares):
        shop_tags.append('No customer price')
    if shop_tags:
        gaps.append(Gap('spares', 'Spare parts', tuple(shop_tags)))

    # A warehouse draw has no shop, no order and no arrival — asking about any
    # of them would report a problem that cannot exist and cannot be fixed,
    # which is how a checklist teaches people to click past it. The customer
    # price is the one figure it does carry, because that is what bills.
    if any(s.total_price is None for s in draws):
        gaps.append(Gap('inventory', 'Inventory items', ('No customer price',)))

    return gaps


def settlement_readiness(jobcard):
    """
    The whole pre-flight, as the settle dialog needs it.

    `completed` is kept apart from the gaps rather than folded in with them, and
    the split is the point: a missing mileage is something to note, while an
    uncompleted card is a *contradiction* — money is being taken for a car the
    system still shows as being worked on, and it is the one item here with a
    fix that can be applied from this screen. It gets its own sentence and its
    own button ("Complete & settle"); everything else gets a line in a list.
    """
    gaps = settlement_gaps(jobcard)
    return {
        'is_completed': bool(jobcard.completed),
        'gaps': gaps,
        # True when the dialog has something to say. When it is False the settle
        # button opens the payment box directly, exactly as it always did —
        # a confirmation that appears on a card with nothing wrong is a
        # confirmation that stops being read.
        'needs_confirmation': bool(gaps) or not jobcard.completed,
    }
