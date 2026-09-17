"""
What the workshop already knows about a number plate.

The Job Card form asks this the moment somebody leaves the Registration box, so
a car the workshop has seen before is not typed in from scratch. Every rule
about WHAT is answered lives here; the form's script decides only which boxes it
may write into (the lookup script at the foot of `jobcard_form.html`).

Four groups, and each is answered WHOLE, from one visit:

* **the car** — brand and model, from the newest visit. Both are required on a
  job card, so the newest visit always carries them.
* **the colour** — `car_color` with `car_color_other`, from the newest visit that
  recorded one. A colour is optional, and 'Other' means nothing without the value
  beside it.
* **the chassis code and the VIN** — `vehicle_ids.latest_recorded`, imported
  rather than restated, because the Car Profile header prints that same answer.
  They are two separate facts, so each comes from its own newest record.
* **the customer** — name with number, from the newest visit that recorded
  either, and only when `include_customer` is set.

⚠ **A GROUP IS NEVER STITCHED TOGETHER FROM TWO VISITS.** The customer is where
that matters most: a car sold on carries the new owner's name on its newest card
and the old owner's number on an older one, and joining the two would put a
stranger's phone number under the new owner's name — the number the invoice's
WhatsApp button opens. So a newest visit carrying only a name answers with that
name and NO number.

⚠ **THE CUSTOMER IS OFFICE AND OWNER ONLY, and the caller decides.** Floor opens
most job cards and uses this same lookup, but its form renders no customer boxes
and this must not hand it what that form hides. With `include_customer` off the
two keys are ABSENT from the answer, not blank, so nothing in a Floor response
can carry them.

⚠ **OLD BILLS ANSWER TOO, FOR WHAT THEIR PAPER CARRIES** — the make, the model
and the customer's name. Never a colour or a phone number: an Excel bill has
neither, so it can only ever answer those groups with blanks, and a group is
taken from the newest visit that RECORDED it, so it simply falls through to a
job card that did. They are merged in by date, so a returning customer from
the Excel years is recognised the first time they come back.

⚠ **THE CUSTOMER IS OFFERED, NEVER FILLED.** Cars change hands, so the form shows
the answer greyed in as the two boxes' placeholders, with one "Use last visit"
button. Only the car, its colour and the two codes go into their boxes by
themselves.

Nothing here queries at import time; the model is imported inside the function,
the shape `vehicle_ids.py` already uses.
"""
from .vehicle_ids import latest_recorded

CAR = ('brand_name', 'model_name')
COLOUR = ('car_color', 'car_color_other')
CUSTOMER = ('customer_name', 'customer_contact')


def _recorded(value):
    return value is not None and str(value).strip() != ''


def _newest_group(visits, fields):
    """The group from the newest visit that recorded ANY of it — whole, never mixed."""
    for visit in visits:
        if any(_recorded(visit[field]) for field in fields):
            return {field: visit[field] or '' for field in fields}
    return {field: '' for field in fields}


def known_car(registration, include_customer=False):
    """
    Everything the form may use for this plate, as one flat dict of strings.

    Blank where nothing was ever recorded, never None, because the answer goes
    straight into input boxes. `found` says whether the workshop has seen this
    car at all. `car_color_hex` is the colour's CSS value — the unset grey when
    none is recorded — from the same `car_color_hex()` every other screen paints
    the car with, so the swatch the form repaints cannot disagree with the rail
    on the Car Profile.

    The typed plate is tidied the way `JobCard.clean()` stores one (trimmed,
    capitals), so 'kl 10 aa 1000' finds 'KL 10 AA 1000'.
    """
    from .models import JobCard, OldBill, car_color_hex

    groups = (CAR, COLOUR) + ((CUSTOMER,) if include_customer else ())
    fields = [field for group in groups for field in group]
    answer = {field: '' for field in fields}
    answer.update(car_color_hex='', chassis_code='', vin='')

    plate = (registration or '').strip().upper()
    visits = []
    if plate:
        cards = [
            ((row.pop('admitted_date'), 1, row.pop('pk')), row)
            for row in JobCard.objects.filter(registration_number=plate)
            .values(*fields, 'admitted_date', 'pk')
        ]
        # An old bill carries only the make, the model and a name; every other
        # field reads blank, so it never answers a group it did not record.
        old_bills = [
            ((row.pop('bill_date'), 0, row.pop('pk')), {field: row.get(field, '') for field in fields})
            for row in OldBill.objects.filter(registration_number=plate)
            .values('brand_name', 'model_name', 'customer_name', 'bill_date', 'pk')
        ]
        # Newest first. On a shared day the job card wins — old bills are older
        # by definition.
        visits = [row for _key, row in sorted(cards + old_bills, key=lambda pair: pair[0], reverse=True)]

    if visits:
        for group in groups:
            answer.update(_newest_group(visits, group))
        for field, value in latest_recorded(plate).items():
            answer[field] = value or ''

    # Always a colour, "none recorded" included: taking a car's colour back off
    # the card has to repaint the swatch as unset, not as nothing at all.
    answer['car_color_hex'] = car_color_hex(answer['car_color'], answer['car_color_other'])
    answer['found'] = bool(visits)
    return answer
