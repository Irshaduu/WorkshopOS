from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
# `why` carries a little markup (the section name in bold), and an owner's NAME
# goes into it — so the name is escaped here rather than trusted. The template
# renders these through `innerHTML`, which is what makes the escape necessary.
from django.utils.html import escape
from datetime import date, timedelta
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Q, Sum, Count, Case, When, Value, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal, InvalidOperation
from .decorators import office_required, is_owner
from .models import CashbookEntry, DeletionLog
from .money import parse_money, fit_text
# The day the money moved, parsed in ONE place — the spare-shop payment form
# asks the same question, and two copies would drift apart at a month boundary,
# which is exactly where an owner reads the difference.
from .money_dates import posted_date, is_future, too_far_back, backdate_floor
from . import delete_window


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


# =============================================================================
# STEERING AN ENTRY TO THE SECTION THAT OWNS IT
# =============================================================================
# Three kinds of money have a dedicated section AND land wrong if they are
# typed here instead, so the Cashbook asks before taking one:
#
#   * WAGES are counted from Salary & Advance. A "Staff salary 40,000" row here
#     is counted TWICE — the Profit page already warns about it after the fact
#     ("wages may be counted twice"), and this is the same fact said before it
#     happens rather than a month later.
#   * AN OWNER DRAW is not an expense at all. `cashbook_expense()` feeds the
#     profit equation, so "Sahad 5,000" here quietly cuts reported profit by
#     5,000 — and the next distribution is decided from the smaller figure.
#     That is the exact defect `OwnerWithdrawal` was built to remove, and in a
#     rush an owner's own NAME is what gets typed.
#   * RENT belongs in Deposit & Rent — all of it. The monthly charge is read
#     from the rate there and the daily handovers are recorded there, so
#     either one typed here is counted twice.
#
# ⚠ THE COMMENT HERE USED TO SAY THE OPPOSITE OF THE CODE BELOW IT, and both
# were written in the same commit (`c594ee8`). It read: '"RENT" IS DELIBERATELY
# NOT IN THIS LIST, and adding it would cost the workshop ₹35,000 a month of
# expense … The word joins this list on the day that line lands, and not
# before' — while the very next declaration was `(['rent', 'deposit'], …)`.
#
# The reasoning was sound and the code never matched it. It also stopped being
# the right reasoning on 2026-09-04: rent now HAS an expense line of its own,
# read from `RentRate`, so the word belongs here and the wording gets SIMPLER
# rather than more careful — exactly as that comment predicted it would.
#
# ⚠ THE WORDS COME FROM `analysis_engine.RENT_WORDS`, imported rather than
# restated, for the reason the shop words are: the entry-time steer and the
# Profit page's own "rent may be counted twice" warning must never come to
# mean different things. `_steers()` adds ONE pair of words the flag does not
# carry — see the note there.
#
# ⚠ IT ASKS, IT NEVER BLOCKS. "Rent agreement stamp paper", "Advance to a
# supplier" and a staff member who shares an owner's first name are all real,
# so a refusal would be wrong and a person who cannot get their work done
# routes around the tool. The app's own rule, from the settle dialog.
#
# ⚠ IT IS A BROWSER PROMPT AND NOTHING ELSE, on purpose. This catches a
# TYPO MADE IN A RUSH; a crafted POST is not that, so there is no server guard
# to write and none to keep in step. What the server does own is the money
# rules themselves, which are unchanged.
# ⚠ EACH ONE IS A QUESTION, THEN THE CONSEQUENCE. That shape is the owner's
# own, after the first attempt shipped as flat statements and they said it did
# not read as stopping them: "Sahad is an owner — money they took belongs in
# Owner Withdrawals" is a FACT, and a fact slides past somebody in a hurry.
# A question ("Is this money an owner took?") makes the reader answer it, and
# naming what goes wrong ("it makes the profit look smaller") is what makes
# answering worth the second it costs. Read in one eye scan, in this order:
#
#     ASK  -> what would go wrong -> where it belongs
#
# `ask` is the heading, `why` the line under it. Both are short on purpose;
# anything longer is read as prose and skipped.

#: A row whose words are resolved at CALL time from `analysis_engine`, so the
#: steer and that page's own warning read one list. `_steers()` fills them in.
#: Sentinel objects rather than `None`, which said "shops" by convention and
#: could not name a second such row.
_FROM_SHOP_WORDS = object()
_FROM_RENT_WORDS = object()

CASHBOOK_STEERS = [
    # ⚠ THE WORDING GOT SIMPLER, EXACTLY AS THE OLD COMMENT PREDICTED.
    #
    # It used to ask "Is this a rent DEPOSIT?" and had to, because the monthly
    # charge still belonged here: the plain wording would have been false and
    # would have cost ₹35,000 a month. An earlier revision spelled the
    # exception out — "The one monthly rent bill is still fine here" — which
    # was true and which the owner read as "workshop rent is fine to add
    # here", the opposite of the point. It was an answer to a question nobody
    # had asked yet.
    #
    # That muddle existed only because rent was half-way out of the Cashbook.
    # It is all the way out now, so there is no exception left to explain and
    # no distinction the reader has to hold: rent goes in Deposit & Rent,
    # monthly charge and daily handover alike.
    (_FROM_RENT_WORDS, {
        'ask': 'Is this rent?',
        'why': 'Rent lives in <strong>Deposit&nbsp;&amp; Rent</strong> now '
               '&mdash; the monthly charge and the daily cash you hand the '
               'collector. Put here it is counted <strong>twice</strong>.',
    }),
    (['salary', 'salaries', 'wage', 'wages', 'advance', 'bonus'], {
        'ask': 'Is this staff pay?',
        'why': 'Wages and advances go in <strong>Salary&nbsp;&amp; '
               'Advance</strong>. Put here they are counted '
               '<strong>twice</strong>.',
    }),
    (['withdrawal', 'withdraw', 'drawing', 'drawings', 'take out', 'takeout'], {
        'ask': 'Is this money an owner took out?',
        'why': 'That goes in <strong>Owner Withdrawals</strong>. Put here it '
               'makes the <strong>profit look smaller</strong> than it is.',
    }),
    # ⚠ THE FOURTH GROUP, AND THE ONE THE PROFIT PAGE ALREADY WARNS ABOUT AFTER
    # THE FACT. `_shoplike_cashbook_count()` counts cashbook rows whose
    # category reads like a shop payment, because paying a shop from here is
    # counted TWICE — once as a cashbook expense, once against that shop's own
    # ledger. CLAUDE.md's own example is "Paid Ninoos 20,000". The words are
    # `analysis_engine.SHOP_WORDS`, imported rather than restated, so the
    # warning and the steer can never come to mean different things.
    (_FROM_SHOP_WORDS, {   # words filled in from SHOP_WORDS by `_steers()`
        'ask': 'Is this a payment to a shop?',
        'why': 'Shop payments go on that shop&rsquo;s own page. Put here they '
               'are counted <strong>twice</strong> &mdash; once here and once '
               'against the shop.',
    }),
]


def _steers():
    """The keyword list, with the CURRENT owners' names appended.

    ⚠ READ FROM THE DATABASE, never hard-coded. "Sahad" and "Rijas" are who
    the owners happen to be today; a third owner, or one renamed, must not need
    a code change to be protected — the same reason `owner_accounts()` is the
    one answer to "who are the owners?" everywhere else.

    A name gets its own message because naming the person is what makes the
    prompt land: "Sahad is an owner" is unarguable in a way that a generic
    line about owner money is not.
    """
    from .decorators import owner_accounts

    from .analysis_engine import RENT_WORDS, SHOP_WORDS

    rows = []
    for words, say in CASHBOOK_STEERS:
        if words is _FROM_SHOP_WORDS:
            words = SHOP_WORDS
        elif words is _FROM_RENT_WORDS:
            # ⚠ BROADER THAN THE PROFIT PAGE'S FLAG BY ONE PAIR OF WORDS, and
            # the difference is deliberate rather than drift. `RENT_WORDS` has
            # to be narrow because it ASSERTS that the profit figure above it
            # is wrong, and a false warning on that page is worse than no
            # warning — a "Security deposit" row is a real running cost. A
            # steer only ASKS and never blocks, so a false positive costs a
            # second; and in this workshop a row reading "Deposit 2,000" is
            # very much more likely to be a rent handover than anything else.
            words = tuple(RENT_WORDS) + ('deposit', 'deposits')
        rows.append(dict(say, words=list(words)))

    # ⚠ THE SHOPS ARE NAMED FROM THE DATABASE, like the owners and for the same
    # reason: "Paid Ninoos 20,000" is CLAUDE.md's own example of the
    # double-count, and Ninoos is a row in a table, not a word in this file.
    # Both ledgers, and archived shops too — money paid to a shop that has
    # since been archived is counted exactly as twice.
    #
    # ⚠ FOUR CHARACTERS MINIMUM, AND THE WHOLE NAME. A short or common shop
    # name ("Oil", "AC") would match half the ledger, and matching a shop's
    # first WORD would fire on "Auto" or "New". The trade is that a shop
    # referred to by half its name is missed — which the word list above still
    # tends to catch, since most are called "... Auto Parts" or "... Spares".
    from inventory.models import SupplierShop
    from .models import SpareShop

    names = set()
    for name in list(SpareShop.objects.values_list('name', flat=True)) + \
            list(SupplierShop.objects.values_list('name', flat=True)):
        cleaned = (name or '').strip()
        if len(cleaned) >= 4:
            names.add(cleaned.lower())
    if names:
        rows.append({
            'words': sorted(names),
            'ask': 'Is this a payment to a shop?',
            'why': 'That is one of your shops. Pay it on its own page &mdash; '
                   'put here it is counted <strong>twice</strong>.',
        })

    for account in owner_accounts():
        name = (account.get_full_name() or account.username).strip()
        first = name.split()[0] if name else ''
        if not first:
            continue
        rows.append({
            'words': sorted({name.lower(), first.lower()}),
            # The NAME is the question, because that is what makes it
            # unarguable: a generic line about owner money is easy to read past,
            # and "Is this money Sahad took out?" is not.
            'ask': f"Is this money {first} took out?",
            'why': f"<strong>{escape(first)}</strong> is an owner. That goes in "
                   f"<strong>Owner Withdrawals</strong> &mdash; put here it makes "
                   f"the <strong>profit look smaller</strong> than it is.",
        })
    return rows


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
        # PRESENTATION ONLY — `too_far_back()` in the view is the control.
        'floor_iso': '' if is_owner(request.user) else backdate_floor().isoformat(),
        # Handed over as data for `json_script`, never interpolated into
        # markup: an owner's name is free text and would otherwise need
        # escaping by hand on every render.
        'steers': _steers(),
        # ⚠ TODAY'S ENTRIES BY NAME, for the repeat line in the confirmation.
        # Keyed on TODAY whatever period the page is filtered to: the question
        # is "have I already keyed this today", not "is it in the window I am
        # looking at". Lower-cased, because the ledger snaps a new heading to a
        # spelling already in use and "Biljo" must count "biljo".
        'today_names': {
            (row['category'] or '').strip().lower(): row['n']
            for row in CashbookEntry.objects.filter(date=today)
                                            .values('category')
                                            .annotate(n=Count('id'))
            if (row['category'] or '').strip()
        },
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
        # ⚠ `<= 0` AS WELL AS None. `parse_money` refuses a zero or a negative
        # BEFORE quantising, so `0.004` passes everything it checks and comes
        # back as `0.00` — and this column carries a CheckConstraint that
        # `amount > 0`, so writing it is an IntegrityError and a 500 rather
        # than a message.
        decimal_amount = parse_money(amount, CashbookEntry, 'amount')
        if decimal_amount is None or decimal_amount <= 0:
            messages.error(request, "Enter a valid amount.")
            return redirect('cashbook')

        # Validated against the list, not merely read: the column is 20
        # characters of free text as far as the database is concerned, and a
        # code that is not in PAYMENT_METHODS prints as itself on the row and
        # matches nothing the search offers.
        if payment_method not in dict(CashbookEntry.PAYMENT_METHODS):
            payment_method = 'CASH'

        entry_date = posted_date(request.POST.get('date'))
        if is_future(entry_date):
            messages.error(request, "A cashbook entry can't be dated in the future.")
            return redirect('cashbook')
        # ⚠ AND HOW FAR BACK. `cashbook_expense()` feeds the profit equation as
        # General Cashbook, so an entry back-dated a year lands inside a period
        # an owner has already read and distributed against — silently, because
        # nothing re-reads a closed month. Office is floored at the 1st of last
        # month, which is the whole of the window a month is reconciled in; an
        # owner is not, and the refusal names them.
        blocked = too_far_back(entry_date, request.user, "A cashbook entry")
        if blocked:
            messages.error(request, blocked)
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

        # Dated by `created_at`, never `entry.date` — the date box exists so a
        # month-end expense keyed the following week lands in the right month,
        # and the money date would refuse Office a row they entered minutes ago.
        stop = delete_window.refusal(
            request.user, entry.created_at, f"This ₹{entry.amount:,.0f} entry")
        if stop:
            messages.error(request, stop)
            return redirect('cashbook')

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

        # See the add view: a sub-paisa figure quantises to 0.00 and the
        # column's CheckConstraint turns that into a 500.
        decimal_amount = parse_money(amount, CashbookEntry, 'amount')
        if decimal_amount is None or decimal_amount <= 0:
            messages.error(request, "Enter a valid amount.")
            return redirect('cashbook')

        if payment_method not in dict(CashbookEntry.PAYMENT_METHODS):
            payment_method = 'CASH'

        entry_date = posted_date(request.POST.get('date'))
        if is_future(entry_date):
            messages.error(request, "A cashbook entry can't be dated in the future.")
            return redirect('cashbook')
        # The EDIT path needs it as badly as the add path: moving an entry back
        # into a closed month is the same act as filing one there, and this is
        # the screen that exists precisely so a date can be corrected.
        blocked = too_far_back(entry_date, request.user, "A cashbook entry")
        if blocked:
            messages.error(request, blocked)
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
