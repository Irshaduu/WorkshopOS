from datetime import date

from django.shortcuts import render
from django.utils import timezone
from django.db.models.functions import Coalesce

from ..models import JobCard
from ..decorators import owner_required


@owner_required  # AUD-0041: restrict to Owner — discount audit is financially sensitive
def audit_high_discounts(request):
    """
    PAID bills that gave away more than `JobCard.HIGH_DISCOUNT_AMOUNT`.

    Owner-only — it exposes what the workshop actually settled for. This is the
    compensating control for the rule in CLAUDE.md that a part-paid walk-in
    books its shortfall as a discount: the discount column is where an unusual
    settlement shows up, so it needs somewhere to be read in bulk rather than
    one invoice at a time. Paid Bills itself is Office-visible; this page is
    not, and its entry in that page's ⋮ menu is gated to match.

    A flat rupee comparison since 2026-08-10, replacing `discount_amount >
    total_bill_amount * 0.30`. Same threshold as the alert and the settlement
    confirmation, read from one constant — the audit page and the notification
    disagreeing about what "large" means is how an owner learns to trust
    neither.

    Filtered by This Year (default), Last Year or a custom range. Only three,
    deliberately: a discount is reviewed in retrospect, so the day-to-day
    Today/This Week vocabulary would return an empty page nearly always —
    which reads as a broken screen rather than an empty period. Same reasoning
    as the Estimates list.

    The window is measured on `paid_date`, falling back to `updated_at` for
    bills settled before that column existed (2026-07-26). `updated_at` is
    `auto_now`, so it moves on any later edit — but it is only reached when
    there is no settlement date at all, and a rough date beats none.
    """
    today = timezone.localdate()
    filter_type = request.GET.get('filter', 'this_year')
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    bills = JobCard.objects.filter(
        payment_status='PAID',
        discount_amount__gt=JobCard.HIGH_DISCOUNT_AMOUNT,
        is_deleted=False
    ).annotate(
        effective_paid_date=Coalesce('paid_date', 'updated_at')
    )

    if filter_type == 'last_year':
        start = today.replace(year=today.year - 1, month=1, day=1)
        end = today.replace(year=today.year - 1, month=12, day=31)
        bills = bills.filter(effective_paid_date__date__gte=start,
                             effective_paid_date__date__lte=end)

    elif filter_type == 'custom':
        # Parsed rather than handed to the ORM as text: an unparseable string
        # reaches `DateTimeField.get_prep_value` and raises, i.e. a 500 from a
        # hand-edited URL. Same shape as `cashbook_views._apply_date_filter`.
        if start_date and end_date:
            try:
                bills = bills.filter(
                    effective_paid_date__date__gte=date.fromisoformat(start_date),
                    effective_paid_date__date__lte=date.fromisoformat(end_date),
                )
            except ValueError:
                pass

    else:  # 'this_year' (default)
        filter_type = 'this_year'
        start = today.replace(month=1, day=1)
        bills = bills.filter(effective_paid_date__date__gte=start)

    bills = bills.order_by('-effective_paid_date', '-updated_at')

    return render(request, 'workshop/jobcard/audit_high_discounts.html', {
        'bills': bills,
        'threshold': JobCard.HIGH_DISCOUNT_AMOUNT,
        'filter_type': filter_type,
        'start_date': start_date,
        'end_date': end_date,
    })
