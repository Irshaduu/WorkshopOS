"""
OLD BILLS — every rule about a bill written in Excel before the system existed.

One module, the codebase's usual shape: the form, the job card's numbering and
the screens all ask here, so no two of them can answer differently.

⚠ This module never imports the money code, and the money code never imports
this module. `test_old_bills.py` enforces the second half.
"""

import re
from datetime import date
from decimal import Decimal
from itertools import zip_longest

from django.conf import settings

from .money import fit_text, parse_money


# ---------------------------------------------------------------------------
# BILL NUMBERS — one JB sequence per year, shared by Excel and the system
# ---------------------------------------------------------------------------
#
# The Excel bills were numbered JB-YY-NNN, restarting at 001 every January, and
# YY is always the bill's own year. The system numbers job cards the same way.
# So a JB number must exist ONCE across both, or a customer holds a paper
# JB-26-001 and a system JB-26-001 for two different visits.
#
# Two rules keep it that way, and each covers the other's gap:
#
# * `LAST_EXCEL_BILL_NUMBER` (set on go-live day) — live job cards of that year
#   start after it, and an old bill cannot take a number after it.
# * A live job card also skips any number an old bill already holds. That is
#   what protects a year BEFORE go-live (a job card mistyped into 2025) and a
#   system where the setting was never filled in.

#: The one shape a bill number has. A leading-zero number is the same number:
#: JB-26-97 and JB-26-097 are one bill.
BILL_NUMBER_RE = re.compile(r'JB-(\d{2})-0*([1-9]\d{0,4})')


def split_bill_number(text):
    """(26, 97) for 'JB-26-097' (any spacing or case); None for anything else."""
    match = BILL_NUMBER_RE.fullmatch(''.join(str(text or '').split()).upper())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def format_bill_number(year, seq):
    """'JB-26-097' — padded to three digits exactly as `JobCard.save()` pads."""
    return f'JB-{int(year):02d}-{str(int(seq)).zfill(3)}'


def last_excel_bill():
    """(26, 245) from the setting, or None while it is blank."""
    return split_bill_number(getattr(settings, 'LAST_EXCEL_BILL_NUMBER', ''))


def live_numbering_floor(year):
    """
    The highest number of `year` (two digits) that belongs to Excel — so a live
    job card of that year starts after it. 0 for every other year.
    """
    last = last_excel_bill()
    return last[1] if last and last[0] == int(year) else 0


def owned_by_the_system(year, seq):
    """True when this number comes after the last Excel bill — a live number."""
    last = last_excel_bill()
    return last is not None and (int(year), int(seq)) > last


def bill_number_problem(number, bill_date=None, exclude_pk=None):
    """
    Why this old bill number cannot be saved, in words — or None.

    Checked in the order a typist would want to hear about it: the shape, then
    whether it matches the bill's own date, then whether the system owns it,
    then whether it is already in.
    """
    from .models import JobCard, OldBill

    parts = split_bill_number(number)
    if parts is None:
        return "Enter the bill number as it is printed, like JB-26-097."
    year, seq = parts
    tidy = format_bill_number(year, seq)

    if bill_date is not None and year != bill_date.year % 100:
        return (
            f"{tidy} is a 20{year:02d} bill, but the date says {bill_date.year}. "
            f"Check the date or the number."
        )

    last = last_excel_bill()
    if owned_by_the_system(year, seq):
        return (
            f"{tidy} comes after the last Excel bill ({format_bill_number(*last)}). "
            f"Numbers from there on belong to the system."
        )

    if JobCard.objects.filter(bill_number=tidy).exists():
        return f"{tidy} is already a job card in the system."

    existing = OldBill.objects.filter(bill_number=tidy)
    if exclude_pk:
        existing = existing.exclude(pk=exclude_pk)
    existing = existing.first()
    if existing:
        car = ' '.join(p for p in (existing.brand_name, existing.model_name) if p)
        return (
            f"{tidy} is already in — {car or existing.registration_number}, "
            f"{existing.bill_date:%d %b %Y}."
        )
    return None


# ---------------------------------------------------------------------------
# TYPING — the date in three boxes, and amounts copied off the paper
# ---------------------------------------------------------------------------
#
# ⚠ `static/js/old-bill-core.js` answers the same questions in the browser, so
# the screen can say what it understood while somebody types. The server is the
# control; the browser only saves a round trip. The two are held to ONE list of
# cases, `workshop/tests/js/old-bill-cases.json`, which both test suites read —
# so they cannot come to disagree about a date or an amount without a test
# failing on both sides.

MONTHS = (
    'january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december',
)

_ONE_OR_TWO_DIGITS = re.compile(r'[0-9]{1,2}')


def read_month(text):
    """4 for '4', '04', 'apr' or 'April' — a name only when it names ONE month."""
    text = str(text or '').strip().lower()
    if _ONE_OR_TWO_DIGITS.fullmatch(text):
        number = int(text)
        return number if 1 <= number <= 12 else None
    if len(text) >= 3 and text.isascii() and text.isalpha():
        matches = [n for n, name in enumerate(MONTHS, start=1) if name.startswith(text)]
        return matches[0] if len(matches) == 1 else None
    return None


def read_date(day, month, year, today):
    """
    (date, None) or (None, why) from the three boxes.

    `1` and `01` are the same day; the month may be a number or its name,
    because the paper prints "10-Apr-2026"; the year may be `26` or `2026`.
    """
    day, month, year = (str(v or '').strip() for v in (day, month, year))
    if not (day or month or year):
        return None, "Enter the date on the bill."
    if not (day and month and year):
        return None, "Enter the day, month and year."
    if not _ONE_OR_TWO_DIGITS.fullmatch(day) or not 1 <= int(day) <= 31:
        return None, "The day must be 1 to 31."
    month_number = read_month(month)
    if month_number is None:
        return None, "The month must be 1 to 12, or its name."
    if re.fullmatch(r'[0-9]{2}', year):
        full_year = 2000 + int(year)
    elif re.fullmatch(r'20[0-9]{2}', year):
        full_year = int(year)
    else:
        return None, "The year must be like 26 or 2026."
    try:
        value = date(full_year, month_number, int(day))
    except ValueError:
        return None, f"There is no {int(day)} {MONTHS[month_number - 1][:3].title()} {full_year}."
    if value > today:
        return None, "That date is in the future."
    return value, None


_AMOUNT = re.compile(r'[0-9]+(\.[0-9]{0,2})?|\.[0-9]{1,2}')


def read_amount(text, model, field_name):
    """
    (Decimal, None), (None, None) when blank, or (None, why).

    ⚠ COMMAS AND ₹ ARE ACCEPTED HERE, and nowhere else in the app. The paper
    prints "22,300.00" and the typist copies what they see. Elsewhere a comma is
    refused because `parseFloat("1,000")` is 1; this reader can only DROP a
    comma ("2,820.00" is 2820.00), and a figure with more than two decimals
    ("2.820") is refused rather than read as 2.82. A figure misread off the
    paper is the typist's to catch: the form works the TOTAL out as they type
    and asks them to check it against the XL bill.

    The bound is read from the column, like every typed amount (`money.py`).
    """
    raw = ''.join(str(text or '').split()).replace(',', '').replace('\u20b9', '')
    if not raw:
        return None, None
    if not _AMOUNT.fullmatch(raw):
        return None, "not a number"
    value = parse_money(raw, model, field_name, allow_zero=True)
    if value is None:
        return None, "too large"
    return value, None


def read_quantity(text):
    """(Decimal, None), (None, None) when blank, or (None, why). Never zero —
    a part fitted is at least some of something; blank means one."""
    from .models import OldBillPartLine

    raw = ''.join(str(text or '').split())
    if not raw:
        return None, None
    if not _AMOUNT.fullmatch(raw):
        return None, "not a number"
    if Decimal(raw) == 0:
        return None, "zero"
    value = parse_money(raw, OldBillPartLine, 'quantity')
    if value is None:
        return None, "too large"
    return value, None


# ---------------------------------------------------------------------------
# THE WHOLE FORM — read and checked in one place
# ---------------------------------------------------------------------------

def read_old_bill(data, today, exclude_pk=None):
    """
    Everything the Old Bill form posted, read and checked.

    Returns (values, problems). `problems` is a list of sentences in the order
    the paper is read, top to bottom; `values` is only usable when it is empty.
    Pure apart from the duplicate-number lookup, so it is tested without a
    request.

    The rows arrive as parallel lists (`job`, `part_name`, `part_qty`,
    `part_amount`) — one entry per row on screen, blank rows included.
    """
    from .models import OldBill, OldBillJobLine, OldBillPartLine

    problems = []

    # -- DATE and # -------------------------------------------------------
    bill_date, why = read_date(data.get('day'), data.get('month'), data.get('year'), today)
    if why:
        problems.append(why)

    num_year = (data.get('num_year') or '').strip()
    num_seq = (data.get('num_seq') or '').strip()
    bill_number = ''
    if not num_year and not num_seq:
        problems.append("Enter the bill number.")
    elif not re.fullmatch(r'[0-9]{2}', num_year):
        problems.append("The year in the bill number must be two digits, like 26.")
    elif not re.fullmatch(r'[0-9]{1,5}', num_seq) or int(num_seq) == 0:
        problems.append("The bill number must be like 097.")
    else:
        bill_number = format_bill_number(num_year, num_seq)
        why = bill_number_problem(bill_number, bill_date, exclude_pk=exclude_pk)
        if why:
            problems.append(why)

    # -- BILL TO / VEHICLE INFO -------------------------------------------
    registration = fit_text(' '.join((data.get('registration_number') or '').split()),
                            OldBill, 'registration_number')
    if not registration:
        problems.append("Enter the registration number.")

    def text(name):
        return fit_text(' '.join((data.get(name) or '').split()), OldBill, name)

    # -- JOB PERFORMED ----------------------------------------------------
    jobs = [
        fit_text(' '.join(line.split()), OldBillJobLine, 'description')
        for line in data.getlist('job') if line.strip()
    ]
    labour, why = read_amount(data.get('labour_amount'), OldBill, 'labour_amount')
    if why:
        problems.append(f"The job SUBTOTAL is {why}.")

    # -- PART NAME ----------------------------------------------------------
    parts = []
    amounts_readable = why is None
    rows = zip_longest(
        data.getlist('part_name'), data.getlist('part_qty'), data.getlist('part_amount'),
        fillvalue='',
    )
    for row_number, (name, qty, amount) in enumerate(rows, start=1):
        name = ' '.join((name or '').split())
        qty, amount = (qty or '').strip(), (amount or '').strip()
        if not (name or qty or amount):
            continue                                   # an untouched row
        label = f"Part row {row_number}" + (f" ({name})" if name else "")
        if not name:
            # A row with content but no name is REFUSED, never dropped — the
            # job card's own rule. Dropping it would lose an amount silently.
            problems.append(f"{label} has a quantity or amount but no part name.")
            amounts_readable = False     # its amount is in no sum, so no sum is honest
        quantity, why = read_quantity(qty)
        if why:
            problems.append(f"{label}: the quantity is {why}.")
        value, why = read_amount(amount, OldBillPartLine, 'amount')
        if why:
            problems.append(f"{label}: the amount is {why}.")
            amounts_readable = False
        if name:
            parts.append((fit_text(name, OldBillPartLine, 'name'), quantity, value))

    # -- TOTAL ------------------------------------------------------------
    # WORKED OUT, NEVER TYPED (the owners' call, 2026-09-17): labour + every
    # part amount, exactly what `OldBill.update_totals()` writes on save. The
    # form shows it and asks for it to be checked against the XL bill — a
    # wrong figure is fixed in its own line, never in the total. It was typed
    # as a check until then. A total too large for its column is still refused
    # here: the SQLite-accepts / Postgres-500s split (`money.py`).
    if amounts_readable:
        total = (labour or Decimal('0')) + sum((v for _, _, v in parts if v is not None), Decimal('0'))
        if parse_money(total, OldBill, 'total_amount', allow_zero=True) is None:
            problems.append("The TOTAL is too large.")

    if not jobs and not parts and not labour:
        problems.append("There is nothing on this bill yet — add the work or the parts.")

    values = {
        'bill_date': bill_date,
        'bill_number': bill_number,
        'registration_number': registration,
        'brand_name': text('brand_name'),
        'model_name': text('model_name'),
        'mileage': text('mileage'),
        'labour_amount': labour or Decimal('0'),
        'jobs': jobs,
        'parts': parts,
    }
    return values, problems
