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


def is_owner(user):
    return user.groups.filter(name='Owner').exists() or user.is_superuser


def is_office_or_owner(user):
    return user.groups.filter(name__in=['Office', 'Owner']).exists() or user.is_superuser


def is_floor_office_owner(user):
    return user.groups.filter(name__in=['Floor', 'Office', 'Owner']).exists() or user.is_superuser


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
