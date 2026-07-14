from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import F, DecimalField, ExpressionWrapper
from ..models import JobCard, BulkPayer

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
        discount_amount__gt=ExpressionWrapper(F('total_bill_amount') * 0.3, output_field=DecimalField()),
        is_deleted=False
    ).order_by('-updated_at')
    
    return render(request, 'workshop/jobcard/audit_high_discounts.html', {
        'bills': bills
    })

@login_required
@user_passes_test(is_owner)
def audit_deleted_bulk_payers(request):
    """
    Shows all soft-deleted (archived) Bulk Payers for Owner recovery.
    """
    archived_payers = BulkPayer.objects.filter(is_trashed=True).order_by('-customer_name')
    
    return render(request, 'workshop/jobcard/audit_deleted_bulk_payers.html', {
        'archived_payers': archived_payers
    })

@login_required
@user_passes_test(is_owner)
def restore_bulk_payer(request, pk):
    """
    Restores a soft-deleted Bulk Payer back to active status.
    """
    if request.method == 'POST':
        payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=True)
        payer.is_trashed = False
        payer.save(update_fields=['is_trashed'])
        messages.success(request, f"Bulk Payer '{payer.customer_name}' restored successfully.")
        return redirect('audit_deleted_bulk_payers')
    return redirect('pending_payments_list')
