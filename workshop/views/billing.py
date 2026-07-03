from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from ..models import JobCard
from ..decorators import office_required


@office_required
def invoice_view(request, pk):
    """Display professional invoice for a job card"""
    
    jobcard = get_object_or_404(
        JobCard.objects.prefetch_related('labours', 'spares'), pk=pk
    )
    
    # Calculate labour subtotal (using correct related name: labours)
    labour_subtotal = sum(
        item.amount or 0 
        for item in jobcard.labours.all()
    )
    
    # Calculate spare parts subtotal (using correct related name: spares)
    spare_subtotal = sum(
        item.total_price or 0 
        for item in jobcard.spares.all()
    )
    
    # Calculate grand total using denormalized field
    grand_total = jobcard.total_bill_amount or 0
    
    # Calculate final totals (NEW LOGIC)
    discount = 0 # Not used for now, or keep as 0
    received = jobcard.received_amount or 0
    balance = jobcard.get_balance_amount
    
    return render(request, 'workshop/invoice/invoice_template.html', {
        'jobcard': jobcard,
        'labour_subtotal': labour_subtotal,
        'spare_subtotal': spare_subtotal,
        'grand_total': grand_total,
        'received': received,
        'balance': balance,
    })


@office_required
def update_bill_status(request, pk):
    """
    Quickly update payment status and received amount from Invoice popup.
    Automatically calculates internal discount if Status is PAID.
    """
    if request.method == 'POST':
        jobcard = get_object_or_404(JobCard, pk=pk)
        
        # Safely convert to Decimal
        raw_received = request.POST.get('received_amount', '0')
        try:
            received = Decimal(str(raw_received) if raw_received else '0')
        except (ValueError, TypeError, ArithmeticError):
            received = Decimal('0')
            
        method = request.POST.get('payment_method', 'CASH')

        jobcard.received_amount = received
        jobcard.payment_method = method
        
        total_bill = Decimal(str(jobcard.total_bill_amount or '0'))
        
        # Automated status and discount calculation
        if received > 0:
            jobcard.payment_status = 'PAID'
            if received > total_bill:
                jobcard.discount_amount = Decimal('0')
                messages.warning(request, f"Received amount (₹{received}) exceeds total bill (₹{total_bill}). Overpayment recorded.")
            else:
                jobcard.discount_amount = max(Decimal('0'), total_bill - received)
        else:
            jobcard.payment_status = 'PENDING'
            jobcard.discount_amount = Decimal('0')

        jobcard.save()

        messages.success(request, f"Billing updated for {jobcard.registration_number}")
    
    return redirect('invoice_view', pk=pk)
