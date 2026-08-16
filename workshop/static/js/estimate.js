/**
 * The Estimate form — add rows, price the rows, suggest a price.
 *
 * Deliberately its own file rather than more of `script.js`. That file drives
 * the Job Card, which is the most load-bearing screen in the app; the three
 * cloning traps documented at the top of it were all silent failures, and there
 * is no reason for a new section to be able to reintroduce one there. Nothing
 * here is loaded on any page but the estimate form.
 *
 * Everything below is EVENT DELEGATION on the two list containers, never
 * per-element wiring. That is the whole reason this file has no "already wired"
 * bookkeeping: a row added five minutes from now is handled by a listener that
 * was attached before it existed, so there is nothing to re-run and nothing to
 * forget to re-run.
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var form = document.getElementById('estimate-form');
        if (!form) return;

        // =====================================================================
        // ADD A ROW
        // =====================================================================
        // The blank row lives in a <template>, not a hidden <div>. A hidden div
        // is in the document, so any global sweep — script.js's autocomplete
        // initialiser, for one — walks into it and treats the __prefix__
        // placeholder row as real. A template's contents are an inert fragment
        // that querySelectorAll cannot reach.
        function addRow(prefix) {
            var total = document.getElementById('id_' + prefix + '-TOTAL_FORMS');
            var list = document.getElementById(prefix + '-list');
            var tpl = document.getElementById(prefix + '-empty-template');
            if (!total || !list || !tpl) return;

            var index = parseInt(total.value, 10);
            if (isNaN(index)) return;

            // Replace on the template's MARKUP, not on a cloned node's
            // innerHTML: __prefix__ also appears in the row element's own
            // attributes (id, name), and setting .innerHTML on a clone leaves
            // the outer element's attributes untouched.
            var holder = document.createElement('div');
            holder.innerHTML = tpl.innerHTML.replace(/__prefix__/g, index);
            var row = holder.firstElementChild;
            if (!row) return;

            list.appendChild(row);
            total.value = index + 1;

            var first = row.querySelector('input:not([type=hidden]):not([type=checkbox])');
            if (first) {
                try {
                    row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                } catch (e) { }
                first.focus();
            }
        }

        form.querySelectorAll('[data-add-row]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                addRow(btn.getAttribute('data-add-row'));
            });
        });

        // Removing a row is deliberately NOT a control here — it is clearing the
        // name and saving. The server deletes any row whose name is blank (see
        // BlankRowIsNoRowFormSet in forms.py), so there is no ✕ to mis-tap on a
        // tablet and no client-side delete state to keep in step with it.

        // =====================================================================
        // UNIT PRICE x QTY -> AMOUNT
        // =====================================================================
        // Identical rule to the Job Card's inventory rows: the rate box is a
        // convenience staff often skip, so typing straight into Amount clears
        // the rate. That keeps the server-side rule (amount = rate x qty
        // whenever a rate exists) from overwriting a figure entered by hand —
        // the two can never end up disagreeing about one line.
        var partsList = document.getElementById('parts-list');

        function recalcRow(row) {
            var rate = row.querySelector('.estimate-rate');
            var qty = row.querySelector('.estimate-qty');
            var amount = row.querySelector('.estimate-amount');
            if (!rate || !amount) return;

            var r = parseFloat(rate.value);
            var q = parseFloat(qty ? qty.value : '');
            if (!isNaN(r) && !isNaN(q)) {
                amount.value = (r * q).toFixed(2).replace(/\.00$/, '');
            }
        }

        if (partsList) {
            partsList.addEventListener('input', function (e) {
                var row = e.target.closest('.est-row');
                if (!row) return;

                if (e.target.matches('.estimate-rate, .estimate-qty')) {
                    recalcRow(row);
                } else if (e.target.matches('.estimate-amount')) {
                    var rate = row.querySelector('.estimate-rate');
                    if (rate && document.activeElement === e.target) rate.value = '';
                }
                // Also on `.estimate-part-name`: clearing a name is how a line
                // is removed, so the total has to follow it down.
                recalcTotal();
            });
        }

        // =====================================================================
        // THE PRICE SUGGESTION
        // =====================================================================
        // What this part last sold for, shown as the Unit Price box's
        // PLACEHOLDER. It is never written into the field and never posted:
        // grey text is a suggestion, a filled box is a decision, and a price on
        // a document a customer is handed must be a decision somebody made. So
        // the worst case when this endpoint is slow, wrong, or down is that the
        // box keeps its ordinary label.
        var HINT_URL = form.getAttribute('data-price-hint-url');
        var hintCache = new Map();
        var hintTimer = null;

        function applyHint(row, data) {
            var rate = row.querySelector('.estimate-rate');
            if (!rate) return;
            // Restore the plain label when there is no history for this name —
            // otherwise a stale suggestion from the previous part sits under
            // the new one.
            if (!data || !data.found) {
                rate.placeholder = rate.getAttribute('data-placeholder') || 'Unit Price (₹)';
                rate.removeAttribute('title');
                return;
            }
            // SHORT. The box is ~150px wide and a placeholder is clipped, not
            // wrapped, so "≈ 1064.00 · avg of 5 sales" showed as "≈ 1064.00 · avg o"
            // — a truncated sentence reads as a bug. Paise are dropped when
            // there are none, because a suggestion does not need them.
            var figure = String(data.average).replace(/\.00$/, '');
            rate.placeholder = 'avg: ' + figure;
            // The sample size still matters when judging whether to trust the
            // number, so it moves to the tooltip rather than being lost.
            rate.title = 'Average of the last ' + data.count +
                (data.count === 1 ? ' sale' : ' sales') + ': ₹' + data.average;
        }

        function fetchHint(row, name) {
            if (!HINT_URL) return;
            if (hintCache.has(name)) {
                applyHint(row, hintCache.get(name));
                return;
            }
            fetch(HINT_URL + '?name=' + encodeURIComponent(name), {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    hintCache.set(name, data);
                    applyHint(row, data);
                })
                // Swallowed on purpose. A failed suggestion must not put an
                // error in front of someone quoting a customer.
                .catch(function () { });
        }

        function requestHint(row, immediate) {
            var input = row.querySelector('.estimate-part-name');
            if (!input) return;
            var name = input.value.trim();
            if (!name) {
                applyHint(row, null);
                return;
            }
            if (hintTimer) clearTimeout(hintTimer);
            if (immediate) {
                fetchHint(row, name);
            } else {
                hintTimer = setTimeout(function () { fetchHint(row, name); }, 400);
            }
        }

        if (partsList) {
            // Two triggers, because a name arrives two ways. Typing fires
            // `input` (debounced, so a ten-letter part name is one request, not
            // ten). Picking from the datalist or leaving the field fires
            // `change`, which runs immediately — by then the name is final.
            partsList.addEventListener('input', function (e) {
                if (!e.target.matches('.estimate-part-name')) return;
                var row = e.target.closest('.est-row');
                if (row) requestHint(row, false);
            });
            partsList.addEventListener('change', function (e) {
                if (!e.target.matches('.estimate-part-name')) return;
                var row = e.target.closest('.est-row');
                if (row) requestHint(row, true);
            });
        }

        // =====================================================================
        // RUNNING TOTAL (screen only)
        // =====================================================================
        // What the customer would be quoted if this were printed now. Purely a
        // reading aid — the figure that is stored and printed is recomputed on
        // the server by Estimate.update_totals(), never taken from here.
        var totalOut = document.getElementById('estimate-running-total');
        var labourInput = document.getElementById('id_labour_amount');

        function recalcTotal() {
            if (!totalOut) return;
            var sum = 0;
            if (partsList) {
                partsList.querySelectorAll('.est-row').forEach(function (row) {
                    // A row whose name has been cleared is on its way out, so
                    // it should stop counting the moment that happens rather
                    // than only after the save.
                    var name = row.querySelector('.estimate-part-name');
                    if (name && !name.value.trim()) return;
                    var amount = parseFloat((row.querySelector('.estimate-amount') || {}).value);
                    if (!isNaN(amount)) sum += amount;
                });
            }
            var labour = parseFloat(labourInput ? labourInput.value : '');
            if (!isNaN(labour)) sum += labour;

            totalOut.textContent = '₹ ' + sum.toLocaleString('en-IN', {
                minimumFractionDigits: 2, maximumFractionDigits: 2
            });
        }

        if (labourInput) labourInput.addEventListener('input', recalcTotal);
        recalcTotal();
    });
}());
