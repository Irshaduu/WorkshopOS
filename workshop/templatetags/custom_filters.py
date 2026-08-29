from django import template
from django.contrib.auth.models import Group
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.utils import timezone

register = template.Library()


# -----------------------------------------------------------------------------
# NAVIGATION
# -----------------------------------------------------------------------------
# Every destination that lives behind the Manage/Menu drawer. The top bar's
# Manage button highlights while the current page is one of these, so the user
# can see which pill they arrived through.
#
# This is a LIST in Python, not a chain of `{% if p|slice:… == '/x/' %}` clauses
# in base.html, because that chain is what the highlight used to be — and it had
# silently fallen two sections behind. Both **Salary & Advance** and
# **Estimates** were in the drawer but missing from it, so opening either left
# Manage looking inactive on a page that is only reachable through it. A missing
# entry in a ten-clause boolean is invisible; a missing entry in a list is one
# line to add, and `NavHighlightTests` checks every drawer link is covered.
DRAWER_SECTION_PREFIXES = (
    '/analysis/',
    '/cashbook/',
    '/pending-payments/',
    '/paid-bills/',
    '/salary-advance/',
    '/inventory/',
    '/spare-shops/',
    '/estimates/',
    '/car-profiles/',
    '/master-lists/',
    '/deletion-history/',
    '/manage/',
    '/about/',
)


@register.filter
def is_drawer_section(path):
    """True while the current path belongs to a section reached via the drawer."""
    return any(str(path or '').startswith(prefix) for prefix in DRAWER_SECTION_PREFIXES)


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
def gt(value, arg):
    """Returns True if value > arg (supports Decimal, float, int, str)"""
    try:
        if value is None or value == "":
            return False
        return float(value) > float(arg)
    except (ValueError, TypeError):
        return False


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


# -----------------------------------------------------------------------------
# NOTIFICATIONS
# -----------------------------------------------------------------------------

@register.filter
def notification_glyph(event):
    """
    The Bootstrap Icons class a feed row draws for this event key.

    Read from `workshop/notifications.py`, never restated here: `EVENTS` is the
    one place that answers "what does this thing notify about", and a second
    table of glyphs keyed on the same strings would be free to fall behind it
    the first time an event is added. `glyph_for` already answers an unknown key
    with a neutral default, which matters because a notification outlives the
    catalogue — a row written a fortnight ago carries whatever `event` string
    was current then, and the feed must draw it rather than 500.
    """
    from ..notifications import glyph_for
    return glyph_for(event)


@register.filter
def short_ago(value):
    """
    "now" / "12m" / "5h" / "3d" / "17 Aug" / "17 Aug 25".

    The feed's own age wording, deliberately not `naturaltime` ("2 hours, 14
    minutes ago" is four words for a fact worth two characters) and deliberately
    not the absolute stamp this replaced. `28 Aug, 11:59 p.m.` answered a
    question nobody asks of a notification — what an owner wants to know is
    whether this is from *this morning* or *last week*, and a relative figure
    answers it without arithmetic.

    Same shape as the Live Report's `_age_label()`: one wording for one fact, so
    the two screens cannot disagree about how old something is.
    """
    if not value:
        return ''
    try:
        delta = timezone.now() - value
    except TypeError:
        return ''

    seconds = delta.total_seconds()
    if seconds < 0:
        return 'now'
    if seconds < 60:
        return 'now'
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"

    # Past 24 HOURS, count CALENDAR days rather than 24-hour blocks — so two
    # nights ago reads "2d" and not "45h". Under 24 hours the hour figure is
    # kept and is the better answer: 11pm last night, read at 8pm today, is
    # "21h", which says more than "1d" would.
    #
    # "Yesterday" was tried and reverted. It sits in the same flex row as the
    # headline, so those nine characters came straight off the line the reader
    # is actually reading — enough to wrap a body that otherwise fitted. Two
    # characters say it, in the vocabulary the Live Report already uses.
    today = timezone.localdate()
    then = timezone.localtime(value).date()
    days = (today - then).days
    if days <= 0:
        return 'now'
    if days < 7:
        return f"{days}d"
    # `.lstrip('0')` rather than `%-d`, which is glibc-only and raises on
    # Windows — this project is developed on Windows and deployed on Linux.
    if then.year == today.year:
        return then.strftime('%d %b').lstrip('0')
    # The one output past six characters ("25 Aug 25"), and the only one that
    # can wrap a headline. Reachable solely by an UNREAD row over a year old —
    # read ones are purged at 14 days — which is rare, sits at the bottom of
    # the feed, and is the one case where the year is the point.
    return then.strftime('%d %b %y').lstrip('0')
