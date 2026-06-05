from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import CarBrand, CarModel, SparePart, ConcernSolution
from ..forms import CarBrandForm, CarModelForm, SparePartForm, ConcernSolutionForm
from ..decorators import staff_required, office_required


# =============================================================================
# MASTER LISTS HOME
# =============================================================================

@office_required
def master_lists_home(request):
    """Landing page for Master Lists section (optional, mostly accessed via dropdown)."""
    return render(request, 'workshop/master_lists/master_lists_home.html')


# =============================================================================
# CARS (Brands & Models)
# =============================================================================

@office_required
def brand_list(request):
    """Grid of Car Brands"""
    brands_query = CarBrand.objects.all()
    paginator = Paginator(brands_query, 24) # 24 for grid layout (4x6 or 3x8)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'workshop/master_lists/brand_list.html', {'brands': page_obj, 'page_obj': page_obj})


@office_required
def brand_create(request):
    form = CarBrandForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        form.save()
        return redirect('brand_list')
    return render(request, 'workshop/master_lists/brand_form.html', {'form': form, 'title': 'Add Brand'})


@office_required
def brand_edit(request, pk):
    brand = get_object_or_404(CarBrand, pk=pk)
    form = CarBrandForm(request.POST or None, request.FILES or None, instance=brand)
    if form.is_valid():
        form.save()
        return redirect('brand_list')
    return render(request, 'workshop/master_lists/brand_form.html', {'form': form, 'title': 'Edit Brand'})


@office_required
def brand_delete(request, pk):
    brand = get_object_or_404(CarBrand, pk=pk)
    if request.method == 'POST':
        brand.delete()
        return redirect('brand_list')
    return render(request, 'workshop/master_lists/brand_confirm_delete.html', {'brand': brand})


@office_required
def brand_model_list(request, brand_id):
    """
    Drilldown: Shows models for a specific brand.
    Used when clicking a Brand Logo in brand_list.
    """
    brand = get_object_or_404(CarBrand, pk=brand_id)
    models = brand.models.all()
    return render(request, 'workshop/master_lists/model_list.html', {'brand': brand, 'models': models})


@office_required
def model_create(request, brand_id=None):
    """
    Create a model. 
    If brand_id is passed (from drilldown), pre-select that brand in the form.
    """
    initial = {}
    if brand_id:
        brand = get_object_or_404(CarBrand, pk=brand_id)
        initial['brand'] = brand
    
    form = CarModelForm(request.POST or None, request.FILES or None, initial=initial)
    if form.is_valid():
        model = form.save()
        # Redirect back to the brand model list
        return redirect('brand_model_list', brand_id=model.brand.id)
        
    return render(request, 'workshop/master_lists/model_form.html', {'form': form, 'title': 'Add Model'})


@office_required
def model_edit(request, pk):
    model = get_object_or_404(CarModel, pk=pk)
    form = CarModelForm(request.POST or None, request.FILES or None, instance=model)
    if form.is_valid():
        form.save()
        return redirect('brand_model_list', brand_id=model.brand.id)
    return render(request, 'workshop/master_lists/model_form.html', {'form': form, 'title': 'Edit Model'})


@office_required
def model_delete(request, pk):
    model = get_object_or_404(CarModel, pk=pk)
    brand_id = model.brand.id
    if request.method == 'POST':
        model.delete()
        return redirect('brand_model_list', brand_id=brand_id)
    return render(request, 'workshop/master_lists/model_confirm_delete.html', {'model': model})


# =============================================================================
# SPARE PARTS
# =============================================================================

@office_required
def spare_list(request):
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip() if is_ajax else ''
    
    spares_query = SparePart.objects.all()
    if q:
        spares_query = spares_query.filter(name__icontains=q)
        
    paginator = Paginator(spares_query, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'workshop/master_lists/spare_list.html', {
        'spares': page_obj, 
        'page_obj': page_obj,
        'q': q
    })


@office_required
def spare_create(request):
    form = SparePartForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect('spare_list')
    return render(request, 'workshop/master_lists/spare_form.html', {'form': form, 'title': 'Add Spare'})


@office_required
def spare_edit(request, pk):
    spare = get_object_or_404(SparePart, pk=pk)
    form = SparePartForm(request.POST or None, instance=spare)
    if form.is_valid():
        form.save()
        return redirect('spare_list')
    return render(request, 'workshop/master_lists/spare_form.html', {'form': form, 'title': 'Edit Spare'})


# =============================================================================
# CONCERNS DATABASE
# =============================================================================

@office_required
def concern_list(request):
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip() if is_ajax else ''
    
    concerns_query = ConcernSolution.objects.all()
    if q:
        for word in q.split():
            concerns_query = concerns_query.filter(
                Q(concern__icontains=word)
            )
            
    paginator = Paginator(concerns_query, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'workshop/master_lists/concern_list.html', {
        'concerns': page_obj, 
        'page_obj': page_obj,
        'q': q
    })


@office_required
def concern_create(request):
    form = ConcernSolutionForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect('concern_list')
    return render(request, 'workshop/master_lists/concern_form.html', {'form': form, 'title': 'Add Concern'})


@staff_required
def concern_edit(request, pk):
    concern = get_object_or_404(ConcernSolution, pk=pk)
    form = ConcernSolutionForm(request.POST or None, instance=concern)
    if form.is_valid():
        form.save()
        return redirect('concern_list')
    return render(request, 'workshop/master_lists/concern_form.html', {'form': form, 'title': 'Edit Concern'})
