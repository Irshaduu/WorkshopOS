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
                        // supplier bill. Show it plainly rather than hiding the
                        // product: the part may well be physically on the shelf.
                        const stock = parseFloat(item.stock);
                        const tone = stock <= 0 ? 'text-danger' : 'text-muted';
                        opt.innerHTML =
                            `<i class="bi bi-box-seam me-2"></i><span class="fw-semibold">${item.name}</span>` +
                            `<span class="${tone} ms-2" style="font-size:0.75rem;">${item.stock} in stock</span>` +
                            `<div class="text-muted" style="font-size:0.7rem;">${item.category}</div>`;

                        opt.addEventListener('click', function (e) {
                            e.preventDefault();
                            input.value = item.name;
                            hiddenId.value = item.id;
                            if (stockHint) {
                                stockHint.textContent = `${item.stock} in stock`;
                                stockHint.classList.toggle('text-danger', stock <= 0);
                                stockHint.classList.toggle('text-muted', stock > 0);
                            }
                            suggestionsBox.innerHTML = '';
                            recalcRow(row);
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
