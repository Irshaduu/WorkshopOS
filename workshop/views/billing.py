from decimal import Decimal

from django.db.models import Prefetch
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import JobCard, JobCardLabourItem, JobCardSpareItem
from ..decorators import office_required
from ..invoice import build_invoice
from ..notifications import notify


def _safe_back(request):
    """
    Honour ?back= only when it points back into this site.

    Every screen that links here appends its own path so the invoice can offer a
    way home, but the value arrives from the URL and was previously written
    straight into an href — so a crafted link could put `javascript:` or another
    origin behind a button styled as this app's own. Same check the login form's
    `?next=` goes through.
    """
    target = request.GET.get('back') or ''
    if target and url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return None


@office_required
def invoice_view(request, pk):
    """
    The printable bill for one job card.

    All of the arithmetic and every naming decision live in `workshop/invoice.py`
    — this resolves the record and renders. `item__category` is selected because
    a warehouse draw is billed under its category name; without it the bill costs
    one query per part.
    """
    jobcard = get_object_or_404(
        JobCard.objects
        .select_related('bulk_payer')
        .prefetch_related(
            # Both ordered by pk — insertion order, so the bill lists work and
            # parts the way they were added rather than in whatever order the
            # database happens to return them. Neither model declares a default
            # ordering, so without this the same bill could print its lines in
            # two different sequences on two different days.
            Prefetch('labours', queryset=JobCardLabourItem.objects.order_by('pk')),
            Prefetch(
                'spares',
                queryset=JobCardSpareItem.objects.select_related('item__category').order_by('pk'),
            ),
        ),
        pk=pk,
    )

    context = build_invoice(jobcard)
    context.update({
        'jobcard': jobcard,
        'back_url': _safe_back(request),
    })
    return render(request, 'workshop/invoice/invoice_template.html', context)


@office_required
def update_bill_status(request, pk):
    """
    Quickly update payment status and received amount from Invoice popup.
    Automatically calculates internal discount if Status is PAID.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)

        # Fleet-billed cards settle only through the Bulk Payer cascade
        # (bulk_payer_pay). Direct settlement here would mark this one job
        # PAID/PENDING while BulkPayer.total_paid_amount (summed from
        # BulkPaymentHistory only) never learns about it, permanently
        # overstating what the fleet still owes.
        if jobcard.bulk_payer_id:
            messages.error(
                request,
                f"{jobcard.registration_number} is billed to Fleet Account "
                f"'{jobcard.bulk_payer.customer_name}' — settle it from that account's page, not here."
            )
            return redirect('invoice_view', pk=pk)

        # Safely convert to Decimal
        raw_received = request.POST.get('received_amount', '0')
        try:
            received = Decimal(str(raw_received) if raw_received else '0')
        except (ValueError, TypeError, ArithmeticError):
            received = Decimal('0')

        # AUD-0015: Guard against negative received amounts — would corrupt financials.
        if received < Decimal('0'):
            messages.error(request, "Received amount cannot be negative.")
            return redirect('invoice_view', pk=pk)
            
        method = request.POST.get('payment_method', 'CASH')

        jobcard.received_amount = received
        jobcard.payment_method = method
        
        total_bill = Decimal(str(jobcard.total_bill_amount or '0'))
        
        # Automated status and discount calculation
        if received > 0:
            jobcard.payment_status = 'PAID'
            jobcard.paid_date = timezone.now()
            if received > total_bill:
                jobcard.discount_amount = Decimal('0')
                messages.warning(request, f"Received amount (₹{received}) exceeds total bill (₹{total_bill}). Overpayment recorded.")
            else:
                jobcard.discount_amount = max(Decimal('0'), total_bill - received)
        else:
            jobcard.payment_status = 'PENDING'
            jobcard.discount_amount = Decimal('0')
            jobcard.paid_date = None

        jobcard.save()

        # A part-paid bill books its shortfall as a discount (see CLAUDE.md —
        # that is the business rule, not a bug), which makes an unusually large
        # discount the signal worth surfacing rather than the payment itself.
        # Same threshold as `audit_high_discounts`, read from one constant so the
        # audit page and the alert can never disagree about what "large" means.
        if jobcard.discount_amount and total_bill > 0:
            ratio = jobcard.discount_amount / total_bill
            if ratio > JobCard.HIGH_DISCOUNT_RATIO:
                notify(
                    'HIGH_DISCOUNT',
                    f"{jobcard.registration_number} — ₹{jobcard.discount_amount} off "
                    f"₹{total_bill} ({ratio:.0%}) on bill {jobcard.bill_number}.",
                    actor=request.user,
                    url=reverse('invoice_view', args=[jobcard.pk]),
                    object_type='JOBCARD',
                    object_id=jobcard.pk,
                )

        messages.success(request, f"Billing updated for {jobcard.registration_number}")
    
    return redirect('invoice_view', pk=pk)
