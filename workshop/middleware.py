from .client_ip import client_ip
from .models import UserSession
from django.utils import timezone

class SessionTrackingMiddleware:
    """
    Background monitor that tracks which devices are accessing the HQ Portal.
    Updates the 'Last Active' timestamp and device metadata on every request.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # We only track sessions for authenticated users (Sahad/Rijas)
        if request.user.is_authenticated:
            session_key = request.session.session_key
            
            # Ensure session is saved if it's new
            if not session_key:
                request.session.save()
                session_key = request.session.session_key
            
            if session_key:
                # The one IP rule (AUD-0107). It used to read the first
                # X-Forwarded-For value unchecked, and a non-IP value there is
                # refused by Postgres's inet column — every page would 500.
                ip = client_ip(request)

                now = timezone.now()
                from datetime import timedelta
                cooldown = timedelta(minutes=5)
                
                existing = UserSession.objects.filter(session_key=session_key).first()
                if not existing or (now - existing.last_activity) >= cooldown:
                    # Update or Create the session record
                    UserSession.objects.update_or_create(
                        session_key=session_key,
                        defaults={
                            'user': request.user,
                            'ip_address': ip,
                            'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                            'last_activity': now
                        }
                    )

        return response


class NoIndexMiddleware:
    """
    Tell search engines not to index anything this app serves.

    A middleware rather than a `<meta name="robots">` tag because the tag has
    to be added to every template that does not extend `base.html` — the
    printed invoice, the printed estimate and the four signed-out auth pages
    are all standalone — and the day someone adds a fifth, nothing fails.
    A header covers every response, including `robots.txt` and `sw.js`, with
    nothing to remember.

    This is NOT a security control and must never be treated as one. Every
    page worth protecting is behind a login; this only keeps the workshop's
    internal system out of search results, which is tidiness, not defence.
    See the note on `robots.txt` in urls.py for why both exist.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['X-Robots-Tag'] = 'noindex, nofollow'
        return response


CONTENT_SECURITY_POLICY = (
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


class ContentSecurityPolicyMiddleware:
    """
    The part of a Content-Security-Policy that cannot break this app (AUD-0043).

    Four directives, and each shuts a door this app never uses:

      object-src 'none'       no <object>/<embed> — a plugin is never needed here
      base-uri 'self'         an injected <base> cannot re-point every relative
                              link and form on the page at another site
      form-action 'self'      a form can only post to this app — an injected
                              form cannot send a typed password somewhere else
      frame-ancestors 'none'  no site may frame this one (clickjacking). The
                              modern form of the X-Frame-Options: DENY that
                              `XFrameOptionsMiddleware` already sends.

    ⚠ IT DELIBERATELY SAYS NOTHING ABOUT SCRIPTS, STYLES, IMAGES OR FETCHES, and
    that is why it is safe to enforce. The frontend is inline JS and CSS by
    design (CLAUDE.md, "Frontend architecture"), so a `script-src` without
    'unsafe-inline' would stop every page working; and photos load from and
    upload to the bucket's own origin, so an `img-src` / `connect-src` that
    forgot it would break photos with no error. Blocking injected SCRIPTS needs
    the inline JS and the 73 inline handlers moved out first — declined
    pre-ship, and not something to reach for from here.

    ⚠ Before adding an <object>, <embed>, <base> or <iframe> of our own pages,
    or a form that posts off-site: this policy refuses it, silently, in the
    browser. `test_content_security_policy.py` fails first.

    A header on every response, the NoIndexMiddleware reasoning: the printed
    invoice, the estimate and the signed-out pages do not extend `base.html`.
    Static files never reach it (WhiteNoise answers them first), and a policy
    only governs documents and workers, so nothing is lost.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if 'Content-Security-Policy' not in response:
            response['Content-Security-Policy'] = CONTENT_SECURITY_POLICY
        return response


class NoStoreMiddleware:
    """
    Keep signed-in pages out of the browser's cache, so Back cannot un-log-out.

    Logging out flushes the session, and the very next request would be bounced
    to the sign-in page — but the browser never made that request. Every page
    the user had already visited sat in the back/forward cache, so pressing Back
    after signing out re-displayed the dashboard, a customer's bill or the
    Profit page from memory, fully rendered, on a device that is now in somebody
    else's hands. That is the entire threat model of the logout button on a
    shared workshop laptop.

    Nothing server-side can fix this after the fact: the page was already sent.
    The only lever is telling the browser at the time not to keep it, which is
    what `no-store` does. `must-revalidate` and the two legacy headers are for
    intermediaries and older browsers that honour one but not the others.

    **Scoped to authenticated responses.** A signed-out page holds nothing worth
    protecting, and leaving the login form cacheable costs nothing. `request.user`
    is available because this runs after `AuthenticationMiddleware`; keep it
    there if the MIDDLEWARE order is ever rearranged.

    Static assets never reach here — WhiteNoise sits earlier in the chain and
    returns them without calling the rest of it — so this cannot accidentally
    make the CSS and JS uncacheable.

    The cost is real and accepted: Back now re-fetches instead of restoring
    instantly. On a workshop LAN that is a page load; the alternative is a
    signed-out phone still showing the month's takings.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if getattr(request, 'user', None) is not None and request.user.is_authenticated:
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'

        return response
