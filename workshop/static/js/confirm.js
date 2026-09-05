/*
 * confirm.js — the app's own confirmation card, and one press per form.
 * ---------------------------------------------------------------------------
 * TWO THINGS, IN ONE FILE, BECAUSE THEY ARE ONE PROBLEM. A dialog that asks
 * "are you sure?" and a guard that refuses the second tap are both answers to
 * the same question: how many times did the person mean to do this once.
 *
 * WHAT IT REPLACED. Twenty-one native browser dialogs — sixteen
 * `window.confirm()`, four `alert()`, one `prompt()`. They opened with
 * "127.0.0.1:8000 says", which is the browser talking rather than the app,
 * and they rendered the question, the reason and the way out as one flat grey
 * block that cannot carry a glyph, a colour or a field.
 *
 * ⚠ ONE DIALOG, NOT TWENTY-ONE. The markup is `_confirm_dialog.html`,
 * included once in base.html; the paint is `.wcf-*` in static/css/style.css.
 * That is the `.rpay-*` rule — a control drawn by more than one template gets
 * ONE declaration — and it is why converting these did not add twenty-one
 * things to keep in step. What varies per screen is the CONTENT and two theme
 * values; what must never vary is the shape.
 *
 * TWO WAYS IN, and the split is deliberate:
 *
 *   DECLARATIVE — `data-confirm` on a <form>. No page script at all, and it
 *   works on a row that arrived by AJAX after load, because the listener is
 *   delegated on `document`. This is how the plain "post this and go" sites
 *   are written: an undo, a handover, a reactivation, a delete.
 *
 *   IMPERATIVE — `wsConfirm(opts)`, returning a Promise. For the sites whose
 *   question depends on what was just typed: the rent date, the master-list
 *   merge, the settlement overwrite, a photo delete inside a fetch handler.
 *
 * ⚠ `bootstrap` IS NOT DEFINED WHILE THIS FILE IS PARSED. base.html renders
 * the content block above its script tags, so a `new bootstrap.Modal(...)` at
 * the top level would throw and take every listener below it with it — the
 * trap CLAUDE.md records. The instance is built on FIRST USE, by which time
 * the bundle has arrived.
 */
(function () {
    'use strict';

    var DIALOG_ID = 'wcfDialog';

    var instance = null;   // the Bootstrap Modal, built on first open
    var settle = null;     // the resolver of the question currently on screen
    var wired = false;

    /* ---------------------------------------------------------------- utils */

    function el(id) { return document.getElementById(id); }

    function finish(result) {
        if (!settle) { return; }
        var done = settle;
        settle = null;
        done(result);
    }

    /*
     * ⚠ HIDE THE PARENT MODAL FIRST, AND WAIT FOR IT TO ACTUALLY BE GONE.
     *
     * Hiding it at all is the house pattern (`confirmSubmit` on both shop pages
     * does the same) and it exists because a shown Bootstrap modal or offcanvas
     * runs a document-wide focus trap: an input in anything opened over it
     * cannot hold the caret, which is the defect that sent every Fleet reversal
     * to the Owner's Deletion History with a blank reason.
     *
     * ⚠ THE WAITING IS THE HALF THAT IS EASY TO GET WRONG, and it shipped wrong
     * for an hour. Calling `hide()` and then `show()` in the same tick does not
     * close the parent: Bootstrap refuses a `hide()` while `_isTransitioning`,
     * and even when it takes, the two overlap — MEASURED on the Data Cleanup
     * rename, which drew TWO stacked backdrops, left the parent open behind the
     * card, and then left ONE BACKDROP BEHIND after Cancel, because the parent's
     * own hide finished after the card had already claimed `modal-open`. A page
     * dimmed by a backdrop nothing can dismiss is unusable, and nothing in the
     * Django suite executes a line of this.
     *
     * So it resolves on `hidden.bs.modal` — the parent is fully gone before the
     * card is shown, one backdrop exists at any moment, and only one focus trap
     * is ever active. The timer is a safety net, not the mechanism: a parent
     * whose event never arrives must not swallow the question.
     */
    function hideParent(node) {
        return new Promise(function (done) {
            if (!node || !node.closest || !window.bootstrap) { done(); return; }

            var parent = node.closest('.modal');
            if (parent && parent.id === DIALOG_ID) { parent = null; }
            var drawer = node.closest('.offcanvas');
            var target = parent || drawer;
            if (!target || !target.classList.contains('show')) { done(); return; }

            var Ctor = parent ? window.bootstrap.Modal : window.bootstrap.Offcanvas;
            var open = Ctor.getInstance(target);
            if (!open) { done(); return; }

            /*
             * ⚠ ASK UNTIL IT ACTUALLY CLOSES, rather than asking once and
             * trusting an event. Bootstrap REFUSES a `hide()` while the modal
             * is still opening — it returns at its own `_isTransitioning` guard
             * and raises nothing at all. MEASURED on Data Cleanup, which
             * renders 222 modals: its open transition had not finished 900ms
             * after the trigger, so a single `hide()` was swallowed, the parent
             * stayed on screen behind the card, and the page was left with a
             * backdrop nothing could dismiss.
             *
             * Two smarter versions were tried first and both were worse. Waiting
             * on `hidden.bs.modal` alone hangs on the swallowed call. Stripping
             * `fade` to force an instant close cut the very transition the retry
             * was waiting on, because Bootstrap finishes a transition from the
             * classes present when it STARTED — so the parent never closed at
             * all. Re-asking on a timer needs no private state and no theory
             * about which event will arrive: it simply keeps asking until the
             * modal is gone.
             *
             * The ceiling exists so a parent that will not close can never
             * swallow the question — after it, the card opens anyway.
             */
            var hiddenEvent = parent ? 'hidden.bs.modal' : 'hidden.bs.offcanvas';
            target.addEventListener(hiddenEvent, function once() {
                target.removeEventListener(hiddenEvent, once);
                done();
            });

            var tries = 0;
            (function attempt() {
                if (!target.classList.contains('show')) { done(); return; }
                if (tries++ > 25) { done(); return; }
                open.hide();
                window.setTimeout(attempt, 80);
            }());
        });
    }

    /*
     * ⚠ LEAVE THE PAGE USABLE, WHATEVER HAPPENED. Two modals overlapping is the
     * one way this card can do real harm: Bootstrap keeps the backdrop and the
     * body scroll lock in its own bookkeeping, and if the counts disagree the
     * page is left dimmed by a backdrop nothing can dismiss and unable to
     * scroll — with no error anywhere to say why.
     *
     * The handoff above is what stops that happening, and it is clean in normal
     * use. This is the recovery, not the mechanism: after the card closes, make
     * the page agree with what is actually on screen. Nothing shown means no
     * backdrop and no lock; something still shown means the lock belongs there.
     *
     * It exists because ONE case survives the handoff — a form submitted inside
     * the ~150ms while its own modal is still animating open, where Bootstrap
     * interrupts its own transition and re-asserts `show` after the hide. That
     * needs a submit faster than anybody can type, so it is not worth more
     * machinery; it IS worth not leaving a workshop with a dead screen.
     */
    function heal() {
        var shown = document.querySelectorAll('.modal.show').length;
        if (shown) {
            document.body.classList.add('modal-open');
            return;
        }
        document.querySelectorAll('.modal-backdrop').forEach(function (b) { b.remove(); });
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('overflow');
        document.body.style.removeProperty('padding-right');
    }

    /* ------------------------------------------------------------- the card */

    function wire() {
        if (wired) { return; }
        wired = true;

        var yes = el('wcfYes');
        var box = el(DIALOG_ID);

        if (yes) {
            yes.addEventListener('click', function () {
                /*
                 * ONE PRESS. The button stops taking taps the instant this one
                 * lands, so a slow POST cannot be sent twice by somebody
                 * pressing again — which is half the reason every confirmation
                 * now goes through one control instead of twenty-one.
                 */
                yes.style.pointerEvents = 'none';
                yes.style.opacity = '0.6';
                var reason = el('wcfReason');
                finish({ ok: true, reason: reason ? reason.value.trim() : '' });
                if (instance) { instance.hide(); }
            });
        }

        if (box) {
            // Escape, the backdrop and Cancel all land here. Answering a
            // question by dismissing it is answering "no".
            box.addEventListener('hidden.bs.modal', function () {
                finish({ ok: false, reason: '' });
                heal();
            });
        }
    }

    /*
     * Open the card. Everything is written on every open and the previous
     * caller's leftovers are cleared — a reason typed and then cancelled must
     * never be filed against the next thing somebody deletes from here.
     */
    function ask(opts) {
        opts = opts || {};

        return new Promise(function (resolve) {
            var box = el(DIALOG_ID);

            /*
             * ⚠ NO CARD MEANS FALL BACK TO THE BROWSER, never fail silently.
             * A page whose include or bundle did not arrive must still be able
             * to ask before it destroys something — an ugly dialog beats an
             * action that happens with no question at all.
             */
            if (!box || !window.bootstrap) {
                resolve({ ok: window.confirm(opts.text || 'Are you sure?'), reason: '' });
                return;
            }

            wire();
            finish({ ok: false, reason: '' });   // settle anything still open
            settle = resolve;

            var card = el('wcfCard');
            var icon = el('wcfIcon');
            var title = el('wcfTitle');
            var text = el('wcfText');
            var wrap = el('wcfReasonWrap');
            var reason = el('wcfReason');
            var yes = el('wcfYes');
            var no = el('wcfNo');

            card.setAttribute('data-theme', opts.theme || 'warning');
            card.setAttribute('data-single', opts.single ? '1' : '0');
            icon.className = 'bi ' + (opts.icon || 'bi-exclamation-triangle-fill');
            title.textContent = opts.title || 'Are you sure?';

            // `text` is the safe path and what every caller uses; `html` exists
            // for a message carrying a figure it wants to weight, and is
            // app-authored in every case.
            if (opts.html) { text.innerHTML = opts.html; }
            else { text.textContent = opts.text || ''; }

            reason.value = '';
            wrap.hidden = !opts.reason;
            if (opts.reason) { reason.setAttribute('aria-label', 'Reason for ' + opts.reason); }

            yes.textContent = opts.ok || (opts.single ? 'OK' : 'Confirm');
            no.textContent = opts.cancel || 'Cancel';
            yes.style.pointerEvents = '';
            yes.style.opacity = '';

            hideParent(opts.parent || null).then(function () {
                // The question may have been settled while the parent was
                // closing — a second caller, or the page navigating. Do not put
                // a card on screen that nothing is waiting on.
                if (settle !== resolve) { return; }

                if (!instance) { instance = new window.bootstrap.Modal(box); }
                instance.show();

                if (opts.reason) {
                    box.addEventListener('shown.bs.modal', function once() {
                        box.removeEventListener('shown.bs.modal', once);
                        reason.focus();
                    });
                }
            });
        });
    }

    /* -------------------------------------------------------- one press only */

    function markBusy(form) {
        form.dataset.wsBusy = '1';
        /*
         * A release valve, not a feature. If the POST never lands — the server
         * hangs, the response is not a navigation — the form must not stay dead
         * for ever. Same 15s the nav bar's own safety timer uses.
         */
        window.setTimeout(function () { delete form.dataset.wsBusy; }, 15000);
    }

    // The reason cannot live in the dialog: this is ONE card and the forms are
    // one per row, often inside a dropdown. So it is copied into the posting
    // form as a hidden input at the moment Confirm is pressed.
    function carryReason(form, name, value) {
        var box = form.querySelector('input[name="' + name + '"]');
        if (!box) {
            box = document.createElement('input');
            box.type = 'hidden';
            box.name = name;
            form.appendChild(box);
        }
        box.value = value || '';
    }

    function submitForm(form) {
        /*
         * `requestSubmit()` rather than `submit()`, deliberately: it fires a
         * real submit event, so the nav bar paints and the busy latch below
         * catches it. A programmatic `.submit()` fires nothing — the trap this
         * codebase records for three other templates — so it is only the
         * fallback, and it latches by hand.
         */
        form.dataset.wsConfirmed = '1';
        if (typeof form.requestSubmit === 'function') {
            form.requestSubmit();
        } else {
            markBusy(form);
            form.submit();
        }
        delete form.dataset.wsConfirmed;
    }

    function optsFrom(form) {
        var d = form.dataset;
        return {
            text: d.confirm || '',
            title: d.confirmTitle || 'Are you sure?',
            icon: d.confirmIcon || 'bi-exclamation-triangle-fill',
            theme: d.confirmTheme || 'warning',
            ok: d.confirmOk || 'Confirm',
            cancel: d.confirmNo || 'Cancel',
            reason: d.confirmReason || '',
            parent: form
        };
    }

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || form.nodeName !== 'FORM') { return; }

        // The second tap on a form already on its way. This is the guard that
        // actually protects the server; the greying in style.css only says so.
        if (form.dataset.wsBusy === '1') { e.preventDefault(); return; }

        if (form.dataset.confirm !== undefined && form.dataset.wsConfirmed !== '1') {
            // Somebody else already refused this submit — do not ask about
            // something that is not going to happen either way.
            if (e.defaultPrevented) { return; }
            e.preventDefault();
            ask(optsFrom(form)).then(function (answer) {
                if (!answer.ok) { return; }
                if (form.dataset.confirmReason) {
                    carryReason(form, form.dataset.confirmReasonName || 'reason', answer.reason);
                }
                submitForm(form);
            });
            return;
        }

        /*
         * ⚠ LATCH IN A setTimeout, AND ONLY IF NOTHING REFUSED IT. Two rules in
         * one line. Disabling a control inside its own submit handler cancels
         * the submission in some browsers; and a submit that a later handler
         * prevents must leave the form UNLATCHED, or the Cashbook's steer —
         * which stops a submit and re-issues it — would kill the entry it was
         * protecting. Read after the event settles, `defaultPrevented` is final.
         */
        window.setTimeout(function () {
            if (e.defaultPrevented) { return; }
            markBusy(form);
        }, 0);
    }, false);

    // A page restored from the back/forward cache re-runs no script but does
    // fire pageshow, so a form latched on the way out does not come back dead.
    window.addEventListener('pageshow', function (e) {
        if (!e.persisted) { return; }
        document.querySelectorAll('form[data-ws-busy]').forEach(function (f) {
            delete f.dataset.wsBusy;
        });
    });

    /* ------------------------------------------------------------------ API */

    window.wsConfirm = ask;
    window.wsAlert = function (opts) {
        opts = opts || {};
        opts.single = true;
        opts.ok = opts.ok || 'OK';
        return ask(opts);
    };
    window.wsSubmit = submitForm;
    window.wsCarryReason = carryReason;
}());
