from datetime import date, timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import office_required, staff_required


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
        # NEWEST COMPLETED FIRST, AND `-id` IS THE TIEBREAKER RATHER THAN
        # DECORATION. `completed_date` is a DateField, so every car handed over
        # today carries the SAME value and the order within that day is
        # whatever the database happens to return — which on the default
        # 'today' filter is the whole list. The car just completed could land
        # anywhere in it, so the one somebody opened this page to see was found
        # by scrolling.
        #
        # NOT `-updated_at`: it is `auto_now=True` and moves on ANY save, so an
        # old card edited for an unrelated reason would jump to the top of
        # today. That is the exact defect `paid_date` exists to keep off Paid
        # Bills. `-id` never moves after the card is created.
        .order_by('-completed_date', '-id')
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
        # Parsed, not handed to the ORM as text — an unparseable string raises
        # in `get_prep_value`, i.e. a 500 from a hand-edited URL.
        if start_date and end_date:
            try:
                completed_jobcards = completed_jobcards.filter(
                    completed_date__gte=date.fromisoformat(start_date),
                    completed_date__lte=date.fromisoformat(end_date),
                )
            except ValueError:
                pass
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


@staff_required
def mark_completed(request, pk):
    """
    Mark job card as completed.
    Auto-sets completed_date to today (actual completion date).

    Floor as well as Office, on the owner's instruction (2026-08-16). The
    mechanic is who knows the car is finished, and the two buttons on the Floor
    board — this and `toggle_hold` — were rendered for them all along while both
    views were `@office_required`, so pressing either gave a mechanic a 403 on
    the one screen they use all day. Widening the view is the half that makes
    the buttons work; the template gate already allowed them.

    It moves no money and it is not a delete: the card leaves the board and the
    Completed list can put it back. `undo_completed` stays Office/Owner because
    it can resurrect a card onto the floor and has to answer the one-active-card
    rule when it does.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)
        # One implementation, shared with "Complete & settle" on the invoice —
        # see JobCard.mark_completed for why the date must not be re-stamped.
        jobcard.mark_completed()
        # This said NOTHING before — the card simply vanished off the Floor
        # board and the page reloaded, which on a tablet is indistinguishable
        # from a mis-tap that did nothing. Every other action in the app
        # reports itself; these three did not. It is also what earns the
        # confirmation sound, since those are driven off the message tag.
        messages.success(request, f"{jobcard.registration_number} marked completed.")
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
        messages.success(
            request,
            f"{jobcard.registration_number} moved back to the workshop floor."
        )
    return redirect('completed_list')


@staff_required
def toggle_hold(request, pk):
    """
    Toggle the on_hold status of a job card.
    Used when waiting for parts or other delays.

    Floor as well as Office — see `mark_completed` for why. Waiting on a part
    is something the mechanic discovers first, and a hold is fully reversible
    by the same button.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)
        jobcard.on_hold = not jobcard.on_hold
        jobcard.save()
        messages.success(
            request,
            f"{jobcard.registration_number} put on hold."
            if jobcard.on_hold else
            f"{jobcard.registration_number} taken off hold."
        )
    return redirect('home')
