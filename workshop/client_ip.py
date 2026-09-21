"""
The ONE answer to "what is the visitor's IP address?" (AUD-0107).

Read by the sign-in lockout (`FailedAttempt`), every security alert that names
an address, the password-reset code record, and the Control Hub session list.
It used to be two answers that disagreed: sign-in read `REMOTE_ADDR` alone,
session tracking read the first `X-Forwarded-For` value unchecked.

MEASURED on the Railway test host, 2026-09-21, rather than taken from a forum
post (Railway's own staff have said both "trust the last value" and "trust
the first"):

  * `REMOTE_ADDR` is Railway's INTERNAL proxy (`100.64.0.13`), not the visitor.
    Five wrong passwords from a visitor at 157.51.207.147 raised an alert naming
    100.64.0.13 — so the IP lockout counted every visitor arriving through
    that proxy as ONE, and 20 wrong passwords from any of them would lock out
    all the others, owners included.
  * The first `X-Forwarded-For` value was the real visitor, and a request that
    SENT a faked `X-Forwarded-For` still recorded the real one: Railway sets it.

So the rule is: trust the first `X-Forwarded-For` value ONLY when the
connection itself came from inside the host's own network (a non-global
`REMOTE_ADDR` — Railway's proxy, or 127.0.0.1 in development). A connection
from a PUBLIC address has no proxy in front of it, and its header is
whatever the visitor typed, so it is ignored — the old spoof-protection, kept
exactly where it still applies.

Every value is PARSED, never passed through. A header reading "unknown" or
"1.2.3.4:5678" would have gone straight into `UserSession.ip_address`, an
`inet` column on PostgreSQL, which refuses it (measured on the dev database)
— and that raise is inside the session middleware, so every page would 500
for that visitor. Railway sets the header, so it cannot arrive there; it can
anywhere the app is reached without that proxy.

⚠ Re-measure on ANY change of host, and if Cloudflare is ever put in front
(`GO_LIVE_RUNBOOK.md` §2.5): the proxy chain changes, and the header that
carries the visitor may change with it.
"""
import ipaddress

UNKNOWN = '0.0.0.0'


def _parse(value):
    try:
        return ipaddress.ip_address((value or '').strip())
    except ValueError:
        return None


def client_ip(request):
    """The visitor's IP as a string; `0.0.0.0` when nothing usable arrived."""
    peer = _parse(request.META.get('REMOTE_ADDR'))
    if peer is not None and not peer.is_global:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        visitor = _parse(forwarded.split(',')[0]) if forwarded else None
        if visitor is not None:
            return str(visitor)
    return str(peer) if peer is not None else UNKNOWN
