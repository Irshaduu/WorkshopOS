from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import office_required


@office_required
def delivered_list(request):
    """
    Show delivered vehicles with calendar-aligned date filters and AJAX search.
    """
    # 1. Base Query (Active only)
    delivered_jobcards = (
        JobCard.objects
        .filter(delivered=True, is_deleted=False)
        .select_related('lead_mechanic')
        .prefetch_related('spares', 'labours')
        .order_by('-discharged_date')
    )

    # 2. Read filter from URL always — non-AJAX and AJAX both respect the same param
    #    Default: 'today'. URL pushState already keeps ?filter= in sync after JS changes.
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    filter_type = request.GET.get('filter', 'today')
    q = request.GET.get('q', '').strip()

    # 3. Apply Search Filters
    if q:
        for word in q.split():
            delivered_jobcards = delivered_jobcards.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )

    # 4. Calendar-aligned date filters (discharged_date is a DateField)
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'

    if filter_type == 'today':
        delivered_jobcards = delivered_jobcards.filter(discharged_date=today)

    elif filter_type == 'this_week':
        start = today - timedelta(days=today.weekday())  # Monday of current week
        delivered_jobcards = delivered_jobcards.filter(discharged_date__gte=start)

    elif filter_type == 'this_month':
        start = today.replace(day=1)
        delivered_jobcards = delivered_jobcards.filter(discharged_date__gte=start)

    elif filter_type == 'this_year':
        start = today.replace(month=1, day=1)
        delivered_jobcards = delivered_jobcards.filter(discharged_date__gte=start)

    elif filter_type == 'last_week':
        start = today - timedelta(days=today.weekday() + 7)  # Previous Mon
        end   = start + timedelta(days=6)                     # Previous Sun
        delivered_jobcards = delivered_jobcards.filter(
            discharged_date__gte=start, discharged_date__lte=end
        )

    elif filter_type == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_of_last_month  = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_of_last_month.replace(day=1)
        delivered_jobcards = delivered_jobcards.filter(
            discharged_date__gte=first_of_last_month,
            discharged_date__lte=last_of_last_month,
        )

    elif filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1,  day=1)
        end   = today.replace(year=today.year - 1, month=12, day=31)
        delivered_jobcards = delivered_jobcards.filter(
            discharged_date__gte=start, discharged_date__lte=end
        )

    elif filter_type == 'custom':
        start_date = request.GET.get('start_date', '')
        end_date   = request.GET.get('end_date', '')
        if start_date and end_date:
            delivered_jobcards = delivered_jobcards.filter(
                discharged_date__gte=start_date,
                discharged_date__lte=end_date,
            )
    # filter_type == 'all' → no date filter applied

    # 5. Pagination
    paginator = Paginator(delivered_jobcards, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Custom range values (for label on initial load)
    custom_start = request.GET.get('start_date', '') if filter_type == 'custom' else ''
    custom_end   = request.GET.get('end_date',   '') if filter_type == 'custom' else ''

    context = {
        'delivered_jobcards': page_obj,
        'page_obj':           page_obj,
        'filter_type':        filter_type,
        'q':                  q,
        'start_date':         custom_start,
        'end_date':           custom_end,
    }

    # 6. AJAX return partial only
    if is_ajax:
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
        jobcard.discharged_date = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
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
