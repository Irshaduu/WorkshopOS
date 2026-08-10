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
                # Capture Real IP (even behind proxies like Cloudflare/Nginx)
                x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
                if x_forwarded_for:
                    ip = x_forwarded_for.split(',')[0].strip()
                else:
                    ip = request.META.get('REMOTE_ADDR')

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
