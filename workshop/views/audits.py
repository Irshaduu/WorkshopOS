from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from ..models import JobCard

def is_owner(user):
    return user.is_superuser or user.groups.filter(name='Owner').exists()

@login_required
@user_passes_test(is_owner)  # AUD-0041: restrict to Owner — discount audit is financially sensitive
def audit_high_discounts(request):
    """
    PAID bills that gave away more than `JobCard.HIGH_DISCOUNT_AMOUNT`.

    Owner-only — it exposes what the workshop actually settled for. This is the
    compensating control for the rule in CLAUDE.md that a part-paid walk-in
    books its shortfall as a discount: the discount column is where an unusual
    settlement shows up, so it needs somewhere to be read in bulk rather than
    one invoice at a time.

    A flat rupee comparison since 2026-08-10, replacing `discount_amount >
    total_bill_amount * 0.30`. Same threshold as the alert and the settlement
    confirmation, read from one constant — the audit page and the notification
    disagreeing about what "large" means is how an owner learns to trust
    neither. The `ExpressionWrapper` this used to need is gone with the ratio.
    """
    bills = JobCard.objects.filter(
        payment_status='PAID',
        discount_amount__gt=JobCard.HIGH_DISCOUNT_AMOUNT,
        is_deleted=False
    ).order_by('-updated_at')

    return render(request, 'workshop/jobcard/audit_high_discounts.html', {
        'bills': bills,
        'threshold': JobCard.HIGH_DISCOUNT_AMOUNT,
    })
