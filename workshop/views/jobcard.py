from datetime import date

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import (
    CarBrand, CarModel, SparePart, ConcernSolution,
    JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem,
    SpareShop,
)
from ..forms import (
    JobCardForm, JobCardConcernFormSet, JobCardSpareFormSet, JobCardLabourFormSet
)
from ..decorators import staff_required, office_required


@staff_required
def jobcard_create(request):
    """
    Create a new job card with formsets for concerns, spares, and labour.
    Admitted date defaults to today but is editable.
    Redirects to edit page after save with success message.
    Prevents duplicate job cards with 3-attempt confirmation.
    """
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


@office_required
def jobcard_list(request):
    """
    SECTION 2: JOBS - List of active saved job cards.
    """
    jobcard_list_query = JobCard.objects.filter(is_deleted=False).select_related('lead_mechanic').prefetch_related('spares', 'labours').order_by('-updated_at', '-pk')
    
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
        messages.warning(request, f"Job Card {jobcard.registration_number} moved to Trash.")
        return redirect('jobcard_list')
    return render(request, 'workshop/jobcard/jobcard_confirm_delete.html', {'jobcard': jobcard})
