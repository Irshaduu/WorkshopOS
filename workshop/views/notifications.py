"""
The notification feed behind the nav bell.

Owner-only, matching who actually receives notifications — an always-empty bell
for Office would be a control that never does anything.

The event catalogue and the `notify()` entry point live in `workshop/notifications.py`
(top-level), the same split as `analysis_engine.py` / `analysis_views.py`: logic
that is testable without a request, views that only fetch and render.
"""

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone

from ..decorators import owner_required
from ..models import Notification

PAGE_SIZE = 45

# How many the floating panel shows. Small on purpose: the panel is a glance,
# not a workspace, and it must stay instant with thousands of rows behind it —
# "View all" is one tap away for the rest.
PANEL_SIZE = 10


@owner_required
def notification_list(request):
    """
    The feed. Newest first, unread called out.

    Sweeps expired notifications on the way in — the same visit-time cleanup
    `manage_dashboard` does for ghost sessions, which keeps the table from
    growing without adding a scheduled job to operate.
    """
    Notification.purge_old()

    queryset = Notification.objects.filter(
        recipient=request.user
    ).select_related('actor')

    page = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))

    return render(request, 'workshop/notifications/notification_list.html', {
        'page_obj': page,
        'unread_total': Notification.unread_count(request.user),
        'retention_days': Notification.RETENTION_DAYS,
    })


@owner_required
def notification_panel(request):
    """
    The floating panel's contents, fetched on open rather than rendered into
    every page.

    That laziness is the point: the bell appears on every screen in the app, and
    baking ten rows plus their actors into every response would cost a join on
    pages that have nothing to do with notifications. The unread *count* is
    cheap enough to carry in the context processor; the list is not.
    """
    notes = list(
        Notification.objects
        .filter(recipient=request.user)
        .select_related('actor')[:PANEL_SIZE]
    )

    # No `unread_total`. `_panel_items.html` never referenced it and neither
    # does notifications.js, which reads the badge it already has — so it was a
    # COUNT query paid on every open of the one control that is meant to feel
    # instant, for a number nothing rendered.
    return render(request, 'workshop/notifications/_panel_items.html', {
        'notes': notes,
    })


@owner_required
@require_POST
def notification_mark_read(request, pk):
    """
    Mark one read without navigating — used by the panel, which stays open.

    Separate from `notification_open` because that one redirects; an AJAX caller
    following a 302 into a full HTML page would be wasteful and confusing.
    """
    updated = Notification.objects.filter(
        pk=pk, recipient=request.user, read_at__isnull=True,
    ).update(read_at=timezone.now())

    return JsonResponse({
        'ok': True,
        'changed': bool(updated),
        'unread': Notification.unread_count(request.user),
    })


@owner_required
def notification_open(request, pk):
    """
    Mark one read and follow it to whatever it is about.

    Scoped to `recipient=request.user`, so one owner cannot mark the other's
    copy read — the rows are fanned out per person and each owns theirs.
    """
    note = get_object_or_404(Notification, pk=pk, recipient=request.user)

    if note.read_at is None:
        note.read_at = timezone.now()
        note.save(update_fields=['read_at'])

    # A notification often outlives its subject (most of them announce a
    # deletion), so an empty url is normal, not a bug — fall back to the feed.
    return redirect(note.url or reverse('notification_list'))


@owner_required
def notification_mark_all_read(request):
    """Clear the badge in one action. POST-only — it changes state."""
    if request.method != 'POST':
        return redirect('notification_list')

    cleared = Notification.mark_all_read(request.user)

    # The panel clears in place rather than navigating; only the full page needs
    # a flash message and a redirect.
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'cleared': cleared, 'unread': 0})

    if cleared:
        messages.success(request, f"Marked {cleared} notification{'s' if cleared != 1 else ''} as read.")
    return redirect('notification_list')
