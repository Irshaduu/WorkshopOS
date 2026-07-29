"""
The notification feed behind the nav bell.

Owner-only, matching who actually receives notifications — an always-empty bell
for Office would be a control that never does anything.

The event catalogue and the `notify()` entry point live in `workshop/notifications.py`
(top-level), the same split as `analysis_engine.py` / `analysis_views.py`: logic
that is testable without a request, views that only fetch and render.
"""

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..decorators import owner_required
from ..models import Notification

PAGE_SIZE = 45


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
        # Public half of the VAPID pair — safe to ship to the browser, and
        # required by PushManager.subscribe(). Empty when push is unconfigured,
        # which the page reports rather than failing.
        'vapid_public_key': settings.VAPID_PUBLIC_KEY,
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
    if request.method == 'POST':
        cleared = Notification.mark_all_read(request.user)
        if cleared:
            messages.success(request, f"Marked {cleared} notification{'s' if cleared != 1 else ''} as read.")

    return redirect('notification_list')
