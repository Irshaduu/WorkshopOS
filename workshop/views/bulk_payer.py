import json
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import (
    Sum, Count, Value, F, OuterRef, Subquery, Max,
    DecimalField, ExpressionWrapper, IntegerField,
)
from django.db.models.functions import Coalesce
from django.db import transaction
from django.core.paginator import Paginator

from ..models import (
    JobCard, JobCardSpareItem, JobCardLabourItem,
    BulkPayer, BulkPaymentHistory,
)
from ..decorators import office_required, owner_required


@office_required
def bulk_payer_list(request):
    """
    Returns the list of all bulk payers as an AJAX partial.
    Called from the Pending Bills page.
    Million-data safe: all aggregation done in SQL, zero Python loops.
    """
    # SQL subquery: count of PENDING/PARTIAL job cards per payer
    pending_count_sq = (
        BulkPayer.job_cards.through.objects
        .filter(
            bulkpayer_id=OuterRef('pk'),
            jobcard__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('bulkpayer_id')
        .annotate(n=Count('jobcard_id'))
        .values('n')
    )

    # SQL subquery: sum of received_amount for PENDING/PARTIAL job cards
    received_sq = (
        BulkPayer.job_cards.through.objects
        .filter(
            bulkpayer_id=OuterRef('pk'),
            jobcard__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('bulkpayer_id')
        .annotate(s=Sum('jobcard__received_amount'))
        .values('s')
    )

    # SQL subquery: sum of spares for PENDING/PARTIAL job cards
    spares_sq = (
        JobCardSpareItem.objects
        .filter(
            job_card__bulk_payers=OuterRef('pk'),
            job_card__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('job_card__bulk_payers')
        .annotate(s=Sum('total_price'))
        .values('s')
    )

    # SQL subquery: sum of labour for PENDING/PARTIAL job cards
    labour_sq = (
        JobCardLabourItem.objects
        .filter(
            job_card__bulk_payers=OuterRef('pk'),
            job_card__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('job_card__bulk_payers')
        .annotate(s=Sum('amount'))
        .values('s')
    )

    bulk_payers = (
        BulkPayer.objects
        .filter(is_trashed=False)
        .annotate(
            card_count=Coalesce(Subquery(pending_count_sq, output_field=IntegerField()), Value(0)),
            total_spares=Coalesce(Subquery(spares_sq, output_field=DecimalField()), Value(0, output_field=DecimalField())),
            total_labour=Coalesce(Subquery(labour_sq, output_field=DecimalField()), Value(0, output_field=DecimalField())),
            total_received=Coalesce(Subquery(received_sq, output_field=DecimalField()), Value(0, output_field=DecimalField())),
        )
        .annotate(
            total_balance=ExpressionWrapper(
                F('total_spares') + F('total_labour') - F('total_received'),
                output_field=DecimalField()
            )
        )
        .order_by('customer_name')
    )

    return render(request, 'workshop/jobcard/bulk_payer_panel.html', {
        'bulk_payers': bulk_payers,
    })


@office_required
def bulk_payer_create(request):
    """
    POST: Create a new BulkPayer and auto-add all matching PENDING/PARTIAL 
    job cards with the same customer_name.
    """
    if request.method == 'POST':
        customer_name = request.POST.get('customer_name', '').strip()
        
        if not customer_name:
            messages.error(request, "Customer name cannot be empty.")
            return redirect('pending_payments_list')
        
        if BulkPayer.objects.filter(customer_name__iexact=customer_name).exists():
            messages.error(request, f"Bulk payer '{customer_name}' already exists.")
            return redirect('pending_payments_list')
        
        bulk_payer = BulkPayer.objects.create(customer_name=customer_name)
        
        # Auto-add all PENDING/PARTIAL job cards with matching customer name
        matching_cards = JobCard.objects.filter(
            customer_name__iexact=customer_name,
            payment_status__in=['PENDING', 'PARTIAL']
        )
        bulk_payer.job_cards.add(*matching_cards)
        
        count = matching_cards.count()
        messages.success(request, f"Bulk payer '{customer_name}' created with {count} pending job card(s).")
        return redirect('bulk_payer_detail', pk=bulk_payer.pk)
    
    return redirect('pending_payments_list')


@office_required
def bulk_payer_detail(request, pk):
    """
    Full page: Shows all cars in a bulk payer group with financials.
    Million-data optimized with SQL subqueries and annotations.
    """
    bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=False)
    
    # Get pending/partial job cards only (PAID and BULK_PAID are hidden)
    base_cards_query = bulk_payer.job_cards.filter(
        payment_status__in=['PENDING', 'PARTIAL']
    )
    
    # -------------------------------------------------------------------------
    # 1. Grand totals (Calculated efficiently in SQL without Python loops)
    # -------------------------------------------------------------------------
    total_received_all = base_cards_query.aggregate(s=Sum('received_amount'))['s'] or Decimal('0.0')
    total_spares = JobCardSpareItem.objects.filter(job_card__in=base_cards_query).aggregate(s=Sum('total_price'))['s'] or Decimal('0.0')
    total_labour = JobCardLabourItem.objects.filter(job_card__in=base_cards_query).aggregate(s=Sum('amount'))['s'] or Decimal('0.0')
    
    total_bill_all = total_spares + total_labour
    total_balance_all = max(Decimal('0.0'), total_bill_all - total_received_all)
    card_count = base_cards_query.count()

    # -------------------------------------------------------------------------
    # 2. Per-row Financial Annotations
    # -------------------------------------------------------------------------
    cards_query = base_cards_query.select_related('lead_mechanic')
    
    cards_query = cards_query.annotate(
        balance_amount=ExpressionWrapper(
            F('total_bill_amount') - F('received_amount'),
            output_field=DecimalField()
        )
    ).order_by('admitted_date', 'pk')
    
    # -------------------------------------------------------------------------
    # 3. True Lazy Pagination (Million-data ready)
    # -------------------------------------------------------------------------
    paginator = Paginator(cards_query, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # -------------------------------------------------------------------------
    # 4. Optimized Visit Counting (Queries ONLY the 21 cars on this page)
    # -------------------------------------------------------------------------
    unique_regs = list(set(card.registration_number for card in page_obj))
    
    if unique_regs:
        reg_counts = dict(
            JobCard.objects.filter(registration_number__in=unique_regs)
            .values('registration_number')
            .annotate(total=Count('id'))
            .values_list('registration_number', 'total')
        )
        
        all_cards_for_regs = (
            JobCard.objects.filter(registration_number__in=unique_regs)
            .order_by('admitted_date', 'pk')
            .values_list('registration_number', 'pk')
        )
        reg_visit_tracker = {}
        for reg, pk_val in all_cards_for_regs:
            if reg not in reg_visit_tracker:
                reg_visit_tracker[reg] = []
            reg_visit_tracker[reg].append(pk_val)
            
        for card in page_obj:
            card.total_visits = reg_counts.get(card.registration_number, 1)
            try:
                card.visit_number = reg_visit_tracker[card.registration_number].index(card.pk) + 1
            except (KeyError, ValueError):
                card.visit_number = 1
    
    return render(request, 'workshop/jobcard/bulk_payer_detail.html', {
        'bulk_payer': bulk_payer,
        'cards': page_obj,
        'page_obj': page_obj,
        'total_bill': total_bill_all,
        'total_received': total_received_all,
        'total_balance': total_balance_all,
        'card_count': card_count,
        'payment_history': bulk_payer.payment_history.filter(is_trashed=False).order_by('-created_at')
    })


@office_required
def bulk_payer_add_card(request, pk):
    """
    POST: Add a job card to a bulk payer group by job card ID.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk)
        job_card_id = request.POST.get('job_card_id', '').strip()
        
        if not job_card_id:
            # Search by registration number instead
            reg_number = request.POST.get('registration_number', '').strip().upper()
            if reg_number:
                matching = JobCard.objects.filter(
                    registration_number__iexact=reg_number,
                    payment_status__in=['PENDING', 'PARTIAL']
                ).exclude(bulk_payers=bulk_payer)
                
                if matching.exists():
                    bulk_payer.job_cards.add(*matching)
                    messages.success(request, f"Added {matching.count()} job card(s) for {reg_number}.")
                else:
                    messages.error(request, f"No pending job cards found for '{reg_number}' or already added.")
            else:
                messages.error(request, "Please provide a registration number or job card ID.")
        else:
            try:
                job_card = JobCard.objects.get(pk=int(job_card_id))
                bulk_payer.job_cards.add(job_card)
                messages.success(request, f"Added {job_card.registration_number} to {bulk_payer.customer_name}.")
            except (JobCard.DoesNotExist, ValueError):
                messages.error(request, "Job card not found.")
    
    return redirect('bulk_payer_detail', pk=pk)


@office_required
def bulk_payer_remove_card(request, pk):
    """
    POST: Remove a job card from a bulk payer group.
    Does NOT delete the job card — just removes the association.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk)
        job_card_id = request.POST.get('job_card_id')
        
        try:
            job_card = JobCard.objects.get(pk=int(job_card_id))
            bulk_payer.job_cards.remove(job_card)
            messages.success(
                request,
                f"Removed {job_card.brand_name} {job_card.model_name} ({job_card.registration_number}) from {bulk_payer.customer_name}."
            )
        except (JobCard.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Job card not found.")
    
    return redirect('bulk_payer_detail', pk=pk)


@office_required
def bulk_payer_pay(request, pk):
    """
    POST: Process a lump sum payment via the Cascade Algorithm.
    Distributes payment oldest-first. Fully paid cards get BULK_PAID status.
    Thread-safe with select_for_update.
    """
    if request.method != 'POST':
        return redirect('bulk_payer_detail', pk=pk)
    
    bulk_payer = get_object_or_404(BulkPayer, pk=pk)
    lump_sum_raw = request.POST.get('lump_sum', '0')
    payment_method = request.POST.get('payment_method', 'CASH')
    
    try:
        lump_sum = Decimal(str(lump_sum_raw))
    except (ValueError, TypeError, ArithmeticError):
        lump_sum = Decimal('0')
    
    if lump_sum <= 0:
        messages.error(request, "Invalid payment amount.")
        return redirect('bulk_payer_detail', pk=pk)
    

    with transaction.atomic():
        pending_cards = bulk_payer.job_cards.select_for_update().filter(
            payment_status__in=['PENDING', 'PARTIAL']
        ).annotate(
            balance_amount=ExpressionWrapper(F('total_bill_amount') - F('received_amount'), output_field=DecimalField())
        ).order_by('admitted_date', 'pk')  # Oldest first
        
        remaining_funds = lump_sum
        jobs_updated = 0
        history_details = []  # Track per-job breakdown for history
        
        for job in pending_cards:
            if remaining_funds <= 0:
                break
            
            balance = job.balance_amount
            if balance <= 0:
                continue
            
            if remaining_funds >= balance:
                # Fully pay this card
                paid_amount = balance
                job.received_amount += balance
                job.payment_status = 'BULK_PAID'
                job.payment_method = payment_method
                job.discount_amount = Decimal('0')
                remaining_funds -= balance
            else:
                # Partial payment
                paid_amount = remaining_funds
                job.received_amount += remaining_funds
                job.payment_status = 'PARTIAL'
                job.payment_method = payment_method
                remaining_funds = Decimal('0')
            
            job.save()
            jobs_updated += 1
            history_details.append({
                'job_id': job.pk,
                'reg': job.registration_number,
                'car': f"{job.brand_name} {job.model_name}",
                'paid': str(paid_amount),
                'status': job.payment_status,
            })
        
        # Create payment history record
        BulkPaymentHistory.objects.create(
            bulk_payer=bulk_payer,
            amount=lump_sum,
            payment_method=payment_method,
            jobs_affected=jobs_updated,
            details=json.dumps(history_details),
        )
    
    messages.success(request, f"₹{lump_sum:,.0f} distributed across {jobs_updated} job(s) for {bulk_payer.customer_name}.")
    return redirect('bulk_payer_detail', pk=pk)


@owner_required
def bulk_payer_delete(request, pk):
    """
    POST: Soft-delete a bulk payer group (move to trash).
    Owner only. Does NOT delete job cards — only hides the grouping.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk)
        bulk_payer.is_trashed = True
        bulk_payer.save()
        messages.success(request, f"Bulk payer '{bulk_payer.customer_name}' moved to trash.")
    
    return redirect('pending_payments_list')


@owner_required
def bulk_payer_trash_list(request):
    """
    Redirect to unified Trash page, Bulk Payers tab.
    Kept for backward compatibility with any existing links/bookmarks.
    """
    return redirect('/trash/?tab=bulkpayers')


@owner_required
def bulk_payer_restore(request, pk):
    """
    POST: Restore a trashed bulk payer. Owner only.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=True)
        bulk_payer.is_trashed = False
        bulk_payer.save()
        messages.success(request, f"Bulk payer '{bulk_payer.customer_name}' restored.")
    return redirect('/trash/?tab=bulkpayers')


@owner_required
def bulk_payer_permanent_delete(request, pk):
    """
    POST: Permanently delete a trashed bulk payer. Owner only.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=True)
        name = bulk_payer.customer_name
        bulk_payer.delete()
        messages.success(request, f"Bulk payer '{name}' permanently deleted.")
    return redirect('/trash/?tab=bulkpayers')


@owner_required
def bulk_payment_history_delete(request, pk, history_pk):
    """
    POST: Delete a payment history entry and reverse the payments.
    Reverses the cascade — subtracts amounts from affected job cards.
    Owner only.
    """
    if request.method != 'POST':
        return redirect('bulk_payer_detail', pk=pk)
    
    bulk_payer = get_object_or_404(BulkPayer, pk=pk)
    history = get_object_or_404(BulkPaymentHistory, pk=history_pk, bulk_payer=bulk_payer)
    
    with transaction.atomic():
        # Reverse payments from the history snapshot
        try:
            details = json.loads(history.details)
        except (json.JSONDecodeError, TypeError):
            details = []
        
        for entry in details:
            try:
                job = JobCard.objects.select_for_update().get(pk=entry['job_id'])
                reversed_amount = Decimal(str(entry['paid']))
                job.received_amount = max(Decimal('0'), job.received_amount - reversed_amount)
                
                # Recalculate status
                if job.received_amount <= 0:
                    job.payment_status = 'PENDING'
                else:
                    job.payment_status = 'PARTIAL'
                
                job.save()
            except (JobCard.DoesNotExist, KeyError, Exception):
                continue
        
        history.is_trashed = True
        history.save()
    
    messages.success(request, f"Payment of ₹{history.amount:,.0f} reversed and moved to Trash.")
    return redirect('bulk_payer_detail', pk=pk)


@owner_required
def permanent_delete_payment_history(request, history_pk):
    """
    POST: Permanently delete a payment history entry from the database.
    Owner only.
    """
    if request.method == 'POST':
        history = get_object_or_404(BulkPaymentHistory, pk=history_pk, is_trashed=True)
        amount = history.amount
        history.delete()
        messages.success(request, f"Payment history of ₹{amount:,.0f} permanently deleted.")
    return redirect('/trash/?tab=payments')
