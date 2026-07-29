from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import F, DecimalField, ExpressionWrapper
from ..models import JobCard

def is_owner(user):
    return user.is_superuser or user.groups.filter(name='Owner').exists()

@login_required
@user_passes_test(is_owner)  # AUD-0041: restrict to Owner — discount audit is financially sensitive
def audit_high_discounts(request):
    """
    Shows PAID bills where the discount amount is > 30% of the total_bill_amount.
    Owner-only view — exposes internal discount rates.
    """
    bills = JobCard.objects.filter(
        payment_status='PAID',
        discount_amount__gt=ExpressionWrapper(
            F('total_bill_amount') * JobCard.HIGH_DISCOUNT_RATIO,
            output_field=DecimalField(),
        ),
        is_deleted=False
    ).order_by('-updated_at')

    return render(request, 'workshop/jobcard/audit_high_discounts.html', {
        'bills': bills
    })
