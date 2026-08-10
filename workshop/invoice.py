"""
What a CUSTOMER DOCUMENT prints — one module, no views, no HTTP.

Two documents leave this workshop: the INVOICE for work that was done, and the
ESTIMATE for work being quoted. `build_invoice()` and `build_estimate()` are the
whole public surface, and they live in one file on purpose.

Same shape as `analysis_engine.py` and `master_data.py`: the rule lives in one
importable place so a second screen (a PDF export, a WhatsApp share, a reprint
from Paid Bills) cannot grow its own slightly-different answer to "what does the
customer see?". The estimate is the sharpest case of that — it is handed over
first and the invoice follows it, so if the two ever disagreed about how a
quantity, a blank price or a labour charge reads, the customer is the one who
finds out. They share `effective_quantity`, `derive_unit_price`, `PartLine`, `JobLine`
and the row-padding constants for exactly that reason. Do not fork them.

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


# Characters that are not legal in a filename on Windows, and are mangled or
# silently rewritten on the others. Stripped rather than substituted: a
# registration number containing one of these is a data anomaly, and a bill
# named "KL11 AJ 2266" reads better than one named "KL11_AJ_2266".
_FILENAME_UNSAFE = str.maketrans({character: None for character in '/\\:*?"<>|\r\n\t'})

# Long enough for any real brand + model + plate + number, short enough that no
# filesystem or mail client truncates it into something ambiguous.
MAX_TITLE_LENGTH = 120


def document_title(record, number, fallback):
    """
    What the browser tab says — and therefore what the saved PDF is called.

    Both sheets are printed with the browser's own Print → Save as PDF, and
    every browser suggests `document.title` as the filename. So the title is not
    decoration: it is the name of the file an owner ends up with in a folder of
    hundreds. "Invoice — Formula D" told them nothing there; "Audi A4 KL11 AJ
    2266 (JB-26-037)" is searchable by car, by plate and by document number at
    once.

    Everything is optional except the result. An estimate may legitimately carry
    no make, no model and no registration (most of the quote form is optional by
    design), so the parts that exist are joined and the ones that do not are
    dropped — never printed as an empty gap or a "None". If the car cannot be
    named at all the document number stands alone, and if there is no number
    either the caller's `fallback` word does, because a blank tab title makes a
    browser fall back to the URL.
    """
    parts = [
        ' '.join(str(value).split())
        for value in (
            getattr(record, 'brand_name', '') or '',
            getattr(record, 'model_name', '') or '',
            getattr(record, 'registration_number', '') or '',
        )
    ]
    car = ' '.join(part for part in parts if part)

    number = ' '.join(str(number or '').split())
    title = f"{car} ({number})" if car and number else (car or number or fallback)

    return title.translate(_FILENAME_UNSAFE).strip()[:MAX_TITLE_LENGTH]


@dataclass(frozen=True)
class JobLine:
    """One line of work. Carries no amount — see decision 3 above."""
    description: str


@dataclass(frozen=True)
class PartLine:
    name: str
    #: The quantity the MONEY is computed from — blank already resolved to 1.
    quantity: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    #: What the QTY column prints. `None` prints an empty cell.
    #:
    #: Separate from `quantity` because the two documents answer differently,
    #: and both answers are right. An INVOICE bills a fitted part, so a blank
    #: box means one and prints as 1 — a fact. An ESTIMATE may be written before
    #: anyone has counted, so a blank box means "not decided yet" and printing 1
    #: would state something the workshop has not established. The arithmetic is
    #: identical either way; only the cell differs.
    display_quantity: Optional[Decimal] = None

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


def derive_unit_price(total_price, quantity):
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
            # A bill always prints a quantity: the part was fitted, so a blank
            # box means one and 1 is the true figure. See PartLine.
            display_quantity=quantity,
            unit_price=derive_unit_price(spare.total_price, quantity),
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

        'document_title': document_title(jobcard, jobcard.bill_number, 'Invoice'),
    }


def build_estimate(estimate):
    """
    Everything the estimate template renders, derived from one Estimate.

    Built from the same parts as the invoice, and it prints the same in every
    respect but TWO. Both exceptions come from one fact: a bill records work
    that happened, an estimate describes work that has not. So a blank box on a
    bill is a fact too obvious to type, while a blank box on an estimate is
    something nobody has decided yet — and printing a number for it would put a
    figure on the page that no one chose.

      * **QTY prints only what was typed; blank stays blank.** `quantity` is
        still resolved to 1 for the arithmetic, so the money is identical — only
        the cell is empty. The bill does the opposite and prints 1, which is the
        documented rule there and stays. (There is no case where an estimate can
        contradict the bill that follows it: nothing carries over, the job card
        is typed fresh.)
      * **UNIT PRICE prints only when a rate was actually entered.** The bill
        DERIVES it from the total every time, which is right there because every
        billed part has a real quantity. Here, deriving would present the
        workshop's own arithmetic as a quoted rate, and on a row with no
        quantity it would divide by a 1 nobody agreed to. When `customer_rate`
        IS set, `amount` was computed from it on save, so `qty x unit` still
        reconciles exactly.

    Everything else is shared verbatim: one parts list, labour as descriptions
    plus a single subtotal, an unpriced part printing an empty cell while a free
    one prints ₹0.00, and the same row padding.
    """
    job_lines = [
        JobLine(description=line.description or '')
        for line in estimate.job_lines.all()
    ]
    job_subtotal = estimate.labour_amount or Decimal('0')

    part_lines = []
    part_subtotal = Decimal('0')
    for part in estimate.parts.all():
        part_lines.append(PartLine(
            name=part.name or '',
            # Blank still counts as one for the money...
            quantity=effective_quantity(part.quantity),
            # ...but the column shows only what somebody typed.
            display_quantity=part.quantity,
            unit_price=part.customer_rate,
            amount=part.amount,
        ))
        part_subtotal += part.amount or Decimal('0')

    return {
        'job_lines': job_lines,
        'job_pad': range(max(0, MIN_JOB_ROWS - len(job_lines))),
        'job_subtotal': job_subtotal,

        'part_lines': part_lines,
        'part_pad': range(max(0, MIN_PART_ROWS - len(part_lines))),
        'part_subtotal': part_subtotal,

        # The denormalized column, same reasoning as the invoice's: a drift
        # between it and the two subtotals should show on the page rather than
        # be quietly recomputed away.
        'grand_total': estimate.total_amount or Decimal('0'),

        'document_title': document_title(estimate, estimate.estimate_number, 'Estimate'),
    }
