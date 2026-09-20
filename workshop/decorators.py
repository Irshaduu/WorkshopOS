from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

# -----------------------------------------------------------------------------
# ROLE-BASED ACCESS CONTROL (RBAC) DECORATORS
#
# Three Django auth Groups back these: Owner, Office, Floor. Superusers pass
# every check. Use these on any new view rather than rolling a custom permission
# check, and keep the drawer entry in `base.html` in step whenever one changes.
#
# Two different outcomes, deliberately:
#   - not signed in  -> redirect to the sign-in page, carrying ?next=
#   - signed in but wrong role -> 403 (templates/403.html)
#
# These used to be the same thing. `user_passes_test` redirects to a login form
# in both cases, so an Office user opening an Owner-only page was bounced to a
# login screen *while already signed in* — visually identical to having been
# logged out, and it reads as the app being broken rather than as a permission
# boundary. Anonymous users still get the login page, because for them that is
# the correct next step.
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# WHAT ROLE IS THIS USER? — ONE ANSWER, READ ONCE PER REQUEST
# -----------------------------------------------------------------------------

OFFICE_OR_OWNER = frozenset({'Office', 'Owner'})
EVERY_ROLE = frozenset({'Floor', 'Office', 'Owner'})


def role_names(user):
    """
    Every group name this account belongs to.

    **THE ONE PLACE THAT ASKS THE DATABASE WHAT SOMEBODY'S ROLE IS.** Both
    halves of the RBAC system read it — the decorators below, and the
    `has_group` template filter — so a page can never enforce one rule and draw
    another. They were two separate implementations of one rule, and it is the
    rule that decides who sees money.

    ⚠ **IT IS CACHED ON THE USER INSTANCE, AND THAT IS THE WHOLE POINT.**
    `user.groups.filter(...).exists()` and `user.groups.all()` are BOTH a fresh
    query on every call — the second looks cached and is not. A related
    manager's `.all()` builds a new queryset each time and only reuses a result
    when `prefetch_related` put one there, and nothing prefetches
    `request.user`. Measured before this existed: ten calls, ten queries, by
    either route; and **32 of the job card form's 48 queries were this one
    question**, because that template alone calls `has_group` 19 times.

    `request.user` is ONE object for the whole request, so caching on it makes
    the answer cost one query however many times it is asked. Measured after: 1.

    ⚠ **A SUPERUSER COSTS NOTHING AT ALL** — every caller tests `is_superuser`
    first and never reaches here. Both owner accounts in this workshop are
    superusers, which is the case that actually matters.

    ⚠ **THE CACHE CLEARS ITSELF WHEN MEMBERSHIP MOVES** — `_forget_roles`
    below, wired in `WorkshopConfig.ready()`. Without it, code that adds a group
    and then re-asks on the SAME instance reads the old answer. Not
    hypothetical: `test_has_group_filter` does exactly that, and it is the one
    thing that makes an instance cache safe rather than merely fast.

    Management commands are deliberately NOT routed through this. They run once,
    outside any request, against accounts they have just loaded — there is
    nothing to amortise, and `sync_owner_identity` is go-live tooling that gains
    nothing from being coupled to a cache.
    """
    if not getattr(user, 'is_authenticated', False):
        return frozenset()
    names = getattr(user, '_role_names', None)
    if names is None:
        names = frozenset(user.groups.values_list('name', flat=True))
        user._role_names = names
    return names


def _forget_roles(sender, instance, action, reverse=False, **kwargs):
    """Drop the cached answer the moment somebody's group membership changes.

    Only the FORWARD direction (`user.groups.add(...)`) is handled, because that
    is the one that hands us the very instance the answer was cached on — and it
    is the only direction this codebase writes. A reverse write
    (`group.user_set.add(...)`) reaches us with the Group instead, and the user
    objects it touched are not ours to reach; if one is ever added, cache-bust it
    there or write it forwards.
    """
    if reverse or action not in ('post_add', 'post_remove', 'post_clear'):
        return
    try:
        del instance._role_names
    except AttributeError:
        pass


ROLE_ORDER = ('Owner', 'Office', 'Floor')


def role_of(user):
    """This account's role as ONE WORD, highest first, or empty for none.

    Not an access check — it is what a notification body prints, so an owner
    reading "'floor2' login deleted" on a phone knows what was taken away
    without opening Control Hub. It was written twice, once in `auth_views` for
    the sign-in alert and once in `management_views` for the account alerts,
    and the second took whichever group the database happened to return first.

    ⚠ It answers about the ACCOUNT, so it deliberately does NOT read
    `is_superuser`: an owner who is only a superuser is in no named role, and
    "Owner" would be an invention. `is_owner` is the access question, and that
    one does read the flag.
    """
    names = role_names(user)
    for role in ROLE_ORDER:
        if role in names:
            return role
    return ''


def is_owner(user):
    """Owner, or a superuser."""
    return user.is_superuser or 'Owner' in role_names(user)


def owner_accounts():
    """
    Every live owner account — `is_owner` asked of the whole table at once.

    ⚠ THE EITHER-OR IS LOAD-BEARING, and it is the same one `is_owner` uses
    one line above. Group membership ALONE is the narrower test and it has
    broken twice in practice: a reseeded or copied database routinely leaves
    both owner accounts `is_superuser=True` with an **empty** Owner group until
    somebody remembers `sync_owner_identity --yes`, and for that whole window a
    group-only query returns nobody. `notifications._recipients` went silently
    dark that way on two demo deployments; here it would draw a withdrawals
    page with no owners on it.

    Ordered by the name actually shown, so the cards and the picker cannot
    disagree about which owner comes first.
    """
    from django.contrib.auth.models import User
    from django.db.models import Q
    return (User.objects
            .filter(Q(is_superuser=True) | Q(groups__name='Owner'), is_active=True)
            .distinct()
            .order_by('first_name', 'username'))


def is_office_or_owner(user):
    """Office staff and Owners — the financial and invoicing surfaces."""
    return user.is_superuser or bool(role_names(user) & OFFICE_OR_OWNER)


def is_floor_office_owner(user):
    """Anybody with a role at all."""
    return user.is_superuser or bool(role_names(user) & EVERY_ROLE)


def _role_required(test_func, login_url):
    """Build a decorator that sends anonymous users to `login_url` and raises
    PermissionDenied for signed-in users who lack the role."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url)
            if not test_func(request.user):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


# `redirect_field_name` is accepted and ignored — it exists so the historical
# call signature keeps working. The field name is always Django's default
# ("next"), which is what the login template posts back.
#
# All three send anonymous visitors to `/login/`. Owner and Office pages used to
# bounce to `/admin-login/`, which still works as a redirect but costs a second
# hop — and, more to the point, told anyone who probed an owner URL that a
# separate admin door existed. There is one door now.
def owner_required(function=None, redirect_field_name=None, login_url='/login/'):
    """Owner (or superuser) only."""
    actual_decorator = _role_required(is_owner, login_url)
    if function:
        return actual_decorator(function)
    return actual_decorator


def office_required(function=None, redirect_field_name=None, login_url='/login/'):
    """Office staff and Owners — financial and invoicing surfaces."""
    actual_decorator = _role_required(is_office_or_owner, login_url)
    if function:
        return actual_decorator(function)
    return actual_decorator


def staff_required(function=None, redirect_field_name=None, login_url='/login/'):
    """Everyone with a role: Floor, Office, Owner."""
    actual_decorator = _role_required(is_floor_office_owner, login_url)
    if function:
        return actual_decorator(function)
    return actual_decorator
