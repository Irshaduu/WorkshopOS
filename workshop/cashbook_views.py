from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from datetime import date, timedelta
from django.contrib import messages
from django.db import models, transaction
from decimal import Decimal, InvalidOperation
from .decorators import office_required
from .models import CashbookEntry, DeletionLog
from .money import parse_money, fit_text


@office_required
def cashbook_view(request):
    """
    Dedicated view for the General Expenses & Income Ledger.
    Accessible via /cashbook/ — Office and Owner only.
    """
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    filter_type = request.GET.get('filter', 'today')
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'

    qs = CashbookEntry.objects.all()

    start_date_str = ''
    end_date_str   = ''

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
        end   = start + timedelta(days=6)
        qs = qs.filter(date__gte=start, date__lte=end)

    elif filter_type == 'last_month':
        first_of_this = today.replace(day=1)
        last_of_last  = first_of_this - timedelta(days=1)
        first_of_last = last_of_last.replace(day=1)
        qs = qs.filter(date__gte=first_of_last, date__lte=last_of_last)

    elif filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1,  day=1)
        end   = today.replace(year=today.year - 1, month=12, day=31)
        qs = qs.filter(date__gte=start, date__lte=end)

    elif filter_type == 'custom':
        start_date_str = request.GET.get('start_date', '')
        end_date_str   = request.GET.get('end_date', '')
        if start_date_str and end_date_str:
            try:
                qs = qs.filter(date__gte=date.fromisoformat(start_date_str),
                               date__lte=date.fromisoformat(end_date_str))
            except ValueError:
                pass

    income  = qs.filter(entry_type='INCOME').aggregate(t=models.Sum('amount'))['t'] or 0
    expense = qs.filter(entry_type='EXPENSE').aggregate(t=models.Sum('amount'))['t'] or 0
    cashbook_totals = {
        'income':  income,
        'expense': expense,
        'net':     income - expense,
    }

    expense_qs = qs.filter(entry_type='EXPENSE').order_by('-date', '-created_at')
    income_qs  = qs.filter(entry_type='INCOME').order_by('-date', '-created_at')
    expense_count, income_count = expense_qs.count(), income_qs.count()
    expenses = expense_qs[:LIST_CAP]
    incomes  = income_qs[:LIST_CAP]

    template = 'workshop/cashbook/cashbook_partial.html' if is_ajax else 'workshop/cashbook/cashbook.html'
    return render(request, template, {
        'expenses':        expenses,
        'incomes':         incomes,
        'cashbook_totals': cashbook_totals,
        'filter_type':     filter_type,
        'start_date':      start_date_str,
        'end_date':        end_date_str,
        # Pre-fills the add forms' date input. localdate(), never date.today():
        # the server may run in UTC while the workshop runs on IST.
        'today_iso':       today.isoformat(),
        # How many rows the totals above are actually made of. The lists are
        # capped, so without these a period holding more than LIST_CAP entries
        # showed a total that visibly did not add up from what was on screen.
        'expense_count':   expense_count,
        'income_count':    income_count,
        'expense_hidden':  max(0, expense_count - LIST_CAP),
        'income_hidden':   max(0, income_count - LIST_CAP),
    })


# The Cashbook list shows at most this many rows per side. The TOTALS above it
# are always computed from the full queryset, so a truncated list and a correct
# total would disagree with no explanation — hence the notice in the template.
LIST_CAP = 300


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
    """Edit the name, amount, and payment method of an existing entry."""
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
        # Income mis-keyed as an expense is a double-sized error on the Profit
        # page (it lands on the wrong side of the equation), and the only fix
        # used to be deleting the row and re-adding it. Only honoured when the
        # form actually posts a valid type, so a payload without it keeps what
        # the entry already has rather than silently flipping it.
        posted_type = request.POST.get('entry_type', '').upper()
        if posted_type in ('INCOME', 'EXPENSE'):
            entry.entry_type = posted_type
        entry.save()
        messages.success(request, "Entry updated.")
    return redirect('cashbook')
