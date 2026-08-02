from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import CarBrand, CarModel, SparePart, ConcernSolution, DeletionLog
from ..forms import CarBrandForm, CarModelForm, SparePartForm, ConcernSolutionForm
from ..master_data import rename_spare, rename_concern, rename_brand, rename_model
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
    """
    Rename a brand — and carry the change onto the job cards that used it.

    Renaming a spare or a concern has always reached the job-card history;
    renaming a brand did not, and reports group by `JobCard.brand_name`, so a
    misspelled brand stayed a second brand in Deep Analysis no matter how many
    times the master list was corrected.
    """
    brand = get_object_or_404(CarBrand, pk=pk)
    # Captured BEFORE the form is validated. `is_valid()` runs `_post_clean()`,
    # which calls `construct_instance()` and writes the posted values straight
    # onto the bound instance — so by the time validation returns, `brand.name`
    # is already the NEW name and the old one is unrecoverable in memory.
    old_name = brand.name
    form = CarBrandForm(request.POST or None, request.FILES or None, instance=brand)

    if request.method == 'POST':
        new_name = (request.POST.get('name') or '').strip()
        if not new_name:
            form.add_error('name', 'Brand name cannot be empty.')
        elif form.is_valid():
            # The form still validates and saves the LOGO (an ImageField whose
            # checks are worth keeping) but must not write the name: renaming
            # the row first would leave rename_brand searching for job cards
            # carrying a name that no longer exists — the master list moves and
            # the history stays behind, the exact bug being fixed here.
            obj = form.save(commit=False)
            obj.name = old_name
            obj.save()

            brand.refresh_from_db()
            final_name, merged = rename_brand(brand, new_name, user=request.user)
            messages.success(
                request,
                f"Merged '{old_name}' into '{final_name}'. Job cards updated." if merged
                else f"Renamed to '{final_name}'. Job cards updated.")
            return redirect('brand_list')

    return render(request, 'workshop/master_lists/brand_form.html', {'form': form, 'title': 'Edit Brand'})


@office_required
def brand_delete(request, pk):
    """
    Delete a brand and, by CASCADE, every model under it.

    Job cards are untouched — `JobCard.brand_name` is free text — so this cannot
    alter a bill or a report. Logged like the spare/concern deletes, because
    this one takes the models down with it and was the largest permanent delete
    in the app that left no record of what had been removed.
    """
    brand = get_object_or_404(CarBrand, pk=pk)
    model_names = list(brand.models.order_by('name').values_list('name', flat=True))

    if request.method == 'POST':
        with transaction.atomic():
            DeletionLog.record(
                DeletionLog.ENTITY_MASTER_DATA, brand,
                user=request.user, reason=request.POST.get('reason', '').strip(),
                label=f"Brand '{brand.name}' and {len(model_names)} model(s)",
                extra={'models_deleted': model_names},
            )
            brand.delete()
        messages.success(
            request,
            f"Brand '{brand.name}' and {len(model_names)} model(s) deleted "
            f"(logged to Deletion History)."
        )
        return redirect('brand_list')

    return render(request, 'workshop/master_lists/brand_confirm_delete.html', {
        'brand': brand, 'model_names': model_names,
    })


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
    """Rename a model — and carry the change onto that brand's job cards."""
    model = get_object_or_404(CarModel, pk=pk)
    form = CarModelForm(request.POST or None, request.FILES or None, instance=model)

    if request.method == 'POST':
        new_name = (request.POST.get('name') or '').strip()
        if new_name:
            brand_id = model.brand_id
            final_name, merged = rename_model(model, new_name, user=request.user)
            messages.success(
                request,
                f"Merged into '{final_name}'. Job cards updated." if merged
                else f"Renamed to '{final_name}'. Job cards updated.")
            return redirect('brand_model_list', brand_id=brand_id)
        form.add_error('name', 'Model name cannot be empty.')

    return render(request, 'workshop/master_lists/model_form.html', {'form': form, 'title': 'Edit Model'})


@office_required
def model_delete(request, pk):
    model = get_object_or_404(CarModel, pk=pk)
    brand_id = model.brand.id
    if request.method == 'POST':
        with transaction.atomic():
            DeletionLog.record(
                DeletionLog.ENTITY_MASTER_DATA, model,
                user=request.user, reason=request.POST.get('reason', '').strip(),
                label=f"Model '{model.brand.name} {model.name}'",
            )
            model.delete()
        messages.success(request, "Model deleted (logged to Deletion History).")
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
    """
    Rename a master spare — through the SAME helper Data Cleanup's rename uses.

    This used to be a plain `form.save()`, so the identical edit meant two
    different things depending on which screen it was made from: Data Cleanup
    merged case-variant duplicates and rewrote the job-card lines carrying the
    old name, this one did neither. `rename_spare` is now the only
    implementation of that rule.
    """
    spare = get_object_or_404(SparePart, pk=pk)
    form = SparePartForm(request.POST or None, instance=spare)

    if request.method == 'POST':
        new_name = (request.POST.get('name') or '').strip()
        if new_name:
            old_name = spare.name
            final_name, merged = rename_spare(spare, new_name, user=request.user)
            if merged:
                messages.success(request, f"Merged '{old_name}' into '{final_name}'. All job cards updated.")
            else:
                messages.success(request, f"Renamed to '{final_name}'. All job cards updated.")
            return redirect('spare_list')
        form.add_error('name', 'Name cannot be empty.')

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


# @office_required, not @staff_required. This was the only view in the section
# Floor could reach: they got 200 here and successfully rewrote master concerns,
# while concern_list next door returned 403 — so the section's own list was
# forbidden but editing its contents was not. A view's decorator and its
# neighbours have to agree, or the drawer gate is meaningless.
@office_required
def concern_edit(request, pk):
    """Rename a master concern — through the same helper Data Cleanup uses."""
    concern = get_object_or_404(ConcernSolution, pk=pk)
    form = ConcernSolutionForm(request.POST or None, instance=concern)

    if request.method == 'POST':
        new_text = (request.POST.get('concern') or '').strip()
        if new_text:
            _final, merged = rename_concern(concern, new_text, user=request.user)
            messages.success(
                request,
                "Merged into the existing concern. All job cards updated." if merged
                else "Concern renamed. All job cards updated.")
            return redirect('concern_list')
        form.add_error('concern', 'Concern text cannot be empty.')

    return render(request, 'workshop/master_lists/concern_form.html', {'form': form, 'title': 'Edit Concern'})
