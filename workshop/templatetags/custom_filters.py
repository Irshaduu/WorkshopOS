from django import template
from django.contrib.auth.models import Group
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.utils import timezone

register = template.Library()

@register.filter
def is_tomorrow(value):
    """Check if a date is tomorrow"""
    if not value:
        return False
    tomorrow = timezone.localdate() + timedelta(days=1)  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
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
    """Display a quantity without trailing zeros: 1.00 -> 1, 1.50 -> 1.5, 5.50 -> 5.5.

    Works entirely in Decimal so it never reintroduces float rounding drift
    (quantities are stored as DecimalField for exactness).
    """
    if value is None or value == "":
        return ""
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value
    # Whole numbers -> plain int ("1", "10"); fractional -> stripped Decimal ("1.5")
    if d == d.to_integral_value():
        return int(d)
    return d.normalize()

# Backwards/forwards-friendly alias
register.filter('qty', clean_qty)
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
