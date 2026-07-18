from datetime import date, timedelta

from django.shortcuts import render
from django.utils import timezone
from django.db.models import (
    Sum, Q, Value, F,
    DecimalField,
)
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import owner_required


@owner_required
def paid_bills_list(request):
    """
    Shows all fully paid job cards (PAID + BULK_PAID).
    Calendar-aligned date filters + AJAX search.
    """
    # 1. Base query: fully paid job cards only
    paid_jobs = JobCard.objects.filter(
        payment_status__in=['PAID', 'BULK_PAID'],
        is_deleted=False,
    ).order_by('-updated_at', '-admitted_date')

    # 2. Read filter from URL always — non-AJAX and AJAX both respect the same param
    #    Default: 'today'. URL pushState already keeps ?filter= in sync after JS changes.
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    filter_type = request.GET.get('filter', 'today')
    q = request.GET.get('q', '').strip()

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

    # 4. Calendar-aligned date filters
    today = timezone.localdate()  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'

    if filter_type == 'today':
        paid_jobs = paid_jobs.filter(updated_at__date=today)

    elif filter_type == 'this_week':
        # Monday of the current calendar week
        start = today - timedelta(days=today.weekday())
        paid_jobs = paid_jobs.filter(updated_at__date__gte=start)

    elif filter_type == 'this_month':
        start = today.replace(day=1)
        paid_jobs = paid_jobs.filter(updated_at__date__gte=start)

    elif filter_type == 'this_year':
        start = today.replace(month=1, day=1)
        paid_jobs = paid_jobs.filter(updated_at__date__gte=start)

    elif filter_type == 'last_week':
        # Previous full calendar week: Mon to Sun
        start = today - timedelta(days=today.weekday() + 7)
        end   = start + timedelta(days=6)
        paid_jobs = paid_jobs.filter(updated_at__date__gte=start, updated_at__date__lte=end)

    elif filter_type == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_of_last_month  = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_of_last_month.replace(day=1)
        paid_jobs = paid_jobs.filter(
            updated_at__date__gte=first_of_last_month,
            updated_at__date__lte=last_of_last_month,
        )

    elif filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1,  day=1)
        end   = today.replace(year=today.year - 1, month=12, day=31)
        paid_jobs = paid_jobs.filter(updated_at__date__gte=start, updated_at__date__lte=end)

    elif filter_type == 'custom':
        start_date = request.GET.get('start_date', '')
        end_date   = request.GET.get('end_date', '')
        if start_date and end_date:
            paid_jobs = paid_jobs.filter(
                updated_at__date__gte=start_date,
                updated_at__date__lte=end_date,
            )
    # filter_type == 'all' → no date filter applied

    # 5. Grand total collected (respects all active filters)
    total_collected = paid_jobs.aggregate(
        total=Sum('received_amount', output_field=DecimalField())
    )['total'] or 0

    total_count = paid_jobs.count()

    # 6. Pagination (45 per page)
    paginator = Paginator(paid_jobs, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Custom range values (for label display on initial load)
    custom_start = request.GET.get('start_date', '') if filter_type == 'custom' else ''
    custom_end   = request.GET.get('end_date',   '') if filter_type == 'custom' else ''

    context = {
        'paid_jobs':       page_obj,
        'total_collected': total_collected,
        'total_count':     total_count,
        'q':               q,
        'filter_type':     filter_type,
        'start_date':      custom_start,
        'end_date':        custom_end,
        'page_obj':        page_obj,
    }

    # 7. AJAX return partial only
    if is_ajax:
        return render(request, 'workshop/jobcard/paid_bills_partial.html', context)

    return render(request, 'workshop/jobcard/paid_bills.html', context)
