from decimal import Decimal

from django.shortcuts import render
from django.db.models import (
    Sum, Q, Value, F, OuterRef, Subquery,
    DecimalField, ExpressionWrapper,
)
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator

from ..models import JobCard, JobCardSpareItem, JobCardLabourItem
from ..decorators import office_required


@office_required
def pending_payments_list(request):
    """
    Shows a list of job cards that are not fully paid.
    Highly optimized for 10M+ records using SQL Subqueries & Annotations.
    """
    # 1. Base Query with Filtering by Payment Status (Indexed)
    # Hide any jobs that are assigned to a bulk payer group
    pending_jobs = JobCard.objects.filter(
        payment_status__in=['PENDING', 'PARTIAL']
    ).exclude(bulk_payer__isnull=False)

    # 2. AJAX Search (Smart Reset: Clear on full refresh)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip() if is_ajax else ''
    if q:
        for word in q.split():
            pending_jobs = pending_jobs.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )

    pending_jobs = pending_jobs.annotate(
        balance_amount=ExpressionWrapper(
            F('total_bill_amount') - F('received_amount'),
            output_field=DecimalField()
        )
    ).order_by('-admitted_date')

    # 4. Global Grand Total
    total_outstanding = pending_jobs.aggregate(
        total=Sum(F('balance_amount'), output_field=DecimalField())
    )['total'] or 0

    # 5. Pagination (21 items per page)
    paginator = Paginator(pending_jobs, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # 5.5 Fetch active bulk payers for the "Move to Bulk Bill" modal
    from ..models import BulkPayer
    active_bulk_payers = BulkPayer.objects.filter(is_trashed=False).order_by('customer_name')

    context = {
        'pending_jobs': page_obj,
        'total_outstanding': total_outstanding,
        'q': q,
        'page_obj': page_obj,
        'active_bulk_payers': active_bulk_payers,
    }

    # 6. AJAX Return Partial
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/jobcard/pending_payments_partial.html', context)

    return render(request, 'workshop/jobcard/pending_payments.html', context)
