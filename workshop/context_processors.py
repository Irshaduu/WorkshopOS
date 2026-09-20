"""
Template context shared by every page.

Kept deliberately thin: a context processor runs on *every* request, and this
database is a network hop away (Singapore, ~75 ms a query — see CLAUDE.md), so
anything added here is paid for on every page load by every user.
"""

from django.conf import settings

from .decorators import is_owner
from .models import Notification


def notifications(request):
    """
    Unread count for the nav bell.

    Costs nothing for anyone who cannot see the bell. Only owners currently
    receive notifications, so only owners are charged the count query — Floor
    and Office short-circuit before it runs.

    ⚠ Group membership goes through `decorators.is_owner` — the one answer the
    RBAC decorators and the `has_group` filter also read, cached on the user
    instance for the life of the request. This used to be a hand-rolled
    `any(g.name == 'Owner' for g in user.groups.all())` under a comment claiming
    Django cached that. **It does not**, so this ran a query of its own and then
    `base.html`'s own `has_group` calls paid for the same fact over again. Being
    the first thing to ask, this now warms the cache for the whole page.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}

    if not is_owner(user):
        return {}

    return {
        'unread_notifications': Notification.unread_count(user),
        # The push on/silent toggle lives in the nav panel, so its public key has
        # to travel with the bell rather than with one view. Reading a setting is
        # free — no query, unlike the count above.
        'vapid_public_key': settings.VAPID_PUBLIC_KEY,
    }
