from django import template
from django.contrib.auth.models import Group
from datetime import date, timedelta

register = template.Library()

@register.filter
def is_tomorrow(value):
    """Check if a date is tomorrow"""
    if not value:
        return False
    tomorrow = date.today() + timedelta(days=1)
    return value == tomorrow

@register.filter(name='has_group')
def has_group(user, group_name):
    """
    Checks if a user belongs to a specific group.
    Usage in template: {% if request.user|has_group:"Owner" %}
    """
    if not user.is_authenticated:
        return False
        
    # Handling superusers (treat them as having all roles for convenience)
    if user.is_superuser:
        return True
        
    # AUD-0046: Avoid N+1 Group.objects.get queries.
    # user.groups.all() is cached on the user instance after the first call.
    return any(g.name == group_name for g in user.groups.all())

@register.filter
def divide(value, arg):
    """Divides value by arg"""
    try:
        if not arg or float(arg) == 0:
            return 0
        return float(value) / float(arg)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0

@register.filter
def multiply(value, arg):
    """Multiplies value by arg"""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0

@register.filter
def clean_qty(value):
    """Removes trailing zeros from decimal (1.0 -> 1, 1.5 -> 1.5)"""
    if value is None:
        return ""
    try:
        f_val = float(value)
        if f_val == int(f_val):
            return int(f_val)
        return f_val
    except (ValueError, TypeError):
        return value
@register.filter
def get_range(value):
    """
    Returns a range object for looping.
    Example: {% for i in 20|get_range %}
    """
    try:
        return range(int(value))
    except (ValueError, TypeError):
        return []
