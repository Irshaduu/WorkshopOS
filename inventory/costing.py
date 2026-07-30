"""
Warehouse cost math — what a litre of oil on the shelf actually cost us.

Pure functions over a date-ordered replay, deliberately holding no view logic,
the same isolation `workshop/analysis_engine.py` uses for the money math it
owns. Nothing here touches `Item.current_stock`; the stock signals do that.

WHY WEIGHTED AVERAGE AND NOT FIFO
---------------------------------
An item is stocked from several supplier shops at different prices — 2 L at
₹1200 from one, 5 L at ₹1000 from another. FIFO would cost the next 4 L drawn
as 2×1200 + 2×1000 = ₹4,400; the weighted average costs it 4 × ₹1057.14 =
₹4,228.57. The two never disagree about what the 7 L cost in total (₹7,400) —
only about which month the cost lands in, and over a year they converge.

The average was chosen for two structural reasons, not just the 3.9% gap:

  • Stock is allowed to go negative (a mechanic records a part they already
    physically took, before the supplier bill is entered — see the clamp note
    in inventory/signals.py). FIFO has no layer to draw from in that state and
    would need uncosted allocations retro-costed when the late bill lands.
    An average simply persists.
  • Restock bills can be edited and deleted. Under FIFO that re-costs every
    consumption which drew from the changed layer, cascading. Here it
    recomputes one number.

Per-batch cost is still recorded forever on `SupplierRestockItem`, so true
FIFO can be reconstructed from history later if it is ever wanted. Choosing
the average today forecloses nothing.

WHY ALWAYS A FULL REPLAY
------------------------
A moving average is path-dependent: it cannot be reversed incrementally, so an
edited or deleted restock bill cannot be "un-averaged". Rather than keep both a
fast incremental path and a slow correcting one — two implementations of one
number, free to disagree — there is only the replay. At this workshop's volume
(a handful of products, a few hundred receipts) it costs milliseconds, and it
is deterministic: the same rows always produce the same average.

Receipts move the average; draws do not (issuing stock at the average leaves
the average unchanged). Draws are replayed anyway, because the *stock level* at
the moment of a receipt decides which rule below applies.
"""

from decimal import Decimal

MONEY = Decimal('0.01')
ZERO = Decimal('0')


def new_average_cost(stock_before, avg_before, qty_in, unit_cost_in):
    """
    The weighted-average unit cost after receiving `qty_in` units at
    `unit_cost_in`, given `stock_before` units already on hand at `avg_before`.

    Three cases where the textbook formula does not apply:

      • qty_in <= 0 — nothing received, nothing to re-average.
      • stock_before <= 0 — the shelf was empty, or overdrawn into the negative.
        Every unit now on hand came from this receipt, so the average is simply
        the incoming price. (Blending a negative balance in would produce a
        figure above every price ever paid.)
      • avg_before <= 0 — stock on hand of unknown cost, e.g. opening stock
        entered before any bill. There is no honest way to weight against "no
        information", so the whole pool adopts the first price actually known.
    """
    qty_in = Decimal(str(qty_in or 0))
    if qty_in <= ZERO:
        return Decimal(str(avg_before or 0))

    unit_cost_in = Decimal(str(unit_cost_in or 0))
    stock_before = Decimal(str(stock_before or 0))
    avg_before = Decimal(str(avg_before or 0))

    if stock_before <= ZERO or avg_before <= ZERO:
        return unit_cost_in

    total_value = (stock_before * avg_before) + (qty_in * unit_cost_in)
    total_qty = stock_before + qty_in
    return total_value / total_qty


def cost_events(item):
    """
    Every stock movement for this product as (date, kind, quantity, unit_cost),
    ordered oldest-first. `kind` is 0 for a receipt and 1 for a draw, which also
    breaks same-day ties in the only sensible direction: stock is received
    before it can be used.

    Each stream is dated by its own natural date — a receipt by its bill date, a
    draw by the job card's admitted date — matching the dating rule the analysis
    engine follows.
    """
    from workshop.models import JobCardSpareItem

    events = []

    for ri in item.restock_items.select_related('bill'):
        # `effective_unit_price`, not `per_unit_price`: a bill-level discount is
        # part of what the stock cost, so it belongs in the average.
        events.append((ri.bill.bill_date, 0,
                       Decimal(str(ri.quantity or 0)), ri.effective_unit_price, None))

    draws = (
        JobCardSpareItem.objects
        .filter(item=item,
                source=JobCardSpareItem.SOURCE_INVENTORY,
                job_card__isnull=False,
                job_card__is_deleted=False)
        .values_list('job_card__admitted_date', 'quantity', 'pk', 'unit_price')
    )
    for admitted_date, qty, pk, unit_price in draws:
        # The draw carries its pk and whether it is still uncosted, so the replay
        # can fill in a price that was not known when it happened.
        events.append((admitted_date, 1, Decimal(str(qty or 0)),
                       None, (pk, unit_price)))

    events.sort(key=lambda e: (e[0], e[1]))
    return events


def _replay(item):
    """
    Walk this product's whole history once.

    Returns `(final_average, corrections)` where `corrections` maps the pk of
    each draw whose stored cost differs from the replay to what it should be.
    """
    stock = ZERO
    avg = ZERO
    backfill = {}

    for _date, kind, qty, unit_cost, payload in cost_events(item):
        if kind == 0:
            avg = new_average_cost(stock, avg, qty, unit_cost)
            stock += qty
        else:
            pk, existing_price = payload
            # Every draw is priced at the running average AS AT ITS OWN DATE.
            # A later receipt cannot reach back and change that, because the
            # replay is date-ordered — which is precisely why re-deriving is safe.
            if avg > ZERO:
                want = avg.quantize(MONEY)
                if existing_price != want:
                    backfill[pk] = want
            stock -= qty

    return avg.quantize(MONEY), backfill


def average_cost_for(item):
    """Replay this product's whole history and return its current average cost.

    Full precision is carried through the replay and rounded only once at the
    end, so a long history cannot accumulate rounding drift.
    """
    return _replay(item)[0]


def _apply_draw_costs(corrections):
    """
    Write each draw's cost as the replay says it should be.

    THE WORKFLOW THIS EXISTS FOR
    ----------------------------
    A Supplies Shop delivers stock and keeps its own book; the workshop enters the
    bill only when the collector arrives at month end. Parts are fitted to cars for
    weeks before the system is told what they cost. Those draws record no price —
    correctly, since none is known — and the shelf runs negative meanwhile, which is
    the intended signal that a bill is outstanding. When the bill finally arrives,
    backdated to the delivery, the replay learns the price for that whole period.
    Leaving those draws blank recorded a month of real consumption at ₹0: ₹36,000 of
    oil reported as free, on one measured month.

    WHY RE-DERIVING IS SAFE — AND WHY IT REPLACED A FROZEN SNAPSHOT
    --------------------------------------------------------------
    The original rule was "snapshot the cost at draw time and never recompute", to
    stop next month's price rise rewriting last month's margin. That protected
    against something which cannot happen: **the replay is date-ordered**, so a draw
    is always priced by the receipts that precede *its own date*. A bill entered
    later but dated later changes nothing about it.

    What does move a past draw is a bill *backdated to before it*, or an existing
    bill corrected — and in both cases the figure SHOULD move, because the workshop
    has learnt what the goods actually cost. Freezing only preserved the guess.

    Re-deriving also fixes an ordering flaw that a fill-only-if-NULL rule had: with
    two suppliers billed in one sitting, whichever bill was keyed first would fix
    every draw before the second was known.

    Nothing customer-facing moves. This column is COST; the customer's bill is
    `total_price`, which is never touched here. Written with `.update()` so it
    cannot re-enter the job-card save path and re-run stock deltas.
    """
    if not corrections:
        return

    from collections import defaultdict
    from workshop.models import JobCardSpareItem

    # One UPDATE per distinct price rather than per row.
    by_price = defaultdict(list)
    for pk, price in corrections.items():
        by_price[price].append(pk)

    for price, pks in by_price.items():
        JobCardSpareItem.objects.filter(pk__in=pks).update(unit_price=price)


def recompute_average_cost(item):
    """
    Recompute and persist `item.avg_cost`. Call after anything that changes this
    product's receipts. Returns the new value.

    Writes with `.update()` — a plain save() here would re-enter the restock
    signals that called us.
    """
    from .models import Item

    avg, corrections = _replay(item)
    _apply_draw_costs(corrections)
    if item.avg_cost != avg:
        Item.objects.filter(pk=item.pk).update(avg_cost=avg)
        item.avg_cost = avg
    return avg
