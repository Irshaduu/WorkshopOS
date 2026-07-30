from django.http import JsonResponse

from ..models import CarBrand, CarModel, SparePart, ConcernSolution
from ..decorators import staff_required


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


@staff_required
def autocomplete_concerns(request):
    """Returns list of concern texts matching query 'q'."""
    q = request.GET.get('q', '')
    if len(q) < 1:
        return JsonResponse([], safe=False)
    concerns = ConcernSolution.objects.filter(concern__icontains=q).values_list('concern', flat=True)[:10]
    return JsonResponse(list(concerns), safe=False)
