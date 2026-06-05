from django.shortcuts import render
from django.http import Http404
from django.db.models import Count, Max, Q
from django.core.paginator import Paginator

from ..models import JobCard
from ..decorators import office_required


@office_required
def car_profile_list(request):
    """Show all unique cars (grouped by registration) with optimized queries and AJAX search."""
    # 1. Base Query: Group by registration and get latest activity
    cars_query = JobCard.objects.values('registration_number').annotate(
        total_visits=Count('id'),
        latest_date=Max('admitted_date'),
        latest_id=Max('id')
    ).order_by('-latest_date')

    # 2. Get Filters (Smart Reset: Clear on full refresh)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip() if is_ajax else ''

    # 3. Apply Multi-Field Search (Database Level)
    if q:
        for word in q.split():
            cars_query = cars_query.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )

    # 4. Pagination (Pro-Active Scaling)
    paginator = Paginator(cars_query, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 5. Fetch Full Details for the current page only (N+1 Resolution)
    # We get the full JobCard objects for the latest_ids on this page
    latest_ids = [car['latest_id'] for car in page_obj]
    
    # Materialize the data into a list of dicts for the template
    # (Using a dict for fast lookup)
    details_map = {
        jc.id: jc for jc in JobCard.objects.filter(id__in=latest_ids)
    }
    
    car_profiles = []
    for car in page_obj:
        jc = details_map.get(car['latest_id'])
        if jc:
            car_profiles.append({
                'registration': car['registration_number'],
                'brand': jc.brand_name,
                'model': jc.model_name,
                'customer': jc.customer_name,
                'total_visits': car['total_visits'],
                'latest_date': car['latest_date'],
                'color_hex': jc.get_car_color_hex,
                'color_name': jc.get_car_color_display,
            })

    context = {
        'car_profiles': car_profiles,
        'page_obj': page_obj,
        'q': q,
    }

    # AJAX Search: Return only the partial template
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/car_profiles/car_list_partial.html', context)
    
    return render(request, 'workshop/car_profiles/car_profile_list.html', context)


@office_required
def car_profile_detail(request, registration):
    """Show all bills for a specific car"""
    
    # Get all job cards for this registration
    bills = JobCard.objects.filter(
        registration_number=registration
    ).order_by('-admitted_date')
    
    if not bills.exists():
        raise Http404("Car not found")
    
    # Get car info from latest job card
    latest = bills.first()
    car_info = {
        'registration': registration,
        'brand': latest.brand_name,
        'model': latest.model_name,
        'customer': latest.customer_name,
    }
    
    # Materialize and attach chronological visit numbers (1 = oldest)
    bills_list = list(bills)
    total_visits = len(bills_list)
    for i, bill in enumerate(bills_list):
        bill.visit_number = total_visits - i
    
    return render(request, 'workshop/car_profiles/car_profile_detail.html', {
        'car_info': car_info,
        'bills': bills_list,
    })
