from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import office_required


@office_required
def completed_list(request):
    """
    Show completed vehicles with calendar-aligned date filters and AJAX search.
    """
    # 1. Base Query (Active only)
    completed_jobcards = (
        JobCard.objects
        .filter(completed=True, is_deleted=False)
        .select_related('lead_mechanic')
        .prefetch_related('spares', 'labours')
        .order_by('-completed_date')
    )

    # 2. Read filter from URL always — non-AJAX and AJAX both respect the same param
    #    Default: 'today'. URL pushState already keeps ?filter= in sync after JS changes.
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    filter_type = request.GET.get('filter', 'today')
    q = request.GET.get('q', '').strip()

    # 3. Apply Search Filters
    if q:
        for word in q.split():
            completed_jobcards = completed_jobcards.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )

    # 4. Calendar-aligned date filters (completed_date is a DateField)
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'

    if filter_type == 'today':
        completed_jobcards = completed_jobcards.filter(completed_date=today)

    elif filter_type == 'this_week':
        start = today - timedelta(days=today.weekday())  # Monday of current week
        completed_jobcards = completed_jobcards.filter(completed_date__gte=start)

    elif filter_type == 'this_month':
        start = today.replace(day=1)
        completed_jobcards = completed_jobcards.filter(completed_date__gte=start)

    elif filter_type == 'this_year':
        start = today.replace(month=1, day=1)
        completed_jobcards = completed_jobcards.filter(completed_date__gte=start)

    elif filter_type == 'last_week':
        start = today - timedelta(days=today.weekday() + 7)  # Previous Mon
        end   = start + timedelta(days=6)                     # Previous Sun
        completed_jobcards = completed_jobcards.filter(
            completed_date__gte=start, completed_date__lte=end
        )

    elif filter_type == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_of_last_month  = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_of_last_month.replace(day=1)
        completed_jobcards = completed_jobcards.filter(
            completed_date__gte=first_of_last_month,
            completed_date__lte=last_of_last_month,
        )

    elif filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1,  day=1)
        end   = today.replace(year=today.year - 1, month=12, day=31)
        completed_jobcards = completed_jobcards.filter(
            completed_date__gte=start, completed_date__lte=end
        )

    elif filter_type == 'custom':
        start_date = request.GET.get('start_date', '')
        end_date   = request.GET.get('end_date', '')
        if start_date and end_date:
            completed_jobcards = completed_jobcards.filter(
                completed_date__gte=start_date,
                completed_date__lte=end_date,
            )
    # filter_type == 'all' → no date filter applied

    # 5. Pagination
    paginator = Paginator(completed_jobcards, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Custom range values (for label on initial load)
    custom_start = request.GET.get('start_date', '') if filter_type == 'custom' else ''
    custom_end   = request.GET.get('end_date',   '') if filter_type == 'custom' else ''

    context = {
        'completed_jobcards': page_obj,
        'page_obj':           page_obj,
        'filter_type':        filter_type,
        'q':                  q,
        'start_date':         custom_start,
        'end_date':           custom_end,
    }

    # 6. AJAX return partial only
    if is_ajax:
        return render(request, 'workshop/completed/completed_list_partial.html', context)

    return render(request, 'workshop/completed/completed_list.html', context)


@office_required
def mark_completed(request, pk):
    """
    Mark job card as completed.
    Auto-sets completed_date to today (actual completion date).
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)
        jobcard.completed = True
        jobcard.completed_date = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
        jobcard.save()
    return redirect('home')


@office_required
def undo_completed(request, pk):
    """
    Undo completion by setting completed=False and clearing completed_date.

    Hard-blocked if a different job card is already active for this vehicle's
    registration number — undoing would otherwise put two active job cards on
    the floor for the same car at once.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)

        existing_job = JobCard.get_active_conflict(jobcard.registration_number, exclude_pk=jobcard.pk)
        if existing_job:
            messages.error(
                request,
                f'Cannot undo completion for {jobcard.registration_number} — it already has a '
                f'different active job card (not yet Completed). Resolve that one first.'
            )
            return redirect('completed_list')

        jobcard.completed = False
        jobcard.completed_date = None
        jobcard.save()
    return redirect('completed_list')


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
