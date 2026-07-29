"""
Template context shared by every page.

Kept deliberately thin: a context processor runs on *every* request, and this
database is a network hop away (Singapore, ~75 ms a query — see CLAUDE.md), so
anything added here is paid for on every page load by every user.
"""

from .models import Notification


def notifications(request):
    """
    Unread count for the nav bell.

    Costs nothing for anyone who cannot see the bell. Only owners currently
    receive notifications, so only owners are charged the count query — Floor
    and Office short-circuit before it runs.

    Group membership is read through `user.groups.all()` rather than a filtered
    query, because Django caches that on the user instance and `base.html` is
    about to call `has_group` anyway; a `.filter(...).exists()` here would be a
    second round trip for the same fact.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}

    is_owner = user.is_superuser or any(g.name == 'Owner' for g in user.groups.all())
    if not is_owner:
        return {}

    return {'unread_notifications': Notification.unread_count(user)}
