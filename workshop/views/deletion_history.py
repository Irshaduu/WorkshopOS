from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator

from ..models import DeletionLog
from ..decorators import owner_required


@owner_required
def deletion_history_list(request):
    """
    Owner-only, read-only history of every permanent deletion in the system.
    Deliberately has NO restore action — reviving stale records would corrupt
    running balances. This replaces the old Trash page.
    """
    entity = request.GET.get('type', '').strip()
    qs = DeletionLog.objects.select_related('deleted_by')
    if entity:
        qs = qs.filter(entity_type=entity)
    page_obj = Paginator(qs, 45).get_page(request.GET.get('page'))
    return render(request, 'workshop/deletion_history/deletion_history_list.html', {
        'page_obj': page_obj,
        'entity': entity,
        'entity_choices': DeletionLog.ENTITY_CHOICES,
    })


@owner_required
def deletion_history_detail(request, pk):
    """Owner-only read-only detail: the full snapshot of one deleted record."""
    log = get_object_or_404(DeletionLog.objects.select_related('deleted_by'), pk=pk)
    snapshot_items = sorted(log.snapshot.items()) if isinstance(log.snapshot, dict) else []
    return render(request, 'workshop/deletion_history/deletion_history_detail.html', {
        'log': log,
        'snapshot_items': snapshot_items,
    })
