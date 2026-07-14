from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from datetime import timedelta
from django.contrib import messages
from django.db import models
from decimal import Decimal, InvalidOperation
from .decorators import office_required
from .models import CashbookEntry


@office_required
def cashbook_view(request):
    """
    Dedicated view for the General Expenses & Income Ledger.
    Accessible via /cashbook/ — Office and Owner only.
    """
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    filter_type = request.GET.get('filter')

    # Default to 'today' when no filter is provided
    if not filter_type:
        filter_type = 'today'

    today = timezone.now().date()
    qs = CashbookEntry.objects.all()

    if filter_type == 'today':
        qs = qs.filter(date=today)
    elif filter_type == 'this_week':
        start_of_week = today - timedelta(days=today.weekday())
        qs = qs.filter(date__gte=start_of_week)
    elif filter_type == 'this_month':
        qs = qs.filter(date__year=today.year, date__month=today.month)
    elif filter_type == 'this_year':
        qs = qs.filter(date__year=today.year)

    income  = qs.filter(entry_type='INCOME').aggregate(t=models.Sum('amount'))['t'] or 0
    expense = qs.filter(entry_type='EXPENSE').aggregate(t=models.Sum('amount'))['t'] or 0
    cashbook_totals = {
        'income':  income,
        'expense': expense,
        'net':     income - expense,
    }

    expenses = qs.filter(entry_type='EXPENSE').order_by('-date', '-created_at')[:300]
    incomes  = qs.filter(entry_type='INCOME').order_by('-date', '-created_at')[:300]

    template = 'workshop/cashbook/cashbook_partial.html' if is_ajax else 'workshop/cashbook/cashbook.html'
    return render(request, template, {
        'expenses':        expenses,
        'incomes':         incomes,
        'cashbook_totals': cashbook_totals,
        'filter_type':     filter_type,
    })


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

        if category and amount:
            try:
                # AUD-0022: Use Decimal — float() introduces rounding errors for money.
                decimal_amount = Decimal(amount)
                if decimal_amount > 0:
                    CashbookEntry.objects.create(
                        entry_type=entry_type,
                        category=category,
                        amount=decimal_amount,
                        payment_method=payment_method,
                        description=description,
                        created_by=request.user,
                    )
                    messages.success(request, f"Successfully added {entry_type.lower()} entry.")
                else:
                    messages.error(request, "Amount must be greater than zero.")
            except (ValueError, InvalidOperation):
                messages.error(request, "Invalid amount provided.")
        else:
            messages.error(request, "Name and Amount are required.")
    return redirect('cashbook')


@office_required
def delete_cashbook_entry(request, pk):
    """Delete a single cashbook entry."""
    if request.method == 'POST':
        entry = get_object_or_404(CashbookEntry, pk=pk)
        entry.delete()
        messages.success(request, "Entry deleted.")
    return redirect('cashbook')


@office_required
def edit_cashbook_entry(request, pk):
    """Edit the name, amount, and payment method of an existing entry."""
    if request.method == 'POST':
        entry          = get_object_or_404(CashbookEntry, pk=pk)
        category       = request.POST.get('category', '').strip()
        amount         = request.POST.get('amount', '').strip()
        payment_method = request.POST.get('payment_method', 'CASH')

        if category and amount:
            try:
                # AUD-0022: Use Decimal — float() introduces rounding errors for money.
                decimal_amount = Decimal(amount)
                if decimal_amount > 0:
                    entry.category       = category
                    entry.amount         = decimal_amount
                    entry.payment_method = payment_method
                    entry.save()
                    messages.success(request, "Entry updated.")
                else:
                    messages.error(request, "Amount must be greater than zero.")
            except (ValueError, InvalidOperation):
                messages.error(request, "Invalid amount provided.")
        else:
            messages.error(request, "Name and Amount are required.")
    return redirect('cashbook')
