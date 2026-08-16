"""
What is still unfilled on a job card — one module, no views, TWO readers.

Settling is the last thing that happens to a job card and the only irreversible
one: a walk-in has exactly one payment event, so the moment Office types a
figure the card is marked PAID and whatever was not handed over becomes a
permanent discount (see CLAUDE.md). Every field that was going to be filled in
has to be filled in *before* that, because afterwards the Financial Lock stands
between the card and anyone correcting it.

Two screens ask this module the same question and must get the same answer:

  * the settle dialog on the invoice, BEFORE the money moves — "you are about
    to close this card, here is what nobody filled in";
  * the "Billed but not filled" container on the Live Report, AFTER it — "these
    cards were billed with holes in them, go and fix them".

They are the two halves of one rule, so there is one implementation of it. A
second copy would drift, and it would drift exactly where it matters: a card
the dialog waved through appearing on the chase list, or the reverse.

Nothing here blocks. It is a list of things a person would want to know they
are about to skip. The owner may well settle anyway — a customer standing at
the counter does not wait for a mechanic to tick a box — and the decision stays
theirs.

**The two part routes are checked differently, and that is the `source` rule
again.** A shop spare carries an ordering workflow — a shop, a price, two dates
— and each of those is a real thing that can be forgotten. A warehouse draw
carries none of them: it came off the shelf already fitted, its cost is derived
from the supplier bills, and its `status` column is meaningless. Chasing a draw
for a received date would report a problem that cannot exist and cannot be
fixed, which is how a checklist teaches people to click past it. The one check
spanning both routes is the customer price, because that is the figure that
bills whichever shelf the part came off.
"""

from dataclasses import dataclass
from decimal import Decimal

from .models import JobCardSpareItem


ZERO = Decimal('0')

# The chip wordings, named once. Both surfaces render these strings verbatim, so
# they are the vocabulary of the feature rather than incidental copy — a chip
# that reads "Shop Price" on one screen and "No shop price" on the other is two
# screens describing one gap in two voices.
#
# They are LABELS, not sentences: this is read in the two seconds between
# agreeing a price and taking the money, so it is built to be scanned. An
# earlier version explained each gap in a sentence and ran four paragraphs deep,
# which on that screen is the same as saying nothing.
MILEAGE = 'Mileage'
MECHANIC = 'Mechanic'
JOB_AMOUNT = 'Job Amount'

SHOP = 'Shop'
DATES = 'Dates'
SHOP_PRICE = 'Shop Price'
CUSTOMER_PRICE = 'Customer Price'

#: What a part with no name is called. `spare_part_name` is nullable, and a row
#: with an empty headline reads as something that failed to load.
UNNAMED = 'Unnamed part'


@dataclass(frozen=True)
class PartGap:
    """One part, and the chips naming what is not filled in on it."""
    name: str
    tags: tuple = ()


@dataclass(frozen=True)
class ConcernGap:
    """
    One concern nobody has marked fixed.

    Carries the wording as well as the status, deliberately, and this reverses
    an earlier decision. The old settle dialog named concerns by status alone
    ("1 Working") because quoting a TextField into a dialog read in two seconds
    cost three lines per concern. The chase list is read differently — somebody
    is deciding which car to walk over to — and there the wording is the whole
    point. Both surfaces clamp it in CSS rather than truncating it here, so the
    stored text is never what gets shortened.
    """
    text: str
    status: str


@dataclass(frozen=True)
class Unfilled:
    """
    Everything unfilled on one card, grouped the way both screens draw it.

    Four groups, in the order somebody would work down the job card fixing
    them: the card's own header, then the concerns, then the two part sections.
    """
    card: tuple = ()          # MILEAGE / MECHANIC / JOB_AMOUNT
    concerns: tuple = ()      # ConcernGap
    inventory: tuple = ()     # PartGap — customer price only, see the module docstring
    spares: tuple = ()        # PartGap

    def __bool__(self):
        return bool(self.card or self.concerns or self.inventory or self.spares)

    @property
    def count(self):
        """
        How many things are wrong — counted in CHIPS, not in rows.

        A spare missing a shop, both dates and both prices is four problems, not
        one, and the headline number is what tells an owner whether this is a
        typo or a card nobody filled in at all. Inventory rows carry exactly one
        chip each (the customer price), so counting them by row is the same
        number either way.
        """
        return (
            len(self.card)
            + len(self.concerns)
            + len(self.inventory)
            + sum(len(part.tags) for part in self.spares)
        )


def unfilled(jobcard):
    """
    Everything unfilled on this card, in the order someone would fix it.

    Reads `jobcard.spares`, `jobcard.concerns` and `jobcard.labours` through the
    relation, so the caller should have prefetched all three — both callers do.
    """
    # ---- The card's own header -------------------------------------------
    header = []
    if not (jobcard.mileage or '').strip():
        header.append(MILEAGE)
    if not jobcard.lead_mechanic_id:
        header.append(MECHANIC)

    # The labour charge. Reported only when work was RECORDED and left unpriced
    # — a card with no job lines is a parts-only bill, where ₹0 labour is the
    # correct answer, and saying so on every one of them is how this list would
    # come to be clicked past without being read.
    #
    # `.all()` rather than `.exists()`: the caller prefetches this relation, and
    # exists() ignores the prefetch cache and issues a fresh query per card —
    # which on the chase list is one query per row.
    if list(jobcard.labours.all()) and (jobcard.labour_amount or ZERO) <= ZERO:
        header.append(JOB_AMOUNT)

    # ---- The work --------------------------------------------------------
    concerns = tuple(
        ConcernGap(text=(c.concern_text or '').strip(), status=c.get_status_display())
        for c in jobcard.concerns.all()
        if c.status != 'FIXED'
    )

    # ---- The parts -------------------------------------------------------
    inventory = []
    spares = []
    for spare in jobcard.spares.all():
        # A part is named here by `spare_part_name` — for a warehouse draw that
        # is the BRANDED SKU ("Castrol Edge 5W-30"), deliberately, and NOT
        # `invoice.part_display_name`'s category. Both surfaces reading this are
        # internal: somebody is about to go and find that row on the job card,
        # where the picker box shows the product. The category is what the
        # CUSTOMER reads, and using it here would name a row by a word that
        # appears nowhere on the screen being sent to.
        name = (spare.spare_part_name or '').strip() or UNNAMED

        if spare.source == JobCardSpareItem.SOURCE_INVENTORY:
            # A draw has no shop, no order and no arrival. Only the figure that
            # bills the customer is chased.
            if spare.total_price is None:
                inventory.append(PartGap(name=name, tags=(CUSTOMER_PRICE,)))
            continue

        tags = []
        if not spare.shop_id:
            tags.append(SHOP)
        # The two dates are chased as ONE chip, the same pairing the job card's
        # own date control uses: a spare is finished when it has been ordered
        # AND received, so half-filled is still incomplete. Which of the two is
        # missing is answered by opening the panel, not by this list.
        if not spare.ordered_date or not spare.received_date:
            tags.append(DATES)
        if spare.unit_price is None:
            tags.append(SHOP_PRICE)
        if spare.total_price is None:
            tags.append(CUSTOMER_PRICE)
        if tags:
            spares.append(PartGap(name=name, tags=tuple(tags)))

    return Unfilled(
        card=tuple(header),
        concerns=concerns,
        inventory=tuple(inventory),
        spares=tuple(spares),
    )


def settlement_readiness(jobcard):
    """
    The whole pre-flight, as the settle dialog needs it.

    `completed` is kept apart from the gaps rather than folded in with them, and
    the split is the point: a missing mileage is something to note, while an
    uncompleted card is a *contradiction* — money is being taken for a car the
    system still shows as being worked on — and it is the one item here with a
    fix that can be applied from that screen. It gets its own line and its own
    button ("Complete & settle"); everything else gets a row in the list.

    The split is also what decides the dialog's COLOUR. An uncompleted card on
    its own is a question ("are you sure?") and wears the amber frame it always
    had. Anything actually unfilled is a warning about data that is about to be
    locked, and turns the frame red. One flag, read by the template, so the two
    cannot come to disagree about which is which.
    """
    holes = unfilled(jobcard)
    return {
        'is_completed': bool(jobcard.completed),
        'unfilled': holes,
        # True when the dialog has something to say. When it is False the settle
        # button opens the payment box directly, exactly as it always did — a
        # confirmation that appears on a card with nothing wrong is a
        # confirmation that stops being read.
        'needs_confirmation': bool(holes) or not jobcard.completed,
        # Red when data is unfilled, amber when the only thing to say is that
        # the car has not been marked Completed.
        'is_critical': bool(holes),
    }
