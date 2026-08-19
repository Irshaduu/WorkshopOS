/*
 * photos.js — the DOM half of the photo feature. All the testable logic is in
 * photos-core.js, which loads before this.
 *
 * ONE COMPONENT, MANY BOXES. Everything here is delegated off `document` and
 * keyed on `[data-photo-box]`, so a box added to a formset row after page load
 * works with nothing re-initialised. That is deliberate: all three of the
 * `script.js` cloning traps recorded in CLAUDE.md live in per-element wiring,
 * and every one of them failed silently. This section has none.
 *
 * THE BOX IS A <div role="button">, NOT A <button>. The Financial Lock disables
 * controls with `form.querySelectorAll('input, select, textarea, button')`, so
 * a real button inside the job-card form would be disabled on a settled card —
 * killing VIEWING as well as adding. Whether photos may be ADDED is decided by
 * the server and arrives as `data-can-edit`.
 *
 * NO TONE PER SHOT. `WorkshopSound` is used only when an upload fails. A burst
 * is ten shots in a few seconds, and ten success tones is precisely the noise
 * that teaches people to stop hearing the tones that matter. The flash and the
 * count bump are the capture feedback.
 */
document.addEventListener('DOMContentLoaded', function () {
    'use strict';

    var root = document.getElementById('photoRoot');
    if (!root || typeof window.PhotoCore === 'undefined') {
        return;                       // page carries no photo feature
    }

    var URLS = {
        sign: root.dataset.signUrl,
        commit: root.dataset.commitUrl,
        list: root.dataset.listUrl,
        remove: root.dataset.deleteUrl
    };
    var CSRF = root.dataset.csrf;
    var MAX_EDGE = 1600;
    var JPEG_QUALITY = 0.75;

    var cam = document.getElementById('photoCam');
    var camVideo = document.getElementById('photoCamVideo');
    var camCount = document.getElementById('photoCamCount');
    var camError = document.getElementById('photoCamError');
    var camShutter = document.getElementById('photoCamShutter');
    var camFlash = document.getElementById('photoCamFlash');

    var gal = document.getElementById('photoGal');
    var galImg = document.getElementById('photoGalImg');
    var galMeta = document.getElementById('photoGalMeta');
    var galPos = document.getElementById('photoGalPos');
    var galEmpty = document.getElementById('photoGalEmpty');
    var galAdd = document.getElementById('photoGalAdd');
    var galMenu = document.getElementById('photoGalMenu');

    /* All mutable state declared up here, before anything that reads it.
     * CLAUDE.md records an afternoon lost to the opposite: a `const` declared
     * next to the functions using it sat in the temporal dead zone during the
     * first sweep, and the ReferenceError died inside a forEach with nothing in
     * any log. */
    var stream = null;
    var shooting = false;
    var active = null;                // {subject, id, limit, canEdit, stored, box}
    var lastFailedCount = 0;
    var gallery = new window.PhotoCore.GalleryState([]);

    /* ------------------------------------------------------------------
     * Transport — the only place that talks to the network.
     * ------------------------------------------------------------------ */

    function appHeaders() {
        return { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF };
    }

    /*
     * A refusal is marked `fatal` so the queue stops instead of retrying. The
     * limit being full or a bill being settled will answer the same way for
     * ever; only a transport failure is worth a second attempt.
     */
    function fatalStatus(status) {
        return status === 400 || status === 403 || status === 404 || status === 409 || status === 503;
    }

    var transport = {
        sign: async function (bytes, meta) {
            var res = await fetch(URLS.sign, {
                method: 'POST',
                headers: appHeaders(),
                body: JSON.stringify({ subject: meta.subject, id: meta.id, bytes: bytes })
            });
            var data = await res.json().catch(function () { return {}; });
            if (!res.ok) {
                var err = new Error(data.error || 'Could not start the upload.');
                err.fatal = fatalStatus(res.status);
                err.limitHit = !!data.limit_hit;
                throw err;
            }
            return data;
        },

        /*
         * Straight to R2, cross-origin. Deliberately no CSRF header and no
         * credentials — cookies are same-origin by default, and adding a custom
         * header would force the preflight to require it in the bucket's
         * AllowedHeaders for no benefit.
         */
        put: async function (url, blob) {
            var res = await fetch(url, {
                method: 'PUT',
                body: blob,
                headers: { 'Content-Type': 'image/jpeg' }
            });
            if (!res.ok) {
                throw new Error('The photo did not reach storage.');
            }
        },

        commit: async function (photoId, bytes, meta) {
            var res = await fetch(URLS.commit, {
                method: 'POST',
                headers: appHeaders(),
                body: JSON.stringify({
                    photo_id: photoId, subject: meta.subject, id: meta.id, bytes: bytes
                })
            });
            var data = await res.json().catch(function () { return {}; });
            if (!res.ok) {
                var err = new Error(data.error || 'Could not save the photo.');
                err.fatal = fatalStatus(res.status);
                throw err;
            }
            return data;
        }
    };

    var queue = new window.PhotoCore.UploadQueue(transport, { onChange: onQueueChange });

    /* ------------------------------------------------------------------
     * Box state
     * ------------------------------------------------------------------ */

    function boxFor(subject, id) {
        return document.querySelector(
            '[data-photo-box][data-subject="' + subject + '"][data-subject-id="' + id + '"]'
        );
    }

    function paintBox(box, count) {
        if (!box) { return; }
        box.dataset.count = String(count);
        box.classList.toggle('has-photos', count > 0);
        var badge = box.querySelector('.photo-box-count');
        if (badge) {
            badge.textContent = count > 0 ? String(count) : '';
            badge.hidden = count === 0;
        }
        var label = count > 0
            ? count + (count === 1 ? ' photo' : ' photos')
            : 'Take a photo';
        box.setAttribute('aria-label', label);
        box.title = label;
    }

    /*
     * The count on the box is what somebody actually watches during a burst, so
     * it counts CONFIRMED photos plus anything still IN FLIGHT — a number that
     * only moved once the server answered would sit still through a whole burst
     * on slow wifi and read as nothing happening.
     *
     * A FAILED photo is deliberately excluded. It is not a photo the workshop
     * has, and leaving it in would make the badge quietly overstate what is
     * actually stored — the one lie this feature must not tell. Losing it from
     * the count is also the signal: the number drops, and the camera's own
     * counter turns red and says how many did not save.
     */
    function onQueueChange() {
        if (!active) { return; }
        var inFlight = queue.items.filter(function (i) {
            return i.state === 'pending';
        }).length;
        var total = active.stored + queue.doneCount() + inFlight;
        paintBox(active.box, total);
        if (camCount) {
            var failed = queue.failedItems().length;
            camCount.textContent = failed
                ? total + ' — ' + failed + ' not saved'
                : String(total);
            camCount.classList.toggle('has-failure', failed > 0);
        }
        if (failedJustAppeared()) {
            showCamError('A photo did not save. It will retry when you reopen the gallery.');
            if (window.WorkshopSound) { window.WorkshopSound.play('error'); }
        }
        updateShutterState(total);
    }

    function failedJustAppeared() {
        var now = queue.failedItems().length;
        var appeared = now > lastFailedCount;
        lastFailedCount = now;
        return appeared;
    }

    function updateShutterState(total) {
        if (!camShutter || !active) { return; }
        var full = window.PhotoCore.limitReached(total, active.limit);
        camShutter.disabled = full || shooting;
        if (full) {
            showCamError('That is the maximum of ' + active.limit + ' photos.');
        }
    }

    function showCamError(message) {
        if (!camError) { return; }
        camError.textContent = message;
        camError.hidden = !message;
    }

    /* ------------------------------------------------------------------
     * Camera
     * ------------------------------------------------------------------ */

    async function openCamera(subject, id, limit, stored, box) {
        /*
         * Drop anything already confirmed. `stored` is re-read from the server
         * at this moment, so leaving finished items in the queue would count
         * the same photo twice — once in the baseline and once in the queue —
         * and the badge would climb by two per shot on a second visit.
         */
        queue.clearFinished();
        lastFailedCount = queue.failedItems().length;
        active = { subject: subject, id: id, limit: limit, stored: stored, box: box, canEdit: true };
        showCamError('');
        cam.hidden = false;
        document.body.classList.add('photo-open');
        onQueueChange();

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            showCamError('This browser cannot open a camera. On a phone, add the app to your Home Screen.');
            return;
        }
        try {
            /*
             * `ideal`, never `exact` — a laptop with only a front camera would
             * throw on exact, and Office opening this from Purchase History is
             * exactly that case. It falls back instead of failing.
             */
            stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: { ideal: 'environment' },
                    width: { ideal: 1920 },
                    height: { ideal: 1080 }
                },
                audio: false
            });
            camVideo.srcObject = stream;
            await camVideo.play().catch(function () {});
        } catch (err) {
            var reason = (err && err.name === 'NotAllowedError')
                ? 'Camera access was refused. Allow it in the browser settings for this site.'
                : 'No camera could be opened on this device.';
            showCamError(reason);
        }
    }

    /*
     * Stopping every track is not optional: leave one running and the tablet's
     * camera light stays on and the device warms up for as long as the page is
     * open. It is the classic bug in every hand-rolled camera.
     */
    function closeCamera() {
        if (stream) {
            stream.getTracks().forEach(function (track) { track.stop(); });
            stream = null;
        }
        if (camVideo) { camVideo.srcObject = null; }
        cam.hidden = true;
        document.body.classList.remove('photo-open');

        /*
         * Spend one more attempt on anything that failed. Closing the camera is
         * usually the moment somebody walks back inside, which is often the
         * moment the wifi comes good — and it costs the person nothing. If it
         * still fails, the frame is held and `beforeunload` is what stops the
         * tab being closed on it.
         */
        queue.failedItems().forEach(function (item) { queue.retry(item.localId); });
    }

    function flash() {
        if (!camFlash) { return; }
        camFlash.classList.remove('is-firing');
        void camFlash.offsetWidth;          // restart the animation on a second shot
        camFlash.classList.add('is-firing');
    }

    function capture() {
        if (shooting || !stream || !active) { return; }
        var width = camVideo.videoWidth;
        var height = camVideo.videoHeight;
        if (!width || !height) { return; }

        /* Held down directly rather than through updateShutterState, which
         * needs a total this function does not have yet. `queue.add` fires
         * onQueueChange immediately after, which recomputes it properly. */
        shooting = true;
        if (camShutter) { camShutter.disabled = true; }

        var size = window.PhotoCore.fitDimensions(width, height, MAX_EDGE);
        var canvas = document.createElement('canvas');
        canvas.width = size.width;
        canvas.height = size.height;
        canvas.getContext('2d').drawImage(camVideo, 0, 0, size.width, size.height);

        flash();

        canvas.toBlob(function (blob) {
            shooting = false;
            if (!blob) {
                if (camShutter) { camShutter.disabled = false; }
                showCamError('That photo could not be processed. Try again.');
                return;
            }
            queue.add(blob, { subject: active.subject, id: active.id });
        }, 'image/jpeg', JPEG_QUALITY);
    }

    /* ------------------------------------------------------------------
     * Gallery
     * ------------------------------------------------------------------ */

    async function openGallery(subject, id, box) {
        /* A fresh object, never a patched one — a stale `limit` or `canEdit`
         * carried over from another subject would be read by paintAddButton
         * before the fetch lands. */
        active = { subject: subject, id: id, box: box, limit: 0, canEdit: false, stored: 0 };
        gal.hidden = false;
        document.body.classList.add('photo-open');
        galImg.removeAttribute('src');
        galEmpty.hidden = true;
        galMeta.textContent = 'Loading…';

        var url = URLS.list + '?subject=' + encodeURIComponent(subject) + '&id=' + encodeURIComponent(id);
        try {
            var res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            var data = await res.json();
            active.limit = data.limit;
            /*
             * BOTH have to agree. The server decides whether this subject may be
             * changed at all (a settled bill freezes its photos); the box decides
             * whether THIS page offers changing it. Purchase History is view-only
             * by that second rule even when the card behind the row is wide open.
             *
             * Only the first of those is a control. The second is presentation,
             * and that is fine here because there is nothing to protect: the
             * person is already allowed to add this photo from the job card, the
             * page simply does not offer it. Where a rule would matter — the
             * freeze — it lives in the view.
             */
            active.canEdit = !!data.can_edit && box.dataset.canEdit === '1';
            active.stored = (data.photos || []).length;
            gallery.setPhotos(data.photos || []);
            paintBox(box, active.stored);
            renderGallery();
        } catch (err) {
            galMeta.textContent = 'Could not load the photos.';
        }
    }

    function renderGallery() {
        var photo = gallery.current();
        var many = gallery.count() > 1;

        galEmpty.hidden = gallery.count() > 0;
        galImg.hidden = gallery.count() === 0;
        document.getElementById('photoGalPrev').hidden = !many;
        document.getElementById('photoGalNext').hidden = !many;

        if (!photo) {
            galMeta.textContent = '';
            galPos.textContent = '';
            galMenu.hidden = true;
            paintAddButton();
            return;
        }

        galImg.src = photo.url;
        galImg.alt = 'Photo taken ' + photo.taken_at;
        galMeta.textContent = photo.taken_by
            ? photo.taken_at + ' · ' + photo.taken_by
            : photo.taken_at;
        galPos.textContent = many ? (gallery.index + 1) + ' of ' + gallery.count() : '';
        galMenu.hidden = !active.canEdit;
        paintAddButton();
    }

    function paintAddButton() {
        if (!galAdd) { return; }
        var full = window.PhotoCore.limitReached(gallery.count(), active.limit);
        galAdd.hidden = !active.canEdit;
        galAdd.disabled = full;
        galAdd.textContent = full
            ? 'Maximum of ' + active.limit + ' photos'
            : 'Add photo';
    }

    function closeGallery() {
        gal.hidden = true;
        document.body.classList.remove('photo-open');
        closeMenu();
    }

    function closeMenu() {
        var menu = document.getElementById('photoGalMenuList');
        if (menu) { menu.hidden = true; }
    }

    async function deleteCurrent() {
        var photo = gallery.current();
        if (!photo) { return; }
        if (!window.confirm('Delete this photo? This cannot be undone.')) { return; }
        try {
            var res = await fetch(URLS.remove, {
                method: 'POST',
                headers: appHeaders(),
                body: JSON.stringify({ photo_id: photo.id })
            });
            var data = await res.json().catch(function () { return {}; });
            if (!res.ok) {
                throw new Error(data.error || 'Could not delete the photo.');
            }
            gallery.remove(photo.id);
            active.stored = gallery.count();
            paintBox(active.box, gallery.count());
            closeMenu();
            renderGallery();
            if (window.WorkshopSound) { window.WorkshopSound.play('success'); }
        } catch (err) {
            galMeta.textContent = err.message;
            if (window.WorkshopSound) { window.WorkshopSound.play('error'); }
        }
    }

    /* ------------------------------------------------------------------
     * Delegated events — one listener each, so a row added later is covered
     * ------------------------------------------------------------------ */

    document.addEventListener('click', function (event) {
        var box = event.target.closest('[data-photo-box]');
        if (box) {
            event.preventDefault();
            var count = Number(box.dataset.count) || 0;
            var subject = box.dataset.subject;
            var id = box.dataset.subjectId;
            var limit = Number(box.dataset.limit) || 10;
            if (count === 0 && box.dataset.canEdit === '1') {
                openCamera(subject, id, limit, 0, box);
            } else {
                openGallery(subject, id, box);
            }
            return;
        }

        if (event.target.closest('#photoCamShutter')) { capture(); return; }
        if (event.target.closest('#photoCamClose')) { closeCamera(); return; }

        if (event.target.closest('#photoGalClose')) { closeGallery(); return; }
        if (event.target.closest('#photoGalPrev')) { gallery.prev(); renderGallery(); return; }
        if (event.target.closest('#photoGalNext')) { gallery.next(); renderGallery(); return; }
        if (event.target.closest('#photoGalDelete')) { deleteCurrent(); return; }

        if (event.target.closest('#photoGalMenu')) {
            var menu = document.getElementById('photoGalMenuList');
            menu.hidden = !menu.hidden;
            return;
        }
        closeMenu();

        if (event.target.closest('#photoGalAdd')) {
            var box2 = active.box;
            closeGallery();
            openCamera(active.subject, active.id, active.limit, gallery.count(), box2);
        }
    });

    /* A box must answer the keyboard too — it is a div playing the part of a
     * button, so it gets none of that for free. */
    document.addEventListener('keydown', function (event) {
        var box = event.target.closest && event.target.closest('[data-photo-box]');
        if (box && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            box.click();
            return;
        }
        if (!gal.hidden) {
            if (event.key === 'Escape') { closeGallery(); }
            if (event.key === 'ArrowLeft') { gallery.prev(); renderGallery(); }
            if (event.key === 'ArrowRight') { gallery.next(); renderGallery(); }
        } else if (!cam.hidden && event.key === 'Escape') {
            closeCamera();
        }
    });

    /*
     * Capture is instant and unreviewed, so a frame that has not finished
     * uploading exists only in this tab. Closing it would lose the photo with
     * nothing said, which is the one failure this design must not have.
     */
    window.addEventListener('beforeunload', function (event) {
        if (queue.hasUnsavedWork()) {
            event.preventDefault();
            event.returnValue = '';
            return '';
        }
    });
});
