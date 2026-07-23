from django.utils import timezone

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
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

            # Hard block: only one active (not delivered, not trashed) job card
            # is allowed per registration number at a time. No bypass — the old
            # "3-attempt confirmation" let staff push through anyway, which is
            # exactly how duplicate active job cards for the same car happened.
            registration = jobcard.registration_number.strip().upper()
            existing_job = JobCard.get_active_conflict(registration)

            if existing_job:
                vehicle_info = f"{existing_job.brand_name} {existing_job.model_name}" if existing_job.brand_name else registration
                messages.error(
                    request,
                    f'{vehicle_info} ({registration}) already has an active job card '
                    f'(not yet Delivered). Deliver or trash that job card before creating a new one.'
                )

                concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
                spare_formset = JobCardSpareFormSet(request.POST, prefix='spares')
                labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')

                return render(request, 'workshop/jobcard/jobcard_form.html', {
                    'form': form,
                    'concern_formset': concern_formset,
                    'spare_formset': spare_formset,
                    'labour_formset': labour_formset,
                    'is_edit': False,
                })

            # Formsets initialization for standard save
            concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
            spare_formset = JobCardSpareFormSet(request.POST, prefix='spares')
            labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')

            if concern_formset.is_valid() and spare_formset.is_valid() and labour_formset.is_valid():
                # AUD-0014: Wrap all formset saves in a single atomic transaction.
                # Without this, a partial failure (e.g. a spare save fails after the
                # JobCard itself is committed) would leave an orphaned record.
                with transaction.atomic():
                    jobcard.save()

                    # Associate instances with jobcard before saving
                    concern_formset.instance = jobcard
                    spare_formset.instance = jobcard
                    labour_formset.instance = jobcard

                    saved_concerns = concern_formset.save()
                    saved_spares = spare_formset.save()
                    labour_formset.save()
                    
                    # AUD-0052: Auto-learn — use case-insensitive lookup to prevent
                    # ghost duplicates like 'Brake Pad' vs 'brake pad'.
                    new_concern_texts = [c.concern_text.strip() for c in saved_concerns if c.concern_text and c.concern_text.strip()]
                    if new_concern_texts:
                        existing_concern_texts = set()
                        for t in new_concern_texts:
                            if ConcernSolution.objects.filter(concern__iexact=t).exists():
                                existing_concern_texts.add(t)
                        new_concerns = [ConcernSolution(concern=t) for t in new_concern_texts if t not in existing_concern_texts]
                        ConcernSolution.objects.bulk_create(new_concerns, ignore_conflicts=True)
                    
                    new_spare_names = [s.spare_part_name.strip() for s in saved_spares if s.spare_part_name and s.spare_part_name.strip()]
                    if new_spare_names:
                        existing_spare_names = set()
                        for n in new_spare_names:
                            if SparePart.objects.filter(name__iexact=n).exists():
                                existing_spare_names.add(n)
                        new_spare_parts = [SparePart(name=n) for n in new_spare_names if n not in existing_spare_names]
                        SparePart.objects.bulk_create(new_spare_parts, ignore_conflicts=True)

                    # AUD-0023: Resolve spare → shop FK using the posted PK, not free-text name.
                    # The template submits shop.pk as the option value, so we can do a direct
                    # ID-based lookup — no case-folding or name-parsing needed.
                    all_spares = list(jobcard.spares.all())

                    # Pre-build a PK→ShopObject map (single query for all shops)
                    shops_by_pk = {s.pk: s for s in SpareShop.objects.filter(is_trashed=False)}

                    shops_to_update = set()
                    for spare in all_spares:
                        # spare_formset.save() just saved the posted PK into spare.shop_name
                        raw_pk = spare.shop_name.strip() if spare.shop_name else ''
                        shop_obj = None
                        if raw_pk:
                            try:
                                shop_obj = shops_by_pk.get(int(raw_pk))
                            except (ValueError, TypeError):
                                shop_obj = None
                        # Set both the FK and the human-readable display name
                        shop_name_val = shop_obj.name if shop_obj else ''
                        JobCardSpareItem.objects.filter(pk=spare.pk).update(
                            shop=shop_obj,
                            shop_name=shop_name_val,
                        )
                        if shop_obj:
                            shops_to_update.add(shop_obj)

                    # Delete imported unassigned spares to prevent duplicates
                    imported_ids = request.POST.getlist('imported_unassigned_ids')
                    if imported_ids:
                        old_items = JobCardSpareItem.objects.filter(pk__in=imported_ids, job_card__isnull=True)
                        for old_item in old_items.select_related('shop'):
                            if old_item.shop:
                                shops_to_update.add(old_item.shop)
                        old_items.delete()

                    # Update totals for all affected shops
                    for shop in shops_to_update:
                        shop.update_totals()

                messages.success(request, f'Job card for {jobcard.registration_number} created successfully!')
                return redirect('jobcard_edit', pk=jobcard.pk)
        else:
            # If form is invalid, we still need to initialize formsets for the context
            concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
            spare_formset = JobCardSpareFormSet(request.POST, prefix='spares')
            labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')
    else:
        # Pre-fill admitted_date with today's date
        initial_data = {'admitted_date': timezone.localdate()}  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
        
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
        'unassigned_spares': JobCardSpareItem.objects.filter(job_card__isnull=True).select_related('shop').order_by('-ordered_date'),
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
            # Hard block: editing this job card's registration number must not collide
            # with a different job card that's already active for that vehicle. Excludes
            # this job card's own pk, so leaving the registration number unchanged never
            # conflicts with itself.
            registration = form.cleaned_data['registration_number'].strip().upper()
            existing_job = JobCard.get_active_conflict(registration, exclude_pk=jobcard.pk)

            if existing_job:
                vehicle_info = f"{existing_job.brand_name} {existing_job.model_name}" if existing_job.brand_name else registration
                messages.error(
                    request,
                    f'{vehicle_info} ({registration}) already has a different active job card '
                    f'(not yet Delivered). Deliver or trash that job card first.'
                )
                return render(request, 'workshop/jobcard/jobcard_form.html', {
                    'form': form,
                    'concern_formset': concern_formset,
                    'spare_formset': spare_formset,
                    'labour_formset': labour_formset,
                    'jobcard': jobcard,
                    'is_edit': True,
                    'next_url': request.GET.get('next'),
                    'spare_shops': SpareShop.objects.filter(is_trashed=False).order_by('name'),
                    'unassigned_spares': JobCardSpareItem.objects.filter(job_card__isnull=True).select_related('shop').order_by('-ordered_date'),
                })

            # AUD-0014: Wrap all formset saves in a single atomic transaction.
            with transaction.atomic():
                form.save()
                saved_concerns = concern_formset.save()
                saved_spares = spare_formset.save()
                labour_formset.save()
                
                # AUD-0052: Auto-learn — case-insensitive duplicate check.
                new_concern_texts = [c.concern_text.strip() for c in saved_concerns if c.concern_text and c.concern_text.strip()]
                if new_concern_texts:
                    existing_concern_texts = set()
                    for t in new_concern_texts:
                        if ConcernSolution.objects.filter(concern__iexact=t).exists():
                            existing_concern_texts.add(t)
                    new_concerns = [ConcernSolution(concern=t) for t in new_concern_texts if t not in existing_concern_texts]
                    ConcernSolution.objects.bulk_create(new_concerns, ignore_conflicts=True)
                
                new_spare_names = [s.spare_part_name.strip() for s in saved_spares if s.spare_part_name and s.spare_part_name.strip()]
                if new_spare_names:
                    existing_spare_names = set()
                    for n in new_spare_names:
                        if SparePart.objects.filter(name__iexact=n).exists():
                            existing_spare_names.add(n)
                    new_spare_parts = [SparePart(name=n) for n in new_spare_names if n not in existing_spare_names]
                    SparePart.objects.bulk_create(new_spare_parts, ignore_conflicts=True)

                # AUD-0023: Resolve spare → shop FK using the posted PK, not free-text name.
                all_spares = list(jobcard.spares.all())

                # Pre-build a PK→ShopObject map (single query for all shops)
                shops_by_pk = {s.pk: s for s in SpareShop.objects.filter(is_trashed=False)}

                shops_to_update = set()
                for spare in all_spares:
                    # spare_formset.save() just saved the posted PK into spare.shop_name
                    raw_pk = spare.shop_name.strip() if spare.shop_name else ''
                    shop_obj = None
                    if raw_pk:
                        try:
                            shop_obj = shops_by_pk.get(int(raw_pk))
                        except (ValueError, TypeError):
                            shop_obj = None
                    # Set both the FK and the human-readable display name
                    shop_name_val = shop_obj.name if shop_obj else ''
                    JobCardSpareItem.objects.filter(pk=spare.pk).update(
                        shop=shop_obj,
                        shop_name=shop_name_val,
                    )
                    if shop_obj:
                        shops_to_update.add(shop_obj)

                # Delete imported unassigned spares to prevent duplicates
                imported_ids = request.POST.getlist('imported_unassigned_ids')
                if imported_ids:
                    old_items = JobCardSpareItem.objects.filter(pk__in=imported_ids, job_card__isnull=True)
                    for old_item in old_items.select_related('shop'):
                        if old_item.shop:
                            shops_to_update.add(old_item.shop)
                    old_items.delete()

                # Update totals for all affected shops
                for shop in shops_to_update:
                    shop.update_totals()

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
        'unassigned_spares': JobCardSpareItem.objects.filter(job_card__isnull=True).select_related('shop').order_by('-ordered_date'),
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
