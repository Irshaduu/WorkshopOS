from django import template
from django.contrib.auth.models import Group
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
def _to_decimal(value):
    """Best-effort Decimal, or None if the value isn't numeric."""
    if value is None or value == "":
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


@register.filter
def inr(value):
    """
    Indian digit grouping: 4523678 -> '45,23,678'.

    Django's humanize `intcomma` groups in thousands (4,523,678), which is not
    how anyone here reads a rupee figure. Last three digits, then pairs.
    Rounds to whole rupees — these are display figures, not ledger lines.
    """
    d = _to_decimal(value)
    if d is None:
        return value
    neg = d < 0
    whole = str(int(abs(d).quantize(Decimal('1'))))
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ','.join(parts) + ',' + tail
    return ('-' if neg else '') + whole


@register.filter
def abs_value(value):
    """Magnitude only — lets a template render its own '−' beside the ₹ sign."""
    d = _to_decimal(value)
    return value if d is None else abs(d)


@register.filter
def inr_amount(value):
    """
    Indian-grouped rupees that keep the paise only when there are any.

    `inr` rounds to whole rupees, which is right for a headline and wrong for a
    ledger line — a row printed as ₹500 when ₹499.50 left the till is a figure
    that cannot be reconciled against the bill it came from. Whole amounts
    still read as '1,200', not '1,200.00'.
    """
    d = _to_decimal(value)
    if d is None:
        return value
    neg = d < 0
    a = abs(d)
    whole = int(a)
    paise = (a - whole).quantize(Decimal('0.01'))
    out = inr(whole)
    if paise:
        out += f".{int(paise * 100):02d}"
    return ('-' if neg else '') + out


@register.filter
def inr_exact(value):
    """
    Indian-grouped rupees with the paise ALWAYS shown: 17800 -> '17,800.00'.

    For the printed invoice. `inr` rounds to whole rupees and `inr_amount` hides
    a '.00', both of which are right on a screen and wrong in a money column a
    customer adds up by eye — '1,200' beside '1,919.00' reads as a different
    kind of number. Empty for None, so a part fitted but not yet costed prints a
    blank cell rather than '₹0.00' against a part that was not free.
    """
    d = _to_decimal(value)
    if d is None:
        return ''
    neg = d < 0
    a = abs(d).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    whole = int(a)
    paise = int((a - whole) * 100)
    return f"{'-' if neg else ''}{inr(whole)}.{paise:02d}"


@register.filter
def inr_compact(value):
    """
    Short rupee figure for hero numbers on a phone: '45.2L', '4.57Cr', '8,500'.

    Owners read the Profit page on mobile, where a nine-digit number either
    wraps or shrinks to nothing. Lakh/crore is how the figure gets said out
    loud anyway. The exact number is always shown next to it, never replaced.
    """
    d = _to_decimal(value)
    if d is None:
        return value
    neg = d < 0
    a = abs(d)
    if a >= Decimal('10000000'):          # >= 1 crore
        out = f"{a / Decimal('10000000'):.2f}".rstrip('0').rstrip('.') + 'Cr'
    elif a >= Decimal('100000'):          # >= 1 lakh
        out = f"{a / Decimal('100000'):.2f}".rstrip('0').rstrip('.') + 'L'
    else:
        out = inr(a)
    return ('-' if neg else '') + out


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
