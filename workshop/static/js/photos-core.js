/*
 * photos-core.js — the parts of the photo feature that are worth testing.
 *
 * WHY THIS FILE IS SEPARATE FROM photos.js
 * ----------------------------------------
 * Nothing in the 1,414 Django tests executes a line of JavaScript, and this
 * codebase has already been bitten by that: all three of the documented
 * `script.js` cloning traps produced "a control that simply did nothing, with a
 * clean console". The camera itself cannot be tested without a browser and a
 * fake media device — but the upload queue and the gallery's index arithmetic
 * are pure, and they are exactly the parts that fail silently on bad shop wifi.
 *
 * So the logic lives here with no DOM, no fetch and no globals, and
 * `photos-core.test.js` runs it under Node's BUILT-IN test runner:
 *
 *     node --test static/js/
 *
 * No npm, no package.json, no node_modules, no bundler, no linter — the rule in
 * CLAUDE.md stands. It is one binary and one command.
 *
 * Loaded in the browser as a plain <script> BEFORE photos.js, deliberately not
 * as an ES module: `CompressedManifestStaticFilesStorage` rewrites URLs inside
 * CSS but NOT inside JS, so a relative `import './photos-core.js'` in a
 * content-hashed file would 404 in production and work perfectly in
 * development.
 */
(function (root, factory) {
    'use strict';
    var api = factory();
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;          // node --test
    } else {
        root.PhotoCore = api;          // browser
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    /* ------------------------------------------------------------------
     * Capture geometry
     * ------------------------------------------------------------------ */

    /**
     * Scale a frame so its longest edge is `maxEdge`, never upscaling.
     *
     * 1600px at JPEG 0.75 lands around 200 KB — enough to read a part number
     * off a box, small enough to move over workshop wifi. A raw phone frame is
     * 3-5 MB, so this is where almost all of the saving comes from.
     */
    function fitDimensions(width, height, maxEdge) {
        if (!width || !height || width < 0 || height < 0) {
            return { width: 0, height: 0 };
        }
        var longest = Math.max(width, height);
        if (longest <= maxEdge) {
            return { width: Math.round(width), height: Math.round(height) };
        }
        var scale = maxEdge / longest;
        return {
            width: Math.max(1, Math.round(width * scale)),
            height: Math.max(1, Math.round(height * scale))
        };
    }

    function limitReached(count, limit) {
        return Number(count) >= Number(limit);
    }

    /* ------------------------------------------------------------------
     * Upload queue
     * ------------------------------------------------------------------ */

    /**
     * Holds captured frames until the server has confirmed them.
     *
     * This exists because capture is instant and unreviewed — the owner chose
     * one tap per photo rather than shutter-then-confirm. That is faster, and
     * it moves the entire risk here: if an upload quietly fails, the only
     * evidence the photo ever existed is gone. So a frame is held in memory
     * until `commit` succeeds, one automatic retry is spent, and anything still
     * broken after that becomes a VISIBLE failed item the person can retry by
     * hand. A photo may never disappear silently.
     *
     * `transport` is injected — {sign, put, commit} — so the tests can drive
     * every one of those outcomes without a network.
     */
    function UploadQueue(transport, options) {
        options = options || {};
        this.transport = transport;
        this.items = [];
        this.maxRetries = options.maxRetries === undefined ? 1 : options.maxRetries;
        this.onChange = options.onChange || function () {};
        this._seq = 0;
        this._running = [];
    }

    UploadQueue.prototype._changed = function () {
        try {
            this.onChange(this);
        } catch (err) {
            /* A broken listener must never stall an upload. */
        }
    };

    UploadQueue.prototype.add = function (blob, meta) {
        var item = {
            localId: 'u' + (++this._seq),
            blob: blob,
            meta: meta || {},
            state: 'pending',
            attempts: 0,
            photo: null,
            error: ''
        };
        this.items.push(item);
        this._changed();
        var promise = this._attempt(item);
        this._running.push(promise);
        return item;
    };

    UploadQueue.prototype._attempt = function (item) {
        var self = this;
        return (async function () {
            /* eslint-disable no-constant-condition */
            while (true) {
                item.attempts += 1;
                item.state = 'pending';
                item.error = '';
                self._changed();
                try {
                    var signed = await self.transport.sign(item.blob.size, item.meta);
                    await self.transport.put(signed.upload_url, item.blob);
                    var result = await self.transport.commit(
                        signed.photo_id, item.blob.size, item.meta
                    );
                    item.state = 'done';
                    item.photo = (result && result.photo) || null;
                    item.blob = null;          // release the frame
                    self._changed();
                    return item;
                } catch (err) {
                    item.error = (err && err.message) || 'Upload failed';
                    /*
                     * A refusal is not a network problem. The limit being full,
                     * a settled bill, a malformed request — retrying those just
                     * burns battery and produces the same answer, so they stop
                     * here and say why.
                     */
                    if (err && err.fatal) {
                        item.state = 'rejected';
                        item.blob = null;
                        self._changed();
                        return item;
                    }
                    if (item.attempts > self.maxRetries) {
                        item.state = 'failed';
                        self._changed();
                        return item;
                    }
                }
            }
        })();
    };

    UploadQueue.prototype.retry = function (localId) {
        var item = this.items.find(function (i) { return i.localId === localId; });
        if (!item || item.state !== 'failed' || !item.blob) {
            return null;
        }
        item.attempts = 0;
        var promise = this._attempt(item);
        this._running.push(promise);
        return item;
    };

    UploadQueue.prototype.pendingCount = function () {
        return this.items.filter(function (i) { return i.state === 'pending'; }).length;
    };

    UploadQueue.prototype.failedItems = function () {
        return this.items.filter(function (i) { return i.state === 'failed'; });
    };

    UploadQueue.prototype.doneCount = function () {
        return this.items.filter(function (i) { return i.state === 'done'; }).length;
    };

    /** True while something could still be lost by closing the page. */
    UploadQueue.prototype.hasUnsavedWork = function () {
        return this.items.some(function (i) {
            return i.state === 'pending' || i.state === 'failed';
        });
    };

    UploadQueue.prototype.settled = function () {
        return Promise.all(this._running.slice());
    };

    UploadQueue.prototype.clearFinished = function () {
        this.items = this.items.filter(function (i) {
            return i.state === 'pending' || i.state === 'failed';
        });
        this._changed();
    };

    /* ------------------------------------------------------------------
     * Gallery paging
     * ------------------------------------------------------------------ */

    /**
     * Which photo the lightbox is showing, and what the arrows do.
     *
     * The list arrives newest-first from the server. That ordering is what
     * makes unreviewed capture safe: opening the gallery straight after a burst
     * shows what was just taken, which is the check that replaces reviewing
     * each shot as it is made.
     */
    function GalleryState(photos) {
        this.photos = (photos || []).slice();
        this.index = 0;
    }

    GalleryState.prototype.count = function () {
        return this.photos.length;
    };

    GalleryState.prototype.current = function () {
        return this.photos[this.index] || null;
    };

    GalleryState.prototype.setPhotos = function (photos, keepId) {
        this.photos = (photos || []).slice();
        var at = -1;
        if (keepId) {
            at = this.photos.findIndex(function (p) { return p.id === keepId; });
        }
        this.index = at === -1 ? 0 : at;
        return this.current();
    };

    GalleryState.prototype.next = function () {
        if (!this.photos.length) { return null; }
        this.index = (this.index + 1) % this.photos.length;
        return this.current();
    };

    GalleryState.prototype.prev = function () {
        if (!this.photos.length) { return null; }
        this.index = (this.index - 1 + this.photos.length) % this.photos.length;
        return this.current();
    };

    /**
     * Drop one photo and land somewhere sensible.
     *
     * Deleting the last photo in the list must not leave the index past the
     * end — that renders a blank lightbox with working arrows, which reads as
     * the gallery having broken rather than as a photo having gone.
     */
    GalleryState.prototype.remove = function (id) {
        var at = this.photos.findIndex(function (p) { return p.id === id; });
        if (at === -1) { return false; }
        this.photos.splice(at, 1);
        if (this.index >= this.photos.length) {
            this.index = Math.max(0, this.photos.length - 1);
        }
        return true;
    };

    return {
        fitDimensions: fitDimensions,
        limitReached: limitReached,
        UploadQueue: UploadQueue,
        GalleryState: GalleryState
    };
});
