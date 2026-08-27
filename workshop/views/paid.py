from datetime import date, timedelta

from django.shortcuts import render
from django.utils import timezone
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import office_required, is_owner


@office_required
def paid_bills_list(request):
    """
    Shows all fully paid job cards (PAID + BULK_PAID).

    Owner: the full calendar-aligned filter vocabulary.

    Office: the last 7 days. Office settles bills, so it needs to look one up
    and check what was taken for it — which is a few days' worth, not the
    year's. The window is enforced HERE and not by hiding the dropdown:
    `?filter=all` is one URL edit away, so the template only decides whether to
    render a control the view already refuses to honour. The bills inside the
    window are shown in full, per-card amounts included.

    There is no grand total any more, for either role — see below.
    """
    user_is_owner = is_owner(request.user)
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'

    # 1. Base query: fully paid job cards only
    paid_jobs = JobCard.objects.filter(
        payment_status__in=['PAID', 'BULK_PAID'],
        is_deleted=False,
    ).order_by('-paid_date', '-admitted_date')

    # 2. Read filter from URL always — non-AJAX and AJAX both respect the same param
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip()

    if not user_is_owner:
        # Office accounts are strictly locked to the last 7 days. Applied once:
        # a second identical filter further down bought nothing and read as
        # though one of the two were doing something the other was not.
        filter_type = 'last_7_days'
        paid_jobs = paid_jobs.filter(paid_date__date__gte=today - timedelta(days=7))
    else:
        filter_type = request.GET.get('filter', 'today')

        # 4. Owner Calendar-aligned date filters
        if filter_type == 'today':
            paid_jobs = paid_jobs.filter(paid_date__date=today)

        elif filter_type == 'this_week':
            # Monday of the current calendar week
            start = today - timedelta(days=today.weekday())
            paid_jobs = paid_jobs.filter(paid_date__date__gte=start)

        elif filter_type == 'this_month':
            start = today.replace(day=1)
            paid_jobs = paid_jobs.filter(paid_date__date__gte=start)

        elif filter_type == 'this_year':
            start = today.replace(month=1, day=1)
            paid_jobs = paid_jobs.filter(paid_date__date__gte=start)

        elif filter_type == 'last_week':
            # Previous full calendar week: Mon to Sun
            start = today - timedelta(days=today.weekday() + 7)
            end   = start + timedelta(days=6)
            paid_jobs = paid_jobs.filter(paid_date__date__gte=start, paid_date__date__lte=end)

        elif filter_type == 'last_month':
            first_of_this_month = today.replace(day=1)
            last_of_last_month  = first_of_this_month - timedelta(days=1)
            first_of_last_month = last_of_last_month.replace(day=1)
            paid_jobs = paid_jobs.filter(
                paid_date__date__gte=first_of_last_month,
                paid_date__date__lte=last_of_last_month,
            )

        elif filter_type == 'last_year':
            start = today.replace(year=today.year - 1, month=1,  day=1)
            end   = today.replace(year=today.year - 1, month=12, day=31)
            paid_jobs = paid_jobs.filter(paid_date__date__gte=start, paid_date__date__lte=end)

        elif filter_type == 'custom':
            start_date = request.GET.get('start_date', '')
            end_date   = request.GET.get('end_date', '')
            # Parsed rather than handed to the ORM as text: an unparseable
            # string reaches `DateTimeField.get_prep_value` and raises, i.e. a
            # 500 from a hand-edited URL. Same shape as
            # `cashbook_views._apply_date_filter`.
            if start_date and end_date:
                try:
                    paid_jobs = paid_jobs.filter(
                        paid_date__date__gte=date.fromisoformat(start_date),
                        paid_date__date__lte=date.fromisoformat(end_date),
                    )
                except ValueError:
                    pass
        # filter_type == 'all' → no date filter applied

    # 3. AJAX Search
    if q:
        for word in q.split():
            paid_jobs = paid_jobs.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word) |
                Q(bill_number__icontains=word)
            )

    # 5. THE GRAND TOTAL IS GONE, and Cash Tracking is why.
    #
    # It summed `received_amount` over cards that reached fully-settled status
    # in the window — exact for a walk-in, who has one payment event at pickup,
    # and wrong for a fleet three ways at once: a card closed this month
    # carried its WHOLE cumulative receipt including instalments collected in
    # earlier months, a PARTIAL card holding real cash appeared nowhere, and
    # banked advance credit appeared nowhere. A 1,20,000 fleet payment could
    # report here as 20,000.
    #
    # The question it was reaching for is answered properly by Cash Tracking on
    # the Profit page, which reads fleet money from `BulkPaymentHistory` — one
    # row per payment, dated by the day the money moved — rather than from the
    # cards. Removed rather than relabelled: a page earns a new figure by
    # dropping one.
    #
    # The COUNT stays for both roles. This is a lookup list of settled bills,
    # and how many there are is a fact about the list itself, not a business
    # figure. Office keeps its 7-day window above.
    total_count = paid_jobs.count()

    # 6. Pagination (45 per page)
    paginator = Paginator(paid_jobs, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Custom range values (for label display on initial load)
    custom_start = request.GET.get('start_date', '') if (user_is_owner and filter_type == 'custom') else ''
    custom_end   = request.GET.get('end_date',   '') if (user_is_owner and filter_type == 'custom') else ''

    context = {
        'paid_jobs':       page_obj,
        'total_count':     total_count,
        'q':               q,
        'filter_type':     filter_type,
        'start_date':      custom_start,
        'end_date':        custom_end,
        'page_obj':        page_obj,
        'is_owner_user':   user_is_owner,
    }

    # 7. AJAX return partial only
    if is_ajax:
        return render(request, 'workshop/jobcard/paid_bills_partial.html', context)

    return render(request, 'workshop/jobcard/paid_bills.html', context)
