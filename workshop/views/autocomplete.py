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
    """Returns list of spare names matching query 'q', combining Master List and Inventory."""
    q = request.GET.get('q', '')
    if len(q) < 1:
        return JsonResponse([], safe=False)
        
    results = []
    
    # 1. Search Inventory Items (Highest priority, styled in yellow on frontend)
    from inventory.models import Item
    inventory_items = Item.objects.filter(name__icontains=q).values_list('name', flat=True)[:5]
    for name in inventory_items:
        results.append({"name": name, "source": "inventory"})
        
    # 2. Search Master List Spares
    master_spares = SparePart.objects.filter(name__icontains=q).exclude(name__in=inventory_items).values_list('name', flat=True)[:10]
    for name in master_spares:
        results.append({"name": name, "source": "master"})
        
    return JsonResponse(results, safe=False)


@staff_required
def autocomplete_concerns(request):
    """Returns list of concern texts matching query 'q'."""
    q = request.GET.get('q', '')
    if len(q) < 1:
        return JsonResponse([], safe=False)
    concerns = ConcernSolution.objects.filter(concern__icontains=q).values_list('concern', flat=True)[:10]
    return JsonResponse(list(concerns), safe=False)
