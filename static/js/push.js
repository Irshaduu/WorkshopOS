// Web Push enrolment, driven by the button on the notifications page.
//
// Permission must be requested from a real user gesture — iOS refuses outright
// otherwise, and Chrome penalises sites that ask on page load. So nothing here
// runs until the button is tapped.
(function () {
    'use strict';

    var root = document.getElementById('push-setup');
    if (!root) { return; }

    var button = document.getElementById('push-toggle');
    var status = document.getElementById('push-status');
    var publicKey = root.dataset.vapidKey;
    var subscribeUrl = root.dataset.subscribeUrl;
    var unsubscribeUrl = root.dataset.unsubscribeUrl;
    var csrfToken = root.dataset.csrf;

    function say(message, kind) {
        status.textContent = message;
        status.className = 'push-status' + (kind ? ' ' + kind : '');
    }

    // VAPID keys travel as base64url; PushManager wants raw bytes.
    function urlBase64ToUint8Array(base64String) {
        var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
        var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        var raw = window.atob(base64);
        var output = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; ++i) { output[i] = raw.charCodeAt(i); }
        return output;
    }

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify(body)
        });
    }

    // The single most confusing failure for an owner: on iOS, Web Push only
    // exists inside an app added to the Home Screen. In a plain Safari tab
    // PushManager is simply absent, and without this explanation the button just
    // looks broken.
    function unsupportedReason() {
        if (!('serviceWorker' in navigator)) {
            return 'This browser does not support notifications.';
        }
        if (!('PushManager' in window)) {
            var isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
            if (isIOS) {
                return 'On iPhone, notifications only work once this app is added to your Home Screen. Tap Share, then "Add to Home Screen", open it from there and try again.';
            }
            return 'This browser does not support notifications.';
        }
        if (!publicKey) {
            return 'Notifications are not configured on the server yet.';
        }
        if (!window.isSecureContext) {
            return 'Notifications need a secure (https) connection.';
        }
        return null;
    }

    function setButton(state) {
        if (state === 'on') {
            button.textContent = 'Turn off on this device';
            button.dataset.state = 'on';
        } else {
            button.textContent = 'Enable on this device';
            button.dataset.state = 'off';
        }
        button.disabled = false;
    }

    function refresh() {
        var reason = unsupportedReason();
        if (reason) {
            button.disabled = true;
            say(reason, 'warn');
            return;
        }
        navigator.serviceWorker.getRegistration('/').then(function (reg) {
            if (!reg) { setButton('off'); say('Off on this device.'); return; }
            reg.pushManager.getSubscription().then(function (sub) {
                if (sub) { setButton('on'); say('On for this device.', 'ok'); }
                else { setButton('off'); say('Off on this device.'); }
            });
        });
    }

    function enable() {
        button.disabled = true;
        say('Asking for permission…');

        navigator.serviceWorker.register('/sw.js', { scope: '/' })
            .then(function (reg) { return navigator.serviceWorker.ready.then(function () { return reg; }); })
            .then(function (reg) {
                return Notification.requestPermission().then(function (permission) {
                    if (permission !== 'granted') {
                        // Browsers only ask once. Re-asking silently returns
                        // "denied" forever, so say where the switch actually is.
                        throw new Error('Permission was refused. You can turn it back on in your browser or app settings for this site.');
                    }
                    return reg.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: urlBase64ToUint8Array(publicKey)
                    });
                });
            })
            .then(function (sub) {
                return post(subscribeUrl, sub.toJSON()).then(function (res) {
                    if (!res.ok) { throw new Error('The server rejected this device.'); }
                    setButton('on');
                    say('On for this device.', 'ok');
                });
            })
            .catch(function (err) {
                setButton('off');
                say(err.message || 'Could not turn notifications on.', 'warn');
            });
    }

    function disable() {
        button.disabled = true;
        say('Turning off…');

        navigator.serviceWorker.getRegistration('/').then(function (reg) {
            if (!reg) { setButton('off'); say('Off on this device.'); return; }
            return reg.pushManager.getSubscription().then(function (sub) {
                if (!sub) { setButton('off'); say('Off on this device.'); return; }
                var endpoint = sub.endpoint;
                return sub.unsubscribe().then(function () {
                    // Tell the server too, or it keeps pushing to a dead endpoint
                    // until the failure counter reaps it.
                    return post(unsubscribeUrl, { endpoint: endpoint });
                }).then(function () {
                    setButton('off');
                    say('Off on this device.');
                });
            });
        }).catch(function () {
            setButton('off');
            say('Could not turn notifications off.', 'warn');
        });
    }

    button.addEventListener('click', function () {
        if (button.dataset.state === 'on') { disable(); } else { enable(); }
    });

    refresh();
})();
