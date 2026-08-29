// Service worker — Web Push only.
//
// Served from the ORIGIN ROOT (/sw.js), not /static/. A service worker can only
// control pages at or below its own path, so one served from /static/sw.js would
// have scope /static/ and never see a push for the app itself. The view that
// renders this also sends `Service-Worker-Allowed: /`.
//
// Deliberately does no offline caching. The workshop is always online, stale
// cached job cards would be worse than a spinner, and a caching bug here would
// be invisible until someone acted on out-of-date money. The `fetch` handler
// below caches nothing and must never start — see the note above it.

self.addEventListener('install', function (event) {
    // Take over immediately rather than waiting for every tab to close —
    // otherwise a fixed push bug would not reach an installed app that is never
    // fully quit, which on a phone is most of the time.
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(self.clients.claim());
});

// A `fetch` handler has to EXIST for Chrome to offer the install prompt.
//
// Chrome dropped the service-worker requirement for installing from the menu
// (108 on mobile, 112 on desktop), but the code path that fires
// `beforeinstallprompt` still checks for a fetch handler — and that event is
// what the app's own "Install Formula D" banner listens for. Without this the
// banner could only ever appear on iOS, which has no such event and is handled
// by a separate branch in base.html. That is exactly what was happening: the
// banner looked like a hosting problem when it was this.
//
// It caches NOTHING, and must not start. The rule at the top of this file
// stands: no app response is ever stored, so no screen can show yesterday's
// money. All this does is pass the request through and, when the network is
// genuinely gone, answer a page navigation with a plain sentence instead of the
// browser's dinosaur — so somebody on bad workshop wifi is told what happened
// rather than being shown a broken app.
var OFFLINE_PAGE =
    '<!doctype html><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>No connection</title>' +
    '<style>body{font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;' +
    'color:#1e293b;display:flex;min-height:100vh;margin:0;align-items:center;' +
    'justify-content:center;text-align:center;padding:24px}' +
    'h1{font-size:1.1rem;margin:0 0 8px}p{color:#64748b;font-size:0.9rem;margin:0}</style>' +
    '<div><h1>No connection</h1><p>Formula D needs the network. ' +
    'Check your network and refresh.</p></div>';

self.addEventListener('fetch', function (event) {
    if (event.request.mode !== 'navigate') {
        return; // everything else takes the browser's default path, untouched
    }
    event.respondWith(
        fetch(event.request).catch(function () {
            return new Response(OFFLINE_PAGE, {
                status: 503,
                headers: { 'Content-Type': 'text/html; charset=utf-8' }
            });
        })
    );
});

self.addEventListener('push', function (event) {
    var payload = { title: 'WorkshopOS', body: '', url: '/notifications/' };

    if (event.data) {
        try {
            payload = Object.assign(payload, event.data.json());
        } catch (e) {
            // A push that isn't our JSON still deserves to surface — showing
            // something generic beats swallowing it. Browsers also penalise a
            // push handler that resolves without showing a notification.
            payload.body = event.data.text();
        }
    }

    event.waitUntil(
        self.registration.showNotification(payload.title, {
            body: payload.body,
            icon: '{{ icon_url }}',
            badge: '{{ badge_url }}',
            data: { url: payload.url }
            // NO `tag`, deliberately — and it used to carry a constant one
            // (`'workshopos'`) whose comment claimed it "collapses repeats of
            // the same event". A tag does not work that way: it is a REPLACE
            // key, and a single constant one meant every push replaced the
            // one before it. Two deletions a minute apart showed as one; a
            // staff sign-in landing after a ₹1,00,000 record deletion wiped
            // the deletion off the lock screen before anybody read it.
            //
            // Only CRITICAL events reach here — money moved unexpectedly,
            // something destroyed, someone got in — and none of them
            // supersede any other, so there is nothing here that a later
            // alert is entitled to replace. Untagged notifications stack,
            // which at this workshop's volume (a handful a day) is the right
            // trade: seeing two is recoverable, missing one is not.
            //
            // `renotify` went with it — it is only meaningful alongside a
            // tag, and an untagged notification alerts anyway.
        })
    );
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    var target = (event.notification.data && event.notification.data.url) || '/notifications/';

    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
            // Reuse an open window if there is one — launching a second copy of
            // an installed app is jarring and loses whatever was on screen.
            for (var i = 0; i < list.length; i++) {
                if ('focus' in list[i]) {
                    list[i].navigate(target);
                    return list[i].focus();
                }
            }
            if (self.clients.openWindow) {
                return self.clients.openWindow(target);
            }
        })
    );
});
