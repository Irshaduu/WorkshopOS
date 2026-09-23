"""
CHANGE HISTORY — one Owner-only, read-only page with three tabs over the books:

    Deleted     every permanent delete (`DeletionLog`)
    Edited      every edit that moved money (`EditLog`)
    Back-dated  money typed in on a later day than it moved (read off the rows)

⚠ ONE MONTH AT A TIME, ON ALL THREE, AND NO PAGER. A month of changes is
naturally bounded however long the workshop runs — the rent log's own shape —
so nothing is ever hidden behind a page two, and the three tabs share one
control. The month is the month the change was MADE (deleted, edited, typed),
because the question this page answers is "what was done to the books?".

⚠ EVERY TAB DRAWS THE SAME ROW. Each view hands the template a list of plain
dicts with the same keys — title, amount, kind, who, when, and one tab-specific
detail — so the markup exists once and the three tabs cannot drift into three
designs.

The URLs keep their `deletion-history` prefix and names deliberately: a
`RECORD_DELETED` notification STORES its URL, so the addresses already sent
must keep working (CLAUDE.md, "A notification's URL is permanent").
"""
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from django.db.models import F
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from inventory.models import SupplierPayment

from ..models import (BulkPaymentHistory, CashbookEntry, DeletionLog, EditLog,
                      OwnerWithdrawal, RentDeposit, SalaryAdvance, SpareShopPayment)
from ..decorators import owner_required
from ..money_dates import BACKDATE_DAYS, filed_past_limit, keyed_on
from ..templatetags.custom_filters import inr_amount
from .withdrawal import display_name

TEMPLATE = 'workshop/deletion_history/deletion_history_list.html'

# Every money table whose date can be typed, and how one of its rows is named.
# (key, model, related rows to fetch, who typed it, what it is). The keys and
# the labels are Deletion History's own, so one record type is called one
# thing on all three tabs.
BACKDATED_SOURCES = [
    (DeletionLog.ENTITY_CASHBOOK, CashbookEntry, ['created_by'], 'created_by',
     lambda r: r.category),
    (DeletionLog.ENTITY_RENT_DEPOSIT, RentDeposit, ['recorded_by'], 'recorded_by',
     lambda r: 'Rent deposit'),
    (DeletionLog.ENTITY_SHOP_PAYMENT, SpareShopPayment, ['shop', 'recorded_by'], 'recorded_by',
     lambda r: r.shop.name),
    (DeletionLog.ENTITY_SUPPLIER_PAYMENT, SupplierPayment, ['supplier', 'recorded_by'], 'recorded_by',
     lambda r: r.supplier.name),
    (DeletionLog.ENTITY_BULK_PAYMENT, BulkPaymentHistory, ['bulk_payer', 'recorded_by'], 'recorded_by',
     lambda r: r.bulk_payer.customer_name),
    (DeletionLog.ENTITY_SALARY_ADVANCE, SalaryAdvance, ['staff', 'created_by'], 'created_by',
     lambda r: r.staff.name),
    (DeletionLog.ENTITY_OWNER_WITHDRAWAL, OwnerWithdrawal, ['owner', 'recorded_by'], 'recorded_by',
     lambda r: display_name(r.owner)),
]
BACKDATED_CHOICES = [
    (key, dict(DeletionLog.ENTITY_CHOICES)[key]) for key, *_ in BACKDATED_SOURCES
]


# ---------------------------------------------------------------------------
# The month, shared by all three tabs
# ---------------------------------------------------------------------------

# No record in this system predates it; a year before it is a typo in the
# address bar. Without a floor, `?month=0001-01` 500'd twice over — the month
# before it does not exist, and its midnight cannot be converted to UTC.
EARLIEST_MONTH = date(2000, 1, 1)


def _month(raw, today):
    """The first of the month asked for — or this month, for anything
    unreadable, not yet begun, or before `EARLIEST_MONTH` (the Estimates list's
    rule: an empty page under a heading naming a month would read as "nothing
    happened then")."""
    try:
        y, m = (int(p) for p in (raw or '').split('-'))
        first = date(y, m, 1)
    except (TypeError, ValueError):
        return today.replace(day=1)
    if first > today or first < EARLIEST_MONTH:
        return today.replace(day=1)
    return first


def _bounds(first, last):
    """The month as two AWARE instants, midnight to midnight IST — a plain
    range over the indexed stamp columns, never a `__date` transform per row."""
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(first, time.min), tz),
            timezone.make_aware(datetime.combine(last + timedelta(days=1), time.min), tz))


def _backdated_querysets(start, end, kind=''):
    """Each money table's rows TYPED in the month on a later day than they
    moved. `created_at__date` is converted to IST in the SQL — the same day
    `keyed_on()` reads — so the filter and the mark cannot disagree."""
    for key, model, related, who, name in BACKDATED_SOURCES:
        if kind and kind != key:
            continue
        qs = (model.objects.select_related(*related)
              .filter(created_at__gte=start, created_at__lt=end,
                      created_at__date__gt=F('date')))
        yield key, who, name, qs


# ---------------------------------------------------------------------------
# One row shape for every tab
# ---------------------------------------------------------------------------

def _kind(title, kind):
    """The record type — or nothing when the title already opens with it, so
    "Rent deposit" is never followed by "Rent Deposit". `DeletionLog.record()`'s
    own rule for a notification's detail line."""
    return '' if title.lower().startswith(kind.lower()) else kind


def _without_amount(label):
    """A deletion's label with its own rupee figure taken out, because the row
    prints the amount once, on the right. Nine `DeletionLog.record()` call sites
    put it in the label ("Restock Bill #669 · Fluid manjeri · ₹31,500"), which
    printed one figure twice on one row. Only a whole ` · `-part that starts
    with ₹ is dropped, so nothing else in a label can be cut."""
    parts = [p for p in label.split(' · ') if not p.strip().startswith('₹')]
    return ' · '.join(parts) or label


def _shown(value, kind):
    """One side of an edit, in the words the rest of the app uses."""
    if value is None:
        return '—'
    if kind == EditLog.KIND_MONEY:
        return f"₹{inr_amount(value)}"
    if kind == EditLog.KIND_DATE:
        try:
            return date.fromisoformat(value).strftime('%d %b %Y')
        except (TypeError, ValueError):
            return value
    return value


def deleted_rows(start, end, kind=''):
    qs = (DeletionLog.objects.select_related('deleted_by')
          .filter(deleted_at__gte=start, deleted_at__lt=end))
    if kind:
        qs = qs.filter(entity_type=kind)
    rows = []
    for log in qs:
        title = _without_amount(log.entity_label) if log.amount is not None else log.entity_label
        rows.append({
            'title': title,
            'amount': log.amount,
            'kind': _kind(title, log.get_entity_type_display()),
            'who': log.deleted_by.username if log.deleted_by else '',
            'when': log.deleted_at,
            'reason': log.reason,
            'link': reverse('deletion_history_detail', args=[log.pk]),
        })
    return rows


def edited_rows(start, end, kind=''):
    qs = (EditLog.objects.select_related('edited_by')
          .filter(edited_at__gte=start, edited_at__lt=end))
    if kind:
        qs = qs.filter(entity_type=kind)
    rows = []
    for log in qs:
        rows.append({
            'title': log.entity_label,
            'amount': None,
            'kind': _kind(log.entity_label, log.get_entity_type_display()),
            'who': log.edited_by.username if log.edited_by else '',
            'when': log.edited_at,
            'changes': [
                {'field': c.get('field', ''),
                 'before': _shown(c.get('before'), c.get('kind')),
                 'after': _shown(c.get('after'), c.get('kind'))}
                for c in (log.changes if isinstance(log.changes, list) else [])
            ],
        })
    return rows


def backdated_rows(start, end, kind=''):
    """
    Money TYPED IN on a later day than it MOVED, newest keystroke first.

    ⚠ NOTHING IS STORED FOR THIS, AND THAT IS WHY IT NEEDS NO TABLE. An edit
    overwrites a figure, so Edit History has to copy the old one before it is
    lost; a back-dated row loses nothing — `date` and `created_at` both stay on
    it — so a second copy would only be a second answer free to drift. The
    red mark is `filed_past_limit`, the rent row mark's own rule, judged as at
    the day the row was keyed.
    """
    labels = dict(BACKDATED_CHOICES)
    rows = []
    for key, who, name, qs in _backdated_querysets(start, end, kind):
        for r in qs:
            typed = getattr(r, who)
            title = name(r)
            rows.append({
                'source': key,
                'title': title,
                'amount': r.amount,
                'kind': _kind(title, labels[key]),
                'who': typed.username if typed else '',
                'when': r.created_at,
                'dated': r.date,
                'days_back': (keyed_on(r.created_at) - r.date).days,
                'past_limit': filed_past_limit(r.date, r.created_at),
            })
    rows.sort(key=lambda row: row['when'], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# The three tabs
# ---------------------------------------------------------------------------

TABS = {
    'deleted':   (deleted_rows,   DeletionLog.ENTITY_CHOICES),
    'edited':    (edited_rows,    EditLog.ENTITY_CHOICES),
    'backdated': (backdated_rows, BACKDATED_CHOICES),
}


def _render(request, tab):
    today = timezone.localdate()
    first = _month(request.GET.get('month'), today)
    last = first.replace(day=monthrange(first.year, first.month)[1])
    start, end = _bounds(first, last)

    build, choices = TABS[tab]
    kind = request.GET.get('type', '').strip()
    if kind not in dict(choices):
        kind = ''

    # Each tab's count for the month, so the tab row itself says where the
    # activity is. Cheap: two indexed counts plus one per money table.
    counts = {
        'deleted': DeletionLog.objects.filter(deleted_at__gte=start, deleted_at__lt=end).count(),
        'edited': EditLog.objects.filter(edited_at__gte=start, edited_at__lt=end).count(),
        'backdated': sum(qs.count() for *_, qs in _backdated_querysets(start, end)),
    }
    return render(request, TEMPLATE, {
        'tab': tab,
        'rows': build(start, end, kind),
        'counts': counts,
        'month': first,
        'prev_month': (first - timedelta(days=1)).replace(day=1),
        'next_month': (last + timedelta(days=1)) if last < today else None,
        'entity': kind,
        'entity_choices': choices,
        'backdate_days': BACKDATE_DAYS,
    })


@owner_required
def deletion_history_list(request):
    """Every permanent delete. Deliberately NO restore — reviving stale records
    would corrupt running balances."""
    return _render(request, 'deleted')


@owner_required
def edit_history_list(request):
    """Every edit that moved money, with what each figure was before. The row
    carries the whole change, so there is no detail page."""
    return _render(request, 'edited')


@owner_required
def backdated_history_list(request):
    """Money typed in on a later day than it moved. Red past the three-day
    limit, which only an owner can reach."""
    return _render(request, 'backdated')


@owner_required
def deletion_history_detail(request, pk):
    """Owner-only read-only detail: the full snapshot of one deleted record."""
    log = get_object_or_404(DeletionLog.objects.select_related('deleted_by'), pk=pk)
    snapshot_items = sorted(log.snapshot.items()) if isinstance(log.snapshot, dict) else []
    return render(request, 'workshop/deletion_history/deletion_history_detail.html', {
        'log': log,
        'snapshot_items': snapshot_items,
    })
