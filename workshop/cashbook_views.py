from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from datetime import date, timedelta
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Q, Sum, Count, Case, When, Value, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal, InvalidOperation
from .decorators import office_required
from .models import CashbookEntry, DeletionLog
from .money import parse_money, fit_text


# One page of the ledger. 45 matches every other list view in the app.
#
# This replaced a flat 300-row cap. The cap was there for performance, but the
# totals above the list were always computed from the whole period, so any
# period holding more rows than the cap printed a total that could not be added
# up from what was on screen — and the only way to reach the missing rows was to
# narrow the date range until they fitted. Paging is the same protection against
# a huge render with none of that: every row stays reachable, so the total and
# the list can never disagree.
PAGE_SIZE = 45

# Summing DecimalField(10,2) rows needs somewhere wider to land than the column
# itself, or a long period overflows the declared precision on PostgreSQL.
_MONEY = DecimalField(max_digits=20, decimal_places=2)
_ZERO = Value(Decimal('0'), output_field=_MONEY)

TYPE_CHOICES = ('all', 'expense', 'income')

# The shared calendar-aligned vocabulary every filtered list in the app uses.
# Whitelisted rather than matched by if/elif alone: an unrecognised value used
# to fall through every branch, leaving the queryset unfiltered while the
# heading still read "Today" — an all-time total labelled as one day's, on the
# page an owner reads to see where the money went.
FILTER_CHOICES = ('today', 'this_week', 'this_month', 'this_year',
                  'last_week', 'last_month', 'last_year', 'custom')

# What the window is CALLED, resolved here rather than as an eight-branch
# `{% if %}` in the template. It was written out twice in `_stats.html` — once
# per figure — which is two copies of one fact, free to drift into describing
# different periods on a headline whose whole job is to say which period the
# two figures belong to.
FILTER_LABELS = {
    'today': 'Today',
    'this_week': 'This Week',
    'this_month': 'This Month',
    'this_year': 'This Year',
    'last_week': 'Last Week',
    'last_month': 'Last Month',
    'last_year': 'Last Year',
}

# Longest term worth sending to the database. The name it searches is 100
# characters wide, so anything past that cannot match a category anyway.
MAX_SEARCH_LEN = 100


def _apply_period(qs, filter_type, request, today):
    """
    Narrow `qs` to the requested date window.

    Returns (qs, start_date_str, end_date_str) — the two strings are only
    non-empty for a custom range, and are echoed back into the picker.
    """
    start_date_str = ''
    end_date_str = ''

    if filter_type == 'today':
        qs = qs.filter(date=today)

    elif filter_type == 'this_week':
        start = today - timedelta(days=today.weekday())
        qs = qs.filter(date__gte=start)

    elif filter_type == 'this_month':
        qs = qs.filter(date__year=today.year, date__month=today.month)

    elif filter_type == 'this_year':
        qs = qs.filter(date__year=today.year)

    elif filter_type == 'last_week':
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=6)
        qs = qs.filter(date__gte=start, date__lte=end)

    elif filter_type == 'last_month':
        first_of_this = today.replace(day=1)
        last_of_last = first_of_this - timedelta(days=1)
        first_of_last = last_of_last.replace(day=1)
        qs = qs.filter(date__gte=first_of_last, date__lte=last_of_last)

    elif filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1, day=1)
        end = today.replace(year=today.year - 1, month=12, day=31)
        qs = qs.filter(date__gte=start, date__lte=end)

    elif filter_type == 'custom':
        start_date_str = request.GET.get('start_date', '')
        end_date_str = request.GET.get('end_date', '')
        if start_date_str and end_date_str:
            try:
                qs = qs.filter(date__gte=date.fromisoformat(start_date_str),
                               date__lte=date.fromisoformat(end_date_str))
            except ValueError:
                pass

    return qs, start_date_str, end_date_str


def _apply_search(qs, q):
    """
    Free-text search across the whole visible content of a row.

    Everything a reader can see on a ledger line is searchable, so nobody has
    to remember which box a word was typed into: the name, the note, and the
    payment method. A purely numeric term also matches the amount exactly,
    which is how a figure gets looked up off a paper bill.
    """
    if not q:
        return qs
    lookup = Q(category__icontains=q) | Q(description__icontains=q)

    # The method is stored as a code and shown as a label — 'TRANSFER' on the
    # row reads "Bank Transfer" — so matching the column directly would fail on
    # the word actually printed. Both are searched. Three characters minimum:
    # one or two would match a whole method and flood the results with rows
    # whose name has nothing to do with what was typed.
    if len(q) >= 3:
        term = q.lower()
        methods = [code for code, label in CashbookEntry.PAYMENT_METHODS
                   if term in label.lower() or term in code.lower()]
        if methods:
            lookup |= Q(payment_method__in=methods)

    # Bounded by the column, same rule as every other typed rupee figure here:
    # a word, an oversized figure or an Infinity comes back None and simply
    # doesn't widen the search, rather than reaching the database as a numeric.
    amount = parse_money(q, CashbookEntry, 'amount')
    if amount is not None:
        lookup |= Q(amount=amount)
    return qs.filter(lookup)


@office_required
def cashbook_view(request):
    """
    The General Expenses & Income ledger — Office and Owner only.

    One stream, not two lists. Money in and money out are the same ledger read
    from opposite sides, and splitting them into two identical-looking sections
    meant two of everything on screen (two totals, two add forms, two lists)
    for a page whose income side is used a handful of times a month. The type
    chips narrow the stream; the totals above it always describe the whole
    period, so switching the view can never appear to change the money.
    """
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    filter_type = request.GET.get('filter', 'today')
    if filter_type not in FILTER_CHOICES:
        filter_type = 'today'
    q = (request.GET.get('q') or '').strip()[:MAX_SEARCH_LEN]
    entry_type_filter = (request.GET.get('type') or 'all').lower()
    if entry_type_filter not in TYPE_CHOICES:
        entry_type_filter = 'all'

    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'

    qs = CashbookEntry.objects.all()
    qs, start_date_str, end_date_str = _apply_period(qs, filter_type, request, today)
    qs = _apply_search(qs, q)

    # Totals AND the chip counts in one round trip. They deliberately ignore the
    # type chip: the headline is what the period did, and a chip is a way of
    # reading it, not a different period.
    summary = qs.aggregate(
        income=Coalesce(Sum(Case(When(entry_type='INCOME', then='amount'),
                                 output_field=_MONEY)), _ZERO),
        expense=Coalesce(Sum(Case(When(entry_type='EXPENSE', then='amount'),
                                  output_field=_MONEY)), _ZERO),
        income_n=Count(Case(When(entry_type='INCOME', then=1))),
        expense_n=Count(Case(When(entry_type='EXPENSE', then=1))),
    )
    cashbook_totals = {
        'income': summary['income'],
        'expense': summary['expense'],
        'net': summary['income'] - summary['expense'],
    }
    type_counts = {
        'all': summary['income_n'] + summary['expense_n'],
        'expense': summary['expense_n'],
        'income': summary['income_n'],
    }

    list_qs = qs
    if entry_type_filter == 'expense':
        list_qs = list_qs.filter(entry_type='EXPENSE')
    elif entry_type_filter == 'income':
        list_qs = list_qs.filter(entry_type='INCOME')

    paginator = Paginator(list_qs.order_by('-date', '-created_at'), PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'entries': page_obj.object_list,
        'page_obj': page_obj,
        'cashbook_totals': cashbook_totals,
        'type_counts': type_counts,
        'filter_type': filter_type,
        'filter_label': (
            f"{start_date_str} – {end_date_str}"
            if filter_type == 'custom' and start_date_str and end_date_str
            else FILTER_LABELS.get(filter_type, 'Custom')
        ),
        'entry_type_filter': entry_type_filter,
        'q': q,
        'start_date': start_date_str,
        'end_date': end_date_str,
        # Pre-fills the add form's date input. localdate(), never date.today():
        # the server may run in UTC while the workshop runs on IST.
        'today_iso': today.isoformat(),
        # Date objects, so the list can head a group with "Today"/"Yesterday"
        # instead of making someone read a date to work out it is this morning.
        'today': today,
        'yesterday': today - timedelta(days=1),
    }

    if is_ajax:
        return render(request, 'workshop/cashbook/cashbook_partial.html', context)

    # Suggestions for the name box. A category is free text with no master
    # list, and _canonical_category() snaps a new one onto whatever spelling
    # got there first — offering that spelling while typing is what stops the
    # snap ever being a surprise. Skipped on the AJAX path: the datalist lives
    # outside the swapped region and never changes with a filter.
    context['categories'] = list(
        CashbookEntry.objects.order_by('category')
        .values_list('category', flat=True).distinct()[:250]
    )
    return render(request, 'workshop/cashbook/cashbook.html', context)


def _canonical_category(name, exclude_pk=None):
    """
    Snap a typed category to the spelling already in use, ignoring case.

    The Profit page breaks General Cashbook down with `values('category')`, and
    the category is free text with no picker — so "Electricity", "electricity"
    and "ELECTRICITY" were three separate lines for one real cost. The rupee
    total stayed right, but the breakdown an owner reads to see where money
    goes was split three ways.

    There is no master list for these, so the entries already recorded ARE the
    list: whichever spelling got there first wins, exactly as a job card snaps
    to the master list's spelling of a car model. The row being edited is
    excluded, so deliberately re-casing the only entry of its kind still works.
    """
    name = ' '.join((name or '').split())
    if not name:
        return name
    qs = CashbookEntry.objects.filter(category__iexact=name)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.values_list('category', flat=True).first() or name


def _entry_date(raw):
    """
    'YYYY-MM-DD' from the form → date, falling back to today.

    The date is what the Profit page files this money under, so it has to be the
    day the money moved, not the day someone got round to typing it. Bad input
    falls back to today rather than 400ing — the field is `required` and
    `type=date` in the template, so anything unparseable arriving here is a
    crafted POST, and today is the same answer the field used to hardcode.
    """
    parsed = None
    if raw:
        try:
            parsed = date.fromisoformat(raw.strip())
        except (ValueError, AttributeError):
            parsed = None
    return parsed or timezone.localdate()



@office_required
def add_cashbook_entry(request):
    """Add a new income or expense entry to the ledger."""
    if request.method == 'POST':
        entry_type = request.POST.get('entry_type', '').upper()
        if entry_type not in ['INCOME', 'EXPENSE']:
            messages.error(request, "Invalid entry type.")
            return redirect('cashbook')

        category       = request.POST.get('category', '').strip()
        amount         = request.POST.get('amount', '').strip()
        payment_method = request.POST.get('payment_method', 'CASH')
        description    = request.POST.get('description', '').strip()

        if not (category and amount):
            messages.error(request, "Name and Amount are required.")
            return redirect('cashbook')

        # One shared rule — see workshop/money.py. The old `Decimal(amount) > 0`
        # let 'Infinity' straight through (it is a valid Decimal, and it IS
        # greater than zero) and stored a 12-digit figure in a numeric(10,2)
        # column, which SQLite accepts and Postgres rejects with a 500.
        decimal_amount = parse_money(amount, CashbookEntry, 'amount')
        if decimal_amount is None:
            messages.error(request, "Enter a valid amount.")
            return redirect('cashbook')

        entry_date = _entry_date(request.POST.get('date'))
        if entry_date > timezone.localdate():
            messages.error(request, "A cashbook entry can't be dated in the future.")
            return redirect('cashbook')

        CashbookEntry.objects.create(
            entry_type=entry_type,
            category=fit_text(_canonical_category(category), CashbookEntry, 'category'),
            amount=decimal_amount,
            payment_method=payment_method,
            description=description,
            date=entry_date,
            created_by=request.user,
        )
        messages.success(request, f"Successfully added {entry_type.lower()} entry.")
    return redirect('cashbook')


@office_required
def delete_cashbook_entry(request, pk):
    """Permanently delete a cashbook entry, logged to the Owner-only Deletion History."""
    if request.method == 'POST':
        entry = get_object_or_404(CashbookEntry, pk=pk)
        reason = request.POST.get('reason', '').strip()
        # Log + delete in one transaction so the history can never record a
        # deletion that didn't happen (see DeletionLog.record).
        with transaction.atomic():
            DeletionLog.record(
                DeletionLog.ENTITY_CASHBOOK, entry,
                user=request.user, reason=reason, amount=entry.amount,
                label=f"{entry.get_entry_type_display()} · {entry.category} · ₹{entry.amount:,.0f}",
            )
            entry.delete()
        messages.success(request, "Entry permanently deleted (logged to Deletion History).")
    return redirect('cashbook')


@office_required
def edit_cashbook_entry(request, pk):
    """Edit the name, amount, note, date, side and payment method of an entry."""
    if request.method == 'POST':
        entry          = get_object_or_404(CashbookEntry, pk=pk)
        category       = request.POST.get('category', '').strip()
        amount         = request.POST.get('amount', '').strip()
        payment_method = request.POST.get('payment_method', 'CASH')

        if not (category and amount):
            messages.error(request, "Name and Amount are required.")
            return redirect('cashbook')

        decimal_amount = parse_money(amount, CashbookEntry, 'amount')
        if decimal_amount is None:
            messages.error(request, "Enter a valid amount.")
            return redirect('cashbook')

        entry_date = _entry_date(request.POST.get('date'))
        if entry_date > timezone.localdate():
            messages.error(request, "A cashbook entry can't be dated in the future.")
            return redirect('cashbook')

        entry.category       = fit_text(
            _canonical_category(category, exclude_pk=entry.pk), CashbookEntry, 'category')
        entry.amount         = decimal_amount
        entry.payment_method = payment_method
        # Editable too: without this, an entry keyed on the wrong day was stuck
        # in the wrong month on the Profit page for good — there was no other
        # way to move it.
        entry.date           = entry_date
        # The note could be written at creation and never corrected afterwards,
        # which made a typo permanent on the one field meant to explain the row.
        # Only honoured when the form posts the key at all, so a payload without
        # it keeps the existing note rather than silently clearing it.
        if 'description' in request.POST:
            entry.description = request.POST.get('description', '').strip()
        # Income mis-keyed as an expense lands on the wrong side of the Profit
        # page equation — a double-sized error — and the only way back used to
        # be deleting the row and re-adding it. Only honoured when the form
        # actually posts a valid type, so a payload without it keeps what the
        # entry already has rather than silently flipping it.
        posted_type = request.POST.get('entry_type', '').upper()
        if posted_type in ('INCOME', 'EXPENSE'):
            entry.entry_type = posted_type
        entry.save()
        messages.success(request, "Entry updated.")
    return redirect('cashbook')
