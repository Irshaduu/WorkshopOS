// ==========================================
// 0. SERVICE WORKER — registered on EVERY page
// ==========================================
//
// It used to be registered in exactly one place: inside enablePush() in
// notifications.js, which only runs when an owner opens the bell panel and taps
// "turn alerts on". So on an ordinary page load there was no service worker at
// all, and two things followed that both looked like hosting problems:
//
//   * Chrome never fired `beforeinstallprompt`, because that requires a
//     registered worker with a fetch handler — so the "Install Formula D"
//     banner in base.html could only ever appear on iOS, which uses a
//     different branch entirely. Moving hosts made it look newly broken; a new
//     ORIGIN is what actually reset it, since registration and install state
//     are both per-origin.
//   * Office and Floor have no bell at all, so no service worker could ever be
//     registered on their devices by any route.
//
// Here rather than inline in base.html because it runs on more than one page,
// which is this codebase's rule for what earns a place in a shared file.
// register() is idempotent, so notifications.js calling it again on subscribe
// changes nothing.
//
// Failure is a no-op by design: no HTTPS, an unsupported browser, or a worker
// that will not parse must never take the rest of this file down with it.
if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js', { scope: '/' })
            .catch(function (err) {
                console.warn('Service worker registration failed:', err);
            });
    });
}

// ==========================================
// 0b. NAVIGATION PROGRESS — the installed app has no chrome to borrow
// ==========================================
//
// manifest.json declares "display": "standalone", so in the installed app there
// is no address bar and no tab spinner. Every page here is a full server-
// rendered navigation over a `no-store` response — nothing is cached, so a tap
// costs a real round trip — and until the new page painted, the app answered
// that tap with nothing whatsoever. The bar is the answer.
//
// Delegated on `document` rather than wired per link, which is this codebase's
// rule and matters here for the ordinary reason: rows are added by script all
// over this app, and a per-element version would work on the ones that were
// there at load and silently do nothing on the rest.
//
// It only ever STARTS. The navigation it reports on replaces the document, so
// the bar is discarded with the page that created it — there is no completion
// path to get wrong. The two ways a navigation can fail to happen are handled
// explicitly below, because a bar left creeping over a page that is going
// nowhere is worse than no bar at all.
(function () {
    /*
     * A NAVIGATION paints at once; an IN-PAGE UPDATE has to earn it.
     *
     * Half this app's list screens do not navigate at all — Completed, Paid
     * Bills, Cashbook, Estimates and Pending Payments answer a filter tap by
     * fetching a partial, swapping innerHTML and calling history.pushState, so
     * the URL changes while the document never unloads. A navigation bar could
     * not see any of that, which is why those screens showed nothing at all.
     *
     * But they are also FAST — measured 22-37ms against the real database — and
     * a bar that flashes for 30ms is the noise this app deliberately avoids
     * everywhere else (see the outcome sounds, which stay silent on `info`).
     * So an in-page update schedules the bar and paints it only if it is still
     * running THRESHOLD_MS later. On the shop laptop that means nothing appears;
     * on an owner's phone, where the same fetch is a real round trip, it does.
     */
    var THRESHOLD_MS = 250;
    var SAFETY_MS = 15000;

    var bar = null;
    var safety = null;
    var scheduled = null;
    var pending = 0;        // in-flight in-page updates

    function paint() {
        if (bar) { return; }
        bar = document.createElement('div');
        bar.className = 'nav-progress';
        bar.setAttribute('aria-hidden', 'true');
        (document.body || document.documentElement).appendChild(bar);
        // Force a frame before the class lands, or the browser collapses the
        // two style changes into one and the animation never plays.
        void bar.offsetWidth;
        bar.classList.add('is-running');
        window.clearTimeout(safety);
        safety = window.setTimeout(clear, SAFETY_MS);
    }

    function clear() {
        window.clearTimeout(scheduled);
        window.clearTimeout(safety);
        scheduled = null;
        safety = null;
        if (bar && bar.parentNode) { bar.parentNode.removeChild(bar); }
        bar = null;
    }

    /*
     * A real navigation. It only ever STARTS: the page it reports on replaces
     * the document, so the bar is discarded with the page that created it and
     * there is no completion path to get wrong. The safety timer covers the two
     * ways a navigation can fail to happen — a download that never becomes a
     * page, or an inline handler that submits later by script.
     */
    function startNavigation() {
        if (bar) { return; }
        window.clearTimeout(scheduled);
        scheduled = null;
        paint();
    }

    /*
     * An in-page update. Returns the function to call when the work is done —
     * `.finally(done)` at the call site, so it runs on success and on failure
     * alike and a failed search can never strand the bar. Counted rather than
     * flagged, because a fast typist has several in flight at once.
     */
    function begin() {
        pending += 1;
        if (!bar && !scheduled) {
            scheduled = window.setTimeout(function () {
                scheduled = null;
                if (pending > 0) { paint(); }
            }, THRESHOLD_MS);
        }
        var settled = false;
        return function done() {
            if (settled) { return; }        // a double call must not go negative
            settled = true;
            pending -= 1;
            if (pending <= 0) { pending = 0; clear(); }
        };
    }

    window.navProgress = { begin: begin };

    // A page restored from the back/forward cache re-runs no script, but it does
    // fire pageshow — so if `no-store` is ever relaxed, a restored page does not
    // come back wearing a stale bar.
    window.addEventListener('pageshow', clear);

    document.addEventListener('click', function (e) {
        // Another handler already refused this click (a confirm() that was
        // cancelled, the Financial Lock, a guard). Nothing is navigating.
        if (e.defaultPrevented) { return; }
        // Anything but a plain left click opens a tab, saves a link or pastes —
        // the current page stays exactly where it is.
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }

        var link = e.target.closest ? e.target.closest('a') : null;
        if (!link) { return; }

        var href = link.getAttribute('href');
        // href="#" is how every AJAX filter in this app is written — it is a
        // handle for a script, not a destination.
        if (!href || href.charAt(0) === '#') { return; }
        if (link.hasAttribute('download')) { return; }
        if (link.target && link.target !== '_self') { return; }
        // Bootstrap drives the drawer, dropdowns and modals through <a> tags;
        // none of them leaves the page.
        if (link.hasAttribute('data-bs-toggle')) { return; }
        if (/^(javascript|mailto|tel|sms):/i.test(href)) { return; }
        if (link.origin && link.origin !== window.location.origin) { return; }
        // Same URL as now — the browser may not navigate at all.
        if (link.href === window.location.href) { return; }

        startNavigation();
    }, false);

    // Bubble phase, deliberately. The guards that refuse a submit — the
    // Financial Lock, the inventory quantity check — run in CAPTURE and call
    // stopPropagation(), so a refused save never reaches this at all. The
    // sixteen `onsubmit="return confirm(...)"` attributes DO reach it, with
    // defaultPrevented already set when the person answered no.
    document.addEventListener('submit', function (e) {
        if (e.defaultPrevented) { return; }
        var form = e.target;
        if (form && form.target && form.target !== '_self') { return; }
        startNavigation();
    }, false);
})();

document.addEventListener('DOMContentLoaded', function () {
    console.log("Workshop Script Loaded");

    // Which inventory rows have already been wired, tracked by element identity
    // rather than a data-* attribute. A data attribute is serialized into the
    // HTML, and the hidden #empty-inventory-form template is itself in the
    // document — so the first sweep marked the template's input as wired, every
    // cloned row inherited the mark, and no added row ever got a listener.
    //
    // Declared HERE, at the very top of the closure, and not beside the functions
    // that use them: `const` is not hoisted like `function` is, so a declaration
    // further down left these in the temporal dead zone when the initial
    // initializeAutocompleteInContainer(document) call ran. That threw a
    // ReferenceError which aborted the rest of this handler — silently, because it
    // surfaced inside a forEach callback.
    const wiredPickers = new WeakSet();
    const wiredPricing = new WeakSet();

    // ==========================================
    // 1. DYNAMIC FORMSETS
    // ==========================================
    const addConcernBtn = document.getElementById('add-concern-btn');
    const addSpareBtn = document.getElementById('add-spare-btn');
    const addLabourBtn = document.getElementById('add-labour-btn');

    if (addConcernBtn) {
        addConcernBtn.addEventListener('click', () => {
            addFormRow('concerns', 'concern-list', 'empty-concern-form');
        });
    }

    if (addSpareBtn) {
        addSpareBtn.addEventListener('click', () => {
            addFormRow('spares', 'spare-list', 'empty-spare-form');
        });
    }

    const addInventoryBtn = document.getElementById('add-inventory-btn');
    if (addInventoryBtn) {
        addInventoryBtn.addEventListener('click', () => {
            addFormRow('inventory', 'inventory-list', 'empty-inventory-form');
        });
    }

    if (addLabourBtn) {
        addLabourBtn.addEventListener('click', () => {
            addFormRow('labours', 'labour-list', 'empty-labour-form');
        });
    }

    function addFormRow(prefix, listId, emptyFormId) {
        const totalFormsInput = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
        const listContainer = document.getElementById(listId);
        const emptyFormTemplate = document.getElementById(emptyFormId); // Wrapper div

        if (!totalFormsInput || !listContainer || !emptyFormTemplate) return;

        // Get current count
        const currentCount = parseInt(totalFormsInput.value);

        // Clone the content properly
        // Note: empty-form-id div contains the row, so we take its first child
        const newRow = emptyFormTemplate.firstElementChild.cloneNode(true);

        // Regex to replace __prefix__ with current index
        const regex = new RegExp('__prefix__', 'g');
        newRow.innerHTML = newRow.innerHTML.replace(regex, currentCount);

        // Append
        listContainer.appendChild(newRow);

        // Update count
        totalFormsInput.value = currentCount + 1;

        // Re-Initialize Autocomplete for new row inputs
        initializeAutocompleteInContainer(newRow);
    }


    // ==========================================
    // 2. AUTOCOMPLETE LOGIC
    // ==========================================

    // Initial Setup
    initializeAutocompleteInContainer(document);

    function initializeAutocompleteInContainer(container) {
        const brands = container.querySelectorAll('.autocomplete-brand');
        const models = container.querySelectorAll('.autocomplete-model');
        const spares = container.querySelectorAll('.autocomplete-spare');
        const concerns = container.querySelectorAll('.autocomplete-concern');

        brands.forEach(input => setupAutocomplete(input, 'brands'));
        models.forEach(input => setupAutocomplete(input, 'models'));
        spares.forEach(input => setupAutocomplete(input, 'spares'));
        concerns.forEach(input => setupAutocomplete(input, 'concerns'));

        container.querySelectorAll('.inventory-item-search').forEach(setupInventoryPicker);
        // querySelectorAll only looks at DESCENDANTS. On the add-a-row path the
        // container passed in IS the new <tr class="inventory-row">, so searching
        // inside it finds nothing and the row's pricing would never be wired.
        inventoryRowsWithin(container).forEach(setupInventoryPricing);
    }

    // ==========================================
    // INVENTORY PICKER — a choice, not a name
    // ==========================================
    // The other autocompletes just write text into the field they are attached to.
    // This one is different on purpose: the draw is linked to the stock product by
    // FK, so the search box is only a way of choosing, and the id it selects is
    // what actually posts. A product can then be renamed without detaching it from
    // the job cards that used it.
    // `document` has no .matches, hence the guard.
    function inventoryRowsWithin(container) {
        const found = [...container.querySelectorAll('.inventory-row')];
        if (container.matches && container.matches('.inventory-row')) found.push(container);
        return found;
    }

    function setupInventoryPicker(input) {
        if (wiredPickers.has(input)) return;

        const row = input.closest('.inventory-row');
        const suggestionsBox = input.parentElement.querySelector('.autocomplete-suggestions');
        const hiddenId = row ? row.querySelector('.inventory-item-id') : null;
        const stockHint = row ? row.querySelector('.inventory-stock-hint') : null;
        // Marked only after the guard: a flag set before it would claim a row was
        // wired when setup had in fact bailed out.
        if (!suggestionsBox || !hiddenId) return;
        wiredPickers.add(input);

        let timeout = null;

        input.addEventListener('input', function () {
            // Typing after a pick invalidates that pick. Clearing the id is what
            // makes a half-edited name fail validation instead of silently keeping
            // the previously chosen product and drawing the wrong stock.
            hiddenId.value = '';
            // Clear the RED with the text. Leaving the class behind meant the
            // next product picked into this row inherited the previous one's
            // out-of-stock colour until something else toggled it.
            if (stockHint) {
                stockHint.textContent = '';
                stockHint.classList.remove('text-danger');
                stockHint.classList.add('text-muted');
            }

            const query = this.value;
            if (timeout) clearTimeout(timeout);
            if (query.length < 1) {
                suggestionsBox.innerHTML = '';
                return;
            }
            timeout = setTimeout(() => fetchInventory(query), 300);
        });

        document.addEventListener('click', function (e) {
            if (e.target !== input && e.target !== suggestionsBox) {
                suggestionsBox.innerHTML = '';
            }
        });

        function fetchInventory(query) {
            fetch(`/api/autocomplete/inventory-items/?q=${encodeURIComponent(query)}`)
                .then(r => r.json())
                .then(data => {
                    suggestionsBox.innerHTML = '';

                    // Say so, rather than showing nothing. An empty dropdown is
                    // indistinguishable from a request that has not come back
                    // yet, and this box is the one control on the form where
                    // typing is NOT how you enter a value — so silence invites
                    // exactly the wrong conclusion, that the name just has to be
                    // typed out in full.
                    if (!data.length) {
                        const none = document.createElement('div');
                        none.classList.add('list-group-item', 'py-2', 'text-muted');
                        none.style.fontSize = '0.78rem';
                        none.textContent =
                            'No stock product matches that. Products are added under ' +
                            'Inventory → Supplier → Add Product.';
                        suggestionsBox.appendChild(none);
                        return;
                    }

                    data.forEach(item => {
                        const opt = document.createElement('a');
                        opt.classList.add('list-group-item', 'list-group-item-action', 'py-2');
                        opt.style.cursor = 'pointer';

                        // Stock can be zero or negative — an overdraw waiting on its
                        // supplier bill. Neither hides the product: the part may
                        // well be physically on the shelf.
                        const stock = parseFloat(item.stock);

                        // The suggestion names the product and its category, and
                        // NOT the shelf count (2026-08-17, on the owner's
                        // instruction: "it's everywhere, it's interrupting").
                        // The count belongs in exactly one place — the line
                        // under the box, written the instant a product is
                        // picked, gone again once the card is saved. Printing it
                        // on every row of a dropdown as well made a number that
                        // matters once into the loudest thing in the list, and
                        // put it in front of somebody who is still reading names.
                        opt.innerHTML =
                            `<i class="bi bi-box-seam me-2"></i><span class="fw-semibold">${item.name}</span>` +
                            `<div class="text-muted" style="font-size:0.7rem;">${item.category}</div>`;

                        opt.addEventListener('click', function (e) {
                            e.preventDefault();
                            input.value = item.name;
                            hiddenId.value = item.id;
                            // What this draw is called outside the warehouse.
                            // The visible box holds the branded SKU; the Job
                            // Performed suggestions want the CATEGORY, which is
                            // also what the printed bill names the part by. The
                            // server renders this attribute for saved rows —
                            // this keeps it current for a row picked now.
                            if (row) row.dataset.category = item.category || '';
                            if (stockHint) {
                                stockHint.textContent = `${item.stock} in stock`;
                                stockHint.classList.toggle('text-danger', stock <= 0);
                                stockHint.classList.toggle('text-muted', stock > 0);
                            }
                            suggestionsBox.innerHTML = '';
                            recalcRow(row);
                            // Picking a product fills the search box, the hidden
                            // id and (via recalcRow) possibly the price — all by
                            // assigning `.value`, which fires no event. The job
                            // card form exposes this so it can drop the "empty"
                            // hairlines on the row and start warning about
                            // unsaved work. Absent on every other page.
                            if (window.jcFormTouched) window.jcFormTouched();
                        });
                        suggestionsBox.appendChild(opt);
                    });
                })
                .catch(err => console.error('Inventory picker error:', err));
        }
    }

    // Unit Price x Qty -> Customer Price.
    //
    // The rate box is a convenience that staff usually skip, typing the total
    // straight in. So it is an INPUT only: typing directly into Customer Price
    // clears the rate, which keeps the server-side rule (total = rate x qty when a
    // rate exists) from overriding a figure someone entered by hand. The two can
    // never end up disagreeing about one line.
    function setupInventoryPricing(row) {
        if (wiredPricing.has(row)) return;

        const rate = row.querySelector('.inventory-rate');
        const total = row.querySelector('.inventory-total');
        const qty = row.querySelector('input[name$="-quantity"]');
        if (!rate || !total) return;
        wiredPricing.add(row);

        rate.addEventListener('input', () => recalcRow(row));
        if (qty) qty.addEventListener('input', () => recalcRow(row));
        total.addEventListener('input', function () {
            if (document.activeElement === total) rate.value = '';
        });
    }

    function recalcRow(row) {
        if (!row) return;
        const rate = row.querySelector('.inventory-rate');
        const total = row.querySelector('.inventory-total');
        const qty = row.querySelector('input[name$="-quantity"]');
        if (!rate || !total) return;

        const r = parseFloat(rate.value);
        const q = parseFloat(qty ? qty.value : '');
        if (!isNaN(r) && !isNaN(q)) {
            total.value = (r * q).toFixed(2).replace(/\.00$/, '');
        }
    }

    function setupAutocomplete(input, type) {
        // Find or create suggestions container
        // Based on my template, it's usually the next sibling .list-group
        let suggestionsBox = input.nextElementSibling;
        if (!suggestionsBox || !suggestionsBox.classList.contains('list-group')) {
            // Fallback if structure changes, though template ensures it exists
            return;
        }

        let timeout = null;

        input.addEventListener('input', function () {
            const query = this.value;

            // Clear previous timeout
            if (timeout) clearTimeout(timeout);

            // Hide if empty
            if (query.length < 1) {
                suggestionsBox.innerHTML = '';
                return;
            }

            // Debounce fetch
            timeout = setTimeout(() => {
                fetchSuggestions(type, query, input, suggestionsBox);
            }, 300);
        });

        // Hide on click outside
        document.addEventListener('click', function (e) {
            if (e.target !== input && e.target !== suggestionsBox) {
                suggestionsBox.innerHTML = '';
            }
        });
    }

    function fetchSuggestions(type, query, inputObj, suggestionsBox) {
        let url = `/api/autocomplete/${type}/?q=${encodeURIComponent(query)}`;

        // Logic for Dependent Model Search
        // If searching models, try to find the brand value
        if (type === 'models') {
            const brandInput = document.querySelector('.autocomplete-brand'); // Simplistic find for main form
            if (brandInput && brandInput.value) {
                url += `&brand=${encodeURIComponent(brandInput.value)}`;
            }
        }

        fetch(url)
            .then(response => response.json())
            .then(data => {
                suggestionsBox.innerHTML = '';

                if (data.length === 0) return;

                data.forEach(item => {
                    const itemName = typeof item === 'object' ? item.name : item;
                    const itemSource = typeof item === 'object' ? item.source : 'master';

                    const itemDiv = document.createElement('a');
                    itemDiv.classList.add('list-group-item', 'list-group-item-action', 'py-2');
                    itemDiv.style.cursor = 'pointer';

                    if (itemSource === 'inventory') {
                        itemDiv.classList.add('list-group-item-warning', 'fw-bold', 'text-dark');
                        itemDiv.innerHTML = `<i class="bi bi-box-seam me-2"></i>${itemName}`;
                    } else {
                        itemDiv.textContent = itemName;
                    }

                    itemDiv.addEventListener('click', function (e) {
                        e.preventDefault(); // Prevent jump
                        inputObj.value = itemName;
                        suggestionsBox.innerHTML = ''; // Clear
                    });

                    suggestionsBox.appendChild(itemDiv);
                });
            })
            .catch(err => console.error('Autocomplete Error:', err));
    }

});
