from datetime import date, timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import office_required


@office_required
def delivered_list(request):
    """
    Show delivered vehicles with date range filtering and AJAX search.
    """
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
    
    # 4. Apply Date Filters (initialize variables to prevent NameError)
    start_date = ''
    end_date = ''
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


@office_required  # Only office/owner can toggle hold as it affects planning
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
