import json
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import (
    Sum, Count, Value, F, OuterRef, Subquery, Max,
    DecimalField, ExpressionWrapper, IntegerField,
)
from django.db.models.functions import Coalesce
from django.db import transaction
from django.core.paginator import Paginator
from django.urls import reverse

from ..models import (
    JobCard, JobCardSpareItem,
    BulkPayer, BulkPaymentHistory, DeletionLog,
)
from ..decorators import office_required, owner_required
from ..notifications import notify
from ..money import parse_money, fit_text
from ..money_dates import posted_date, is_future
from .. import delete_window


@office_required
def bulk_payer_list(request):
    """
    Returns the list of all bulk payers as an AJAX partial.
    Called from the Pending Bills page.
    Million-data safe: all aggregation done in SQL, zero Python loops.
    """
    # SQL subquery: count of PENDING/PARTIAL job cards per payer
    pending_count_sq = (
        JobCard.objects
        .filter(
            bulk_payer=OuterRef('pk'),
            payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('bulk_payer')
        .annotate(n=Count('pk'))
        .values('n')
    )

    # SQL subquery: sum of received_amount for PENDING/PARTIAL job cards
    received_sq = (
        JobCard.objects
        .filter(
            bulk_payer=OuterRef('pk'),
            payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('bulk_payer')
        .annotate(s=Sum('received_amount'))
        .values('s')
    )

    # SQL subquery: sum of spares for PENDING/PARTIAL job cards
    spares_sq = (
        JobCardSpareItem.objects
        .filter(
            job_card__bulk_payer=OuterRef('pk'),
            job_card__payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('job_card__bulk_payer')
        .annotate(s=Sum('total_price'))
        .values('s')
    )

    # SQL subquery: sum of labour for PENDING/PARTIAL job cards.
    # Off the CARDS — `labour_amount` is one charge per card, and the dormant
    # per-line column it replaced would report zero labour for everything raised
    # since 2026-08-04, understating what each fleet still owes.
    labour_sq = (
        JobCard.objects
        .filter(
            bulk_payer=OuterRef('pk'),
            payment_status__in=['PENDING', 'PARTIAL'],
        )
        .values('bulk_payer')
        .annotate(s=Sum('labour_amount'))
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
    POST: Create a new BulkPayer.
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
        
        messages.success(request, f"Bulk payer '{customer_name}' created successfully. You can now add job cards manually.")
        return redirect('bulk_payer_detail', pk=bulk_payer.pk)
    
    return redirect('pending_payments_list')


@office_required
def bulk_payer_edit(request, pk):
    """
    POST: rename a Fleet Account.

    SAFE BY CONSTRUCTION, and worth saying why, because renaming a brand or a
    spare part in this app is NOT. Those are free text copied onto every job
    card, so `master_data.py` has to carry the new spelling across the history.
    A Fleet Account is a real row that everything points AT: `JobCard.bulk_payer`
    and `BulkPaymentHistory.bulk_payer` are ForeignKeys, and the Deep Analysis
    fleet section groups by the FK id and pulls the name through the join. So
    one UPDATE reaches the account page, the picker, the fleet insight table and
    the "Fleet · <name>" chip on a printed invoice, with nothing to propagate
    and nothing that can fall out of step.

    Two things deliberately keep the OLD name and must not be "fixed": a
    `DeletionLog` snapshot and a `Notification` body. Both are frozen records of
    what was true when they were written.

    Mirrors `spare_shop_edit`, which is the same shape on the sibling model:
    `__iexact` because the column's `unique=True` is case-sensitive, and
    `.exclude(pk=pk)` because without it a model-level uniqueness check fires
    before the view runs and refuses the account its own name back — so fixing
    the capitalisation of the only account of that name would be impossible.
    """
    payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=False)
    if request.method == 'POST':
        # Trimmed to the column rather than crashing: `customer_name` is
        # max_length=150, and an oversized value is the SQLite-accepts /
        # Postgres-500s split this codebase keeps hitting.
        name = fit_text(request.POST.get('customer_name', '').strip(),
                        BulkPayer, 'customer_name')

        if not name:
            messages.error(request, "Fleet account name cannot be empty.")
            return redirect('bulk_payer_detail', pk=pk)

        if BulkPayer.objects.filter(customer_name__iexact=name).exclude(pk=pk).exists():
            messages.error(request, f"Another fleet account named '{name}' already exists.")
            return redirect('bulk_payer_detail', pk=pk)

        was = payer.customer_name
        payer.customer_name = name
        payer.save(update_fields=['customer_name'])
        if was != name:
            messages.success(request, f"Renamed '{was}' to '{name}'.")
    return redirect('bulk_payer_detail', pk=pk)


@office_required
def bulk_payer_detail(request, pk):
    """
    Full page: Shows all cars in a bulk payer group with financials.
    Million-data optimized with SQL subqueries and annotations.
    """
    bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=False)
    today_iso = timezone.localdate().isoformat()
    
    # Pending/Partial cards — used for totals and the main active list
    base_cards_query = bulk_payer.job_cards.filter(
        payment_status__in=['PENDING', 'PARTIAL']
    )
    # Settled cards — display-only, capped at 30 most recent to avoid huge lists
    paid_cards = list(
        bulk_payer.job_cards
        .filter(payment_status='BULK_PAID')
        .order_by('-admitted_date', '-pk')[:30]
    )
    
    # -------------------------------------------------------------------------
    # 1. Grand totals (Calculated efficiently in SQL without Python loops)
    #    Totals are always calculated from pending/partial only.
    # -------------------------------------------------------------------------
    total_received_all = base_cards_query.aggregate(s=Sum('received_amount'))['s'] or Decimal('0.0')
    total_spares = JobCardSpareItem.objects.filter(job_card__in=base_cards_query).aggregate(s=Sum('total_price'))['s'] or Decimal('0.0')
    # Off the cards, not off their job lines. This summed
    # `JobCardLabourItem.amount` until 2026-08-04; that column is dormant now, so
    # the fleet's outstanding balance would have silently shed all the labour on
    # every card raised since.
    total_labour = base_cards_query.aggregate(s=Sum('labour_amount'))['s'] or Decimal('0.0')

    total_bill_all = total_spares + total_labour
    total_balance_all = total_bill_all - total_received_all  # Can be negative (fully settled)
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
        'paid_cards': paid_cards,
        'total_bill': total_bill_all,
        'total_received': total_received_all,
        'total_balance': total_balance_all,
        'advance_balance': bulk_payer.advance_balance,
        'card_count': card_count,
        'today_iso': today_iso,
        # ORDERED BY THE DAY THE MONEY MOVED. This explicit `order_by` overrode
        # `Meta.ordering`, so adding the column without changing it here would
        # have left a field nothing reads — which is worse than no field,
        # because it looks fixed. `created_at` still breaks ties inside a day.
        'payment_history': bulk_payer.payment_history.filter(is_trashed=False)
                                     .order_by('-date', '-created_at'),
    })


@office_required
def move_jobcard_to_bulk(request):
    """
    POST: Move a job card to a bulk payer group.
    Called from the Pending Bills list.
    """
    if request.method == 'POST':
        job_card_id = request.POST.get('job_card_id', '').strip()
        bulk_payer_id = request.POST.get('bulk_payer_id', '').strip()
        
        if not job_card_id or not bulk_payer_id:
            messages.error(request, "Missing job card or bulk payer selection.")
            return redirect('pending_payments_list')
            
        try:
            job_card = JobCard.objects.get(pk=int(job_card_id))
            # An ARCHIVED account must not take new work. Its detail page is
            # gone (404), it is absent from every picker, and update_bill_status
            # refuses any card carrying a bulk_payer — so a card attached here
            # would be billed to a screen nobody can open and settleable by no
            # route at all. The picker only ever renders active accounts, but
            # the account can be archived between render and submit, and this is
            # the only server-side gate.
            bulk_payer = BulkPayer.objects.get(pk=int(bulk_payer_id))
            if bulk_payer.is_trashed:
                # Named explicitly rather than falling through to the generic
                # "Invalid ... selected" below: the account exists and was
                # chosen on purpose, so "invalid" reads like a system fault.
                messages.error(
                    request,
                    f"'{bulk_payer.customer_name}' is archived and can't take new job cards. "
                    f"Reactivate it from Archived Fleet Accounts first."
                )
                return redirect('pending_payments_list')

            # Prevent moving already fully paid cards or cards already assigned
            if job_card.payment_status not in ['PENDING', 'PARTIAL'] or job_card.bulk_payer:
                messages.error(request, "This job card cannot be assigned to a bulk payer.")
                return redirect('pending_payments_list')

            bulk_payer.job_cards.add(job_card)
            bulk_payer.update_totals()
            
            messages.success(request, f"Moved {job_card.registration_number} to {bulk_payer.customer_name}.")
        except (JobCard.DoesNotExist, BulkPayer.DoesNotExist, ValueError):
            messages.error(request, "Invalid job card or bulk payer selected.")
            
    return redirect('pending_payments_list')


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

            # A card that's PARTIAL or BULK_PAID has already received money
            # through this fleet's cascade — silently detaching it would
            # leave a non-fleet job card sitting at PARTIAL (a state normal
            # customers can never be in) while the fleet's BulkPaymentHistory
            # still shows that money collected. Block rather than guess at a
            # reversal — the money may belong to a lump payment shared with
            # other cards, so there's no clean single amount to claw back.
            if job_card.received_amount and job_card.received_amount > 0:
                messages.error(
                    request,
                    f"Can't remove {job_card.registration_number} — it has already received "
                    f"₹{job_card.received_amount} through this Fleet Account's payments. "
                    f"Reverse the relevant Fleet payment first if this was a mistake."
                )
                return redirect('bulk_payer_detail', pk=pk)

            bulk_payer.job_cards.remove(job_card)
            bulk_payer.update_totals()
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

    # `is_trashed=False`, matching spare_shop_pay: an archived account takes no
    # new payments. Its page is unreachable, so money sent here would land as
    # advance credit on an account nobody can open.
    bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=False)
    lump_sum_raw = request.POST.get('lump_sum', '0')
    payment_method = request.POST.get('payment_method', 'CASH')
    # `fit_text` to the column, never a raw write: an over-long note is stored
    # by SQLite in violation of the declared width and 500s on Postgres with
    # `value too long for type character varying(255)`. The same split every
    # other typed string in this app goes through, and trimming beats crashing
    # on a screen where money is about to move. Blank stays NULL rather than
    # becoming '' — nobody wrote a note is a different fact from an empty one.
    note = fit_text(request.POST.get('note'), BulkPaymentHistory, 'note') or None

    # workshop/money.py, same as every other typed amount. 'Infinity' passes
    # `lump_sum <= 0` honestly and would have settled the whole account at an
    # infinite receipt; 'NaN' made that same comparison raise
    # decimal.InvalidOperation outside the try/except above, 500ing the page;
    # and 11 digits overflow numeric(12,2) on Postgres. parse_money refuses zero
    # by default, which is the rule this view already wanted.
    # `<= 0` as well as None: parse_money refuses a zero BEFORE quantising, so
    # `0.004` comes back as `0.00`. This column carries no CheckConstraint, so
    # it is the one of the four that would have written the row — a ₹0 payment
    # in the ledger, cascading nothing, with a history entry to reverse.
    lump_sum = parse_money(lump_sum_raw, BulkPaymentHistory, 'amount')
    if lump_sum is None or lump_sum <= 0:
        messages.error(request, "Invalid payment amount.")
        return redirect('bulk_payer_detail', pk=pk)

    # THE DAY THE MONEY MOVED, not the day it was keyed. A fleet collector
    # comes round and the office keys the receipt when it gets to it, so the
    # two routinely fall in different months — and these are the largest
    # single receipts the workshop takes. Same rule, same helpers, as the
    # Cashbook and both shop ledgers; refused forward, because a date ahead of
    # today is a mistyped year far more often than a plan.
    pay_date = posted_date(request.POST.get('date'))
    if is_future(pay_date):
        messages.error(request, "A payment cannot be dated in the future.")
        return redirect('bulk_payer_detail', pk=pk)
    

    with transaction.atomic():
        # Lock the payer row to safely read/update advance_balance
        bulk_payer = BulkPayer.objects.select_for_update().get(pk=pk)

        # Pool new payment with any existing advance credit
        advance_used = bulk_payer.advance_balance
        remaining_funds = lump_sum + advance_used
        bulk_payer.advance_balance = Decimal('0')

        pending_cards = bulk_payer.job_cards.select_for_update().filter(
            payment_status__in=['PENDING', 'PARTIAL']
        ).annotate(
            balance_amount=ExpressionWrapper(F('total_bill_amount') - F('received_amount'), output_field=DecimalField())
        ).order_by('admitted_date', 'pk')  # Oldest first
        
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
                job.paid_date = timezone.now()
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
        
        # Store any remaining funds as advance credit for future bills
        new_advance = remaining_funds if remaining_funds > Decimal('0') else Decimal('0')
        bulk_payer.advance_balance = new_advance
        bulk_payer.save(update_fields=['advance_balance'])

        # Record history with full advance tracking (dict format for new records)
        BulkPaymentHistory.objects.create(
            bulk_payer=bulk_payer,
            amount=lump_sum,
            payment_method=payment_method,
            note=note,
            date=pay_date,
            jobs_affected=jobs_updated,
            details=json.dumps({
                'jobs': history_details,
                'advance_used': str(advance_used),
                'advance_stored': str(new_advance),
            }),
        )

    # Build descriptive success message
    msg_parts = [f"₹{lump_sum:,.0f} processed for {bulk_payer.customer_name}."]
    if jobs_updated:
        msg_parts.append(f"{jobs_updated} job(s) settled.")
    if new_advance > 0:
        msg_parts.append(f"₹{new_advance:,.0f} stored as advance credit.")
    messages.success(request, " ".join(msg_parts))
    return redirect('bulk_payer_detail', pk=pk)


@office_required
def bulk_payer_delete(request, pk):
    """
    POST: Deactivate (archive) a Fleet Account.

    Reversible and safe: hides the account from active lists but keeps every
    linked job card and all payment history intact. A Fleet Account is NEVER
    hard-deleted — that would CASCADE-destroy its payment ledger. Reactivate it
    any time from the Archived list.

    GUARD: an account still holding unsettled job cards cannot be archived.
    Archiving hides it from every screen at once — the detail page 404s, the
    picker drops it, Pending Bills already excludes any card with a bulk_payer,
    and update_bill_status refuses to settle one — so its unpaid cards had no
    remaining route to a payment. A PARTIAL card could not even be detached,
    because the received-money guard in bulk_payer_remove_card (correctly)
    blocks that. Blocking here rather than opening a back door keeps one rule:
    money owed is always reachable from exactly one screen.
    """
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk)

        unsettled = bulk_payer.job_cards.filter(payment_status__in=['PENDING', 'PARTIAL'])
        # One aggregate for the count and the total, one narrow query for the
        # names to show. Only ever runs on the archive attempt itself.
        totals = unsettled.aggregate(
            n=Count('pk'),
            owed=Coalesce(
                Sum(F('total_bill_amount') - F('received_amount'), output_field=DecimalField()),
                Value(Decimal('0'), output_field=DecimalField()),
                output_field=DecimalField(),
            ),
        )
        if totals['n']:
            n, outstanding = totals['n'], totals['owed']
            shown = list(
                unsettled.order_by('admitted_date', 'pk')
                         .values_list('registration_number', flat=True)[:6]
            )
            names = ", ".join(shown)
            more = f" and {n - len(shown)} more" if n > len(shown) else ""
            messages.error(
                request,
                f"Can't archive '{bulk_payer.customer_name}' — {n} job card(s) are still "
                f"unsettled (₹{outstanding:,.0f} outstanding): {names}{more}. "
                f"Settle them from this page, or remove them from the account first."
            )
            return redirect('bulk_payer_detail', pk=pk)

        bulk_payer.is_trashed = True
        bulk_payer.save(update_fields=['is_trashed'])
        notify(
            'ACCOUNT_ARCHIVED',
            f"{bulk_payer.customer_name} archived",
            detail="Fleet Account",
            actor=request.user,
            url=reverse('bulk_payer_archived'),
            object_type='BULK_PAYER', object_id=bulk_payer.pk,
        )
        messages.success(request, f"Fleet Account '{bulk_payer.customer_name}' deactivated (archived).")
    return redirect('pending_payments_list')


@office_required
def bulk_payer_archived(request):
    """List archived (deactivated) Fleet Accounts, each with a Reactivate action."""
    payers = BulkPayer.objects.filter(is_trashed=True).order_by('customer_name')
    page_obj = Paginator(payers, 45).get_page(request.GET.get('page'))
    return render(request, 'workshop/jobcard/bulk_payer_archived.html', {
        'page_obj': page_obj,
    })


@office_required
def bulk_payer_restore(request, pk):
    """POST: Reactivate an archived Fleet Account."""
    if request.method == 'POST':
        bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=True)
        bulk_payer.is_trashed = False
        bulk_payer.save(update_fields=['is_trashed'])
        messages.success(request, f"Fleet Account '{bulk_payer.customer_name}' reactivated.")
    return redirect('bulk_payer_archived')


@office_required
def bulk_payment_history_delete(request, pk, history_pk):
    """
    POST: Permanently delete a Fleet payment — reverses its effect, logs a
    snapshot to the Owner-only Deletion History, then removes the record.

    Reversal restores the affected job cards' balances and the payer's advance
    credit, so running totals stay correct. There is no restore: the record is
    gone and its financial effect is undone in one atomic step. Owner + Office.

    GUARD: a payment whose effects a *later* payment has already consumed is
    refused, not part-reversed — see the PRE-FLIGHT block below.
    """
    if request.method != 'POST':
        return redirect('bulk_payer_detail', pk=pk)

    # An archived account holds only settled cards (bulk_payer_delete enforces
    # that). Reversing here would un-settle them on an account whose page no
    # longer opens, recreating exactly the stranding that guard prevents.
    bulk_payer = get_object_or_404(BulkPayer, pk=pk, is_trashed=False)
    history = get_object_or_404(BulkPaymentHistory, pk=history_pk, bulk_payer=bulk_payer)

    # Office fixes a recent mistake; an owner takes anything older. These are
    # the largest single receipts the workshop handles, and `created_at` is the
    # right column — the date box on this form back-dates a receipt to the day
    # the collector actually came.
    #
    # Consequence accepted knowingly: a reversal must go NEWEST FIRST (see the
    # pre-flight below), so if any payment in that chain is past the window the
    # owner does the whole chain rather than Office starting it. This escalates
    # more often here than anywhere else the window applies, and that is the
    # right way round for the biggest money on the page.
    stop = delete_window.refusal(
        request.user, history.created_at, f"This ₹{history.amount:,.0f} payment")
    if stop:
        messages.error(request, stop)
        return redirect('bulk_payer_detail', pk=pk)

    reason = request.POST.get('reason', '').strip()

    with transaction.atomic():
        # Lock the payer row for safe advance_balance reversal
        bulk_payer = BulkPayer.objects.select_for_update().get(pk=pk)

        # Parse history details — handle both old list format and new dict format
        try:
            raw = json.loads(history.details)
        except (json.JSONDecodeError, TypeError):
            raw = []

        if isinstance(raw, list):
            # Old format: plain list of job entries
            job_entries = raw
            advance_stored = Decimal('0')
            advance_used = Decimal('0')
        else:
            # New format: dict with jobs + advance tracking
            job_entries = raw.get('jobs', [])
            advance_stored = Decimal(str(raw.get('advance_stored', '0')))
            advance_used = Decimal(str(raw.get('advance_used', '0')))

        # PRE-FLIGHT. Reversal is exact only while this payment's effects are
        # still intact. Both max(0, …) clamps below silently absorb the
        # difference when they are not — and a clamped reversal breaks the one
        # invariant this ledger has:
        #
        #     Σ(card.received_amount) + advance_balance == Σ(history.amount)
        #
        # Overpay ₹1,500 on a ₹1,000 bill (₹500 stored as credit), let a later
        # ₹300 payment spend that credit on a second car, then reverse the
        # first payment: the credit is no longer there to take back, the clamp
        # writes 0 instead of −500, and the second car stays BULK_PAID on ₹800
        # the fleet never handed over. Refuse instead, and say which payment to
        # reverse first — the same "block rather than guess at a reversal"
        # choice bulk_payer_remove_card makes for the same reason.
        blockers = []
        jobs_to_reverse = []
        for entry in job_entries:
            try:
                job = JobCard.objects.select_for_update().get(pk=entry['job_id'])
                reversed_amount = Decimal(str(entry['paid']))
            except (JobCard.DoesNotExist, KeyError, InvalidOperation):
                # Only skip the entries we can legitimately expect to be bad
                # (missing job, malformed snapshot key, unparseable amount).
                # Any other error propagates and rolls back the whole reversal
                # so we never commit a half-reversed payment.
                continue
            if job.received_amount < reversed_amount:
                blockers.append(
                    f"{job.registration_number} has only ₹{job.received_amount:,.0f} "
                    f"left of the ₹{reversed_amount:,.0f} this payment put on it"
                )
            jobs_to_reverse.append((job, reversed_amount))

        if bulk_payer.advance_balance + advance_used < advance_stored:
            blockers.append(
                f"₹{advance_stored:,.0f} of the credit this payment left over has "
                f"since been spent on later bills"
            )

        if blockers:
            messages.error(
                request,
                f"Can't reverse this ₹{history.amount:,.0f} payment — a later payment has "
                f"already used part of it ({'; '.join(blockers)}). Reverse the newer "
                f"payment(s) first, newest to oldest."
            )
            return redirect('bulk_payer_detail', pk=pk)

        for job, reversed_amount in jobs_to_reverse:
            job.received_amount = job.received_amount - reversed_amount

            # Recalculate status — reversal always lands on PENDING or
            # PARTIAL, neither of which is a "paid" state, so clear
            # paid_date too (it was only ever set on the BULK_PAID branch).
            if job.received_amount <= 0:
                job.payment_status = 'PENDING'
            else:
                job.payment_status = 'PARTIAL'
            job.paid_date = None

            job.save()

        # Reverse advance balance changes from this payment:
        # remove what this payment stored as advance, restore what it consumed.
        # The pre-flight above guarantees this cannot go negative.
        new_advance = max(Decimal('0'), bulk_payer.advance_balance - advance_stored + advance_used)
        bulk_payer.advance_balance = new_advance
        bulk_payer.save(update_fields=['advance_balance'])

        # Log a full snapshot for the Owner-only Deletion History, then hard-delete.
        DeletionLog.record(
            DeletionLog.ENTITY_BULK_PAYMENT, history,
            user=request.user, reason=reason, amount=history.amount,
            label=f"{bulk_payer.customer_name} · ₹{history.amount:,.0f} payment",
        )
        amount = history.amount
        history.delete()

    messages.success(request, f"Payment of ₹{amount:,.0f} reversed and permanently deleted (logged to Deletion History).")
    return redirect('bulk_payer_detail', pk=pk)
