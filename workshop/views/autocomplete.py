from decimal import Decimal, ROUND_HALF_UP

from django.http import JsonResponse

from ..models import CarBrand, CarModel, SparePart, ConcernSolution, JobCardSpareItem
from ..decorators import staff_required, office_required
from ..invoice import effective_quantity


@staff_required
def autocomplete_brands(request):
    """Returns list of brand names matching query 'q'."""
    q = request.GET.get('q', '')
    if len(q) < 1:
        return JsonResponse([], safe=False)
    brands = CarBrand.objects.filter(name__icontains=q).values_list('name', flat=True)[:10]
    return JsonResponse(list(brands), safe=False)


@staff_required
def autocomplete_models(request):
    """
    Returns list of model names matching query 'q'.
    Optional 'brand' param filters by brand name.
    """
    q = request.GET.get('q', '')
    brand = request.GET.get('brand', '')
    
    qs = CarModel.objects.filter(name__icontains=q)
    if brand:
        qs = qs.filter(brand__name__icontains=brand)
        
    models = qs.values_list('name', flat=True)[:10]
    return JsonResponse(list(models), safe=False)


@staff_required
def autocomplete_spares(request):
    """
    Spare-part names for the Spare Parts (shop) section only.

    Inventory products are deliberately NOT mixed in here any more. They used to
    be, flagged with `source: "inventory"` and highlighted yellow, because the one
    section handled both routes and the highlight was the only hint that picking
    that name would quietly deduct warehouse stock. Warehouse draws now have their
    own section and their own endpoint below, where the product is a real choice
    rather than a name that happens to match.
    """
    q = request.GET.get('q', '')
    if len(q) < 1:
        return JsonResponse([], safe=False)

    names = SparePart.objects.filter(name__icontains=q).values_list('name', flat=True)[:10]
    return JsonResponse([{"name": n, "source": "master"} for n in names], safe=False)


@staff_required
def autocomplete_inventory_items(request):
    """
    Stock products for the Job Card's Inventory section.

    Returns the `id` as well as the name, because the picker writes it into a
    hidden field: the draw is linked by FK, so a product can be renamed without
    detaching it from the job cards that used it. Stock and cost ride along so the
    mechanic can see what is on the shelf while choosing.

    Stock may legitimately be zero or negative — an overdraw awaiting its supplier
    bill — and such products are still offered. Hiding them would block recording
    a part that has physically already been taken, which is the whole reason
    negative stock is allowed.
    """
    from inventory.models import Item

    q = request.GET.get('q', '')
    if len(q) < 1:
        return JsonResponse([], safe=False)

    items = (
        Item.objects.filter(name__icontains=q)
        .select_related('category')
        .order_by('-usage_count', 'name')[:10]
    )
    return JsonResponse([
        {
            "id": it.pk,
            "name": it.name,
            "category": it.category.name,
            "stock": str(it.current_stock),
            "cost": str(it.avg_cost),
        }
        for it in items
    ], safe=False)


# How many past sales the hint averages over. Five is the number the owner
# asked for and it is a reasonable one: enough to absorb the odd oddly-priced
# job, short enough that a price rise six months ago has already washed out.
PRICE_HINT_SAMPLE = 5


@office_required
def spare_price_hint(request):
    """
    What this part usually sells for — a SUGGESTION for the Estimate screen.

    Returns the average customer price per unit over the last few times this
    part name was billed, so the Estimate form can put it in the Unit Price
    box's *placeholder*. It is never written into the field, and nothing on the
    server ever reads it back: a price a human did not type must not be able to
    reach a document a customer is handed. If the hint is wrong, the worst case
    is grey text nobody uses.

    Three decisions worth not re-deriving:

    * **The figure is the CUSTOMER price, not the cost.** It fills a
      customer-facing box, and it is derived with `derive_unit_price`'s own rule
      — `total_price / effective_quantity` — so the suggestion means exactly
      what the printed UNIT PRICE column means. `JobCardSpareItem.unit_price`
      is the workshop's cost and is deliberately NOT read here; suggesting it
      would quote parts at cost.

    * **Job cards only, never past estimates.** A job card is what the workshop
      actually charged and collected. An estimate is a proposal that may have
      been refused, and letting estimates feed each other would let one
      optimistic quote drift the suggestion upward forever with nothing real
      underneath it.

    * **Ordered by most recently recorded** (`-pk`), which is what "the last 5
      entries" means, and it needs no join. Rows with no price are skipped
      before the slice rather than after, so a part priced once and left blank
      four times still returns that one real figure instead of nothing.

    `@office_required`, not `@staff_required` like its neighbours: this is a
    price, and Floor is not shown prices anywhere else in the app.
    """
    name = (request.GET.get('name') or '').strip()
    if not name:
        return JsonResponse({'found': False})

    # `__iexact` over an unindexed column, deliberately: the table is small
    # (single-digit thousands of rows) and `spare_part_name` is free text, so a
    # plain btree index would not serve a case-insensitive match anyway. If this
    # ever shows up in a slow query log, the fix is a functional index on
    # UPPER(spare_part_name), not a change of rule.
    rows = (
        JobCardSpareItem.objects
        .filter(spare_part_name__iexact=name, total_price__isnull=False, total_price__gt=0)
        .order_by('-pk')
        .values_list('total_price', 'quantity')[:PRICE_HINT_SAMPLE]
    )

    unit_prices = [
        Decimal(total) / effective_quantity(qty)
        for total, qty in rows
    ]
    if not unit_prices:
        return JsonResponse({'found': False})

    average = (sum(unit_prices) / len(unit_prices)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    return JsonResponse({
        'found': True,
        'average': str(average),
        # The sample size rides along so the screen can say "avg of last 2"
        # rather than implying five sales that did not happen.
        'count': len(unit_prices),
    })


@staff_required
def autocomplete_concerns(request):
    """Returns list of concern texts matching query 'q'."""
    q = request.GET.get('q', '')
    if len(q) < 1:
        return JsonResponse([], safe=False)
    concerns = ConcernSolution.objects.filter(concern__icontains=q).values_list('concern', flat=True)[:10]
    return JsonResponse(list(concerns), safe=False)
