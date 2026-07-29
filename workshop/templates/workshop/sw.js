// Service worker — Web Push only.
//
// Served from the ORIGIN ROOT (/sw.js), not /static/. A service worker can only
// control pages at or below its own path, so one served from /static/sw.js would
// have scope /static/ and never see a push for the app itself. The view that
// renders this also sends `Service-Worker-Allowed: /`.
//
// Deliberately does no offline caching. The workshop is always online, stale
// cached job cards would be worse than a spinner, and a caching bug here would
// be invisible until someone acted on out-of-date money.

self.addEventListener('install', function (event) {
    // Take over immediately rather than waiting for every tab to close —
    // otherwise a fixed push bug would not reach an installed app that is never
    // fully quit, which on a phone is most of the time.
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(self.clients.claim());
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
            data: { url: payload.url },
            // Collapses repeats of the same event instead of stacking them.
            tag: 'workshopos',
            renotify: true
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
