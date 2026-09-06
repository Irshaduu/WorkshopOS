"""
An odometer reading, as a NUMBER — one module, no HTTP, no models.

`JobCard.mileage` and `Estimate.mileage` are `CharField`s, and deliberately so:
the box is filled on the shop floor from whatever the cluster happens to show,
and a car occasionally arrives with no working odometer at all. A required
integer field would stop a mechanic mid-shift over a number that is context,
not money.

The cost of that is paid here. The moment anything wants to SUBTRACT two
readings — how far since the last service, how far since the oil was changed,
how many kilometres this car covers in a month — a free-text column has to
become an integer, and the answer has to be the same everywhere. Two callers
already need it (the model's own normalisation on save, and the service history
document), so it lives in one importable place rather than growing a second
slightly-different spelling. Same shape as `money.py`, `spare_dates.py` and
`money_dates.py`: one question, one answer, no request.

**IT IS AN ALLOWLIST OF SHAPES, NOT A SCRUB-AND-HOPE.** The tempting version
strips every non-digit and reads what is left, and it is wrong in the one
direction that matters: `approx 50000` becomes 50000, `50000 miles` becomes
50000, and `85000 2` becomes 850002 — three readings this module cannot
honestly produce, each silently wrong rather than absent. A shape that is not
recognised returns None, the document prints "no reading recorded", and nobody
is misled. **A missing figure is recoverable; a wrong one is not.**

⚠ **MILES ARE REFUSED, NEVER CONVERTED.** An imported car showing miles is
real, and 1.609 is not a secret — but a column that silently mixes two units
is exactly how an interval comes out 60% short with nothing on screen to say
so. Refusing puts the decision in front of a person. If the workshop ever wants
miles, it is a stored unit on the card, not a guess made here.
"""

import re
from decimal import Decimal, ROUND_HALF_UP


#: The ceiling. The highest-mileage car on record is around five million
#: kilometres; a premium car in this workshop tops out in the low hundreds of
#: thousands. Anything past this is a slipped keypress or a pasted phone
#: number, and returning it would blow up every interval computed from it.
#:
#: ⚠ It is a guard against GARBAGE, not against typos, and the difference
#: matters. An extra zero on 85,000 gives 850,000 — under this ceiling, so it
#: is accepted here and has to be caught downstream, where a reading is
#: compared against the one before it. Both guards are needed; neither
#: replaces the other.
MAX_KM = 2_000_000

#: Units spelled out. Stripped before the number is read, so `50,000 km` and
#: `50000` are the same reading. A bare trailing `k` is deliberately NOT in
#: here — that one means thousands, and it is handled by the pattern below.
#: Every entry ends in a letter other than `k`, so stripping these first can
#: never eat the multiplier.
_UNIT = re.compile(r'\s*(?:k\s*\.?\s*m\s*\.?s?|kilometers?|kilometres?)\s*$', re.IGNORECASE)

#: The shapes a reading may take.
#:
#: The comma rule is strict on purpose. `\d{1,3}(?:,\d{2,3})*` accepts both
#: groupings this workshop sees — Western `50,000` and Indian `1,02,340` — and
#: refuses `50,0` and `1,2,3`, which are not numbers anybody wrote down. The
#: alternative branch covers a plain run of digits with no separators at all,
#: which is what most cards carry.
_READING = re.compile(
    r"""
    ^
    (?P<digits> \d{1,3}(?:,\d{2,3})+ | \d+ )   # 50000  ·  50,000  ·  1,02,340
    (?: \. (?P<frac>\d+) )?                    # a decimal part, if any
    \s*
    (?P<kilo> k )?                             # a bare 'k' — thousands
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_km(value):
    """
    The reading as a whole number of kilometres, or None if it cannot be read.

    None is the honest answer for a blank box, a dash, a note, a unit this
    module does not accept, and a figure outside `MAX_KM`. Every caller treats
    it the same way — print the text as it stands and compute nothing from it —
    so an unreadable reading costs one row's interval and never a wrong one.

    Accepted, with the reasoning for each:

      ``50000``      the ordinary case
      ``50,000``     Western grouping, pasted or typed out of habit
      ``1,02,340``   Indian grouping — how a reading is actually read aloud here
      ``50000 km``   the unit typed out, in any of its spellings
      ``50k``        the shorthand the field's own help text invites
      ``50.5k``      the same shorthand with a half, giving 50,500
      ``50000.4``    an odometer showing tenths; rounded to the whole kilometre

    Refused, each for its own reason:

      ``0``          not a reading. See below.
      ``-100``       the pattern takes no sign; an odometer has no direction.
      ``50000 mi``   a different unit — see the module docstring.
      ``approx 50``  a note about a reading, not the reading.
      ``n/a``        somebody saying they do not know, which None already says.

    ⚠ **ZERO IS REFUSED, and it is the one refusal that looks wrong.** A car
    genuinely at 0 km does not reach a workshop that services used premium
    cars; what reaches the box is somebody filling it in to get past it. Taking
    it literally is expensive in a way a blank is not — the NEXT visit then
    reports the car's entire lifetime distance as one service interval, which
    is a figure on a customer's document that is off by an order of magnitude.
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    match = _READING.match(_UNIT.sub('', text).strip())
    if not match:
        return None

    # Commas have done their job as a legibility aid and are dropped only after
    # the pattern above has confirmed they were placed like a number's.
    number = Decimal(match.group('digits').replace(',', ''))
    if match.group('frac'):
        number += Decimal('0.' + match.group('frac'))
    if match.group('kilo'):
        number *= 1000

    # ROUND_HALF_UP rather than Python's own round(), which is banker's
    # rounding — the same choice `invoice.derive_unit_price` makes, so a figure
    # rounded on the bill and a figure rounded here agree about a half.
    kilometres = int(number.quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    if kilometres <= 0 or kilometres > MAX_KM:
        return None
    return kilometres


def normalise(value):
    """
    What to STORE, given what somebody typed.

    A reading that can be read is stored as plain digits, so `50k`, `50,000`
    and `50000 km` all become `50000` — and every screen that prints the column
    raw (the invoice's VEHICLE INFO block, the job card, the car profile chip,
    the Live Report) shows one spelling of one number instead of three. The
    column is 20 characters; `MAX_KM` is seven digits, so a normalised reading
    can never overflow it.

    ⚠ **ANYTHING UNREADABLE IS RETURNED AS TYPED, MERELY TRIMMED — never
    blanked.** This runs inside `clean()`, which runs on every save including
    the ones nobody is watching, so a rule that discarded what it could not
    parse would delete a mechanic's note about a broken odometer the next time
    an unrelated field on that card was edited. Normalising is tidying, and
    tidying may not lose data. The cost is that a stored value is *usually*
    plain digits rather than *always*, which is why `parse_km` is still the
    only thing allowed to read the column as a number.
    """
    if value is None:
        return value

    text = str(value).strip()
    kilometres = parse_km(text)
    return str(kilometres) if kilometres is not None else text
