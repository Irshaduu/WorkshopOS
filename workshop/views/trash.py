from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import (
    JobCard, BulkPayer, BulkPaymentHistory,
    SpareShop, SpareShopPayment,
)
from ..decorators import owner_required


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
        messages.success(request, f"Successfully permanently deleted {reg}.")
    return redirect('/trash/?tab=jobcards')
