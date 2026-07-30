from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Count, Q
from django.db.models.functions import Lower, Trim
from .decorators import office_required
from .models import SparePart, ConcernSolution, JobCardSpareItem, JobCardConcern


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
    """Delete a spare part from the master list."""
    if request.method == 'POST':
        spare = get_object_or_404(SparePart, pk=spare_id)
        name = spare.name
        spare.delete()
        messages.success(request, f"✅ Spare part '{name}' removed from master list.")
    return redirect('data_cleanup')


@office_required
def cleanup_rename_spare(request, spare_id):
    """
    Rename a spare part — also updates all existing job card lines
    that used the old name, so history stays accurate.
    """
    if request.method == 'POST':
        spare = get_object_or_404(SparePart, pk=spare_id)
        new_name = request.POST.get('new_name', '').strip().title()

        if not new_name:
            messages.error(request, "New name cannot be empty.")
            return redirect('data_cleanup')

        # Check if target name already exists (merge scenario)
        existing = SparePart.objects.filter(name__iexact=new_name).exclude(pk=spare_id).first()

        # Update the job card lines that used the old name — SHOP rows only.
        #
        # This is the Spare Parts master list, which only feeds the free-text
        # shop-purchase autocomplete. An inventory draw takes its name from the
        # `Item` it points at, so renaming it from here would put a job card's
        # displayed name out of step with the product it is actually linked to.
        # Rename a stock product on its supplier catalog instead.
        #
        # `.update()` deliberately bypasses signals, which is safe *because* this
        # is scoped to shop rows: they move no stock. It would not be safe on an
        # inventory draw.
        old_name = spare.name
        JobCardSpareItem.objects.filter(
            source=JobCardSpareItem.SOURCE_SHOP,
            spare_part_name__iexact=old_name,
        ).update(spare_part_name=new_name)

        if existing:
            # Merge: delete the typo entry, keep the correct one
            spare.delete()
            messages.success(request, f"✅ Merged '{old_name}' → '{new_name}'. All job cards updated.")
        else:
            # Simple rename
            spare.name = new_name
            spare.save()
            messages.success(request, f"✅ Renamed '{old_name}' → '{new_name}'. All job cards updated.")

    return redirect('data_cleanup')


@office_required
def cleanup_delete_concern(request, concern_id):
    """Delete a concern from the master list."""
    if request.method == 'POST':
        concern = get_object_or_404(ConcernSolution, pk=concern_id)
        name = concern.concern[:40]
        concern.delete()
        messages.success(request, f"✅ Concern '{name}...' removed from master list.")
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

        old_text = concern.concern

        # Update all job card concerns that used the old text
        JobCardConcern.objects.filter(concern_text__iexact=old_text).update(
            concern_text=new_text
        )

        # Check for merge (same concern already exists)
        existing = ConcernSolution.objects.filter(
            concern__iexact=new_text
        ).exclude(pk=concern_id).first()

        if existing:
            concern.delete()
            messages.success(request, f"✅ Merged concern into existing entry. All job cards updated.")
        else:
            concern.concern = new_text
            concern.save()
            messages.success(request, f"✅ Concern renamed. All job cards updated.")

    return redirect('data_cleanup')
