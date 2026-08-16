from datetime import date, timedelta

from django.shortcuts import render
from django.utils import timezone
from django.db.models import (
    Sum, Q, Value, F,
    DecimalField,
)
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import office_required, is_owner


@office_required
def paid_bills_list(request):
    """
    Shows all fully paid job cards (PAID + BULK_PAID).

    Owner: the full calendar-aligned filter vocabulary, plus the Total Collected
    grand total.

    Office: the last 7 days, and no grand total. Office settles bills, so it
    needs to look one up and check what was taken for it — which is a few days'
    worth, not the year's. The window is enforced HERE and not by hiding the
    dropdown: `?filter=all` is one URL edit away, so the template only decides
    whether to render a control the view already refuses to honour. The bills
    inside the window are shown in full, per-card amounts included; what is
    withheld is the aggregate, which is a business figure rather than a
    settlement one.
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

    # 5. Grand total collected (Owner only — completely hidden for Office)
    if user_is_owner:
        total_collected = paid_jobs.aggregate(
            total=Sum('received_amount', output_field=DecimalField())
        )['total'] or 0
    else:
        total_collected = None

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
        'total_collected': total_collected,
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
