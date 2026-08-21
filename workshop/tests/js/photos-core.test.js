/*
 * Tests for photos-core.js.
 *
 *     node --test workshop/tests/js/
 *
 * It lives here rather than beside photos-core.js so that `collectstatic`
 * never ships a test file into staticfiles, where it would be publicly
 * served and would occupy a manifest entry.
 *
 * The FIRST JavaScript tests in this repository, and they add no dependency to
 * it: `node --test` is built into Node 18+, so there is no npm, no
 * package.json, no node_modules, no bundler and no linter. The rule in
 * CLAUDE.md stands.
 *
 * They do NOT run under `manage.py test`. Two commands, and that is worth
 * knowing — a test nobody remembers to run is worse than none, so it is
 * documented next to the Django one.
 *
 * What is covered here is what cannot be covered anywhere else: the upload
 * queue's behaviour when the network misbehaves, which on shop wifi is the
 * normal case rather than the exotic one, and the gallery's index arithmetic.
 * The camera needs a browser and a fake media device and is verified by hand.
 */

const { test } = require('node:test');
const assert = require('node:assert');

const { fitDimensions, limitReached, UploadQueue, GalleryState } = require('../../static/js/photos-core.js');

/* ------------------------------------------------------------------------- */
/* Capture geometry                                                          */
/* ------------------------------------------------------------------------- */

test('a frame larger than the cap is scaled down by its longest edge', () => {
    assert.deepStrictEqual(fitDimensions(4000, 3000, 1600), { width: 1600, height: 1200 });
    assert.deepStrictEqual(fitDimensions(3000, 4000, 1600), { width: 1200, height: 1600 });
});

test('a frame smaller than the cap is never blown up', () => {
    // Upscaling costs bytes and adds no detail; a 640px webcam should stay 640.
    assert.deepStrictEqual(fitDimensions(640, 480, 1600), { width: 640, height: 480 });
});

test('a frame with no dimensions yet produces nothing', () => {
    // videoWidth is 0 until the stream has actually started.
    assert.deepStrictEqual(fitDimensions(0, 0, 1600), { width: 0, height: 0 });
});

test('an extreme aspect ratio still yields at least one pixel', () => {
    const size = fitDimensions(8000, 3, 1600);
    assert.strictEqual(size.width, 1600);
    assert.ok(size.height >= 1, 'height collapsed to zero');
});

test('the limit is reached at the limit, not past it', () => {
    assert.strictEqual(limitReached(9, 10), false);
    assert.strictEqual(limitReached(10, 10), true);
    assert.strictEqual(limitReached(11, 10), true);
});

/* ------------------------------------------------------------------------- */
/* Upload queue                                                              */
/* ------------------------------------------------------------------------- */

const blob = (size) => ({ size: size || 1000 });

/*
 * A behaviour function is asked about EVERY call and answers with an error or
 * with null — `(n) => (n === 1 ? new Error('network') : null)` means "the first
 * PUT dies, the rest are fine". So the returned value has to be checked before
 * it is thrown.
 *
 * This helper used to `throw behaviour.putFails(n)` unconditionally, which
 * threw `null` on the calls that were supposed to SUCCEED. `null` is not fatal
 * and carries no message, so the queue read it as an ordinary network failure
 * and retried it into oblivion — and the two tests that describe the retry
 * path (a flaky first PUT, and clearing finished work) failed for a reason that
 * had nothing to do with photos-core.js, which was correct throughout.
 *
 * Worth stating because of what it cost: the single most likely real failure on
 * shop wifi had a test written for it that could never have passed.
 */
function raise(fn, n) {
    if (!fn) { return; }
    const err = fn(n);
    if (err) { throw err; }
}

function transportThat(behaviour) {
    const calls = { sign: 0, put: 0, commit: 0 };
    return {
        calls,
        sign: async () => {
            calls.sign += 1;
            raise(behaviour.signFails, calls.sign);
            return { photo_id: 'p' + calls.sign, upload_url: 'https://bucket/p' };
        },
        put: async () => {
            calls.put += 1;
            raise(behaviour.putFails, calls.put);
        },
        commit: async () => {
            calls.commit += 1;
            raise(behaviour.commitFails, calls.commit);
            return { photo: { id: 'photo-' + calls.commit } };
        }
    };
}

test('a photo that uploads cleanly ends up done and releases its frame', async () => {
    const transport = transportThat({});
    const queue = new UploadQueue(transport);

    const item = queue.add(blob(), { subject: 'card', id: 1 });
    await queue.settled();

    assert.strictEqual(item.state, 'done');
    assert.strictEqual(item.photo.id, 'photo-1');
    // The frame must be dropped or a ten-photo burst holds ~10 MB on a tablet.
    assert.strictEqual(item.blob, null);
    assert.strictEqual(queue.hasUnsavedWork(), false);
});

test('a flaky network is retried once and then succeeds', async () => {
    // The single most likely real failure: the first PUT dies on shop wifi.
    const transport = transportThat({
        putFails: (n) => (n === 1 ? new Error('network') : null)
    });
    const queue = new UploadQueue(transport);

    const item = queue.add(blob(), {});
    await queue.settled();

    assert.strictEqual(item.state, 'done');
    assert.strictEqual(item.attempts, 2);
});

test('a photo that never uploads becomes VISIBLY failed, never silently lost', async () => {
    // This is the property the whole no-review design rests on. If this ever
    // becomes "done" or disappears, a mechanic's photo vanishes with no sign.
    const transport = transportThat({ putFails: () => new Error('network') });
    const queue = new UploadQueue(transport);

    const item = queue.add(blob(), {});
    await queue.settled();

    assert.strictEqual(item.state, 'failed');
    assert.strictEqual(queue.failedItems().length, 1);
    assert.strictEqual(queue.hasUnsavedWork(), true, 'a failed photo must still block leaving');
    assert.notStrictEqual(item.blob, null, 'the frame must survive so it can be retried');
});

test('a refusal is not retried', async () => {
    // The limit being full or a bill being settled will answer identically for
    // ever. Retrying burns battery on a tablet and changes nothing.
    const fatal = () => {
        const err = new Error('That is the maximum of 10 photos.');
        err.fatal = true;
        return err;
    };
    const transport = transportThat({ signFails: fatal });
    const queue = new UploadQueue(transport);

    const item = queue.add(blob(), {});
    await queue.settled();

    assert.strictEqual(item.state, 'rejected');
    assert.strictEqual(transport.calls.sign, 1, 'a refusal was retried');
    assert.strictEqual(queue.hasUnsavedWork(), false, 'a refusal is not unsaved work');
});

test('a failed photo can be retried by hand and then succeeds', async () => {
    let broken = true;
    const transport = {
        sign: async () => ({ photo_id: 'p', upload_url: 'u' }),
        put: async () => { if (broken) { throw new Error('network'); } },
        commit: async () => ({ photo: { id: 'photo-1' } })
    };
    const queue = new UploadQueue(transport);

    const item = queue.add(blob(), {});
    await queue.settled();
    assert.strictEqual(item.state, 'failed');

    broken = false;
    queue.retry(item.localId);
    await queue.settled();

    assert.strictEqual(item.state, 'done');
});

test('retrying something that is not failed does nothing', async () => {
    const queue = new UploadQueue(transportThat({}));
    const item = queue.add(blob(), {});
    await queue.settled();
    assert.strictEqual(queue.retry(item.localId), null);
    assert.strictEqual(queue.retry('nonexistent'), null);
});

test('clearing finished work keeps anything still at risk', async () => {
    // photos.js calls this when the camera reopens, so the badge does not count
    // the same photo twice — once in the stored baseline and once in the queue.
    const transport = transportThat({
        putFails: (n) => (n === 2 ? new Error('network') : null)
    });
    const queue = new UploadQueue(transport, { maxRetries: 0 });

    queue.add(blob(), {});
    queue.add(blob(), {});
    await queue.settled();
    queue.clearFinished();

    assert.strictEqual(queue.items.length, 1);
    assert.strictEqual(queue.items[0].state, 'failed');
});

test('the change listener fires as work moves, and a broken one cannot stall an upload', async () => {
    let fired = 0;
    const queue = new UploadQueue(transportThat({}), {
        onChange: () => { fired += 1; throw new Error('a listener blew up'); }
    });

    const item = queue.add(blob(), {});
    await queue.settled();

    assert.ok(fired >= 2, 'expected changes on enqueue and on completion');
    assert.strictEqual(item.state, 'done');
});

/* ------------------------------------------------------------------------- */
/* Gallery paging                                                            */
/* ------------------------------------------------------------------------- */

const photos = (n) => Array.from({ length: n }, (_, i) => ({ id: 'p' + i }));

test('it opens on the newest photo', () => {
    // The server sends newest first, which is what makes unreviewed capture
    // safe: the gallery shows what was just taken.
    const gallery = new GalleryState(photos(3));
    assert.strictEqual(gallery.current().id, 'p0');
});

test('the arrows wrap in both directions', () => {
    const gallery = new GalleryState(photos(3));
    assert.strictEqual(gallery.next().id, 'p1');
    assert.strictEqual(gallery.next().id, 'p2');
    assert.strictEqual(gallery.next().id, 'p0', 'forward did not wrap');
    assert.strictEqual(gallery.prev().id, 'p2', 'backward did not wrap');
});

test('the arrows do nothing on an empty gallery', () => {
    const gallery = new GalleryState([]);
    assert.strictEqual(gallery.next(), null);
    assert.strictEqual(gallery.prev(), null);
    assert.strictEqual(gallery.current(), null);
});

test('deleting the last photo does not strand the index past the end', () => {
    // Otherwise the lightbox renders blank with working arrows, which reads as
    // the gallery having broken rather than as a photo having gone.
    const gallery = new GalleryState(photos(3));
    gallery.next();
    gallery.next();                       // on p2, the last
    assert.strictEqual(gallery.remove('p2'), true);
    assert.strictEqual(gallery.index, 1);
    assert.strictEqual(gallery.current().id, 'p1');
});

test('deleting the only photo leaves an empty gallery, not a broken one', () => {
    const gallery = new GalleryState(photos(1));
    gallery.remove('p0');
    assert.strictEqual(gallery.count(), 0);
    assert.strictEqual(gallery.index, 0);
    assert.strictEqual(gallery.current(), null);
});

test('deleting an earlier photo keeps you looking at roughly the same place', () => {
    const gallery = new GalleryState(photos(4));
    gallery.next();                       // on p1
    gallery.remove('p0');
    assert.strictEqual(gallery.current().id, 'p2');
});

test('deleting something that is not there changes nothing', () => {
    const gallery = new GalleryState(photos(2));
    assert.strictEqual(gallery.remove('nope'), false);
    assert.strictEqual(gallery.count(), 2);
});

test('reloading the list can hold your place', () => {
    const gallery = new GalleryState(photos(3));
    gallery.next();                       // on p1
    gallery.setPhotos(photos(3), 'p1');
    assert.strictEqual(gallery.current().id, 'p1');
});

test('reloading falls back to the newest when the photo you were on has gone', () => {
    const gallery = new GalleryState(photos(3));
    gallery.setPhotos(photos(2), 'p2');
    assert.strictEqual(gallery.current().id, 'p0');
});
