"""
Where a page sends you when you leave it.

The installed app declares `"display": "standalone"`, so it carries no address
bar and no browser Back button. Most pages answer that with the shared `.pg-back`
pill and a destination fixed at render time. Three cannot: the printed invoice,
the printed estimate and a spare shop's printed purchase report are standalone
templates that do not extend `base.html`, are opened FROM several different
screens, and have to hand the reader back to the one they actually came from —
so those carry a `?back=` instead.

That value arrives from the URL and ends up in an `href`, which is why it is
checked rather than trusted: without this, a crafted link puts `javascript:` or
another origin behind a button wearing this app's own styling, on a page that
is otherwise about to be handed to a customer.

There were two byte-identical copies of this — `views/billing.py` and
`views/estimate.py` — before a third was needed. Two implementations of one rule
is how they start disagreeing, and a rule about which origins are allowed is not
one to let drift.

⚠ `auth_views._safe_next` is a fourth spelling of the same host check and is
DELIBERATELY not folded in here. It answers a different question — where to send
somebody AFTER they sign in, not where they came from — it reads POST as well as
GET, and it is the more security-sensitive of the two with its own tests. Fold
it in only as its own change, with those tests in front of you.
"""

from django.utils.http import url_has_allowed_host_and_scheme


def safe_return(request, param='back'):
    """
    The `?<param>=` target, but only when it points back into this site.

    Returns None when the parameter is absent, empty, or points anywhere else —
    so every caller can treat None as "no return URL was supplied" and fall back
    to a destination of its own. A page must never end up with no way out at
    all, so the fallback is the caller's job and is not optional.
    """
    target = request.GET.get(param) or ''
    if target and url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return None
