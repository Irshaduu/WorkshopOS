"""
Web Push endpoints: the service worker, and subscribe/unsubscribe.

Sending lives in `workshop/push.py` (top-level); this module is only the HTTP
surface, the same split as `analysis_engine.py` / `analysis_views.py`.
"""

import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.http import require_POST

from ..decorators import owner_required
from ..models import PushSubscription


def service_worker(request):
    """
    Serve the service worker from the origin root.

    It cannot be a plain static file: WhiteNoise would serve it under /static/,
    and a worker's scope is limited to its own directory — /static/sw.js could
    only ever control pages under /static/. Rendering it from a view puts it at
    /sw.js, and `Service-Worker-Allowed` states the scope explicitly so the
    browser is not relying on path inference alone.

    Deliberately public: a browser fetches this before anyone signs in, and the
    file contains no secrets.
    """
    response = render(
        request,
        'workshop/sw.js',
        {
            'icon_url': static('images/icons/icon-192.png'),
            'badge_url': static('images/icons/icon-192.png'),
        },
        content_type='application/javascript',
    )
    response['Service-Worker-Allowed'] = '/'
    # Never cache the worker itself, or a fix ships and nobody receives it.
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@owner_required
@require_POST
def push_subscribe(request):
    """
    Record this browser's push endpoint.

    Owner-only, matching who receives notifications at all. Keyed on `endpoint`,
    which the browser regenerates whenever permission is reset or the app is
    reinstalled — so re-subscribing on the same device updates the existing row
    rather than accumulating duplicates, and a re-subscribe also clears any
    accumulated failures.
    """
    try:
        payload = json.loads(request.body or '{}')
        endpoint = payload['endpoint']
        keys = payload['keys']
        p256dh, auth = keys['p256dh'], keys['auth']
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Malformed subscription.'}, status=400)

    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user,
            'p256dh': p256dh,
            'auth': auth,
            'user_agent': request.META.get('HTTP_USER_AGENT', ''),
            'failure_count': 0,
        },
    )
    return JsonResponse({'ok': True})


@owner_required
@require_POST
def push_unsubscribe(request):
    """
    Forget this browser.

    Scoped to `user=request.user`: one owner must not be able to silence the
    other's phone by posting their endpoint.
    """
    try:
        endpoint = json.loads(request.body or '{}')['endpoint']
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Malformed request.'}, status=400)

    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({'ok': True})
