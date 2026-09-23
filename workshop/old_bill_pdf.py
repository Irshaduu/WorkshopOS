"""
FILL FROM PDF — reading an Excel-era bill off the PDF the workshop kept.

The owners saved every Excel bill as a PDF ("Microsoft Print To PDF") and sent
that to the customer, so the PDF IS the record the Old Bills form is typed from.
This reads one and hands back what the boxes should hold — nothing more.

⚠ IT FILLS BOXES AND NOTHING ELSE. It saves nothing, stores no file, and
decides nothing about what may be saved: the form is drawn filled, a person
checks it against the PDF and presses Save, and every rule then runs exactly as
it does for a typed bill (`old_bills.read_old_bill`). A PDF this cannot read
leaves the form empty and the bill is typed by hand, as before.

Built to the ONE template the Excel bills were printed from — DATE, #, BILL TO /
VEHICLE INFO, JOB PERFORMED with one SUBTOTAL, PART NAME with QTY, UNIT PRICE
and AMOUNT, then TOTAL — over any number of pages. The only change the template
is known to have had is its heading's spelling ("JOB PERFOMED" became "JOB
PERFORMED"), so no heading is matched by its exact wording.

The PDF's own printed TOTAL comes back too: the form compares it with the total
it works out, so a line read wrongly shows up red before anybody saves.
"""

import logging
import re
from decimal import Decimal, InvalidOperation

from .old_bills import read_month

# pypdf warns about every quirk it tolerates ("invalid pdf header", repaired
# cross-references). Here those are either read fine or answered by "could not
# read — type it in", so they are not worth a line in errors.log each time.
logging.getLogger('pypdf').setLevel(logging.ERROR)


#: A bill PDF is well under 1 MB (the four samples were 0.6 MB each, most of it
#: the embedded logo and fonts). Anything far bigger is not one of these bills.
MAX_BYTES = 5 * 1024 * 1024
#: The longest sample is two pages; a bill of twenty would be ~500 parts.
MAX_PAGES = 20


class UnreadablePdf(Exception):
    """Not a bill PDF this can read — the form is left for typing by hand."""


def pdf_text(upload):
    """
    The PDF's text laid out as it looks on the page — every page, in order.

    Layout mode keeps the columns apart with runs of spaces; the plain text
    mode runs a row's cells together ("Spark plugs61,980.00" for qty 6 at
    1,980.00), which is exactly the misreading this must never make.
    """
    from pypdf import PdfReader     # only this screen needs it

    try:
        reader = PdfReader(upload)
        if len(reader.pages) > MAX_PAGES:
            raise UnreadablePdf('too many pages')
        return '\n'.join(page.extract_text(extraction_mode='layout') for page in reader.pages)
    except UnreadablePdf:
        raise
    except Exception as exc:        # a damaged or encrypted file, or not a PDF at all
        raise UnreadablePdf(str(exc)) from exc


# A figure as the bill prints it: 2,820.00 · 2,29,836.00 · 1200.00 · 8 · 1.5
_NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
# Money always carries exactly two decimals (Excel's number format on those
# columns); a QTY is printed as typed, so "8" or "1.5".
_MONEY = re.compile(r'\d[\d,]*\.\d{2}')
_COLUMNS = re.compile(r'\s{2,}')


def _plain(text):
    """'2,29,836.00' → '229836', '75.50' → '75.50', '8' → '8' — the box's own shape."""
    try:
        value = Decimal(text.replace(',', ''))
    except InvalidOperation:
        return text
    plain = f'{value:f}'
    return plain[:-3] if plain.endswith('.00') else plain


def _cells(line):
    """A line's cells, split on the runs of spaces layout mode leaves between
    columns. ₹ is dropped: it has a cell of its own on the bill, and which side
    of the amount it lands in the text differs from row to row."""
    return [cell for cell in _COLUMNS.split(line.replace('₹', ' ').strip()) if cell]


def _figures_at_end(cells):
    """(the words, the figures after them) — 'Engine oil | 8 | 1200.00 | 9,600.00'
    is ('Engine oil', ['8', '1200.00', '9,600.00'])."""
    figures = []
    while cells and _NUMBER.fullmatch(cells[-1]):
        figures.insert(0, cells.pop())
    return ' '.join(' '.join(cells).split()), figures


def _part(line):
    """One PART NAME row as the form's name, qty and amount boxes.

    QTY and AMOUNT are read; UNIT PRICE is skipped, because the form never
    types it (the bill works it out from the amount). Which figure is which is
    told by its shape — money has two decimals, a quantity does not — so a row
    with a quantity and no unit price (Distilled water · 2 · 50.00) and one with
    all three (Engine oil · 8 · 1200.00 · 9,600.00) both come out right.
    """
    name, figures = _figures_at_end(_cells(line))
    qty = amount = ''
    if len(figures) == 3:
        qty, amount = figures[0], figures[2]
    elif len(figures) == 2:
        if _MONEY.fullmatch(figures[0]):       # a unit price and no quantity
            amount = figures[1]
        else:
            qty, amount = figures
    elif len(figures) == 1:
        if _MONEY.fullmatch(figures[0]):
            amount = figures[0]
        else:
            qty = figures[0]
    elif figures:
        # More figures than the bill has columns: keep them in the name for the
        # person to sort out, rather than guess which one is the amount.
        name = ' '.join([name] + figures)
    return {'name': name, 'qty': _plain(qty) if qty else '', 'amount': _plain(amount) if amount else ''}


def _search(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match


def read_bill_text(text):
    """
    Everything the form needs, from the laid-out text of one bill.

    Returns a dict: `fields` (the form's own box names), `jobs`, `parts`,
    `total` (the TOTAL printed on the PDF, '' when there is none) and `missing`
    (what the form must be told was not found). Raises UnreadablePdf when the
    text is not this bill's template at all.
    """
    lines = text.splitlines()
    head = next((i for i, line in enumerate(lines) if re.search(r'\bJOB\s+PERF', line, re.I)), None)
    if head is None:
        raise UnreadablePdf('no JOB PERFORMED heading')

    top = '\n'.join(lines[:head])
    fields = {key: '' for key in (
        'day', 'month', 'year', 'num_year', 'num_seq',
        'registration_number', 'brand_name', 'model_name', 'mileage', 'labour_amount',
    )}
    missing = []

    # DATE: 10-Apr-2026 — or, on the workshop's earliest bills, 09-07-2024.
    # A numeric date is DAY first: those PDFs were created on 9 July 2024.
    date = _search(r'DATE:\s*(\d{1,2})\s*-\s*([A-Za-z]{3,9}|\d{1,2})\s*-\s*(\d{4}|\d{2})', top)
    month = read_month(date.group(2)) if date else None
    if date and month:
        fields.update(day=str(int(date.group(1))), month=str(month), year=date.group(3)[-2:])
    else:
        missing.append('the date')

    # #: JB-26-097
    number = _search(r'#:\s*JB\s*-\s*(\d{2})\s*-\s*(\d{1,5})', top)
    if number:
        fields.update(num_year=number.group(1), num_seq=number.group(2).zfill(3))
    else:
        missing.append('the bill number')

    # VEHICLE INFO — REG NO sits on NAME's line and MODEL on MAKE's, so each
    # value runs from its label to the end of the line.
    reg = _search(r'REG\s*NO\s*:(.*)$', top)
    fields['registration_number'] = ' '.join(reg.group(1).split()) if reg else ''
    if not fields['registration_number']:
        missing.append('the registration')
    make = _search(r'MAKE\s*:(.*?)(?:MODEL\s*:(.*))?$', top)
    if make:
        fields['brand_name'] = ' '.join(make.group(1).split())
        fields['model_name'] = ' '.join((make.group(2) or '').split())
    if not fields['model_name']:
        model = _search(r'MODEL\s*:(.*)$', top)
        fields['model_name'] = ' '.join(model.group(1).split()) if model else ''
    mileage = _search(r'MIL(?:E)?AGE\s*:(.*)$', top)
    fields['mileage'] = ' '.join(mileage.group(1).split()) if mileage else ''

    # JOB PERFORMED → SUBTOTAL (labour) → PART NAME → SUBTOTAL → TOTAL, read
    # straight down every page, so a list running onto the next page carries on.
    jobs, parts, total = [], [], ''
    state = 'jobs'
    for line in lines[head + 1:]:
        cells = _cells(line)
        if not cells:
            continue
        if state == 'jobs':
            if cells[0].upper() == 'SUBTOTAL':
                money = [c for c in cells[1:] if _MONEY.fullmatch(c)]
                fields['labour_amount'] = _plain(money[-1]) if money else ''
                state = 'between'
            else:
                words, _ = _figures_at_end(cells)
                if words:
                    jobs.append(words)
        elif state == 'between':
            if re.search(r'\bPART\s+NAME\b', line, re.I):
                state = 'parts'
        elif state == 'parts':
            if cells[0].upper() == 'SUBTOTAL':
                state = 'end'
            else:
                row = _part(line)
                if row['name'] or row['amount']:
                    parts.append(row)
        if state == 'end':
            found = re.search(r'(?<![A-Z])TOTAL\s+(\d[\d,]*\.\d{2})', line.replace('₹', ' '), re.I)
            if found:
                total = _plain(found.group(1))
                break

    if not total:
        missing.append('the TOTAL')
    return {'fields': fields, 'jobs': jobs, 'parts': parts, 'total': total, 'missing': missing}


def read_bill_pdf(upload):
    """The uploaded PDF, read. Raises UnreadablePdf when it cannot be."""
    if upload.size > MAX_BYTES:
        raise UnreadablePdf('too large')
    return read_bill_text(pdf_text(upload))


# ---------------------------------------------------------------------------
# MAKE and MODEL — the master list's spelling
# ---------------------------------------------------------------------------

def _key(text):
    """'Mercedes Benz', 'mercedes-benz' and 'MERCEDES BENZ' are one name."""
    return re.sub(r'[\s\-]+', '', text or '').lower()


def fit_to_master_list(brand, model):
    """
    (brand, model) in the master list's spelling when the list holds them,
    comparing without spaces, hyphens or capitals — the bills print "Mercedes
    Benz", the list says "Mercedes-Benz". Anything the list does not hold comes
    back exactly as printed.
    """
    from .models import CarBrand, CarModel

    found = next((b for b in CarBrand.objects.all() if brand and _key(b.name) == _key(brand)), None)
    if found is None:
        return brand, model
    names = CarModel.objects.filter(brand=found).values_list('name', flat=True)
    model = next((name for name in names if model and _key(name) == _key(model)), model)
    return found.name, model
