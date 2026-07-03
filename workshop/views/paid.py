from datetime import date, timedelta

from django.shortcuts import render
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
    Date range filter + AJAX search — identical architecture to delivered_list.
    """
    # 1. Base query: fully paid job cards only
    paid_jobs = JobCard.objects.filter(
        payment_status__in=['PAID', 'BULK_PAID'],
        is_deleted=False,
    ).order_by('-updated_at', '-admitted_date')

    # 2. Smart Reset: default to 'month' on full refresh
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    if not is_ajax:
        filter_type = 'month'
        q = ''
    else:
        filter_type = request.GET.get('filter', 'month')
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

    # 4. Date range filter (mirrors delivered.py exactly)
    start_date = ''
    end_date = ''
    today = date.today()

    if filter_type == 'today':
        paid_jobs = paid_jobs.filter(updated_at__date=today)
    elif filter_type == 'week':
        start_date = today - timedelta(days=7)
        paid_jobs = paid_jobs.filter(updated_at__date__gte=start_date)
    elif filter_type == 'month':
        start_date = today - timedelta(days=30)
        paid_jobs = paid_jobs.filter(updated_at__date__gte=start_date)
    elif filter_type == 'year':
        start_date = today - timedelta(days=365)
        paid_jobs = paid_jobs.filter(updated_at__date__gte=start_date)
    elif filter_type == 'custom':
        start_date = request.GET.get('start_date', '')
        end_date = request.GET.get('end_date', '')
        if start_date and end_date:
            paid_jobs = paid_jobs.filter(
                updated_at__date__gte=start_date,
                updated_at__date__lte=end_date,
            )
    # filter_type == 'all' → no date filter applied

    # 5. Grand total collected (after filters)
    total_collected = paid_jobs.aggregate(
        total=Sum('received_amount', output_field=DecimalField())
    )['total'] or 0

    total_count = paid_jobs.count()

    # 6. Pagination (45 per page)
    paginator = Paginator(paid_jobs, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'paid_jobs': page_obj,
        'total_collected': total_collected,
        'total_count': total_count,
        'q': q,
        'filter_type': filter_type,
        'start_date': start_date if filter_type == 'custom' else '',
        'end_date':   end_date   if filter_type == 'custom' else '',
        'page_obj': page_obj,
    }

    # 7. AJAX return partial
    if is_ajax:
        return render(request, 'workshop/jobcard/paid_bills_partial.html', context)

    return render(request, 'workshop/jobcard/paid_bills.html', context)
