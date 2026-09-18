"""
OLD BILLS — typing in the bills the workshop wrote in Excel before the system.

History only. An old bill is connected to nothing that counts money or stock
(see the `OldBill` model), so these views do what they look like: read a form,
save a bill, list what is in. Every rule about what may be saved lives in
`workshop/old_bills.py`; nothing here decides whether a date, a number or an
amount is acceptable.

Built for typing about eight hundred bills in a row, so three things matter
more than usual: the form reads top to bottom in the paper's own order, "Save
& add next" keeps the month and year, and a refusal keeps everything typed.
"""

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from ..decorators import office_required
from ..invoice import build_old_bill
from ..models import OldBill, OldBillJobLine, OldBillPartLine, SparePart
from ..old_bill_pdf import UnreadablePdf, fit_to_master_list, read_bill_pdf
from ..old_bills import (
    bill_number_problem, format_bill_number, last_excel_bill, read_date, read_old_bill,
    split_bill_number,
)
from ..return_to import safe_return


def _ordered_lines():
    """Both line relations in the order they were typed. Neither model declares
    an ordering, so without this one bill could list its rows two ways."""
    return [
        Prefetch('job_lines', queryset=OldBillJobLine.objects.order_by('pk')),
        Prefetch('part_lines', queryset=OldBillPartLine.objects.order_by('pk')),
    ]


def _open_rows(rows, blank):
    """
    The rows that hold something, plus ONE open row.

    One, not the paper's nine and eleven: a screen of empty boxes was scroll
    for nothing. The page adds the next row by itself the moment the open one
    is typed into, so there is always exactly one waiting.
    """
    rows = list(rows)
    while rows and not any(str(value).strip() for value in rows[-1].values()):
        rows.pop()
    return rows + [blank]


def _form_from_post(post):
    """What was typed, exactly, for re-drawing the form after a refusal."""
    jobs = [{'text': text} for text in post.getlist('job')]
    names, qtys, amounts = post.getlist('part_name'), post.getlist('part_qty'), post.getlist('part_amount')
    parts = [
        {'name': names[i] if i < len(names) else '',
         'qty': qtys[i] if i < len(qtys) else '',
         'amount': amounts[i] if i < len(amounts) else ''}
        for i in range(max(len(names), len(qtys), len(amounts)))
    ]
    fields = {key: post.get(key, '') for key in (
        'day', 'month', 'year', 'num_year', 'num_seq',
        'registration_number', 'brand_name', 'model_name', 'mileage',
        'labour_amount',
        # The TOTAL printed on a PDF the form was filled from, so a refusal
        # keeps comparing against it. Shown, never read by any rule.
        'pdf_total',
    )}
    return fields, jobs, parts


def _plain(value):
    """A stored amount as the box shows it: 22300, 75.50 — no trailing .00."""
    if value is None:
        return ''
    text = f'{value:f}'
    return text[:-3] if text.endswith('.00') else text


def _form_from_bill(bill):
    """A saved bill, as the form's boxes."""
    year, seq = split_bill_number(bill.bill_number)
    fields = {
        'day': str(bill.bill_date.day),
        'month': str(bill.bill_date.month),
        'year': f'{bill.bill_date.year % 100:02d}',
        'num_year': f'{year:02d}',
        'num_seq': f'{seq:03d}',
        'registration_number': bill.registration_number,
        'brand_name': bill.brand_name,
        'model_name': bill.model_name,
        'mileage': bill.mileage,
        'labour_amount': _plain(bill.labour_amount) if bill.labour_amount else '',
    }
    jobs = [{'text': line.description} for line in bill.job_lines.all()]
    parts = [
        {'name': line.name, 'qty': _plain(line.quantity), 'amount': _plain(line.amount)}
        for line in bill.part_lines.all()
    ]
    return fields, jobs, parts


def _save(bill, values, user):
    """Write the bill and replace its lines, all or nothing.

    Replacing rather than matching rows up is safe here and only here: nothing
    anywhere points at an old bill's line, no signal listens to one, and no
    money moves — so a line's identity carries no meaning worth preserving.
    """
    with transaction.atomic():
        if bill is None:
            bill = OldBill(created_by=user)
        for field in ('bill_date', 'bill_number', 'registration_number', 'brand_name',
                      'model_name', 'mileage', 'labour_amount'):
            setattr(bill, field, values[field])
        bill.save()
        bill.job_lines.all().delete()
        bill.part_lines.all().delete()
        OldBillJobLine.objects.bulk_create(
            OldBillJobLine(old_bill=bill, description=text) for text in values['jobs']
        )
        OldBillPartLine.objects.bulk_create(
            OldBillPartLine(old_bill=bill, name=name, quantity=quantity, amount=amount)
            for name, quantity, amount in values['parts']
        )
        bill.update_totals()
    return bill


def _known_part_names():
    """
    The names PART NAME may suggest besides this bill's own job lines: the
    warehouse categories (a live bill prints a stock part under its category,
    and so did Excel) and the Spare Parts master list.

    READ ONLY. An old bill never adds a name to either list — eight hundred
    bills of Excel typing would fill the master list with every slip in them.
    """
    from inventory.models import Category      # the local import the app uses for inventory

    return {
        'categories': list(Category.objects.order_by('name').values_list('name', flat=True)),
        'spares': list(SparePart.objects.order_by('name').values_list('name', flat=True)),
    }


def _render_form(request, bill, fields, jobs, parts, problems, back_url, from_pdf=False):
    return render(request, 'workshop/old_bills/old_bill_form.html', {
        'bill': bill,
        'is_new': bill is None,
        # Drawn at the Fill-from-PDF address, so Save must name the Add page.
        'from_pdf': from_pdf,
        'f': fields,
        'jobs': _open_rows(jobs, {'text': ''}),
        'parts': _open_rows(parts, {'name': '', 'qty': '', 'amount': ''}),
        'problems': problems,
        'back_url': back_url,
        # A named destination when it is the list, plain "Back" when the page
        # was opened from somewhere else (a Car Profile, the Add page).
        'back_label': 'Old Bills' if back_url.startswith(reverse('old_bill_list')) else 'Back',
        'saved': _last_saved(request) if bill is None else None,
        'known_parts': _known_part_names(),
    })


def _last_saved(request):
    """The bill just saved, named on the Add page with a way back into it."""
    pk = request.GET.get('saved', '')
    return OldBill.objects.filter(pk=pk).first() if pk.isdigit() else None


# -----------------------------------------------------------------------------
# ADD / EDIT
# -----------------------------------------------------------------------------

@office_required
def old_bill_add(request):
    list_url = reverse('old_bill_list')
    if request.method == 'POST':
        values, problems = read_old_bill(request.POST, timezone.localdate())
        if not problems:
            try:
                bill = _save(None, values, request.user)
            except IntegrityError:
                # Two people saved the same number in the same moment. The
                # database refused the second; say so in the usual words.
                problems = [bill_number_problem(values['bill_number'], values['bill_date'])
                            or "That bill number was just saved by someone else."]
            else:
                messages.success(request, f"Saved {bill.bill_number}.")
                month = bill.bill_date
                if request.POST.get('after') == 'close':
                    return redirect(f"{list_url}?{urlencode({'month': f'{month:%Y-%m}'})}")
                # Save & add next: the next bill in the pile is usually the same
                # month, so the month and year stay and the cursor starts on Day.
                return redirect(f"{reverse('old_bill_add')}?" + urlencode({
                    'month': month.month, 'year': f'{month.year % 100:02d}', 'saved': bill.pk,
                }))
        fields, jobs, parts = _form_from_post(request.POST)
        messages.error(request, "Not saved — see what needs fixing at the top.")
        return _render_form(request, None, fields, jobs, parts, problems, list_url)

    month, year = request.GET.get('month', ''), request.GET.get('year', '')
    fields = {
        'month': month if month.isdigit() and 1 <= int(month) <= 12 else '',
        'year': year if year.isdigit() and len(year) == 2 else '',
    }
    fields['num_year'] = fields['year']
    return _render_form(request, None, fields, [], [], [], list_url)


@office_required
@require_POST
def old_bill_from_pdf(request):
    """
    FILL FROM PDF — the Add form, drawn filled from the bill's own PDF.

    Saves nothing and keeps no file: the PDF is read in memory and dropped, and
    the page is the ordinary Add form with its boxes filled. The person checks
    it against the PDF and presses Save, and `old_bill_add` then applies every
    rule exactly as for a typed bill. What the PDF could not give is named, and
    a number already in is said now rather than after the checking is done.
    """
    list_url = reverse('old_bill_list')
    add_url = reverse('old_bill_add')
    upload = request.FILES.get('pdf')
    if upload is None:
        messages.error(request, "Choose the bill's PDF first.")
        return redirect(add_url)
    try:
        got = read_bill_pdf(upload)
    except UnreadablePdf:
        messages.error(request, f"Could not read {upload.name} — type this bill in by hand.")
        return redirect(add_url)

    fields = got['fields']
    fields['brand_name'], fields['model_name'] = fit_to_master_list(fields['brand_name'], fields['model_name'])
    fields['pdf_total'] = got['total']

    messages.success(request, f"Filled from {upload.name}. Check every line against the PDF, then save.")
    if got['missing']:
        messages.warning(request, f"Not found in the PDF: {', '.join(got['missing'])} — type it in.")
    if fields['num_year'] and fields['num_seq']:
        bill_date, _ = read_date(fields['day'], fields['month'], fields['year'], timezone.localdate())
        taken = bill_number_problem(format_bill_number(fields['num_year'], fields['num_seq']), bill_date)
        if taken:
            messages.warning(request, taken)

    return _render_form(request, None, fields, [{'text': t} for t in got['jobs']], got['parts'],
                        [], list_url, from_pdf=True)


@office_required
def old_bill_edit(request, pk):
    bill = get_object_or_404(OldBill.objects.prefetch_related(*_ordered_lines()), pk=pk)
    back_url = safe_return(request) or f"{reverse('old_bill_list')}?{urlencode({'month': f'{bill.bill_date:%Y-%m}'})}"

    if request.method == 'POST':
        values, problems = read_old_bill(request.POST, timezone.localdate(), exclude_pk=bill.pk)
        if not problems:
            try:
                bill = _save(bill, values, request.user)
            except IntegrityError:
                problems = [bill_number_problem(values['bill_number'], values['bill_date'], exclude_pk=bill.pk)
                            or "That bill number was just saved by someone else."]
            else:
                messages.success(request, f"Saved {bill.bill_number}.")
                return redirect(back_url)
        fields, jobs, parts = _form_from_post(request.POST)
        messages.error(request, "Not saved — see what needs fixing at the top.")
        return _render_form(request, bill, fields, jobs, parts, problems, back_url)

    fields, jobs, parts = _form_from_bill(bill)
    return _render_form(request, bill, fields, jobs, parts, [], back_url)


@office_required
def old_bill_invoice(request, pk):
    """
    One old bill, reprinted on the invoice's own sheet.

    Rendered by the All Invoices page with a single sheet, so the bill is drawn
    by exactly the partial every other bill is — there is no second design of
    a bill to drift. The toolbar gains an Edit, because this is where an old
    bill is opened from its Car Profile.
    """
    bill = get_object_or_404(OldBill.objects.prefetch_related(*_ordered_lines()), pk=pk)
    doc = build_old_bill(bill)
    here = request.get_full_path()
    return render(request, 'workshop/car_profiles/all_invoices_print.html', {
        'sheets': [{'doc': doc, 'jobcard': bill}],
        'count': 1,
        'chip_label': 'Old bill',
        'edit_url': f"{reverse('old_bill_edit', args=[bill.pk])}?{urlencode({'back': here})}",
        'registration': bill.registration_number,
        'document_title': doc['document_title'],
        'back_url': safe_return(request) or reverse('car_profile_detail', args=[bill.registration_number]),
        'issued': timezone.localdate(),
    })


@office_required
@require_POST
def old_bill_delete(request, pk):
    """
    Remove an old bill. Permanent, confirmed, and NOT written to Deletion
    History — the Estimate's reasoning: it moves no money, sits in no ledger
    and appears in no report, and `DeletionLog.record()` pushes a CRITICAL alert
    to both owners' phones. Correcting a typing slip during an eight-hundred-bill
    entry must not buzz two phones.
    """
    bill = get_object_or_404(OldBill, pk=pk)
    number, month = bill.bill_number, bill.bill_date
    bill.delete()
    messages.success(request, f"Deleted {number}.")
    return redirect(f"{reverse('old_bill_list')}?{urlencode({'month': f'{month:%Y-%m}'})}")


# -----------------------------------------------------------------------------
# LIST
# -----------------------------------------------------------------------------

PAGE_SIZE = 45
MONTH_NAMES = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
#: A year names its missing numbers only once this few are left — while most of
#: a year is still untyped, a list of them is noise and the count says enough.
MISSING_NAMED = 12


def _years(selected):
    """
    One block per year that has old bills, newest first: a count for every
    month, and the numbers in that year's JB sequence that are not in yet.

    The gaps are what make eight hundred bills safe to split between three
    people. Excel numbered every bill of a year from 001 without skipping, so a
    number missing below the highest one typed — or below the last Excel bill,
    in the year the system went live — is a paper bill nobody has typed yet.
    """
    counts = {
        (row['month'].year, row['month'].month): row['n']
        for row in OldBill.objects.annotate(month=TruncMonth('bill_date'))
        .values('month').annotate(n=Count('id')).order_by()
    }
    if not counts:
        return []

    numbers = {}
    for text in OldBill.objects.values_list('bill_number', flat=True):
        parts = split_bill_number(text)
        if parts:
            numbers.setdefault(2000 + parts[0], set()).add(parts[1])

    last = last_excel_bill()
    years = []
    for year in range(max(y for y, _ in counts), min(y for y, _ in counts) - 1, -1):
        seqs = numbers.get(year, set())
        up_to = max(seqs, default=0)
        if last and 2000 + last[0] == year:
            up_to = max(up_to, last[1])
        missing = [n for n in range(1, up_to + 1) if n not in seqs]
        years.append({
            'year': year,
            'typed': sum(n for (y, _), n in counts.items() if y == year),
            'up_to': format_bill_number(year % 100, up_to) if up_to else '',
            'missing_count': len(missing),
            'missing_named': (
                [format_bill_number(year % 100, n) for n in missing]
                if len(missing) <= MISSING_NAMED else []
            ),
            'months': [
                {
                    'key': f'{year}-{month:02d}',
                    'label': MONTH_NAMES[month - 1],
                    'count': counts.get((year, month), 0),
                    'selected': (year, month) == selected,
                }
                for month in range(1, 13)
            ],
        })
    return years


def _chosen_month(request):
    """(year, month) from ?month=YYYY-MM, or the newest month holding a bill."""
    text = request.GET.get('month', '')
    try:
        year, month = (int(part) for part in text.split('-'))
        if 2000 <= year <= 2099 and 1 <= month <= 12:
            return year, month
    except ValueError:
        pass
    newest = OldBill.objects.order_by('-bill_date').values_list('bill_date', flat=True).first()
    today = timezone.localdate()
    return (newest.year, newest.month) if newest else (today.year, today.month)


@office_required
def old_bill_list(request):
    """
    Every old bill, a month at a time — the page three people split the pile on.

    A month is naturally bounded (about thirty bills), so the month view needs no
    pager; a search reaches across every month and is paged like every other
    list. Within a month the bills run in DATE and NUMBER order, the order the
    paper file is kept in, so the screen can be ticked off against it.
    """
    q = ' '.join((request.GET.get('q') or '').split())
    here = request.get_full_path()
    context = {
        'q': q,
        'here': here,
        'total_count': OldBill.objects.count(),
        'last_excel': format_bill_number(*last_excel_bill()) if last_excel_bill() else '',
    }

    if q:
        found = OldBill.objects.all()
        for word in q.split():
            found = found.filter(
                Q(bill_number__icontains=word) | Q(registration_number__icontains=word)
                | Q(customer_name__icontains=word) | Q(brand_name__icontains=word)
                | Q(model_name__icontains=word)
            )
        page_obj = Paginator(found.order_by('-bill_date', '-pk'), PAGE_SIZE).get_page(request.GET.get('page'))
        context.update({'page_obj': page_obj, 'rows': list(page_obj.object_list)})
    else:
        selected = _chosen_month(request)
        rows = sorted(
            OldBill.objects.filter(bill_date__year=selected[0], bill_date__month=selected[1]),
            key=lambda bill: (bill.bill_date, split_bill_number(bill.bill_number) or (0, 0)),
        )
        context.update({
            'years': _years(selected),
            'rows': rows,
            'month_label': f'{MONTH_NAMES[selected[1] - 1]} {selected[0]}',
        })

    return render(request, 'workshop/old_bills/old_bill_list.html', context)
