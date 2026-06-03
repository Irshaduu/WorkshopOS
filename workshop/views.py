from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q 
from django.http import HttpResponse
from django.core.paginator import Paginator

from .models import (
    CarBrand, CarModel, SparePart, ConcernSolution,
    JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem,
    BulkPayer, BulkPaymentHistory,
    SpareShop, SpareShopPayment
)
from .forms import (
    CarBrandForm, CarModelForm, SparePartForm, ConcernSolutionForm, SpareShopForm,
    JobCardForm, JobCardConcernFormSet, JobCardSpareFormSet, JobCardLabourFormSet
)
from .decorators import staff_required, office_required, owner_required

# =============================================================================
# 1. CORE SECTION (HOME & JOBS)
# =============================================================================

@staff_required
def home(request):
    """
    Dashboard homepage showing all active job cards.
    Discharge date is a planning field, not a filter.
    Cars only move to Delivered when "Delivered" button is clicked.
    """
    from datetime import date
    
    from django.db.models import Count, Q
    
    # Get only non-delivered job cards (where delivered=False)
    # Optimized with select_related and prefetch_related for 1M+ records
    active_jobcards = JobCard.objects.filter(delivered=False, is_deleted=False).select_related('lead_mechanic').prefetch_related('concerns', 'spares', 'labours').annotate(
        total_concerns=Count('concerns'),
        fixed_concerns=Count('concerns', filter=Q(concerns__status='FIXED'))
    ).order_by('-updated_at', '-pk')
    
    # Count delivered today (Active only)
    delivered_count = JobCard.objects.filter(
        delivered=True,
        is_deleted=False,
        discharged_date=date.today()
    ).count()

    # Count pending bills (Delivered but not fully paid, Active only)
    pending_bills_count = JobCard.objects.filter(
        is_deleted=False,
        payment_status__in=['PENDING', 'PARTIAL']
    ).count()
    
    # 5. Pagination for Floor (45 items per page)
    paginator = Paginator(active_jobcards, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'workshop/dashboard/dashboard_home.html', {
        'active_jobcards': page_obj, # Pass page_obj as active_jobcards
        'delivered_count': delivered_count,
        'pending_bills_count': pending_bills_count,
        'page_obj': page_obj,
        'today': date.today(),
    })


@staff_required
def jobcard_create(request):
    """
    Create a new job card with formsets for concerns, spares, and labour.
    Admitted date defaults to today but is editable.
    Redirects to edit page after save with success message.
    Prevents duplicate job cards with 3-attempt confirmation.
    """
    from datetime import date
    from django.contrib import messages
    
    if request.method == 'POST':
        form = JobCardForm(request.POST)

        if form.is_valid():
            jobcard = form.save(commit=False)
            
            # Check for existing active job card for this vehicle
            registration = jobcard.registration_number.strip().upper()
            existing_job = JobCard.objects.filter(
                registration_number__iexact=registration,
                delivered=False
            ).exclude(pk=jobcard.pk).first()
            
            if existing_job:
                # Get or initialize confirmation counter
                session_key = f'duplicate_confirm_{registration}'
                confirm_count = request.session.get(session_key, 0)
                
                if confirm_count < 2:
                    # Increment counter
                    request.session[session_key] = confirm_count + 1
                    
                    # Build message with vehicle details
                    vehicle_info = f"{existing_job.brand_name} {existing_job.model_name}" if existing_job.brand_name else registration
                    
                    # Show warning message
                    messages.warning(
                        request,
                        f'{vehicle_info} ({registration}) has an active job (not marked Delivered).'
                    )
                    
                    # Don't save, return to form with data
                    concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
                    spare_formset = JobCardSpareFormSet(request.POST, prefix='spares')
                    labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')
                    
                    # Fetch master lists for datalists
                    brands = CarBrand.objects.all()
                    models = CarModel.objects.all()
                    spares = SparePart.objects.all()
                    concerns = ConcernSolution.objects.all()
                    
                    return render(request, 'workshop/jobcard/jobcard_form.html', {
                        'form': form,
                        'concern_formset': concern_formset,
                        'spare_formset': spare_formset,
                        'labour_formset': labour_formset,
                        'is_edit': False,
                        'brands': brands,
                        'models': models,
                        'spares': spares,
                        'concerns': concerns,
                    })
                else:
                    # Third attempt - clear counter and proceed with save
                    del request.session[session_key]
            
            # Formsets initialization for standard save
            concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
            spare_formset = JobCardSpareFormSet(request.POST, prefix='spares')
            labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')

            if concern_formset.is_valid() and spare_formset.is_valid() and labour_formset.is_valid():
                jobcard.save()

                # Associate instances with jobcard before saving
                concern_formset.instance = jobcard
                spare_formset.instance = jobcard
                labour_formset.instance = jobcard

                saved_concerns = concern_formset.save()
                saved_spares = spare_formset.save()
                labour_formset.save()
                
                # Auto-learn: Add new concerns to master lists (Case-Insensitive)
                for concern in saved_concerns:
                    if concern.concern_text:
                        text = concern.concern_text.strip()
                        if text and not ConcernSolution.objects.filter(concern__iexact=text).exists():
                            ConcernSolution.objects.create(concern=text)
                
                # Auto-learn: Add new spare parts to master lists (Case-Insensitive)
                for spare in saved_spares:
                    if spare.spare_part_name:
                        name = spare.spare_part_name.strip()
                        if name and not SparePart.objects.filter(name__iexact=name).exists():
                            SparePart.objects.create(name=name)

                # Sync shop FK from shop_name text for all spares on this job card
                for spare in jobcard.spares.all():
                    if spare.shop_name and spare.shop_name.strip():
                        shop_obj = SpareShop.objects.filter(name__iexact=spare.shop_name.strip(), is_trashed=False).first()
                        JobCardSpareItem.objects.filter(pk=spare.pk).update(shop=shop_obj)
                    else:
                        JobCardSpareItem.objects.filter(pk=spare.pk).update(shop=None)

                messages.success(request, f'Job card for {jobcard.registration_number} created successfully!')
                return redirect('jobcard_edit', pk=jobcard.pk)
        else:
            # If form is invalid, we still need to initialize formsets for the context
            concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
            spare_formset = JobCardSpareFormSet(request.POST, prefix='spares')
            labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')
    else:
        # Pre-fill admitted_date with today's date
        initial_data = {'admitted_date': date.today()}
        
        # Pre-fill from GET parameters (Cloning/New Visit feature)
        for field in ['registration_number', 'brand_name', 'model_name', 'customer_name', 'customer_contact']:
            val = request.GET.get(field)
            if val:
                initial_data[field] = val
                
        form = JobCardForm(initial=initial_data)
        concern_formset = JobCardConcernFormSet(prefix='concerns')
        spare_formset = JobCardSpareFormSet(prefix='spares')
        labour_formset = JobCardLabourFormSet(prefix='labours')

    context = {
        'form': form,
        'concern_formset': concern_formset,
        'spare_formset': spare_formset,
        'labour_formset': labour_formset,
        'is_edit': False,
        'spare_shops': SpareShop.objects.filter(is_trashed=False).order_by('name'),
    }
    return render(request, 'workshop/jobcard/jobcard_form.html', context)



@staff_required
def live_report(request):
    """
    SECTION 2.1: LIVE REPORT - Quick scroll for all roles.
    Shows active jobs, concerns, and spares status.
    """
    # Search and Filter support (Titan Exhaustive)
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    
    active_jobs = JobCard.objects.filter(is_deleted=False, delivered=False).select_related('lead_mechanic').prefetch_related('concerns', 'spares').annotate(
        total_concerns=Count('concerns'),
        fixed_concerns=Count('concerns', filter=Q(concerns__status='FIXED'))
    )

    if q:
        for word in q.split():
            active_jobs = active_jobs.filter(
                Q(registration_number__icontains=word) |
                Q(bill_number__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )
            
    if status == 'PAID':
        active_jobs = active_jobs.filter(payment_status='PAID')
    elif status == 'PENDING':
        active_jobs = active_jobs.filter(payment_status='PENDING')

    active_jobs = active_jobs.order_by('-updated_at')
    
    return render(request, 'workshop/jobcard/live_report.html', {
        'active_jobs': active_jobs,
        'q': q,
        'status_filter': status,
    })


@office_required
def jobcard_list(request):
    """
    SECTION 2: JOBS - List of active saved job cards.
    """
    jobcard_list_query = JobCard.objects.filter(is_deleted=False).select_related('lead_mechanic').prefetch_related('spares', 'labours')
    
    # Detect AJAX vs Full Refresh for "Smart Reset"
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip() if is_ajax else ''
    
    if q:
        for word in q.split():
            jobcard_list_query = jobcard_list_query.filter(
                Q(registration_number__icontains=word) |
                Q(bill_number__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(customer_contact__icontains=word) |
                Q(lead_mechanic__name__icontains=word)
            )
        
    paginator = Paginator(jobcard_list_query, 45)  # Show 45 jobs per page
    
    page_number = request.GET.get('page')
    jobcards = paginator.get_page(page_number)
    
    # AJAX Search: Return only the partial template for thousands-ready performance
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/jobcard/job_list_partial.html', {'jobcards': jobcards, 'page_obj': jobcards, 'q': q})
    
    return render(request, 'workshop/jobcard/jobcard_list.html', {'jobcards': jobcards, 'page_obj': jobcards, 'q': q})


@staff_required
def jobcard_detail(request, pk):
    """
    Clean View for a Job Card (Read-Only).
    """
    jobcard = get_object_or_404(
        JobCard.objects.select_related('lead_mechanic').prefetch_related('concerns', 'spares', 'labours'),
        pk=pk
    )

    return render(request, 'workshop/jobcard/jobcard_detail.html', {
        'jobcard': jobcard,
    })


@staff_required
def jobcard_edit(request, pk):
    """
    Edit an existing Job Card. Pre-populates form and formsets.
    Stays on same page after save with success message.
    """
    from django.contrib import messages
    
    jobcard = get_object_or_404(JobCard, pk=pk)

    if request.method == 'POST':
        form = JobCardForm(request.POST, instance=jobcard)
        concern_formset = JobCardConcernFormSet(request.POST, instance=jobcard, prefix='concerns')
        spare_formset = JobCardSpareFormSet(request.POST, instance=jobcard, prefix='spares')
        labour_formset = JobCardLabourFormSet(request.POST, instance=jobcard, prefix='labours')

        if form.is_valid() and concern_formset.is_valid() and spare_formset.is_valid() and labour_formset.is_valid():
            form.save()
            saved_concerns = concern_formset.save()
            saved_spares = spare_formset.save()
            labour_formset.save()
            
            # Auto-learn: Add new concerns to master lists (Case-Insensitive)
            for concern in saved_concerns:
                if concern.concern_text:
                    text = concern.concern_text.strip()
                    if text and not ConcernSolution.objects.filter(concern__iexact=text).exists():
                        ConcernSolution.objects.create(concern=text)
            
            # Auto-learn: Add new spare parts to master lists (Case-Insensitive)
            for spare in saved_spares:
                if spare.spare_part_name:
                    name = spare.spare_part_name.strip()
                    if name and not SparePart.objects.filter(name__iexact=name).exists():
                        SparePart.objects.create(name=name)

            # Sync shop FK from shop_name text for all spares on this job card
            for spare in jobcard.spares.all():
                if spare.shop_name and spare.shop_name.strip():
                    shop_obj = SpareShop.objects.filter(name__iexact=spare.shop_name.strip(), is_trashed=False).first()
                    JobCardSpareItem.objects.filter(pk=spare.pk).update(shop=shop_obj)
                else:
                    JobCardSpareItem.objects.filter(pk=spare.pk).update(shop=None)

            messages.success(request, f'Job card for {jobcard.registration_number} updated successfully!')
            
            # Smart Redirect based on original context
            next_url = request.GET.get('next')
            if next_url == 'mini':
                return redirect('live_report')
                
            return redirect('jobcard_edit', pk=jobcard.pk)
    else:
        form = JobCardForm(instance=jobcard)
        concern_formset = JobCardConcernFormSet(instance=jobcard, prefix='concerns')
        spare_formset = JobCardSpareFormSet(instance=jobcard, prefix='spares')
        labour_formset = JobCardLabourFormSet(instance=jobcard, prefix='labours')

    context = {
        'form': form,
        'concern_formset': concern_formset,
        'spare_formset': spare_formset,
        'labour_formset': labour_formset,
        'jobcard': jobcard,
        'is_edit': True,
        'next_url': request.GET.get('next'),
        'spare_shops': SpareShop.objects.filter(is_trashed=False).order_by('name'),
    }
    return render(request, 'workshop/jobcard/jobcard_form.html', context)


@office_required
def jobcard_delete(request, pk):
    """
    Soft-delete a job card and move it to the Trash.
    """
    jobcard = get_object_or_404(JobCard, pk=pk)
    if request.method == 'POST':
        jobcard.is_deleted = True
        jobcard.save()
        from django.contrib import messages
        messages.warning(request, f"Job Card {jobcard.registration_number} moved to Trash.")
        return redirect('jobcard_list')
    return render(request, 'workshop/jobcard/jobcard_confirm_delete.html', {'jobcard': jobcard})


# =============================================================================
# NEW: TRASH & RECOVERY SECTION
# =============================================================================

@owner_required
def trash_list(request):
    """
    Unified Trash dashboard — all soft-deleted records in one place.
    Optimized for 1M data using strict pagination and AJAX.
    """
    tab = request.GET.get('tab', 'jobcards')
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip()
    page_number = request.GET.get('page')

    context = {
        'active_tab': tab,
        'q': q,
        'jobcard_trash_count': JobCard.objects.filter(is_deleted=True).count(),
        'bulk_payer_trash_count': BulkPayer.objects.filter(is_trashed=True).count(),
        'payments_trash_count': BulkPaymentHistory.objects.filter(is_trashed=True).count(),
        'shop_trash_count': SpareShop.objects.filter(is_trashed=True).count(),
        'shop_payments_trash_count': SpareShopPayment.objects.filter(is_trashed=True).count(),
    }

    if tab == 'jobcards':
        qs = JobCard.objects.filter(is_deleted=True).select_related('lead_mechanic').order_by('-updated_at')
        if q:
            for word in q.split():
                qs = qs.filter(
                    Q(registration_number__icontains=word) |
                    Q(brand_name__icontains=word) |
                    Q(model_name__icontains=word) |
                    Q(customer_name__icontains=word)
                )
        page_obj = Paginator(qs, 45).get_page(page_number)
        context['page_obj'] = page_obj
        if is_ajax:
            return render(request, 'workshop/jobcard/trash_list_partial.html', context)
            
    elif tab == 'bulkpayers':
        qs = BulkPayer.objects.filter(is_trashed=True).order_by('customer_name')
        if q:
            qs = qs.filter(customer_name__icontains=q)
        page_obj = Paginator(qs, 45).get_page(page_number)
        context['page_obj'] = page_obj
        if is_ajax:
            return render(request, 'workshop/jobcard/trash_bulkpayers_partial.html', context)
            
    elif tab == 'payments':
        qs = BulkPaymentHistory.objects.filter(is_trashed=True).order_by('-created_at')
        if q:
            qs = qs.filter(bulk_payer__customer_name__icontains=q)
        page_obj = Paginator(qs, 45).get_page(page_number)
        context['page_obj'] = page_obj
        if is_ajax:
            return render(request, 'workshop/jobcard/trash_payments_partial.html', context)
            
    elif tab == 'spare_shops':
        qs = SpareShop.objects.filter(is_trashed=True).order_by('name')
        if q:
            qs = qs.filter(name__icontains=q)
        page_obj = Paginator(qs, 45).get_page(page_number)
        context['page_obj'] = page_obj
        if is_ajax:
            return render(request, 'workshop/jobcard/trash_spareshops_partial.html', context)
            
    elif tab == 'shop_payments':
        qs = SpareShopPayment.objects.filter(is_trashed=True).order_by('-created_at')
        if q:
            qs = qs.filter(shop__name__icontains=q)
        page_obj = Paginator(qs, 45).get_page(page_number)
        context['page_obj'] = page_obj
        if is_ajax:
            return render(request, 'workshop/jobcard/trash_shoppayments_partial.html', context)

    return render(request, 'workshop/jobcard/trash_list.html', context)



@owner_required
def restore_jobcard(request, pk):
    """
    Restore a record from the Trash to the main Floor.
    """
    jobcard = get_object_or_404(JobCard, pk=pk)
    jobcard.is_deleted = False
    jobcard.save()
    from django.contrib import messages
    messages.success(request, f"Successfully restored {jobcard.registration_number} to the Floor.")
    return redirect('/trash/?tab=jobcards')

@owner_required
def permanent_delete_jobcard(request, pk):
    """
    Permanently delete a record from the database.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk, is_deleted=True)
        reg = jobcard.registration_number
        jobcard.delete()
        from django.contrib import messages
        messages.success(request, f"Successfully permanently deleted {reg}.")
    return redirect('/trash/?tab=jobcards')


# =============================================================================
# NEW: DELIVERED SECTION (Workshop Dashboard)
# =============================================================================

@office_required
def delivered_list(request):
    """
    Show delivered vehicles with date range filtering and AJAX search.
    """
    from datetime import date, timedelta
    
    # 1. Base Query (Active only)
    delivered_jobcards = JobCard.objects.filter(delivered=True, is_deleted=False).select_related('lead_mechanic').prefetch_related('spares', 'labours').order_by('-discharged_date')
    
    # 2. Smart Reset: Reset to "Today" on full page refresh to avoid confusion with historical data
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    
    if not is_ajax:
        filter_type = 'today'
        q = ''
    else:
        filter_type = request.GET.get('filter', 'today')
        q = request.GET.get('q', '').strip()

    # 3. Apply Search Filters (Registration, Customer, Brand, Model)
    if q:
        for word in q.split():
            delivered_jobcards = delivered_jobcards.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )
    
    # 4. Apply Date Filters
    today = date.today()
    if filter_type == 'today':
        delivered_jobcards = delivered_jobcards.filter(discharged_date=today)
    elif filter_type == 'week':
        start_date = today - timedelta(days=7)
        delivered_jobcards = delivered_jobcards.filter(discharged_date__gte=start_date)
    elif filter_type == 'month':
        start_date = today - timedelta(days=30)
        delivered_jobcards = delivered_jobcards.filter(discharged_date__gte=start_date)
    elif filter_type == 'year':
        start_date = today - timedelta(days=365)
        delivered_jobcards = delivered_jobcards.filter(discharged_date__gte=start_date)
    elif filter_type == 'custom':
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        if start_date and end_date:
            delivered_jobcards = delivered_jobcards.filter(
                discharged_date__gte=start_date,
                discharged_date__lte=end_date
            )
    
    # 4. Pagination
    paginator = Paginator(delivered_jobcards, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'delivered_jobcards': page_obj,
        'page_obj': page_obj, # Explicitly included for consistency
        'filter_type': filter_type,
        'q': q,
        'start_date': start_date if filter_type == 'custom' else '',
        'end_date': end_date if filter_type == 'custom' else '',
    }
    
    # 5. AJAX Return Partial
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/delivered/delivered_list_partial.html', context)
        
    return render(request, 'workshop/delivered/delivered_list.html', context)

@office_required
def mark_delivered(request, pk):
    """
    Mark job card as delivered.
    Auto-sets discharged_date to today (actual delivery date).
    """
    if request.method == 'POST':
        from datetime import date
        jobcard = get_object_or_404(JobCard, pk=pk)
        jobcard.delivered = True
        jobcard.discharged_date = date.today()
        jobcard.save()
    return redirect('home')


@office_required
def undo_delivered(request, pk):
    """
    Undo delivery by setting delivered=False and clearing discharged_date.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)
        jobcard.delivered = False
        jobcard.discharged_date = None
        jobcard.save()
    return redirect('delivered_list')


@office_required # Only office/owner can toggle hold as it affects planning
def toggle_hold(request, pk):
    """
    Toggle the on_hold status of a job card.
    Used when waiting for parts or other delays.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)
        jobcard.on_hold = not jobcard.on_hold
        jobcard.save()
    return redirect('home')



# =============================================================================
# 2. MASTER LISTS (CARS, SPARES, CONCERNS)
# =============================================================================

@office_required
def master_lists_home(request):
    """Landing page for Master Lists section (optional, mostly accessed via dropdown)."""
    return render(request, 'workshop/master_lists/master_lists_home.html')

# --- CARS (Brands & Models) ---

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

# --- SPARE PARTS ---

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

# --- CONCERNS DATABASE ---

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


# =============================================================================
# 3. AUTOCOMPLETE API
# =============================================================================

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


# ============================================================================
# INVOICE VIEW
# ============================================================================

from django.http import HttpResponseForbidden

@office_required
def invoice_view(request, pk):
    """Display professional invoice for a job card"""
    
    jobcard = get_object_or_404(JobCard, pk=pk)
    
    # Calculate labour subtotal (using correct related name: labours)
    labour_subtotal = sum(
        item.amount or 0 
        for item in jobcard.labours.all()
    )
    
    # Calculate spare parts subtotal (using correct related name: spares)
    spare_subtotal = sum(
        item.total_price or 0 
        for item in jobcard.spares.all()
    )
    
    # Calculate grand total
    grand_total = labour_subtotal + spare_subtotal
    
    # Calculate final totals (NEW LOGIC)
    discount = 0 # Not used for now, or keep as 0
    received = jobcard.received_amount or 0
    balance = jobcard.get_balance_amount
    
    return render(request, 'workshop/invoice/invoice_template.html', {
        'jobcard': jobcard,
        'labour_subtotal': labour_subtotal,
        'spare_subtotal': spare_subtotal,
        'grand_total': grand_total,
        'received': received,
        'balance': balance,
    })


from decimal import Decimal

@office_required
def update_bill_status(request, pk):
    """
    Quickly update payment status and received amount from Invoice popup.
    Automatically calculates internal discount if Status is PAID.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)
        
        # Safely convert to Decimal
        raw_received = request.POST.get('received_amount', '0')
        try:
            received = Decimal(str(raw_received) if raw_received else '0')
        except:
            received = Decimal('0')
            
        method = request.POST.get('payment_method')
        status = request.POST.get('payment_status', 'PAID')

        jobcard.received_amount = received
        jobcard.payment_method = method
        jobcard.payment_status = status
        
        # Calculate internal discount silently for admin reports
        if status == 'PAID':
            total_bill = Decimal(str(jobcard.get_total_amount or '0'))
            jobcard.discount_amount = max(Decimal('0'), total_bill - received)
        else:
            jobcard.discount_amount = Decimal('0')

        jobcard.save()

        from django.contrib import messages
        messages.success(request, f"Billing updated for {jobcard.registration_number}")
    
    return redirect('invoice_view', pk=pk)


# ============================================================================
# BULK PAYER SYSTEM (Persistent Fleet/Repeat Customer Groups)
# ============================================================================

@office_required
def bulk_payer_list(request):
    """
    Returns the list of all bulk payers as an AJAX partial.
    Called from the Pending Bills page.
    Million-data safe: all aggregation done in SQL, zero Python loops.
    """
    from django.db.models import (
        Sum, Count, Value, F, OuterRef, Subquery,
        DecimalField, ExpressionWrapper, IntegerField
    )
    from django.db.models.functions import Coalesce

    # SQL subquery: count of PENDING/PARTIAL job cards per payer
    pending_count_sq = (
        BulkPayer.job_cards.through.objects
        .filter(
            bulkpayer_id=OuterRef('pk'),
            jobcard__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('bulkpayer_id')
        .annotate(n=Count('jobcard_id'))
        .values('n')
    )

    # SQL subquery: sum of received_amount for PENDING/PARTIAL job cards
    received_sq = (
        BulkPayer.job_cards.through.objects
        .filter(
            bulkpayer_id=OuterRef('pk'),
            jobcard__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('bulkpayer_id')
        .annotate(s=Sum('jobcard__received_amount'))
        .values('s')
    )

    # SQL subquery: sum of spares for PENDING/PARTIAL job cards
    spares_sq = (
        JobCardSpareItem.objects
        .filter(
            job_card__bulk_payers=OuterRef('pk'),
            job_card__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('job_card__bulk_payers')
        .annotate(s=Sum('total_price'))
        .values('s')
    )

    # SQL subquery: sum of labour for PENDING/PARTIAL job cards
    labour_sq = (
        JobCardLabourItem.objects
        .filter(
            job_card__bulk_payers=OuterRef('pk'),
            job_card__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('job_card__bulk_payers')
        .annotate(s=Sum('amount'))
        .values('s')
    )

    bulk_payers = (
        BulkPayer.objects
        .filter(is_trashed=False)
        .annotate(
            card_count=Coalesce(Subquery(pending_count_sq, output_field=IntegerField()), Value(0)),
            total_spares=Coalesce(Subquery(spares_sq, output_field=DecimalField()), Value(0, output_field=DecimalField())),
            total_labour=Coalesce(Subquery(labour_sq, output_field=DecimalField()), Value(0, output_field=DecimalField())),
            total_received=Coalesce(Subquery(received_sq, output_field=DecimalField()), Value(0, output_field=DecimalField())),
        )
        .annotate(
            total_balance=ExpressionWrapper(
                F('total_spares') + F('total_labour') - F('total_received'),
                output_field=DecimalField()
            )
        )
        .order_by('customer_name')
    )

    return render(request, 'workshop/jobcard/bulk_payer_panel.html', {
        'bulk_payers': bulk_payers,
    })


@office_required
def bulk_payer_create(request):
    """
    POST: Create a new BulkPayer and auto-add all matching PENDING/PARTIAL 
    job cards with the same customer_name.
    """
    if request.method == 'POST':
        customer_name = request.POST.get('customer_name', '').strip()
        
        if not customer_name:
            messages.error(request, "Customer name cannot be empty.")
            return redirect('pending_payments_list')
        
        if BulkPayer.objects.filter(customer_name__iexact=customer_name).exists():
            messages.error(request, f"Bulk payer '{customer_name}' already exists.")
            return redirect('pending_payments_list')
        
        bulk_payer = BulkPayer.objects.create(customer_name=customer_name)
        
        # Auto-add all PENDING/PARTIAL job cards with matching customer name
        matching_cards = JobCard.objects.filter(
            customer_name__iexact=customer_name,
            payment_status__in=['PENDING', 'PARTIAL']
        )
        bulk_payer.job_cards.add(*matching_cards)
        
        count = matching_cards.count()
        messages.success(request, f"Bulk payer '{customer_name}' created with {count} pending job card(s).")
        return redirect('bulk_payer_detail', pk=bulk_payer.pk)
    
    return redirect('pending_payments_list')


@office_required
def bulk_payer_detail(request, pk):
    """
    Full page: Shows all cars in a bulk payer group with financials.
    Million-data optimized with SQL subqueries and annotations.
    """
    from django.db.models import Sum, Value, F, OuterRef, Subquery, DecimalField, ExpressionWrapper, Count, Max
    from django.db.models.functions import Coalesce
    
    bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=False)
    
    # Get pending/partial job cards only (PAID and BULK_PAID are hidden)
    base_cards_query = bulk_payer.job_cards.filter(
        payment_status__in=['PENDING', 'PARTIAL']
    )
    
    # -------------------------------------------------------------------------
    # 1. Grand totals (Calculated efficiently in SQL without Python loops)
    # -------------------------------------------------------------------------
    total_received_all = base_cards_query.aggregate(s=Sum('received_amount'))['s'] or Decimal('0.0')
    total_spares = JobCardSpareItem.objects.filter(job_card__in=base_cards_query).aggregate(s=Sum('total_price'))['s'] or Decimal('0.0')
    total_labour = JobCardLabourItem.objects.filter(job_card__in=base_cards_query).aggregate(s=Sum('amount'))['s'] or Decimal('0.0')
    
    total_bill_all = total_spares + total_labour
    total_balance_all = max(Decimal('0.0'), total_bill_all - total_received_all)
    card_count = base_cards_query.count()

    # -------------------------------------------------------------------------
    # 2. Per-row Financial Annotations
    # -------------------------------------------------------------------------
    cards_query = base_cards_query.select_related('lead_mechanic')
    
    spares_subquery = JobCardSpareItem.objects.filter(
        job_card=OuterRef('pk')
    ).values('job_card').annotate(total=Sum('total_price')).values('total')
    
    labours_subquery = JobCardLabourItem.objects.filter(
        job_card=OuterRef('pk')
    ).values('job_card').annotate(total=Sum('amount')).values('total')
    
    cards_query = cards_query.annotate(
        annotated_spares=Coalesce(Subquery(spares_subquery), Value(Decimal('0.0'), output_field=DecimalField())),
        annotated_labour=Coalesce(Subquery(labours_subquery), Value(Decimal('0.0'), output_field=DecimalField()))
    ).annotate(
        total_bill=ExpressionWrapper(
            F('annotated_spares') + F('annotated_labour'),
            output_field=DecimalField()
        )
    ).annotate(
        balance_amount=ExpressionWrapper(
            F('total_bill') - F('received_amount'),
            output_field=DecimalField()
        )
    ).order_by('admitted_date', 'pk')
    
    # -------------------------------------------------------------------------
    # 3. True Lazy Pagination (Million-data ready)
    # -------------------------------------------------------------------------
    paginator = Paginator(cards_query, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # -------------------------------------------------------------------------
    # 4. Optimized Visit Counting (Queries ONLY the 21 cars on this page)
    # -------------------------------------------------------------------------
    unique_regs = list(set(card.registration_number for card in page_obj))
    
    if unique_regs:
        reg_counts = dict(
            JobCard.objects.filter(registration_number__in=unique_regs)
            .values('registration_number')
            .annotate(total=Count('id'))
            .values_list('registration_number', 'total')
        )
        
        all_cards_for_regs = (
            JobCard.objects.filter(registration_number__in=unique_regs)
            .order_by('admitted_date', 'pk')
            .values_list('registration_number', 'pk')
        )
        reg_visit_tracker = {}
        for reg, pk_val in all_cards_for_regs:
            if reg not in reg_visit_tracker:
                reg_visit_tracker[reg] = []
            reg_visit_tracker[reg].append(pk_val)
            
        for card in page_obj:
            card.total_visits = reg_counts.get(card.registration_number, 1)
            try:
                card.visit_number = reg_visit_tracker[card.registration_number].index(card.pk) + 1
            except (KeyError, ValueError):
                card.visit_number = 1
    
    return render(request, 'workshop/jobcard/bulk_payer_detail.html', {
        'bulk_payer': bulk_payer,
        'cards': page_obj,
        'page_obj': page_obj,
        'total_bill': total_bill_all,
        'total_received': total_received_all,
        'total_balance': total_balance_all,
        'card_count': card_count,
        'payment_history': bulk_payer.payment_history.filter(is_trashed=False).order_by('-created_at')
    })


@office_required
def bulk_payer_add_card(request, pk):
    """
    POST: Add a job card to a bulk payer group by job card ID.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk)
        job_card_id = request.POST.get('job_card_id', '').strip()
        
        if not job_card_id:
            # Search by registration number instead
            reg_number = request.POST.get('registration_number', '').strip().upper()
            if reg_number:
                matching = JobCard.objects.filter(
                    registration_number__iexact=reg_number,
                    payment_status__in=['PENDING', 'PARTIAL']
                ).exclude(bulk_payers=bulk_payer)
                
                if matching.exists():
                    bulk_payer.job_cards.add(*matching)
                    messages.success(request, f"Added {matching.count()} job card(s) for {reg_number}.")
                else:
                    messages.error(request, f"No pending job cards found for '{reg_number}' or already added.")
            else:
                messages.error(request, "Please provide a registration number or job card ID.")
        else:
            try:
                job_card = JobCard.objects.get(pk=int(job_card_id))
                bulk_payer.job_cards.add(job_card)
                messages.success(request, f"Added {job_card.registration_number} to {bulk_payer.customer_name}.")
            except (JobCard.DoesNotExist, ValueError):
                messages.error(request, "Job card not found.")
    
    return redirect('bulk_payer_detail', pk=pk)


@office_required
def bulk_payer_remove_card(request, pk):
    """
    POST: Remove a job card from a bulk payer group.
    Does NOT delete the job card — just removes the association.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk)
        job_card_id = request.POST.get('job_card_id')
        
        try:
            job_card = JobCard.objects.get(pk=int(job_card_id))
            bulk_payer.job_cards.remove(job_card)
            messages.success(
                request,
                f"Removed {job_card.brand_name} {job_card.model_name} ({job_card.registration_number}) from {bulk_payer.customer_name}."
            )
        except (JobCard.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Job card not found.")
    
    return redirect('bulk_payer_detail', pk=pk)


@office_required
def bulk_payer_pay(request, pk):
    """
    POST: Process a lump sum payment via the Cascade Algorithm.
    Distributes payment oldest-first. Fully paid cards get BULK_PAID status.
    Thread-safe with select_for_update.
    """
    if request.method != 'POST':
        return redirect('bulk_payer_detail', pk=pk)
    
    bulk_payer = get_object_or_404(BulkPayer, pk=pk)
    lump_sum_raw = request.POST.get('lump_sum', '0')
    payment_method = request.POST.get('payment_method', 'CASH')
    
    try:
        lump_sum = Decimal(str(lump_sum_raw))
    except:
        lump_sum = Decimal('0')
    
    if lump_sum <= 0:
        messages.error(request, "Invalid payment amount.")
        return redirect('bulk_payer_detail', pk=pk)
    
    from django.db.models import Sum, Value, F, OuterRef, Subquery, DecimalField, ExpressionWrapper
    from django.db.models.functions import Coalesce
    from django.db import transaction
    
    spares_subquery = JobCardSpareItem.objects.filter(job_card=OuterRef('pk')).values('job_card').annotate(total=Sum('total_price')).values('total')
    labours_subquery = JobCardLabourItem.objects.filter(job_card=OuterRef('pk')).values('job_card').annotate(total=Sum('amount')).values('total')
    
    with transaction.atomic():
        pending_cards = bulk_payer.job_cards.select_for_update().filter(
            payment_status__in=['PENDING', 'PARTIAL']
        ).annotate(
            annotated_spares=Coalesce(Subquery(spares_subquery), Value(Decimal('0.0'), output_field=DecimalField())),
            annotated_labour=Coalesce(Subquery(labours_subquery), Value(Decimal('0.0'), output_field=DecimalField()))
        ).annotate(
            total_bill=ExpressionWrapper(F('annotated_spares') + F('annotated_labour'), output_field=DecimalField())
        ).annotate(
            balance_amount=ExpressionWrapper(F('total_bill') - F('received_amount'), output_field=DecimalField())
        ).order_by('admitted_date', 'pk')  # Oldest first
        
        remaining_funds = lump_sum
        jobs_updated = 0
        history_details = []  # Track per-job breakdown for history
        
        for job in pending_cards:
            if remaining_funds <= 0:
                break
            
            balance = job.balance_amount
            if balance <= 0:
                continue
            
            if remaining_funds >= balance:
                # Fully pay this card
                paid_amount = balance
                job.received_amount += balance
                job.payment_status = 'BULK_PAID'
                job.payment_method = payment_method
                job.discount_amount = Decimal('0')
                remaining_funds -= balance
            else:
                # Partial payment
                paid_amount = remaining_funds
                job.received_amount += remaining_funds
                job.payment_status = 'PARTIAL'
                job.payment_method = payment_method
                remaining_funds = Decimal('0')
            
            job.save()
            jobs_updated += 1
            history_details.append({
                'job_id': job.pk,
                'reg': job.registration_number,
                'car': f"{job.brand_name} {job.model_name}",
                'paid': str(paid_amount),
                'status': job.payment_status,
            })
        
        # Create payment history record
        import json
        BulkPaymentHistory.objects.create(
            bulk_payer=bulk_payer,
            amount=lump_sum,
            payment_method=payment_method,
            jobs_affected=jobs_updated,
            details=json.dumps(history_details),
        )
    
    messages.success(request, f"₹{lump_sum:,.0f} distributed across {jobs_updated} job(s) for {bulk_payer.customer_name}.")
    return redirect('bulk_payer_detail', pk=pk)


@owner_required
def bulk_payer_delete(request, pk):
    """
    POST: Soft-delete a bulk payer group (move to trash).
    Owner only. Does NOT delete job cards — only hides the grouping.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk)
        bulk_payer.is_trashed = True
        bulk_payer.save()
        messages.success(request, f"Bulk payer '{bulk_payer.customer_name}' moved to trash.")
    
    return redirect('pending_payments_list')


@owner_required
def bulk_payer_trash_list(request):
    """
    Redirect to unified Trash page, Bulk Payers tab.
    Kept for backward compatibility with any existing links/bookmarks.
    """
    return redirect('/trash/?tab=bulkpayers')


@owner_required
def bulk_payer_restore(request, pk):
    """
    POST: Restore a trashed bulk payer. Owner only.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=True)
        bulk_payer.is_trashed = False
        bulk_payer.save()
        messages.success(request, f"Bulk payer '{bulk_payer.customer_name}' restored.")
    return redirect('/trash/?tab=bulkpayers')


@owner_required
def bulk_payer_permanent_delete(request, pk):
    """
    POST: Permanently delete a trashed bulk payer. Owner only.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=True)
        name = bulk_payer.customer_name
        bulk_payer.delete()
        messages.success(request, f"Bulk payer '{name}' permanently deleted.")
    return redirect('/trash/?tab=bulkpayers')


@owner_required
def bulk_payment_history_delete(request, pk, history_pk):
    """
    POST: Delete a payment history entry and reverse the payments.
    Reverses the cascade — subtracts amounts from affected job cards.
    Owner only.
    """
    if request.method != 'POST':
        return redirect('bulk_payer_detail', pk=pk)
    
    import json
    from django.db import transaction
    
    bulk_payer = get_object_or_404(BulkPayer, pk=pk)
    history = get_object_or_404(BulkPaymentHistory, pk=history_pk, bulk_payer=bulk_payer)
    
    with transaction.atomic():
        # Reverse payments from the history snapshot
        try:
            details = json.loads(history.details)
        except (json.JSONDecodeError, TypeError):
            details = []
        
        for entry in details:
            try:
                job = JobCard.objects.select_for_update().get(pk=entry['job_id'])
                reversed_amount = Decimal(str(entry['paid']))
                job.received_amount = max(Decimal('0'), job.received_amount - reversed_amount)
                
                # Recalculate status
                if job.received_amount <= 0:
                    job.payment_status = 'PENDING'
                else:
                    job.payment_status = 'PARTIAL'
                
                job.save()
            except (JobCard.DoesNotExist, KeyError, Exception):
                continue
        
        history.is_trashed = True
        history.save()
    
    messages.success(request, f"Payment of ₹{history.amount:,.0f} reversed and moved to Trash.")
    return redirect('bulk_payer_detail', pk=pk)

@owner_required
def permanent_delete_payment_history(request, history_pk):
    """
    POST: Permanently delete a payment history entry from the database.
    Owner only.
    """
    if request.method == 'POST':
        history = get_object_or_404(BulkPaymentHistory, pk=history_pk, is_trashed=True)
        amount = history.amount
        history.delete()
        messages.success(request, f"Payment history of ₹{amount:,.0f} permanently deleted.")
    return redirect('/trash/?tab=payments')


# ============================================================================
# SPARE SHOP SYSTEM (Supplier Payment Tracking)
# Office + Owner only — mirrors BulkPayer but for money going OUT to shops.
# ============================================================================

@office_required
def spare_shop_list(request):
    """
    Lists all registered spare shops with annotated financial totals.
    Calculates total purchased (unit_price sum), total paid, and balance owed
    entirely in SQL — zero Python loops.
    """
    from django.db.models import Sum, Count, F, Value, ExpressionWrapper, DecimalField
    from django.db.models.functions import Coalesce

    shops = (
        SpareShop.objects.filter(is_trashed=False)
        .annotate(
            total_purchases=Coalesce(
                Sum(ExpressionWrapper(F('spare_items__unit_price') * Coalesce(F('spare_items__quantity'), Value(1, output_field=DecimalField())), output_field=DecimalField())),
                Value(0, output_field=DecimalField())
            ),
            total_paid=Coalesce(
                Sum('spare_items__shop_paid_amount'),
                Value(0, output_field=DecimalField())
            ),
            item_count=Count('spare_items', distinct=True),
        )
        .annotate(
            total_balance=ExpressionWrapper(
                F('total_purchases') - F('total_paid'),
                output_field=DecimalField()
            )
        )
        .order_by('name')
    )

    return render(request, 'workshop/spare_shops/shop_list.html', {
        'shops': shops,
    })


@office_required
def spare_shop_create(request):
    """POST: Create a new SpareShop entry."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()

        if not name:
            messages.error(request, "Shop name cannot be empty.")
            return redirect('spare_shop_list')

        if SpareShop.objects.filter(name__iexact=name).exists():
            messages.error(request, f"Shop '{name}' already exists.")
            return redirect('spare_shop_list')

        shop = SpareShop.objects.create(
            name=name,
            phone=phone or None,
            address=address or None,
        )
        messages.success(request, f"Shop '{shop.name}' created successfully.")
        return redirect('spare_shop_detail', pk=shop.pk)

    return redirect('spare_shop_list')


@office_required
def spare_shop_edit(request, pk):
    """POST: Edit an existing SpareShop (name, phone, address)."""
    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()

        if not name:
            messages.error(request, "Shop name cannot be empty.")
            return redirect('spare_shop_detail', pk=pk)

        if SpareShop.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, f"Another shop named '{name}' already exists.")
            return redirect('spare_shop_detail', pk=pk)

        shop.name = name
        shop.phone = phone or None
        shop.address = address or None
        shop.save()
        messages.success(request, f"Shop '{shop.name}' updated.")
    return redirect('spare_shop_detail', pk=pk)


@office_required
def spare_shop_detail(request, pk):
    """
    Full page: All spare items purchased from this shop across all job cards.
    Shows per-item financials and payment history.
    """
    from decimal import Decimal
    from datetime import date, timedelta
    from django.db.models import Sum, Value, F, ExpressionWrapper, DecimalField
    from django.db.models.functions import Coalesce

    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)

    # All spare items from this shop, ordered oldest first (cascade order)
    items_qs = (
        JobCardSpareItem.objects
        .filter(shop=shop)
        .select_related('job_card')
        .order_by('job_card__admitted_date', 'pk')
    )

    payment_qs = shop.payments.filter(is_trashed=False).order_by('-created_at')

    # Date Filtering
    filter_type = request.GET.get('filter', 'all')
    start_date_str = ''
    end_date_str = ''
    today = date.today()

    if filter_type == 'month':
        sd = today - timedelta(days=30)
        items_qs = items_qs.filter(job_card__admitted_date__gte=sd)
        payment_qs = payment_qs.filter(created_at__date__gte=sd)
    elif filter_type == 'year':
        sd = today - timedelta(days=365)
        items_qs = items_qs.filter(job_card__admitted_date__gte=sd)
        payment_qs = payment_qs.filter(created_at__date__gte=sd)
    elif filter_type == 'custom':
        start_date_str = request.GET.get('start_date', '')
        end_date_str = request.GET.get('end_date', '')
        if start_date_str and end_date_str:
            items_qs = items_qs.filter(
                job_card__admitted_date__gte=start_date_str,
                job_card__admitted_date__lte=end_date_str
            )
            payment_qs = payment_qs.filter(
                created_at__date__gte=start_date_str,
                created_at__date__lte=end_date_str
            )

    # Grand totals (pure SQL)
    totals = items_qs.aggregate(
        total_purchases=Coalesce(Sum(ExpressionWrapper(F('unit_price') * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField())), output_field=DecimalField())), Value(Decimal('0'), output_field=DecimalField()), output_field=DecimalField()),
        total_paid=Coalesce(Sum('shop_paid_amount'), Value(Decimal('0')), output_field=DecimalField()),
    )
    total_purchases = totals['total_purchases']
    total_paid = totals['total_paid']
    total_balance = max(Decimal('0'), total_purchases - total_paid)
    item_count = items_qs.count()

    # Annotate per-item balance for the template
    items_qs = items_qs.annotate(
        item_balance=ExpressionWrapper(
            (Coalesce(F('unit_price'), Value(Decimal('0'), output_field=DecimalField())) * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField()))) - F('shop_paid_amount'),
            output_field=DecimalField()
        )
    )

    paginator = Paginator(items_qs, 45)
    page_obj = paginator.get_page(request.GET.get('page'))

    pay_paginator = Paginator(payment_qs, 15)
    pay_page_obj = pay_paginator.get_page(request.GET.get('pay_page'))

    return render(request, 'workshop/spare_shops/shop_detail.html', {
        'shop': shop,
        'items': page_obj,
        'page_obj': page_obj,
        'total_purchases': total_purchases,
        'total_paid': total_paid,
        'total_balance': total_balance,
        'item_count': item_count,
        'pay_page_obj': pay_page_obj,
        'pay_count': payment_qs.count(),
        'filter_type': filter_type,
        'start_date': start_date_str if filter_type == 'custom' else '',
        'end_date': end_date_str if filter_type == 'custom' else '',
    })


@office_required
def spare_shop_pay(request, pk):
    """
    POST: Process a lump-sum payment to a shop using the Cascade Algorithm.
    Distributes the amount across unpaid items oldest-first.
    Thread-safe via select_for_update. Creates a SpareShopPayment audit record.
    """
    from decimal import Decimal
    from django.db import transaction
    from django.db.models import F, Value, ExpressionWrapper, DecimalField, Sum
    from django.db.models.functions import Coalesce
    import json

    if request.method != 'POST':
        return redirect('spare_shop_detail', pk=pk)

    shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
    payment_method = request.POST.get('payment_method', 'CASH')
    note = request.POST.get('note', '').strip()

    try:
        lump_sum = Decimal(str(request.POST.get('lump_sum', '0')))
    except Exception:
        lump_sum = Decimal('0')

    if lump_sum <= 0:
        messages.error(request, "Invalid payment amount.")
        return redirect('spare_shop_detail', pk=pk)

    with transaction.atomic():
        pending_items = (
            JobCardSpareItem.objects
            .select_for_update()
            .filter(shop=shop)
            .exclude(unit_price__isnull=True)
            .annotate(
                item_balance=ExpressionWrapper(
                    (F('unit_price') * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField()))) - F('shop_paid_amount'),
                    output_field=DecimalField()
                )
            )
            .filter(item_balance__gt=0)
            .order_by('job_card__admitted_date', 'pk')
        )

        total_outstanding = pending_items.aggregate(
            total=Coalesce(
                Sum(ExpressionWrapper((F('unit_price') * Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField()))) - F('shop_paid_amount'), output_field=DecimalField())),
                Value(Decimal('0'), output_field=DecimalField())
            )
        )['total']

        if lump_sum > total_outstanding:
            messages.error(request, f"Amount (₹{lump_sum:,.0f}) exceeds total balance of ₹{total_outstanding:,.0f}.")
            return redirect('spare_shop_detail', pk=pk)

        remaining = lump_sum
        items_updated = 0
        history_details = []

        for item in pending_items:
            if remaining <= 0:
                break
            balance = (item.unit_price * (item.quantity or Decimal('1'))) - item.shop_paid_amount
            if balance <= 0:
                continue

            if remaining >= balance:
                paid_amount = balance
                item.shop_paid_amount += balance
                remaining -= balance
            else:
                paid_amount = remaining
                item.shop_paid_amount += remaining
                remaining = Decimal('0')

            item.save(update_fields=['shop_paid_amount'])
            items_updated += 1
            history_details.append({
                'item_id': item.pk,
                'job_id': item.job_card_id,
                'part': item.spare_part_name or '—',
                'paid': str(paid_amount),
            })

        SpareShopPayment.objects.create(
            shop=shop,
            amount=lump_sum,
            payment_method=payment_method,
            note=note or None,
            items_affected=items_updated,
            details=json.dumps(history_details),
        )

    messages.success(request, f"₹{lump_sum:,.0f} distributed across {items_updated} item(s) for {shop.name}.")
    return redirect('spare_shop_detail', pk=pk)


@office_required
def spare_shop_pay_item(request, shop_pk, item_pk):
    """
    POST: Pay a single spare item immediately (Pay Now button).
    Pays the full remaining balance for that specific item.
    Creates a SpareShopPayment audit record.
    """
    from decimal import Decimal
    from django.db import transaction
    import json

    if request.method != 'POST':
        return redirect('spare_shop_detail', pk=shop_pk)

    shop = get_object_or_404(SpareShop, pk=shop_pk, is_trashed=False)
    item = get_object_or_404(JobCardSpareItem, pk=item_pk, shop=shop)
    payment_method = request.POST.get('payment_method', 'CASH')
    note = request.POST.get('note', '').strip()

    unit_price = item.unit_price or Decimal('0')
    qty = item.quantity or Decimal('1')
    total_cost = unit_price * qty
    already_paid = item.shop_paid_amount or Decimal('0')
    pay_now = total_cost - already_paid

    if pay_now <= 0:
        messages.info(request, "This item is already fully paid.")
        return redirect('spare_shop_detail', pk=shop_pk)

    with transaction.atomic():
        item.shop_paid_amount = total_cost
        item.save(update_fields=['shop_paid_amount'])

        SpareShopPayment.objects.create(
            shop=shop,
            amount=pay_now,
            payment_method=payment_method,
            note=note or None,
            items_affected=1,
            details=json.dumps([{
                'item_id': item.pk,
                'job_id': item.job_card_id,
                'part': item.spare_part_name or '—',
                'paid': str(pay_now),
            }]),
        )

    messages.success(request, f"₹{pay_now:,.0f} paid for '{item.spare_part_name or 'item'}' to {shop.name}.")
    return redirect('spare_shop_detail', pk=shop_pk)


@owner_required
def spare_shop_payment_reverse(request, shop_pk, payment_pk):
    """
    POST: Reverse a SpareShopPayment and subtract amounts from affected items.
    Uses the stored JSON snapshot to roll back precisely. Owner only.
    """
    from decimal import Decimal
    from django.db import transaction
    import json

    if request.method != 'POST':
        return redirect('spare_shop_detail', pk=shop_pk)

    shop = get_object_or_404(SpareShop, pk=shop_pk)
    payment = get_object_or_404(SpareShopPayment, pk=payment_pk, shop=shop, is_trashed=False)

    with transaction.atomic():
        try:
            details = json.loads(payment.details)
        except (json.JSONDecodeError, TypeError):
            details = []

        for entry in details:
            try:
                item = JobCardSpareItem.objects.select_for_update().get(pk=entry['item_id'])
                reversed_amount = Decimal(str(entry['paid']))
                item.shop_paid_amount = max(Decimal('0'), item.shop_paid_amount - reversed_amount)
                item.save(update_fields=['shop_paid_amount'])
            except (JobCardSpareItem.DoesNotExist, KeyError):
                continue

        payment.is_trashed = True
        payment.save()

    messages.success(request, f"Payment of ₹{payment.amount:,.0f} reversed and moved to Trash.")
    return redirect('spare_shop_detail', pk=shop_pk)


@owner_required
def spare_shop_delete(request, pk):
    """POST: Soft-delete a spare shop (move to trash). Owner only."""
    if request.method == 'POST':
        shop = get_object_or_404(SpareShop, pk=pk, is_trashed=False)
        shop.is_trashed = True
        shop.save()
        messages.success(request, f"Shop '{shop.name}' moved to trash.")
    return redirect('spare_shop_list')


@owner_required
def spare_shop_restore(request, pk):
    """POST: Restore a trashed spare shop. Owner only."""
    if request.method == 'POST':
        shop = get_object_or_404(SpareShop, pk=pk, is_trashed=True)
        shop.is_trashed = False
        shop.save()
        messages.success(request, f"Shop '{shop.name}' restored.")
    return redirect('/trash/?tab=spare_shops')


@owner_required
def spare_shop_permanent_delete(request, pk):
    """POST: Permanently delete a trashed spare shop. Owner only."""
    if request.method == 'POST':
        shop = get_object_or_404(SpareShop, pk=pk, is_trashed=True)
        name = shop.name
        shop.delete()
        messages.success(request, f"Shop '{name}' permanently deleted.")
    return redirect('/trash/?tab=spare_shops')


@owner_required
def spare_shop_payment_permanent_delete(request, payment_pk):
    """POST: Permanently delete a trashed shop payment record. Owner only."""
    if request.method == 'POST':
        payment = get_object_or_404(SpareShopPayment, pk=payment_pk, is_trashed=True)
        amount = payment.amount
        payment.delete()
        messages.success(request, f"Shop payment of ₹{amount:,.0f} permanently deleted.")
    return redirect('/trash/?tab=shop_payments')


@office_required
def pending_payments_list(request):

    """
    Shows a list of job cards that are not fully paid.
    Highly optimized for 10M+ records using SQL Subqueries & Annotations.
    """
    from django.db.models import Sum, Q, Value, F, OuterRef, Subquery
    from django.db.models.functions import Coalesce
    
    # 1. Base Query with Filtering by Payment Status (Indexed)
    pending_jobs = JobCard.objects.filter(
        payment_status__in=['PENDING', 'PARTIAL']
    )

    # 2. AJAX Search (Smart Reset: Clear on full refresh)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip() if is_ajax else ''
    if q:
        for word in q.split():
            pending_jobs = pending_jobs.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )

    # 3. SQL Annotations (The Scale Optimizer)
    # Using Subqueries to prevent Cartesian Product/Double Counting when summing 
    # over multiple related tables (Spares & Labours).
    
    from django.db.models import Sum, Q, Value, F, OuterRef, Subquery, DecimalField, ExpressionWrapper
    from django.db.models.functions import Coalesce
    from decimal import Decimal
    
    spares_subquery = JobCardSpareItem.objects.filter(
        job_card=OuterRef('pk')
    ).values('job_card').annotate(
        total=Sum('total_price')
    ).values('total')

    labours_subquery = JobCardLabourItem.objects.filter(
        job_card=OuterRef('pk')
    ).values('job_card').annotate(
        total=Sum('amount')
    ).values('total')

    pending_jobs = pending_jobs.annotate(
        annotated_spares=Coalesce(Subquery(spares_subquery), Value(Decimal('0.0'), output_field=DecimalField())),
        annotated_labour=Coalesce(Subquery(labours_subquery), Value(Decimal('0.0'), output_field=DecimalField()))
    ).annotate(
        total_bill=ExpressionWrapper(
            F('annotated_spares') + F('annotated_labour'),
            output_field=DecimalField()
        )
    ).annotate(
        balance_amount=ExpressionWrapper(
            F('total_bill') - F('received_amount'),
            output_field=DecimalField()
        )
    ).order_by('-admitted_date')

    # 4. Global Grand Total
    total_outstanding = pending_jobs.aggregate(
        total=Sum(F('balance_amount'), output_field=DecimalField())
    )['total'] or 0

    # 5. Pagination (21 items per page)
    paginator = Paginator(pending_jobs, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'pending_jobs': page_obj,
        'total_outstanding': total_outstanding,
        'q': q,
        'page_obj': page_obj,
    }

    # 6. AJAX Return Partial
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/jobcard/pending_payments_partial.html', context)

    return render(request, 'workshop/jobcard/pending_payments.html', context)


# ============================================================================
# CAR PROFILES
# ============================================================================

from django.db.models import Count, Max

@office_required
def car_profile_list(request):
    """Show all unique cars (grouped by registration) with optimized queries and AJAX search."""
    # 1. Base Query: Group by registration and get latest activity
    cars_query = JobCard.objects.values('registration_number').annotate(
        total_visits=Count('id'),
        latest_date=Max('admitted_date'),
        latest_id=Max('id')
    ).order_by('-latest_date')

    # 2. Get Filters (Smart Reset: Clear on full refresh)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip() if is_ajax else ''

    # 3. Apply Multi-Field Search (Database Level)
    if q:
        for word in q.split():
            cars_query = cars_query.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )

    # 4. Pagination (Pro-Active Scaling)
    paginator = Paginator(cars_query, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 5. Fetch Full Details for the current page only (N+1 Resolution)
    # We get the full JobCard objects for the latest_ids on this page
    latest_ids = [car['latest_id'] for car in page_obj]
    
    # Materialize the data into a list of dicts for the template
    # (Using a dict for fast lookup)
    details_map = {
        jc.id: jc for jc in JobCard.objects.filter(id__in=latest_ids)
    }
    
    car_profiles = []
    for car in page_obj:
        jc = details_map.get(car['latest_id'])
        if jc:
            car_profiles.append({
                'registration': car['registration_number'],
                'brand': jc.brand_name,
                'model': jc.model_name,
                'customer': jc.customer_name,
                'total_visits': car['total_visits'],
                'latest_date': car['latest_date'],
                'color_hex': jc.get_car_color_hex,
                'color_name': jc.get_car_color_display,
            })

    # Get unique brands (no longer used for filter, but keeping for other UI if needed - actually better to remove)
    # all_brands = JobCard.objects.values_list('brand_name', flat=True).distinct().order_by('brand_name')

    context = {
        'car_profiles': car_profiles,
        'page_obj': page_obj,
        'q': q,
    }

    # AJAX Search: Return only the partial template
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/car_profiles/car_list_partial.html', context)
    
    return render(request, 'workshop/car_profiles/car_profile_list.html', context)


@office_required
def car_profile_detail(request, registration):
    """Show all bills for a specific car"""
    
    # Get all job cards for this registration
    bills = JobCard.objects.filter(
        registration_number=registration
    ).order_by('-admitted_date')
    
    if not bills.exists():
        raise Http404("Car not found")
    
    # Get car info from latest job card
    latest = bills.first()
    car_info = {
        'registration': registration,
        'brand': latest.brand_name,
        'model': latest.model_name,
        'customer': latest.customer_name,
    }
    
    # Materialize and attach chronological visit numbers (1 = oldest)
    bills_list = list(bills)
    total_visits = len(bills_list)
    for i, bill in enumerate(bills_list):
        bill.visit_number = total_visits - i
    
    return render(request, 'workshop/car_profiles/car_profile_detail.html', {
        'car_info': car_info,
        'bills': bills_list,
    })



