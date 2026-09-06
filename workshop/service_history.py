"""
What a car's SERVICE HISTORY prints — one module, no views, no HTTP.

The third customer document, after the invoice and the estimate, and the first
that describes a CAR rather than a visit. It is handed over when a customer
asks for their record — most often because they are SELLING the car — so every
figure on it will be read by somebody with no reason to give the workshop the
benefit of the doubt, beside a stack of invoices they can check it against.

Same shape as `invoice.py`, and it depends on that module rather than
paraphrasing it. `part_display_name`, `effective_quantity` and `document_title`
are imported, never restated: a part named one way on the bill and another way
here is the workshop contradicting itself in a folder where both documents sit
together.

**THE THING THIS DOCUMENT DOES THAT NOTHING ELSE IN THE APP DOES: IT FOLLOWS A
PART ACROSS VISITS.** A wheel bearing fitted three times over four years is
three rows on three job cards and nothing joins them up. Here they are one
chain, numbered from the first time this car ever had that part, each carrying
the distance it covered before it was replaced:

    (3) Wheel Bearing - Left    10,000 km   RUNNING
    (2) Wheel Bearing - Left    12,800 km
    (1) Wheel Bearing - Left    13,000 km

That is the answer to "how long does this part last on THIS car", and it is
worth more to a buyer than any single invoice. It costs nothing but ordering:
every figure is one stored reading minus another.

⚠ **WHAT THIS PRINTS AS AN AMOUNT IS `total_bill_amount` — WHAT THE INVOICE
SAID — AND NOT WHAT THE CAR PROFILE PAGE CALLS "billed".** They are different
figures and the difference is deliberate:

  * `car_profile_detail` reports `total_bill_amount - discount_amount`, which
    is REVENUE as `analysis_engine` defines it. Right for an internal screen
    asking what the workshop earned from this car.
  * This document reprints the figure that was on the paper the customer was
    handed. A part-paid walk-in is marked PAID with the shortfall booked as a
    discount (see CLAUDE.md), so the two differ on exactly the visits a
    customer is most likely to check — and a history disagreeing with an
    invoice about one visit is the one failure that would make the whole
    document worthless.

    The discount itself is never printed, for the reason `settlement()` gives:
    it is the workshop's own write-off, agreed verbally at the counter.

**NO PAYMENT STATE APPEARS ANYWHERE.** This is a record of WORK, not of debt.

Nothing here is a money source of truth; every amount is the job card's own
denormalized column, read the way every other screen reads it.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from .invoice import document_title, effective_quantity, part_display_name
from .mileage import parse_km


ZERO = Decimal('0')
ONE = Decimal('1')

#: A reading rising faster than this, sustained between two visits, is treated
#: as not credible — almost always a slipped keypress (85,000 typed as 850,000)
#: rather than a car that genuinely covered the distance.
#:
#: ⚠ **THE SECOND OF THE TWO ODOMETER GUARDS, AND IT CATCHES WHAT THE FIRST
#: CANNOT.** `mileage.MAX_KM` refuses garbage — twenty digits, a pasted phone
#: number. It cannot refuse 850,000, which is perfectly plausible in isolation
#: and is only wrong NEXT TO the 85,000 before it. This guard is the only thing
#: that looks at a reading in context.
#:
#: 1,000 km/day is far above anything real: a car driven hard covers 300. The
#: margin is deliberate — a false flag on a customer's own document is worse
#: than a missed one, and a genuine long gap (a car serviced elsewhere for
#: three years) must never be marked.
IMPLAUSIBLE_KM_PER_DAY = 1000

#: Below this the rate is not worth computing — one day's gap turns a single
#: long drive into an implausible rate, and two visits in one day divide by
#: zero.
RATE_CHECK_MIN_DAYS = 2

#: A usage figure ("runs about 1,150 km a month") needs a long enough base to
#: mean anything. Two months of history describes one trip, not a habit.
USAGE_MIN_MONTHS = 2

#: How far through its usual life a fitted part has to be before the sheet says
#: so. A dial, not a law — and deliberately short of 1.0, because the useful
#: moment to tell a customer is BEFORE the part is overdue rather than after.
#:
#: ⚠ The comparison is only ever against THIS CAR'S OWN completed lives. This
#: system holds no manufacturer intervals and inventing one would be the sheet
#: asserting something nobody at this workshop agreed.
DUE_AT_FRACTION = Decimal('0.9')


def part_key(name):
    """
    The identity a part is followed by, across visits and across both routes.

    Free text on the shop side, a category name on the warehouse side, typed by
    different people over several years — so the chain is grouped on a
    normalised form rather than on the raw string. Case is folded, runs of
    whitespace collapse, and the three dash characters are unified, because the
    master list itself carries 'Brake Pads – Front' with an en dash while a
    keyboard produces a hyphen. Two spellings of one part would otherwise be
    two chains, each looking complete, each wrong.

    ⚠ **A WAREHOUSE DRAW AND A SHOP PURCHASE OF THE SAME THING SHARE A CHAIN,
    AND THAT IS INTENDED.** The customer's question is "when was the engine oil
    last changed", not "which shelf did it come off". Both routes reach here
    already named by `part_display_name`, which is what makes them comparable.
    """
    text = (name or '').lower()
    for dash in ('–', '—', '−'):
        text = text.replace(dash, '-')
    text = re.sub(r'\s*-\s*', ' - ', text)
    return ' '.join(text.split())


@dataclass(frozen=True)
class PartInstance:
    """
    One fitting of one part, and how far it went.

    `number` counts from the FIRST time this car ever had this part, so (1) is
    always the oldest and the numbers read the same way whichever end of the
    document you start from.
    """
    name: str
    key: str
    number: int
    quantity: Optional[Decimal]
    fitted_date: date
    fitted_km: Optional[int]
    #: Distance covered before it was replaced — or, on the newest one, so far.
    #: None whenever either end of the subtraction is unknown.
    life_km: Optional[int]
    #: The newest fitting of this part: what is on the car now.
    running: bool
    #: This car's own average completed life for this part, and how many
    #: completed lives that average is over. Carried on the instance as well as
    #: on the chain so the visit card and the summary cannot disagree.
    typical_km: Optional[int]
    sample_count: int
    #: Running, and at or past `DUE_AT_FRACTION` of `typical_km`.
    due_soon: bool


@dataclass(frozen=True)
class Chain:
    """Every fitting of one part on one car, newest first."""
    name: str
    key: str
    instances: tuple
    typical_km: Optional[int]
    sample_count: int
    due_soon: bool


@dataclass(frozen=True)
class Visit:
    """One completed visit, measured against the one before it."""
    number: int                       # 1 is the OLDEST, whichever way it prints
    date: date
    bill_number: str
    reading: Optional[int]
    reading_text: str                 # what to print when it is not a number
    #: Distance and days since the visit before this one. On the sheet these
    #: are drawn in the connector BENEATH the card, because it prints newest
    #: first and the card below is the earlier visit.
    gap_km: Optional[int]
    gap_days: Optional[int]
    #: The reading is LOWER than the previous visit's. No gap is offered.
    reading_dropped: bool
    #: The gap is real arithmetic but the implied rate is not credible. Kept
    #: apart from `reading_dropped` because a drop cannot be true whereas this
    #: might be, so the figure still stands and is only marked.
    rate_implausible: bool
    concerns: tuple
    jobs: tuple
    parts: tuple
    amount: Decimal                   # exactly what the invoice totalled


@dataclass(frozen=True)
class Summary:
    """
    The car over its whole life here.

    Every field tolerates a history of NOTHING. A car on its first visit and
    still on the floor has no completed visits at all, and a customer can
    perfectly well ask for their record while their car is in the workshop.
    """
    visits: int
    first_date: Optional[date]
    last_date: Optional[date]
    span_label: str
    first_reading: Optional[int]
    latest_reading: Optional[int]
    latest_reading_date: Optional[date]
    distance: Optional[int]
    km_per_month: Optional[int]
    total_billed: Decimal
    in_progress: int
    #: What the customer said on the phone, and what RUNNING measures against.
    #: `reference_km` falls back to `latest_reading`, so every running figure
    #: has a basis even when nobody was asked.
    current_km: Optional[int]
    reference_km: Optional[int]


def current_km_problem(current_km, latest_reading):
    """
    Why a typed current reading cannot be used — or None if it can.

    ONE implementation, read by the options page (which shows the message) and
    by the sheet (which must not be reachable with a bad one through a
    hand-edited URL). Two copies would be two answers, and they would differ on
    exactly the reading somebody is arguing about.

    Returns a sentence, not a code: there is one caller that displays it and
    the wording is the whole point.

    ⚠ **THE FIGURES ARE GROUPED THE INDIAN WAY, THROUGH `inr`.** Python's own
    `{:,}` writes 120,000 where every other screen in this app — and every
    other figure on the page this message appears on — writes 1,20,000. One
    number in two groupings on one screen reads as two different numbers, and
    this message exists precisely to be compared against the reading beside it.
    `inr` is a plain function that happens to be registered as a template
    filter, and it is the app's ONE implementation of that grouping; importing
    it locally keeps this module free of a Django import at load time.
    """
    if current_km is None:
        return None
    if latest_reading is not None and current_km < latest_reading:
        from .templatetags.custom_filters import inr
        return (
            f"The car last came in at {inr(latest_reading)} km, so it cannot "
            f"be showing {inr(current_km)} km now. Check the reading."
        )
    return None


def _months_between(start, end):
    """
    Whole months from `start` to `end`, the way a person counts them.

    3 Feb to 2 Mar is nought months — the day of the month has to come round
    before a month has passed. Used for the history's span and for the usage
    figure, so both count the same way.
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def span_label(start, end):
    """
    How long the history runs, in words: '4 years 2 months', '1 year', '9 days'.

    Written out here rather than left to the template because the singular is a
    genuine trap and templates get it wrong — '1 years 1 months' on the one car
    whose history is exactly that long.

    Returns '' when there is no span: a car with one visit has a history, not a
    duration, and '0 days' would read as a defect.
    """
    if not start or not end or end <= start:
        return ''

    months = _months_between(start, end)
    if months < 1:
        return _plural((end - start).days, 'day')
    if months < 12:
        return _plural(months, 'month')

    years, rest = divmod(months, 12)
    label = _plural(years, 'year')
    return f"{label} {_plural(rest, 'month')}" if rest else label


def _rate_is_implausible(gap_km, gap_days):
    """
    Whether a gap implies a speed no car sustains.

    Silent on a short gap: two visits a day apart can legitimately straddle one
    long drive, and a car brought back the same morning divides by zero.
    """
    if gap_km is None or not gap_days or gap_days < RATE_CHECK_MIN_DAYS:
        return False
    return gap_km / gap_days > IMPLAUSIBLE_KM_PER_DAY


def _raw_parts(jobcard):
    """
    The parts fitted on one visit, named as the invoice names them.

    ⚠ **BOTH ROUTES, ONE LIST, AND A WAREHOUSE DRAW IS NAMED BY ITS CATEGORY.**
    `part_display_name` enforces the second half — `Item.name` is the branded
    SKU the workshop buys, `Category.name` is what the part is, and naming the
    brand on a document the workshop hands out publishes its supply chain. That
    rule already governs the invoice; importing it is what stops this document
    quietly growing a second answer.
    """
    out = []
    for spare in jobcard.spares.all():
        name = part_display_name(spare)
        if not name:
            continue
        quantity = effective_quantity(spare.quantity)
        out.append((
            name,
            # The invoice's own rule: one is the figure this workshop never
            # writes down, so the cell stays empty and the name stands alone.
            quantity if quantity != ONE else None,
        ))
    return out


def _build_chains(ordered, reference_km):
    """
    Follow every part across the car's whole history.

    `ordered` is the visits OLDEST FIRST — the numbering counts from the first
    time this car ever had the part, so it has to be built in the direction
    time runs whatever order the sheet prints in.

    Two passes, and the split is what keeps it honest. The first lays the
    fittings out in order and measures each completed life against the fitting
    that ENDED it. The second needs the completed lives to already exist,
    because a running part is judged against the average of the ones before it
    — and on the first fitting of a part there is nothing to judge against, so
    nothing is said.

    Returns `{key: [PartInstance, ...]}` oldest first.
    """
    fittings = {}
    for visit_date, reading, parts in ordered:
        for name, quantity in parts:
            fittings.setdefault(part_key(name), []).append(
                {'name': name, 'quantity': quantity,
                 'date': visit_date, 'km': reading}
            )

    chains = {}
    for key, rows in fittings.items():
        # ⚠ ONE SPELLING FOR THE WHOLE CHAIN, and it is the COMMONEST rather
        # than the newest.
        #
        # `part_key` joins 'Wheel Bearing - Left', 'Wheel Bearing – Left' and
        # 'wheel bearing - left' into one chain, which is the point — but left
        # alone each instance then prints the spelling it happened to be typed
        # with, so a chain of three reads as three different parts stacked
        # under one number sequence.
        #
        # The newest was the tempting rule (it is what `_title` uses to decide
        # what the CAR is called) and it is wrong here: a car's brand is
        # corrected on a later card, whereas a part name is typed fresh every
        # visit, so the most recent one is as likely to be the typo as the fix.
        # The commonest spelling is what the workshop actually uses; the newest
        # only breaks a tie.
        display = max(
            {row['name'] for row in rows},
            key=lambda name: (
                sum(1 for row in rows if row['name'] == name),
                max(index for index, row in enumerate(rows) if row['name'] == name),
            ),
        )

        # Pass one: how far each fitting went before the next one replaced it.
        lives = []
        for index, row in enumerate(rows):
            nxt = rows[index + 1] if index + 1 < len(rows) else None
            life = None
            if nxt is not None and row['km'] is not None and nxt['km'] is not None:
                # A replacement dated at a LOWER reading is an odometer
                # problem, not a negative life. Nothing is offered rather than
                # a figure that cannot be true.
                span = nxt['km'] - row['km']
                life = span if span >= 0 else None
            lives.append(life)

        # The average is over COMPLETED lives only — the running one is still
        # accumulating, so including it would drag every average down and make
        # a part look shorter-lived the longer it survives.
        completed = [life for life in lives if life is not None]
        typical = round(sum(completed) / len(completed)) if completed else None

        instances = []
        for index, row in enumerate(rows):
            running = index == len(rows) - 1
            life = lives[index]
            if running:
                # What is on the car now, measured to whatever the best
                # available reading is: what the customer said on the phone,
                # or failing that the last reading this workshop recorded.
                # Both are honest; only one is current.
                if reference_km is not None and row['km'] is not None:
                    span = reference_km - row['km']
                    life = span if span >= 0 else None

            due = bool(
                running and typical and life is not None
                and Decimal(life) >= Decimal(typical) * DUE_AT_FRACTION
            )
            instances.append(PartInstance(
                name=display, key=key, number=index + 1,
                quantity=row['quantity'], fitted_date=row['date'],
                fitted_km=row['km'], life_km=life, running=running,
                typical_km=typical, sample_count=len(completed), due_soon=due,
            ))
        chains[key] = instances
    return chains


def build_service_history(jobcards, current_km=None):
    """
    Everything the service history sheet renders, derived from a car's cards.

    Takes every job card for one registration — not a pre-filtered list — so
    that WHICH VISITS COUNT is decided here, once, and can be tested without a
    request. Same division of labour as `build_invoice`: the caller resolves
    the records and prefetches, this returns plain values, the template prints
    them.

    The caller is expected to have prefetched `labours`, `concerns` and
    `spares` (with `item__category` selected). Nothing here works around a
    missing prefetch; it simply costs queries, exactly as on the invoice.

    `current_km` is what the customer said on the phone. It is **never
    stored** — it arrives as a query parameter and leaves with the page,
    because it is one person's word on one day and the workshop did not measure
    it. It changes exactly one class of figure: how far the parts currently on
    the car have run.

    **Three rules about which visits appear, each of which changes a figure:**

    * **Deleted cards are out.** Every consumer in the app filters them.
    * **Only COMPLETED visits are listed.** A car still on the floor has parts
      being added to it and a total that is not final, so including it would
      put a figure on a customer's document that changes after they were handed
      it. The count of open visits is reported instead, and the sheet says so.
    * **Sorted OLDEST FIRST here and printed NEWEST FIRST.** Both directions
      are needed and they are not interchangeable: a part's chain and a visit's
      gap can only be built in the direction time runs, while the sheet leads
      with the most recent visit because that is the one being asked about.
      `pk` breaks a same-day tie — `admitted_date` is a DateField and several
      cars share a date, so without it the order inside a day is whatever the
      database returns, which differs between PostgreSQL and SQLite.
    """
    live = [card for card in jobcards if not card.is_deleted]
    completed = sorted(
        (card for card in live if card.completed),
        key=lambda card: (card.admitted_date, card.pk),
    )

    readings = [parse_km(card.mileage) for card in completed]
    known = [km for km in readings if km is not None]
    latest_reading = known[-1] if known else None

    # A hand-edited URL must not be able to poison every RUNNING figure at
    # once, so the same rule the options page shows a message for is enforced
    # here as well. An unusable reading is dropped, never clamped.
    if current_km is not None and current_km_problem(current_km, latest_reading):
        current_km = None
    reference_km = current_km if current_km is not None else latest_reading

    chains = _build_chains(
        [(card.admitted_date, readings[index], _raw_parts(card))
         for index, card in enumerate(completed)],
        reference_km,
    )
    # Handed out in the order each visit fitted them, so a card's parts print
    # in the order they were typed rather than grouped by chain.
    taken = {key: 0 for key in chains}

    visits = []
    previous = None
    for index, card in enumerate(completed):
        reading = readings[index]

        # ⚠ ONE ANCHOR. Both gaps are measured against the IMMEDIATELY previous
        # visit — never against "the last visit that happened to have a
        # reading". Two different baselines in adjacent figures is a document
        # that needs a footnote to be read, and if the previous visit has no
        # reading then the honest answer to "how far since" is nothing at all.
        gap_km = gap_days = None
        dropped = False
        if previous is not None:
            gap_days = (card.admitted_date - previous.date).days
            if reading is not None and previous.reading is not None:
                difference = reading - previous.reading
                # A reading below the one before it is an odometer that was
                # replaced, a cluster swapped, or a digit mistyped. Never a
                # negative distance — saying so is worth more to a buyer than
                # any figure the document could invent in its place.
                if difference < 0:
                    dropped = True
                else:
                    gap_km = difference

        parts = []
        for name, _quantity in _raw_parts(card):
            key = part_key(name)
            parts.append(chains[key][taken[key]])
            taken[key] += 1

        visits.append(Visit(
            number=index + 1,
            date=card.admitted_date,
            bill_number=card.bill_number or '',
            reading=reading,
            # The text is still shown when it is not a number: 'cluster not
            # working' is a fact about that visit, and blanking it would look
            # like nobody recorded one.
            reading_text=(card.mileage or '').strip(),
            gap_km=gap_km,
            gap_days=gap_days,
            reading_dropped=dropped,
            rate_implausible=_rate_is_implausible(gap_km, gap_days),
            concerns=tuple(
                concern.concern_text for concern in card.concerns.all()
                if concern.concern_text
            ),
            jobs=tuple(
                labour.job_description for labour in card.labours.all()
                if labour.job_description
            ),
            parts=tuple(parts),
            # See the module docstring: what the invoice totalled, never
            # revenue and never what was received.
            amount=card.total_bill_amount or ZERO,
        ))
        previous = visits[-1]

    summary = _summarise(
        visits, in_progress=sum(1 for card in live if not card.completed),
        current_km=current_km, reference_km=reference_km,
    )
    return {
        # NEWEST FIRST — the sheet's own order, so the template never reverses
        # anything and cannot disagree with the summary about which visit is
        # the latest.
        'visits': tuple(reversed(visits)),
        'summary': summary,
        'chains': _summarise_chains(chains),
        'document_title': _title(live, completed),
    }


def _summarise_chains(chains):
    """
    The PART LIFE section: every part this car has had, newest fitting first.

    Ordered by what is closest to needing attention, then by how recently it
    was touched — a section read to answer "what should I be thinking about"
    is useless sorted alphabetically, and a part fitted once four years ago
    should not sit above one due next month.
    """
    out = []
    for instances in chains.values():
        newest = instances[-1]
        out.append(Chain(
            name=newest.name,
            key=newest.key,
            instances=tuple(reversed(instances)),
            typical_km=newest.typical_km,
            sample_count=newest.sample_count,
            due_soon=newest.due_soon,
        ))
    out.sort(key=lambda chain: (
        not chain.due_soon,
        -(chain.instances[0].fitted_date.toordinal()),
        chain.name.lower(),
    ))
    return tuple(out)


def _summarise(visits, in_progress, current_km, reference_km):
    """
    The whole history in one block, computed from the visits already built.

    Derived from `visits` rather than re-queried, so the summary and the log
    can never disagree — the rule the Cashbook follows for its totals, applied
    the other way round: there the rows are paginated so the totals must be
    their own aggregate, here nothing is paginated so re-asking would only
    create a second answer.

    `visits` arrives OLDEST FIRST. This runs before the reversal for exactly
    that reason: 'the first reading' and 'the latest reading' are far easier to
    get wrong reading backwards.
    """
    if not visits:
        return Summary(
            visits=0, first_date=None, last_date=None, span_label='',
            first_reading=None, latest_reading=None, latest_reading_date=None,
            distance=None, km_per_month=None, total_billed=ZERO,
            in_progress=in_progress, current_km=current_km,
            reference_km=reference_km,
        )

    readable = [visit for visit in visits if visit.reading is not None]
    first_reading = readable[0].reading if readable else None
    latest = readable[-1] if readable else None

    # Distance is measured between the readings themselves, not between the
    # first and last VISIT — a history whose middle visits have no reading
    # still spans exactly the two that do. Never negative: a car whose odometer
    # was replaced can end lower than it started, and 'covered -40,000 km' is
    # not a fact about anything.
    distance = None
    if latest is not None and first_reading is not None and len(readable) > 1:
        difference = latest.reading - first_reading
        distance = difference if difference > 0 else None

    # How hard the car is used — a genuine selling point for a lightly-driven
    # one, and one of the few things a stack of invoices cannot show.
    km_per_month = None
    if distance is not None:
        months = _months_between(readable[0].date, latest.date)
        if months >= USAGE_MIN_MONTHS:
            km_per_month = round(distance / months)

    return Summary(
        visits=len(visits),
        first_date=visits[0].date,
        last_date=visits[-1].date,
        span_label=span_label(visits[0].date, visits[-1].date),
        first_reading=first_reading,
        latest_reading=latest.reading if latest else None,
        latest_reading_date=latest.date if latest else None,
        distance=distance,
        km_per_month=km_per_month,
        total_billed=sum((visit.amount for visit in visits), ZERO),
        in_progress=in_progress,
        current_km=current_km,
        reference_km=reference_km,
    )


def _title(live, completed):
    """
    What the browser tab says, and therefore what the saved PDF is called.

    `document_title` is imported rather than reimplemented, so this file lands
    in a customer's folder beside their invoices under the same naming:

        Audi A4 KL11 AJ 2266 (JB-26-037).pdf
        Audi A4 KL11 AJ 2266 (Service History).pdf

    The car is described by the NEWEST card there is — including one still on
    the floor, which is the most current record of what the car is called even
    though its visit is not listed.
    """
    newest = max(
        live or completed,
        key=lambda card: (card.admitted_date, card.pk),
        default=None,
    )
    if newest is None:
        return 'Service History'
    return document_title(newest, 'Service History', 'Service History')
