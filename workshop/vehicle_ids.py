"""
The chassis code and the VIN — the two facts that say exactly which car this is.

The owners asked for both (2026-09), and they are two different things that
happen to share the word "chassis":

* **Chassis code** — the platform or generation code: BMW **F30**, Mercedes
  **W205**, Audi **B9**, Porsche **991.2**. A few characters, shared by every car
  of that generation, and it is the fact the *model* cannot carry: the master
  list's "320d" is an E90, an F30 or a G20, and those share almost no parts.
  It is what decides which part fits.
* **VIN** — the Vehicle Identification Number, the RC book's "Chassis No.":
  seventeen characters, one car and only that car.

Both are optional and both are free text on the card, like the brand and the
model — typed on the floor, never picked from a master list. One MODEL has
several chassis codes, so a list keyed to the model could never be right.

**The registration stays the car's identity.** Car Profiles, the service
history, All Invoices and the one-active-card-per-plate rule are all keyed by
the plate, and none of that moves. A VIN is a better identity in principle
(plates change on a state transfer) and switching would touch every one of
those screens — it is deliberately not done.

**This module is the one implementation of every rule about either field**, for
the reason `spare_dates.py` and `money_dates.py` exist: the Job Card form and the
Estimate form both take these boxes, and two copies of "what is a valid VIN"
would be two answers free to disagree.

**Nothing here queries at import time.** `models.py` imports the constants below
to size its columns, so `latest_recorded` imports the model inside the function
— the same shape `models.py` already uses to import `SPARE_COST`.
"""
import re

#: The column widths. Read by `models.py` so the column, the tidy-up and the
#: refusal message can never name three different lengths.
CHASSIS_CODE_MAX_LENGTH = 20
VIN_LENGTH = 17

_WHITESPACE = re.compile(r'\s+')
# A VIN is often written down in groups — "WBA 8E9C 50GK 123456" or with dashes —
# so both are dropped before it is judged. Nothing else is: a dot or a slash is
# not a way anybody writes a VIN, and quietly deleting it would be saving a
# value nobody typed.
_VIN_SEPARATORS = re.compile(r'[\s\-]+')
_LETTERS_AND_NUMBERS = re.compile(r'^[A-Z0-9]+$')
# ISO 3779 leaves these three out of every VIN because they read as 1, 0 and 0.
# One of them in a typed VIN is almost always exactly that misreading.
_NEVER_IN_A_VIN = frozenset('IOQ')


def normalise_chassis_code(value):
    """
    'f 30' → 'F30'. Blank → None.

    Capitals and no spaces, and nothing else. Real codes carry characters a
    stricter rule would destroy — Porsche's '991.2', Land Rover's 'L494' — so a
    chassis code is never refused for its shape. NULL rather than '' for a blank
    box, because "nobody recorded one" is the only thing a blank can mean, and
    the column is nullable for exactly that.
    """
    if value is None:
        return None
    tidy = _WHITESPACE.sub('', str(value)).upper()
    return tidy or None


def normalise_vin(value):
    """
    'wba 8e9c-50gk 123456' → 'WBA8E9C50GK123456'. Blank → None.

    Tidying only. Whether the result IS a VIN is `vin_problem`'s question —
    kept separate so the model can tidy on every save (shell, seeders) while only
    the forms refuse, which is the split `mileage.normalise` and the job card's
    `clean_admitted_date` already follow.
    """
    if value is None:
        return None
    tidy = _VIN_SEPARATORS.sub('', str(value)).upper()
    return tidy or None


def vin_problem(value):
    """
    What is wrong with this typed VIN, as a sentence — or '' when nothing is.

    A blank box has no problem: the VIN is optional. Everything else is REFUSED,
    never corrected — the codebase-wide rule, because a fallback saves a value
    nobody typed, and a VIN is printed nowhere a wrong one could be caught later.
    One sentence at a time, in the order a person would fix them.

    ⚠ **THERE IS NO CHECK-DIGIT TEST, deliberately.** Position 9 is a mandatory
    check digit only on cars built for North America (and China). European
    makers routinely fill it with anything, so testing it would refuse a
    perfectly correct BMW or Mercedes VIN — the brands this workshop services.

    ⚠ **A car too old to have a 17-character number is not given a VIN.** A
    pre-1981 chassis number is not a VIN; the box stays blank rather than being
    stretched to hold something else.
    """
    vin = normalise_vin(value)
    if not vin:
        return ''
    if not _LETTERS_AND_NUMBERS.match(vin):
        return 'A VIN is letters and numbers only.'
    if len(vin) != VIN_LENGTH:
        return f'A VIN has {VIN_LENGTH} characters — this one has {len(vin)}.'
    if _NEVER_IN_A_VIN & set(vin):
        return 'A VIN never uses the letters I, O or Q — check for a 1 or a 0.'
    return ''


def latest_recorded(registration):
    """
    This car's chassis code and VIN, each from the NEWEST visit that recorded one.

    **Not simply the newest visit's.** Both boxes are typed fresh on every job
    card, so a visit where nobody filled them in is ordinary — and reading the
    newest card alone would make a VIN vanish from the Car Profile the day a
    later card was saved without it. Each field is therefore read separately:
    the chassis code can come from one visit and the VIN from an earlier one.

    One implementation, read by two things that must agree: the Car Profile
    header, and the Job Card form's lookup that fills the two boxes when a known
    plate is typed. Newest is `-admitted_date, -pk`, the order the service
    history already uses for "the newest card". Deleted cards are not excluded —
    `is_deleted` is a dormant column, and the Car Profile's own visit query does
    not filter it either.

    The typed plate is tidied the way `JobCard.clean()` stores one (trimmed,
    capitals), so 'kl 10 aa 1000' finds 'KL 10 AA 1000'. Returns both keys
    always, None where nothing was ever recorded.
    """
    from .models import JobCard

    plate = (registration or '').strip().upper()
    found = {'chassis_code': None, 'vin': None}
    if not plate:
        return found

    visits = JobCard.objects.filter(registration_number=plate).order_by('-admitted_date', '-pk')
    for field in found:
        found[field] = (
            visits.exclude(**{f'{field}__isnull': True})
                  .exclude(**{field: ''})
                  .values_list(field, flat=True)
                  .first()
        )
    return found
