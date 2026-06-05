from datetime import date

from django.shortcuts import render
from django.db.models import Count, Q
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import staff_required


@staff_required
def home(request):
    """
    Dashboard homepage showing all active job cards.
    Discharge date is a planning field, not a filter.
    Cars only move to Delivered when "Delivered" button is clicked.
    """
    # Get only non-delivered job cards (where delivered=False)
    # Optimized with select_related and prefetch_related for 1M+ records
    active_jobcards = JobCard.objects.filter(delivered=False, is_deleted=False).select_related('lead_mechanic').prefetch_related('concerns', 'spares', 'labours').annotate(
        total_concerns=Count('concerns'),
        fixed_concerns=Count('concerns', filter=Q(concerns__status='FIXED'))
    ).order_by('-updated_at', '-pk')
    
    # Count delivered today (Active only)
    delivered_count = JobCard.objects.filter(
        delivered=True,
        is_deleted=False,
        discharged_date=date.today()
    ).count()

    # Count pending bills (Delivered but not fully paid, Active only)
    pending_bills_count = JobCard.objects.filter(
        is_deleted=False,
        payment_status__in=['PENDING', 'PARTIAL']
    ).count()
    
    # 5. Pagination for Floor (45 items per page)
    paginator = Paginator(active_jobcards, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'workshop/dashboard/dashboard_home.html', {
        'active_jobcards': page_obj, # Pass page_obj as active_jobcards
        'delivered_count': delivered_count,
        'pending_bills_count': pending_bills_count,
        'page_obj': page_obj,
        'today': date.today(),
    })


@staff_required
def live_report(request):
    """
    SECTION 2.1: LIVE REPORT - Quick scroll for all roles.
    Shows active jobs, concerns, and spares status.
    """
    # Search and Filter support (Titan Exhaustive)
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    
    active_jobs = JobCard.objects.filter(is_deleted=False, delivered=False).select_related('lead_mechanic').prefetch_related('concerns', 'spares').annotate(
        total_concerns=Count('concerns'),
        fixed_concerns=Count('concerns', filter=Q(concerns__status='FIXED'))
    )

    if q:
        for word in q.split():
            active_jobs = active_jobs.filter(
                Q(registration_number__icontains=word) |
                Q(bill_number__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )
            
    if status == 'PAID':
        active_jobs = active_jobs.filter(payment_status='PAID')
    elif status == 'PENDING':
        active_jobs = active_jobs.filter(payment_status='PENDING')

    active_jobs = active_jobs.order_by('-updated_at')
    
    # Pagination (prevents performance degradation at scale)
    paginator = Paginator(active_jobs, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'workshop/jobcard/live_report.html', {
        'active_jobs': page_obj,
        'page_obj': page_obj,
        'q': q,
        'status_filter': status,
    })
