# inventory/views.py
from decimal import Decimal
from datetime import timedelta
from itertools import groupby

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import F, Q, Sum, Min, Count, ProtectedError
from django.db.models.functions import Lower
from django.core.paginator import Paginator

from .models import Category, Item
from workshop.decorators import staff_required, office_required
from workshop.models import JobCardSpareItem, Mechanic


@staff_required
def inventory_home(request):
    return redirect('inventory_list')

@office_required
def inventory_manage(request):
    """Category screen (Office/Owner): add / list / edit categories. Read-only for products."""
    q = request.GET.get('q', '').strip()
    # Count in SQL rather than prefetching every Item — the screen only needs the
    # number, and `distinct=True` keeps it correct across the search JOIN below.
    categories_query = (
        Category.objects
        .annotate(product_count=Count('items', distinct=True))
        .order_by('name')
    )

    if q:
        categories_query = categories_query.filter(
            Q(name__icontains=q) | Q(items__name__icontains=q)
        ).distinct()

    paginator = Paginator(categories_query, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'inventory/manage.html', {'categories': page_obj, 'page_obj': page_obj, 'q': q})

@office_required
def add_category(request):
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, "Category name is required.")
            return redirect('inventory_manage')
        # Case-insensitive dedupe, like every other taxonomy in the codebase.
        # Duplicates are actively harmful here: add_shop_catalog_item resolves the
        # category with get_or_create(name__iexact=...), which raises
        # MultipleObjectsReturned the moment two spellings coexist.
        existing = Category.objects.filter(name__iexact=name).first()
        if existing:
            messages.error(request, f"Category '{existing.name}' already exists.")
            return redirect('inventory_manage')
        Category.objects.create(name=name)
        messages.success(request, f"Category '{name}' created.")
        return redirect('inventory_manage')
    return render(request, 'inventory/add_category.html')

@office_required
def edit_category(request, category_id):
    category = get_object_or_404(Category, pk=category_id)
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, "Category name is required.")
            return redirect('inventory_manage')
        clash = Category.objects.filter(name__iexact=name).exclude(pk=category.pk).exists()
        if clash:
            messages.error(request, f"Another category named '{name}' already exists.")
            return redirect('inventory_manage')
        category.name = name
        category.save()
        messages.success(request, f"Category updated to '{name}'.")
        return redirect('inventory_manage')
    return render(request, 'inventory/edit_category.html', {'category': category})

@office_required
def delete_category(request, category_id):
    """Delete a category — allowed only while it holds no products.

    `Item.category` is `on_delete=PROTECT`, so a non-empty category can't be
    removed at the DB level either; this turns that into a clear message rather
    than a 500. Products are removed from their supplier shops, not from here.
    """
    if request.method != 'POST':
        return redirect('inventory_manage')
    category = get_object_or_404(Category, pk=category_id)
    name = category.name
    product_count = category.items.count()
    if product_count:
        messages.error(
            request,
            f"'{name}' still has {product_count} product(s), so it can't be deleted. "
            f"Remove them from their supplier shops first."
        )
        return redirect('inventory_manage')
    try:
        category.delete()
        messages.success(request, f"Category '{name}' deleted.")
    except ProtectedError:
        messages.error(request, f"'{name}' can't be deleted — products still reference it.")
    return redirect('inventory_manage')

@office_required
def category_detail(request, category_id):
    """Read-only: the category's products, each with the shop(s) that stock it."""
    category = get_object_or_404(Category, pk=category_id)
    # Paginate rather than prefetching the whole category — a well-used category
    # can hold hundreds of products, and each one pulls its shop links with it.
    items_query = (
        category.items
        .prefetch_related('shop_catalogs__shop')
        .order_by('name')
    )
    page_obj = Paginator(items_query, 45).get_page(request.GET.get('page'))
    return render(request, 'inventory/category_detail.html', {
        'category': category,
        'items': page_obj,
        'page_obj': page_obj,
    })

@staff_required
def inventory_list(request):
    q = request.GET.get('q', '').strip()
    categories_query = Category.objects.prefetch_related('items').all().order_by('name')
    
    if q:
        categories_query = categories_query.filter(
            Q(name__icontains=q) | Q(items__name__icontains=q)
        ).distinct()
        
    paginator = Paginator(categories_query, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'inventory/inventory_list.html', {'categories': page_obj, 'page_obj': page_obj, 'q': q})

@staff_required
def inventory_low_stock(request):
    q = request.GET.get('q', '').strip()
    low_stock_query = Item.objects.select_related('category').filter(
        average_stock__gt=0
    ).filter(
        Q(current_stock__lte=0) |
        Q(current_stock__lt=F('average_stock') * Decimal('0.25'))
    )
    # Search in SQL, not in the browser: a client-side filter would only ever
    # search the 50 rows on the current page while appearing to search everything.
    if q:
        low_stock_query = low_stock_query.filter(
            Q(name__icontains=q) | Q(category__name__icontains=q)
        )
    low_stock_query = low_stock_query.order_by('name')

    paginator = Paginator(low_stock_query, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Out-of-stock is the urgent subset; surfaced as a count so Floor can triage.
    # Exactly zero, not `<= 0`: a negative balance is counted as a discrepancy
    # below instead. Letting it fall in both buckets reported one overdrawn product
    # as two separate problems — "1 out of stock" (reorder it) alongside "1 stock
    # discrepancy" (don't) — which is the confusion this split exists to end.
    out_of_stock = sum(1 for i in page_obj if i.current_stock == 0)

    # A NEGATIVE balance is not a low-stock problem and must not be triaged as
    # one. Low means "order more"; negative means more was drawn than the shelf
    # was recorded as holding — a Supplies Shop bill has not been entered yet, or
    # a count is wrong. Ordering against it would buy a part that is physically
    # sitting on the shelf. Counted separately, over the whole filtered set rather
    # than one page, because it is a books problem the owner needs the true size of.
    discrepancies = low_stock_query.filter(current_stock__lt=0)
    disc_shown = list(discrepancies.order_by('current_stock')[:10])
    disc_count = discrepancies.count()

    return render(request, 'inventory/low_stock.html', {
        'items': page_obj,
        'page_obj': page_obj,
        'q': q,
        'out_of_stock': out_of_stock,
        'discrepancy_count': disc_count,
        'discrepancy_items': disc_shown,
        'discrepancy_more': max(disc_count - len(disc_shown), 0),
    })

def _week_range(which):
    """(start, end) dates for 'this' or 'last' week (Mon-Sun), IST-safe via localdate()."""
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    if which == 'last':
        return monday - timedelta(days=7), monday - timedelta(days=1)
    return monday, monday + timedelta(days=6)


# A week of consumption at this workshop's volume is a few dozen rows. The cap only
# exists so a bad date range can never pull an unbounded result set into memory —
# it is not pagination, because the day-grouped layout must not be split across pages.
HISTORY_ROW_CAP = 500


@staff_required
def consumption_history(request):
    """
    Stock History = the real consumption log, read live from job cards:
    every warehouse item used on a car shown as item | qty | mechanic | car | reg,
    grouped by date. Filter: This Week (default) / Last Week.

    Scoped to `source=INVENTORY` since 2026-07-30 — these are draws off the shelf,
    and a spare bought from a shop for one job never touched the warehouse. This
    page used to list every spare and flag the ones whose name matched no product
    as "not from stock", because the route had to be guessed from the name. With
    the route stored, an unmatched row cannot exist: rows here moved stock by
    definition.
    """
    which = request.GET.get('range', 'this')
    if which not in ('this', 'last'):
        which = 'this'
    start, end = _week_range(which)

    rows = list(
        JobCardSpareItem.objects
        .filter(source=JobCardSpareItem.SOURCE_INVENTORY,
                job_card__isnull=False,
                job_card__is_deleted=False,
                job_card__admitted_date__range=(start, end))
        .exclude(spare_part_name__isnull=True).exclude(spare_part_name='')
        .select_related('job_card', 'job_card__lead_mechanic')
        .order_by('-job_card__admitted_date', '-pk')[:HISTORY_ROW_CAP + 1]
    )
    truncated = len(rows) > HISTORY_ROW_CAP
    rows = rows[:HISTORY_ROW_CAP]

    grouped = [(day, list(items)) for day, items in
               groupby(rows, key=lambda r: r.job_card.admitted_date)]

    return render(request, 'inventory/consumption_history.html', {
        'grouped': grouped,
        'range': which,
        'start': start,
        'end': end,
        'truncated': truncated,
        'row_cap': HISTORY_ROW_CAP,
    })


@staff_required
def inventory_history_mechanic(request, mechanic_id):
    """Per-mechanic consumption totals: how much of each spare a mechanic used
    (This Week / Last Week)."""
    mechanic = get_object_or_404(Mechanic, pk=mechanic_id)
    which = request.GET.get('range', 'this')
    if which not in ('this', 'last'):
        which = 'this'
    start, end = _week_range(which)

    # Grouped by name for display only. Rows are already one product each via the
    # `item` FK; the grouping just merges spellings that differ in case, which old
    # rows can still carry.
    totals = list(
        JobCardSpareItem.objects
        .filter(source=JobCardSpareItem.SOURCE_INVENTORY,
                job_card__lead_mechanic=mechanic,
                job_card__is_deleted=False,
                job_card__admitted_date__range=(start, end))
        .exclude(spare_part_name__isnull=True).exclude(spare_part_name='')
        .annotate(lname=Lower('spare_part_name'))
        .values('lname')
        .annotate(total=Sum('quantity'), name=Min('spare_part_name'))
        .order_by('-total', 'lname')
    )

    return render(request, 'inventory/consumption_by_mechanic.html', {
        'mechanic': mechanic,
        'totals': totals,
        'range': which,
        'start': start,
        'end': end,
    })
