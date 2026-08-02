from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Lower, Trim
from django.urls import reverse
from .decorators import office_required
from .master_data import (
    rename_spare, rename_concern, spare_usage_count, concern_usage_count,
)
from .models import (
    SparePart, ConcernSolution, JobCardSpareItem, JobCardConcern, DeletionLog,
)


@office_required
def data_cleanup_view(request):
    """
    Data Cleanup Tool for Office/Owner.
    Optimized: uses 2 DB queries total via annotation instead of N+1 loop.
    """
    # Build usage lookup in a single aggregated query 
    # Using Trim() + Lower() ensures "ABS " and "abs" are counted together
    # SHOP rows only: this list drives the free-text shop-purchase autocomplete,
    # so counting warehouse draws here would credit a stock product's usage to a
    # master-list entry that has nothing to do with it.
    spare_usage = {
        item['spare_part_name_clean']: item['count']
        for item in JobCardSpareItem.objects
            .filter(source=JobCardSpareItem.SOURCE_SHOP)
            .annotate(spare_part_name_clean=Lower(Trim('spare_part_name')))
            .values('spare_part_name_clean').annotate(count=Count('id'))
    }
    # Build concern usage lookup
    concern_usage = {
        item['concern_text_clean']: item['count']
        for item in JobCardConcern.objects.annotate(
            concern_text_clean=Lower(Trim('concern_text'))
        ).values('concern_text_clean').annotate(count=Count('id'))
    }

    # Attach usage counts from the lookup dict (no extra DB hits)
    spares = list(SparePart.objects.all().order_by('name'))
    for spare in spares:
        spare.usage_count = spare_usage.get(spare.name.lower(), 0)

    concerns = list(ConcernSolution.objects.all().order_by('concern'))
    for concern in concerns:
        concern.usage_count = concern_usage.get(concern.concern.lower(), 0)

    return render(request, 'workshop/manage/data_cleanup.html', {
        'spares': spares,
        'concerns': concerns,
    })


@office_required
def cleanup_delete_spare(request, spare_id):
    """
    Remove a spare-part name from the master list.

    Safe by construction: job cards store `spare_part_name` as free text, so
    deleting the master row changes no bill, no shop ledger and no report — and
    auto-learn puts the name back the next time someone types it. What it used
    to be was *untraceable*: one POST, no confirmation and no DeletionLog, so an
    entry removed by accident simply stopped existing.

    GET now shows a confirmation carrying the usage count, so nobody deletes a
    name that is on 300 job cards without seeing that first — and, when it is in
    use, the page points at renaming-to-merge instead, which keeps both wordings'
    history. POST logs a snapshot and deletes.
    """
    spare = get_object_or_404(SparePart, pk=spare_id)
    usage = spare_usage_count(spare.name)

    if request.method != 'POST':
        return render(request, 'workshop/manage/master_confirm_delete.html', {
            'kind': 'Spare Part', 'label': spare.name, 'usage': usage,
            'action': reverse('cleanup_delete_spare', args=[spare.pk]),
            'rename_hint': 'rename it to the entry you want to keep from Data Cleanup',
        })

    name = spare.name
    with transaction.atomic():
        DeletionLog.record(
            DeletionLog.ENTITY_MASTER_DATA, spare,
            user=request.user, reason=request.POST.get('reason', '').strip(),
            label=f"Spare part '{name}'",
            extra={'job_card_lines_using_it': usage},
        )
        spare.delete()
    messages.success(request, f"✅ Spare part '{name}' removed from master list (logged to Deletion History).")
    return redirect('data_cleanup')


@office_required
def cleanup_rename_spare(request, spare_id):
    """
    Rename a spare part — also updates all existing job card lines
    that used the old name, so history stays accurate.
    """
    if request.method == 'POST':
        spare = get_object_or_404(SparePart, pk=spare_id)
        new_name = request.POST.get('new_name', '').strip()

        if not new_name:
            messages.error(request, "New name cannot be empty.")
            return redirect('data_cleanup')

        # One implementation, shared with Master Lists' spare edit — see
        # workshop/master_data.py for why that matters.
        old_name = spare.name
        final_name, merged = rename_spare(spare, new_name, user=request.user)
        if merged:
            messages.success(request, f"✅ Merged '{old_name}' → '{final_name}'. All job cards updated.")
        else:
            messages.success(request, f"✅ Renamed '{old_name}' → '{final_name}'. All job cards updated.")

    return redirect('data_cleanup')


@office_required
def cleanup_delete_concern(request, concern_id):
    """Remove a concern from the master list. Same rules as cleanup_delete_spare."""
    concern = get_object_or_404(ConcernSolution, pk=concern_id)
    usage = concern_usage_count(concern.concern)

    if request.method != 'POST':
        return render(request, 'workshop/manage/master_confirm_delete.html', {
            'kind': 'Concern', 'label': concern.concern, 'usage': usage,
            'action': reverse('cleanup_delete_concern', args=[concern.pk]),
            'rename_hint': 'rename it to the entry you want to keep from Data Cleanup',
        })

    text = concern.concern
    with transaction.atomic():
        DeletionLog.record(
            DeletionLog.ENTITY_MASTER_DATA, concern,
            user=request.user, reason=request.POST.get('reason', '').strip(),
            label=f"Concern '{text[:80]}'",
            extra={'job_card_lines_using_it': usage},
        )
        concern.delete()
    messages.success(request, f"✅ Concern '{text[:40]}…' removed from master list (logged to Deletion History).")
    return redirect('data_cleanup')


@office_required
def cleanup_rename_concern(request, concern_id):
    """
    Rename a concern — also updates all existing job card concern lines.
    """
    if request.method == 'POST':
        concern = get_object_or_404(ConcernSolution, pk=concern_id)
        new_text = request.POST.get('new_name', '').strip()

        if not new_text:
            messages.error(request, "New concern text cannot be empty.")
            return redirect('data_cleanup')

        # Shared with Master Lists' concern edit — see workshop/master_data.py.
        _final, merged = rename_concern(concern, new_text, user=request.user)
        if merged:
            messages.success(request, "✅ Merged concern into existing entry. All job cards updated.")
        else:
            messages.success(request, "✅ Concern renamed. All job cards updated.")

    return redirect('data_cleanup')
