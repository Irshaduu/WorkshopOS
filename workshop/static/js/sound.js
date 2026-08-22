/*
 * Outcome sounds.
 *
 * WHY THIS IS DRIVEN OFF DJANGO'S MESSAGES AND NOT OFF INDIVIDUAL BUTTONS
 * ----------------------------------------------------------------------
 * The app already tags every outcome: 78 `messages.success`, 94
 * `messages.error`, 6 warnings and 2 infos across the view modules. That is the
 * codebase's own list of "something notable just happened", it is already
 * correct, and it is already maintained — a new view that reports a result gets
 * a sound for free, and one that reports nothing stays silent — the right
 * answer in both cases.
 *
 * The alternative was wiring a click handler per action. With ~180 call sites
 * that is 180 chances to attach the wrong tone, and every one of them would be
 * a lie the moment the server rejected the action anyway: a sound at click time
 * says "done" before anything has been done. These fire on the RESULT.
 *
 * NO AUDIO FILES, NO DEPENDENCY
 * -----------------------------
 * Tones are synthesised with the Web Audio API — a few oscillator nodes. That
 * keeps the rule this project runs on: no npm, no bundler, no new runtime
 * dependency, and nothing loaded from a third party. It is also the only way to
 * get a sound that is genuinely short; an mp3 of a 90ms beep is mostly header.
 *
 * THE AUTOPLAY CONSTRAINT, STATED HONESTLY
 * ----------------------------------------
 * Most actions here POST and redirect, so the sound has to play on a freshly
 * loaded page — and browsers block audio without user activation on that page.
 * Chrome exempts an INSTALLED PWA, which is how the owners and the Floor tablet
 * run this, so it is reliable there. In a plain browser tab (Office, on the
 * laptop) the first outcome of a session may be silent and every one after it
 * audible, because by then the page has been clicked. That is a browser rule,
 * not something to work around — and a silent confirmation is a missing nicety,
 * never a missing fact, because the banner is on screen either way.
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'workshopSoundEnabled';

    function enabled() {
        try {
            // Default ON. A workshop wants the confirmation; someone who does
            // not can turn it off per device, the same shape as the push
            // toggle.
            return localStorage.getItem(STORAGE_KEY) !== 'off';
        } catch (e) {
            return true; // private mode — nothing to read, fall back to on
        }
    }

    function setEnabled(on) {
        try { localStorage.setItem(STORAGE_KEY, on ? 'on' : 'off'); } catch (e) { /* harmless */ }
    }

    var context = null;

    function audio() {
        if (context) return context;
        var Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor) return null;
        try { context = new Ctor(); } catch (e) { return null; }
        return context;
    }

    /*
     * One note. `when` is an offset in seconds so a two-note tone is scheduled
     * in one go rather than with a setTimeout, which would drift.
     *
     * The gain ramp is not decoration: an oscillator started and stopped at
     * full volume produces a click at each end, which on a phone speaker is
     * louder and nastier than the note itself.
     */
    function note(freq, when, duration, peak) {
        var ctx = audio();
        if (!ctx) return;

        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        var start = ctx.currentTime + when;

        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, start);

        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(peak, start + Math.min(0.008, duration * 0.4));
        gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);

        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + duration + 0.02);
    }

    // Deliberately quiet and deliberately short. This plays in a room where
    // people are working; it is a confirmation, not an announcement.
    var TONES = {
        // Two notes rising — the shape everyone already reads as "went through".
        success: function () { note(660, 0, 0.09, 0.05); note(880, 0.075, 0.11, 0.05); },
        // Two notes falling, lower and blunter. Distinguishable from success
        // without looking, which is the whole point on a shop floor.
        error: function () { note(320, 0, 0.11, 0.06); note(240, 0.09, 0.15, 0.06); },
        // One flat mid note: something happened, it was not a failure, look up.
        warning: function () { note(500, 0, 0.16, 0.05); },
        // "Stop and read this." Single high note, shorter and noticeably
        // quieter than the three outcome tones — it accompanies a dialog that
        // is already demanding attention visually, so it only has to mark the
        // moment, not carry it. Distinct from `warning` on purpose: a warning
        // reports something that HAS happened, this asks about something that
        // has not yet.
        prompt: function () { note(950, 0, 0.07, 0.03); },
        // Camera shutter: crisp dual-click mechanical snap.
        shutter: function () {
            note(1500, 0, 0.025, 0.07);
            note(850, 0.032, 0.028, 0.05);
        }
    };

    /*
     * `blocking` says the caller is about to hand the main thread to something
     * that will not give it back until the user answers — i.e. `window.confirm`.
     * It changes only what happens on a SUSPENDED context: normally the tone is
     * queued behind `resume()`'s promise, but a promise cannot settle while
     * confirm() is up, so the beep would arrive *after* the answer, where it
     * reads as the outcome sound for the decision just made. Announcing the
     * wrong thing is worse than announcing nothing, so that case resumes for
     * next time and stays quiet now.
     */
    function play(kind, blocking) {
        if (!enabled()) return;
        var tone = TONES[kind];
        if (!tone) return;

        var ctx = audio();
        if (!ctx) return;

        // Suspended is the normal state before this page has been interacted
        // with. resume() succeeds silently in an installed PWA and rejects in a
        // plain tab that has not been clicked yet — either way nothing here may
        // throw into the caller.
        if (ctx.state === 'suspended') {
            if (blocking) {
                ctx.resume().catch(function () { /* nothing to do, and nothing owed */ });
                return;
            }
            ctx.resume().then(tone).catch(function () { /* blocked — the banner is still on screen */ });
            return;
        }
        try { tone(); } catch (e) { /* never let a beep break a page */ }
    }

    // Django's tags, mapped to the three tones. 'info' and 'debug' are
    // deliberately silent: they are not outcomes, and a sound for every one
    // would train everyone to stop hearing the two that matter.
    var TAG_TONES = {
        success: 'success',
        error: 'error',
        warning: 'warning'
    };

    function announce(root) {
        var banners = (root || document).querySelectorAll('[data-sound-tag]');
        // Only ONE sound per page load, whatever the banner count — three
        // errors are one failed action reported three ways, and three
        // overlapping tones is a noise, not information. Error wins over
        // success if a page somehow carries both, because the thing that went
        // wrong is the thing worth hearing.
        var chosen = null;
        banners.forEach(function (el) {
            var kind = TAG_TONES[el.getAttribute('data-sound-tag')];
            if (!kind) return;
            if (kind === 'error') { chosen = 'error'; return; }
            if (chosen !== 'error') chosen = chosen || kind;
        });
        if (chosen) play(chosen);
    }

    // Exposed so the drawer toggle and any future caller share one
    // implementation rather than growing a second AudioContext.
    window.WorkshopSound = {
        play: play,
        isEnabled: enabled,
        setEnabled: setEnabled
    };

    /*
     * "A confirmation just appeared."
     *
     * Every destructive action in this app goes through one of THREE things: a
     * Bootstrap modal (the shared `confirmSubmit()` helper, the logout confirm,
     * the delete confirms), a native <dialog> (the invoice settle box and its
     * large-discount gate), or a plain `window.confirm()` (the `onsubmit="return
     * confirm(…)"` forms — reactivating a shop, deleting an advance, marking a
     * car completed, unlocking a settled bill, merging a master-list entry).
     * Hooking all three HERE rather than at ~40 call sites is the same reasoning
     * as driving the outcome tones off the message tags: the app has a fixed
     * number of ways of asking "are you sure?", so a hook on each covers every
     * one of them, including any added later.
     *
     * The third was missed when this was written — the comment said "exactly
     * two" — and `window.confirm()` turned out to be 16 of the app's 35
     * confirmations, so roughly half of them asked their question in silence.
     * It is the oldest and plainest of the three, which is exactly why it was
     * easy to overlook: nothing in a template reading `onsubmit="return
     * confirm(…)"` looks like a dialog that needs wiring.
     *
     * This one fires on a real user gesture — the click that opened the dialog
     * — so unlike the outcome tones it is never blocked by the autoplay policy,
     * in an installed app or a plain tab. It is also what warms the
     * AudioContext, so on a page where a confirmation precedes the action, the
     * outcome tone afterwards is audible even in a browser tab.
     */
    function wirePrompts() {
        // Bootstrap modals: `show.bs.modal` bubbles to the document, so one
        // listener catches every modal in the app without knowing their ids.
        document.addEventListener('show.bs.modal', function (event) {
            // Not every modal is a question. The ones that are carry a confirm
            // button or are the shared confirm dialog; a plain "add a payment"
            // form modal is a workspace and should open silently.
            var modal = event.target;
            if (!modal || !modal.querySelector) return;
            var isQuestion = modal.id === 'confirmActionModal' ||
                             modal.id === 'logoutConfirmModal' ||
                             modal.hasAttribute('data-sound-prompt');
            if (isQuestion) play('prompt');
        });

        // Native <dialog>. There is no bubbling "opened" event, so the method
        // itself is wrapped — once, guarded, and calling through to the
        // original. Only `showModal()` is hooked: a non-modal dialog is not
        // demanding an answer.
        var proto = window.HTMLDialogElement && window.HTMLDialogElement.prototype;
        if (proto && !proto.__soundWrapped) {
            var original = proto.showModal;
            proto.showModal = function () {
                try { play('prompt'); } catch (e) { /* a beep may never block a dialog */ }
                return original.apply(this, arguments);
            };
            proto.__soundWrapped = true;
        }

        // window.confirm(). Wrapped for the same reason and in the same shape
        // as showModal above: there is no event to listen for, and the call
        // sites are inline `onsubmit` attributes scattered across sixteen
        // templates — which resolve `confirm` from the global at call time, so
        // replacing it here reaches every one of them, including the two in
        // shared .js files.
        //
        // `blocking` is passed because confirm() freezes the main thread until
        // it is answered; see play() for what that changes. The return value is
        // the native one, untouched — these sites are `return confirm(…)` on a
        // form's onsubmit, so anything else here would silently submit or
        // silently refuse to.
        if (!window.__confirmSoundWrapped) {
            var nativeConfirm = window.confirm;
            window.confirm = function () {
                try { play('prompt', true); } catch (e) { /* a beep may never block a question */ }
                return nativeConfirm.apply(window, arguments);
            };
            window.__confirmSoundWrapped = true;
        }
    }

    function start() {
        wirePrompts();
        announce(document);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
