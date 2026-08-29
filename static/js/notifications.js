// Floating notification panel + the device on/silent toggle in its header.
//
// The panel's contents are fetched on first open, not baked into every page:
// the bell is on every screen, and ten rows plus their actors is a join that
// most pages have no reason to pay for.
(function () {
    'use strict';

    var bell = document.getElementById('notifBell');
    if (!bell) { return; }                       // Not an owner — no bell, nothing to do.

    var panel = document.getElementById('notifPanel');
    var body = document.getElementById('notifPanelBody');
    var backdrop = document.getElementById('notifBackdrop');
    var badge = document.getElementById('notifBadge');
    var bellIcon = document.getElementById('notifBellIcon');
    var markAll = document.getElementById('notifMarkAll');
    var pushBtn = document.getElementById('pushToggle');
    var pushIcon = document.getElementById('pushToggleIcon');
    var pushCta = document.getElementById('notifPushCta');
    var pushCtaText = document.getElementById('notifPushCtaText');

    var panelUrl = '/notifications/panel/';
    var markAllUrl = '/notifications/read-all/';

    // What the panel currently holds, and how old it is. `loadedUnread` is what
    // the badge said at the moment it was rendered — if the badge has moved
    // since, the panel is definitely stale and there is no point trusting it.
    var loadedAt = 0;
    var loadedUnread = null;
    var inFlight = false;
    var STALE_MS = 45000;

    function csrf() {
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        if (m) { return decodeURIComponent(m[1]); }
        var input = document.querySelector('input[name=csrfmiddlewaretoken]');
        return input ? input.value : '';
    }

    function post(url, extraHeaders) {
        var headers = { 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' };
        if (extraHeaders) {
            Object.keys(extraHeaders).forEach(function (k) { headers[k] = extraHeaders[k]; });
        }
        return fetch(url, { method: 'POST', headers: headers });
    }

    // ---- badge -------------------------------------------------------
    function setUnread(count) {
        if (!badge) { return; }
        if (count > 0) {
            badge.textContent = count > 99 ? '99+' : String(count);
            badge.hidden = false;
            if (bellIcon) { bellIcon.className = 'bi bi-bell-fill'; }
        } else {
            badge.hidden = true;
            if (bellIcon) { bellIcon.className = 'bi bi-bell'; }
        }
    }

    // ---- panel -------------------------------------------------------
    function currentUnread() {
        if (!badge || badge.hidden) { return 0; }
        return badge.textContent.trim();
    }

    function isFresh() {
        return loadedAt > 0 &&
            (Date.now() - loadedAt) < STALE_MS &&
            String(loadedUnread) === String(currentUnread());
    }

    function loadPanel(force) {
        if (inFlight) { return; }
        if (!force && isFresh()) { return; }
        inFlight = true;

        var unreadAtRequest = currentUnread();
        fetch(panelUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (res) { return res.ok ? res.text() : Promise.reject(res.status); })
            .then(function (html) {
                body.innerHTML = html;
                loadedAt = Date.now();
                loadedUnread = unreadAtRequest;
                bindRows();
            })
            .catch(function () {
                // Never leave a spinner running — the full page always works.
                // The clock is NOT stamped on failure, so the next open retries
                // rather than caching the error for 45 seconds.
                body.innerHTML = '<div class="notif-loading">Could not load. ' +
                    '<a href="/notifications/">Open the full list</a>.</div>';
            })
            .then(function () { inFlight = false; });
    }

    function bindRows() {
        // Rows are ordinary links, so they work without JS. This only keeps the
        // badge honest when one is opened in a new tab, where no navigation
        // happens in this document to refresh it.
        body.querySelectorAll('.nf-row.is-unread').forEach(function (row) {
            row.addEventListener('click', function (event) {
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1) {
                    var id = row.dataset.noteId;
                    post('/notifications/' + id + '/read/').then(function (res) {
                        return res.json();
                    }).then(function (data) {
                        setUnread(data.unread);
                        row.classList.remove('is-unread');
                        row.classList.add('is-read');
                    }).catch(function () { /* best effort */ });
                }
            });
        });
    }

    function openPanel() {
        panel.classList.add('is-open');
        if (backdrop) { backdrop.classList.add('is-open'); }
        bell.setAttribute('aria-expanded', 'true');
        // NOT `loadPanel(true)`. Forcing a refetch on every open meant the
        // panel showed "Loading…" every single time it was opened, including
        // the second time in ten seconds — on an owner's phone that is a real
        // round trip, and it is the whole reason this control ever feels slow.
        // What is already on screen is reused unless the badge has moved or it
        // has gone stale, and the prefetch below usually means there IS
        // something on screen the first time too.
        loadPanel(false);
        refreshPushState();
    }

    function closePanel() {
        panel.classList.remove('is-open');
        if (backdrop) { backdrop.classList.remove('is-open'); }
        bell.setAttribute('aria-expanded', 'false');
    }

    function isOpen() { return panel.classList.contains('is-open'); }

    // Warm the panel the moment somebody REACHES for the bell, not when the
    // page loads. Fetching on load would put an extra request and an extra
    // query on every page an owner opens, which is exactly the cost this panel
    // is lazy in order to avoid; fetching on approach costs nothing until the
    // control is about to be used and buys the ~100-300ms between a pointer
    // arriving (or a finger landing) and the click resolving. `once` is not
    // used — a second approach after the content has gone stale should warm it
    // again.
    ['pointerenter', 'touchstart'].forEach(function (kind) {
        bell.addEventListener(kind, function () { loadPanel(false); },
            kind === 'touchstart' ? { passive: true } : undefined);
    });

    bell.addEventListener('click', function (event) {
        // The bell stays a real link to /notifications/ so it degrades without
        // JS and still works from a keyboard or a new tab.
        if (event.metaKey || event.ctrlKey || event.shiftKey) { return; }
        event.preventDefault();
        isOpen() ? closePanel() : openPanel();
    });

    document.addEventListener('click', function (event) {
        if (!isOpen()) { return; }
        if (!panel.contains(event.target) && !bell.contains(event.target)) { closePanel(); }
    });

    if (backdrop) { backdrop.addEventListener('click', closePanel); }

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && isOpen()) { closePanel(); bell.focus(); }
    });

    if (markAll) {
        markAll.addEventListener('click', function () {
            markAll.disabled = true;
            post(markAllUrl)
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    setUnread(data.unread);
                    loadPanel(true);
                })
                .catch(function () { /* the full page has a non-AJAX form */ })
                .then(function () { markAll.disabled = false; });
        });
    }

    // ---- device on / silent ------------------------------------------
    //
    // One small bell: filled and blue when this device receives alerts, struck
    // through when silenced. Replaces the "Alerts on this device" card, which
    // spent three lines of prose on a binary.
    function setPushUi(state, title) {
        if (!pushBtn) { return; }
        pushBtn.dataset.state = state;
        pushBtn.title = title;
        pushBtn.setAttribute('aria-label', title);
        pushBtn.disabled = (state === 'unavailable');
        pushBtn.classList.toggle('is-on', state === 'on');
        if (pushIcon) {
            pushIcon.className = state === 'on' ? 'bi bi-bell-fill' : 'bi bi-bell-slash';
        }

        // The one line in the panel that says this phone is not getting
        // alerts. Hidden entirely once they are on, so it can never become
        // furniture; when push is unavailable it states the reason and is not
        // pressable, because there is nothing here to press.
        if (pushCta && pushCtaText) {
            pushCta.hidden = (state === 'on');
            pushCta.disabled = (state === 'unavailable');
            pushCtaText.textContent = state === 'unavailable'
                ? title
                : 'Alerts are silent on this phone — tap to turn them on';
        }
    }

    function pushUnsupportedReason() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
            if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
                // The single most confusing failure on the device the owners
                // actually use: iOS exposes Push only to an installed app.
                return 'Add this app to your Home Screen first, then alerts can be turned on';
            }
            return 'This browser cannot show alerts';
        }
        if (!pushBtn || !pushBtn.dataset.vapidKey) { return 'Alerts are not set up on the server'; }
        if (!window.isSecureContext) { return 'Alerts need a secure (https) connection'; }
        return null;
    }

    function refreshPushState() {
        if (!pushBtn) { return; }
        var reason = pushUnsupportedReason();
        if (reason) { setPushUi('unavailable', reason); return; }

        navigator.serviceWorker.getRegistration('/').then(function (reg) {
            if (!reg) { setPushUi('off', 'Alerts silent on this device — tap to turn on'); return; }
            return reg.pushManager.getSubscription().then(function (sub) {
                setPushUi(sub ? 'on' : 'off',
                    sub ? 'Alerts on for this device — tap to silence'
                        : 'Alerts silent on this device — tap to turn on');
            });
        }).catch(function () {
            setPushUi('off', 'Alerts silent on this device — tap to turn on');
        });
    }

    function urlBase64ToUint8Array(base64String) {
        var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
        var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        var raw = window.atob(base64);
        var out = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; ++i) { out[i] = raw.charCodeAt(i); }
        return out;
    }

    function enablePush() {
        setPushUi('off', 'Asking for permission…');
        pushBtn.disabled = true;

        navigator.serviceWorker.register('/sw.js', { scope: '/' })
            .then(function (reg) { return navigator.serviceWorker.ready.then(function () { return reg; }); })
            .then(function (reg) {
                return Notification.requestPermission().then(function (permission) {
                    if (permission !== 'granted') {
                        // Browsers ask once; after that a re-ask silently returns
                        // "denied", so point at where the switch really is.
                        throw new Error('Blocked — allow notifications for this site in your browser settings');
                    }
                    return reg.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(pushBtn.dataset.vapidKey)
                    });
                });
            })
            .then(function (sub) {
                return fetch(pushBtn.dataset.subscribeUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                    body: JSON.stringify(sub.toJSON())
                });
            })
            .then(function (res) {
                if (!res.ok) { throw new Error('The server rejected this device'); }
                setPushUi('on', 'Alerts on for this device — tap to silence');
            })
            .catch(function (err) {
                setPushUi('off', err.message || 'Could not turn alerts on');
                pushBtn.disabled = false;
            });
    }

    function disablePush() {
        pushBtn.disabled = true;
        navigator.serviceWorker.getRegistration('/').then(function (reg) {
            if (!reg) { setPushUi('off', 'Alerts silent on this device — tap to turn on'); return; }
            return reg.pushManager.getSubscription().then(function (sub) {
                if (!sub) { setPushUi('off', 'Alerts silent on this device — tap to turn on'); return; }
                var endpoint = sub.endpoint;
                return sub.unsubscribe().then(function () {
                    // Tell the server too, or it keeps pushing to a dead endpoint
                    // until the failure counter reaps it.
                    return fetch(pushBtn.dataset.unsubscribeUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                        body: JSON.stringify({ endpoint: endpoint })
                    });
                }).then(function () {
                    setPushUi('off', 'Alerts silent on this device — tap to turn on');
                });
            });
        }).catch(function () {
            setPushUi('off', 'Could not change alerts');
        }).then(function () {
            if (pushBtn.dataset.state !== 'unavailable') { pushBtn.disabled = false; }
        });
    }

    if (pushBtn) {
        pushBtn.addEventListener('click', function () {
            if (pushBtn.dataset.state === 'on') { disablePush(); } else { enablePush(); }
        });
        // The strip is the same action as the toggle, not a second one — it
        // exists because a 34px unlabelled icon is not a control anybody finds.
        if (pushCta) {
            pushCta.addEventListener('click', function () {
                if (pushBtn.dataset.state !== 'unavailable') { enablePush(); }
            });
        }
        refreshPushState();
    }
})();
