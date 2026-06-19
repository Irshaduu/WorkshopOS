from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied

# -----------------------------------------------------------------------------
# ROLE-BASED ACCESS CONTROL (RBAC) DECORATORS
# -----------------------------------------------------------------------------

def is_owner(user):
    return user.groups.filter(name='Owner').exists() or user.is_superuser

def is_office_or_owner(user):
    return user.groups.filter(name__in=['Office', 'Owner']).exists() or user.is_superuser

def is_floor_office_owner(user):
    return user.groups.filter(name__in=['Floor', 'Office', 'Owner']).exists() or user.is_superuser

# Decorator for views only accessible by the Owner
def owner_required(function=None, redirect_field_name=None, login_url='/admin-login/'):
    actual_decorator = user_passes_test(
        is_owner,
        login_url=login_url,
        redirect_field_name=redirect_field_name
    )
    if function:
        return actual_decorator(function)
    return actual_decorator

# Decorator for views accessible by Office staff and Owners (Financial/Invoicing)
def office_required(function=None, redirect_field_name=None, login_url='/admin-login/'):
    actual_decorator = user_passes_test(
        is_office_or_owner,
        login_url=login_url,
        redirect_field_name=redirect_field_name
    )
    if function:
        return actual_decorator(function)
    return actual_decorator

# Decorator for views accessible by everyone (Floor, Office, Owner) like Job Cards
def staff_required(function=None, redirect_field_name=None, login_url='/login/'):
    actual_decorator = user_passes_test(
        is_floor_office_owner,
        login_url=login_url,
        redirect_field_name=redirect_field_name
    )
    if function:
        return actual_decorator(function)
    return actual_decorator
