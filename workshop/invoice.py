"""
What an invoice PRINTS — one module, no views, no HTTP.

Same shape as `analysis_engine.py` and `master_data.py`: the rule lives in one
importable place so a second screen (a PDF export, a WhatsApp share, a reprint
from Paid Bills) cannot grow its own slightly-different answer to "what does the
customer see?".

The printed bill is deliberately NOT a transcription of the job card. Four
things differ, and each is a decision rather than a shortcut:

1. **Both spare routes print in one PART NAME section.** `JobCardSpareItem`
   already holds a shop purchase and a warehouse draw in one table, told apart
   by `source` — the Job Card *edits* them as two sections because a draw has no
   shop and no ordering workflow, but a customer has no interest in which shelf
   a part came off. One list, insertion order, one subtotal.

2. **A warehouse draw is named by its CATEGORY, not its product.** `Item.name`
   is the branded SKU the workshop buys ("Castrol Edge 5W-30"); `Category.name`
   is what it is ("Engine Oil"). The bill says what was fitted. Naming the brand
   on a customer document also publishes the workshop's supply chain, which is
   nobody's business but theirs. Shop-bought spares keep their free-text name —
   those are typed per job and are already described the way the customer would
   describe them.

3. **Labour prints without per-line amounts, because there are none.** The
   workshop quotes work as a whole — a customer is told "₹22,300 for the job",
   never a price per line — so Office types one figure into
   `JobCard.labour_amount` and the Jobs section lists only what was done. The
   printed section mirrors that exactly: descriptions, then one SUBTOTAL.
   `JobCardLabourItem.amount` is the column this replaced and is dormant; the
   subtotal here comes from the card, never from summing the lines.

4. **A blank QTY is ONE.** Staff routinely leave the box empty for a single
   part. Before this, blank meant the unit-price column divided by nothing and
   printed ₹0.00 beside a real amount. Blank and a typed 1 now produce byte-for-
   byte identical output, which is the only defensible reading of a field whose
   two states mean the same thing to the person filling it in.

Nothing here is a money source of truth. `grand_total` is the job card's own
denormalized `total_bill_amount`, exactly as the rest of the app reads it; the
two subtotals are the same rows re-added for display, and equal it by
construction (see `JobCard.update_totals`).
"""

from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass
from typing import Optional

from .models import JobCardSpareItem


# How many rows each table is padded out to with blanks. The reference invoice
# is a fixed skeleton: padding is what keeps the footer at the same height on
# every bill, whether it carries three parts or eleven. A list longer than this
# is simply not padded — the minimum never truncates anything.
MIN_JOB_ROWS = 9
MIN_PART_ROWS = 11

ONE = Decimal('1')
CENT = Decimal('0.01')


def effective_quantity(quantity):
    """
    The quantity a bill should bill by.

    Blank means one. So does a zero or a negative, which no form should be able
    to produce for a shop spare and which would otherwise divide the unit-price
    column by nothing — one rule covers all three rather than leaving the last
    two to crash the page.

    Only shop spares can reach here without a quantity: `InventoryDrawForm`
    refuses a draw with an empty one ("Enter how many were taken"), because that
    number moves warehouse stock. So normalising here can never make the printed
    quantity disagree with what came off the shelf.
    """
    if quantity is None or quantity <= 0:
        return ONE
    return quantity


def part_display_name(spare):
    """
    What this part is called on the customer's bill.

    A warehouse draw is named by its category; a shop purchase keeps the name
    Office typed. `item` is the FK behind a draw and is only ever NULL on one
    through a data anomaly — falling back to the stored name keeps a line on the
    bill rather than printing an empty row for a part the customer was charged
    for.
    """
    if spare.source == JobCardSpareItem.SOURCE_INVENTORY and spare.item_id:
        category = getattr(spare.item, 'category', None)
        if category and category.name:
            return category.name
    return spare.spare_part_name or ''


@dataclass(frozen=True)
class JobLine:
    """One line of work. Carries no amount — see decision 3 above."""
    description: str


@dataclass(frozen=True)
class PartLine:
    name: str
    quantity: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]

    @property
    def priced(self):
        """
        Whether this row prints a figure at all.

        Explicit rather than left to the template to test, because the two false
        cases differ: a part with NO price yet prints an empty cell, while one
        genuinely given away prints '₹ 0.00'. A plain truthiness check would
        collapse them and quietly drop the rupee sign off the free part.
        """
        return self.amount is not None


def _unit_price(total_price, quantity):
    """
    The per-unit figure printed beside a part.

    Always DERIVED from the customer total, never read from a stored field.
    Two reasons, and the second is the load-bearing one:

    * `JobCardSpareItem.unit_price` is the workshop's COST per unit — the shop's
      price, or the warehouse average. Printing it on a customer's bill would
      publish the margin on every part.
    * `customer_rate` *is* a customer per-unit price, but only inventory rows
      carry one. Deriving gives the identical answer where it is set
      (`total_price = customer_rate x quantity` is enforced on save) while also
      covering every shop row, so the column is computed one way for all parts
      and `qty x unit` always reconciles to the amount beside it.

    A row with no price yet prints nothing at all, in both columns — the
    reference invoice does exactly this for parts fitted but not yet costed.
    """
    if total_price is None:
        return None
    return (total_price / quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def build_invoice(jobcard):
    """
    Everything the invoice template renders, derived from one job card.

    Pure: it reads the job card's already-loaded relations and returns plain
    values, so the whole printed document is testable without a request. The
    caller is expected to have prefetched `labours` and `spares` (with
    `item__category` selected) — nothing here works around a missing prefetch,
    it just costs queries.
    """
    # The lines say what was done; the charge is one figure on the card. They are
    # read from two places on purpose — see decision 3 in the module docstring.
    job_lines = [
        JobLine(description=labour.job_description or '')
        for labour in jobcard.labours.all()
    ]
    job_subtotal = jobcard.labour_amount or Decimal('0')

    part_lines = []
    part_subtotal = Decimal('0')
    for spare in jobcard.spares.all():
        quantity = effective_quantity(spare.quantity)
        part_lines.append(PartLine(
            name=part_display_name(spare),
            quantity=quantity,
            unit_price=_unit_price(spare.total_price, quantity),
            amount=spare.total_price,
        ))
        part_subtotal += spare.total_price or Decimal('0')

    return {
        'job_lines': job_lines,
        'job_pad': range(max(0, MIN_JOB_ROWS - len(job_lines))),
        'job_subtotal': job_subtotal,

        'part_lines': part_lines,
        'part_pad': range(max(0, MIN_PART_ROWS - len(part_lines))),
        'part_subtotal': part_subtotal,

        # The denormalized column, same as every other screen reads. Equal to
        # job_subtotal + part_subtotal by construction; deliberately not
        # recomputed here, so a drift between the two would show on the page as
        # a bill that does not add up rather than being papered over.
        'grand_total': jobcard.total_bill_amount or Decimal('0'),
    }
