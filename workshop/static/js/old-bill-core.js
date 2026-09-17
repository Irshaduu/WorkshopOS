/*
 * old-bill-core.js — what the Old Bill form understood, while somebody types.
 *
 * Reads the three date boxes and an amount copied off the paper, and works out
 * the bill's TOTAL. No DOM, no fetch, no globals.
 *
 * THE SERVER IS THE CONTROL
 * -------------------------
 * `workshop/old_bills.py` answers every one of these questions again on save,
 * and its answer is the one that counts. This copy only lets the screen say
 * "Fri 10 Apr 2026" or the TOTAL before the button is pressed. The two are
 * held to one list of cases — `workshop/tests/js/old-bill-cases.json` — which
 * both test suites read, so they cannot drift apart silently.
 *
 * MONEY IS WHOLE PAISE, AS BigInt — never a float (see pricing-core.js for the
 * measured reason). COMMAS AND ₹ ARE ACCEPTED on this form alone: this reader
 * can only drop a comma, never shrink a figure the way parseFloat("1,000") does,
 * and the worked-out TOTAL is checked against the XL bill by the typist.
 *
 * Loaded as a plain <script>, not an ES module. Tested by:
 *
 *     node --test "workshop/tests/js/*.test.js"
 */
(function (root, factory) {
    'use strict';
    var api = factory();
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;          // node --test
    } else {
        root.OldBillCore = api;        // browser
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    var MONTHS = ['january', 'february', 'march', 'april', 'may', 'june',
                  'july', 'august', 'september', 'october', 'november', 'december'];
    var SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

    // The columns: labour and the total are max_digits=12 (10 whole digits), a
    // part amount max_digits=10 (8 whole digits).
    var WHOLE_DIGITS = { labour: 10, total: 10, part: 8 };

    function text(value) {
        return String(value === null || value === undefined ? '' : value).trim();
    }

    function pad2(n) { return (n < 10 ? '0' : '') + n; }

    /** 4 for '4', '04', 'apr' or 'April' — a name only when it names ONE month. */
    function readMonth(value) {
        var t = text(value).toLowerCase();
        if (/^[0-9]{1,2}$/.test(t)) {
            var n = parseInt(t, 10);
            return (n >= 1 && n <= 12) ? n : null;
        }
        if (/^[a-z]{3,}$/.test(t)) {
            var found = [];
            for (var i = 0; i < MONTHS.length; i++) {
                if (MONTHS[i].indexOf(t) === 0) { found.push(i + 1); }
            }
            return found.length === 1 ? found[0] : null;
        }
        return null;
    }

    /**
     * {iso, label} or {error} from the three boxes. `todayISO` is built by the
     * caller from LOCAL date parts — never toISOString(), which is UTC and
     * reports yesterday for the whole of an IST morning.
     */
    function readDate(day, month, year, todayISO) {
        var d = text(day), m = text(month), y = text(year);
        if (!d && !m && !y) { return { error: 'Enter the date on the bill.' }; }
        if (!d || !m || !y) { return { error: 'Enter the day, month and year.' }; }
        if (!/^[0-9]{1,2}$/.test(d) || parseInt(d, 10) < 1 || parseInt(d, 10) > 31) {
            return { error: 'The day must be 1 to 31.' };
        }
        var mm = readMonth(m);
        if (mm === null) { return { error: 'The month must be 1 to 12, or its name.' }; }
        var yyyy;
        if (/^[0-9]{2}$/.test(y)) { yyyy = 2000 + parseInt(y, 10); }
        else if (/^20[0-9]{2}$/.test(y)) { yyyy = parseInt(y, 10); }
        else { return { error: 'The year must be like 26 or 2026.' }; }

        var dd = parseInt(d, 10);
        var when = new Date(Date.UTC(yyyy, mm - 1, dd));
        if (when.getUTCFullYear() !== yyyy || when.getUTCMonth() !== mm - 1 || when.getUTCDate() !== dd) {
            return { error: 'There is no ' + dd + ' ' + SHORT[mm - 1] + ' ' + yyyy + '.' };
        }
        var iso = yyyy + '-' + pad2(mm) + '-' + pad2(dd);
        if (iso > todayISO) { return { error: 'That date is in the future.' }; }
        return { iso: iso, label: DAYS[when.getUTCDay()] + ' ' + dd + ' ' + SHORT[mm - 1] + ' ' + yyyy };
    }

    /**
     * Whether a box already holds everything it can, so the cursor may move on.
     * A single 3 might still become 30, a single 1 might become 12, and a year
     * of "20" is always the start of 2024–2026 — the workshop has no 2020 bills.
     */
    function boxIsFull(kind, value) {
        var t = text(value);
        if (kind === 'day') { return /^[0-9]{2}$/.test(t) || /^[4-9]$/.test(t); }
        if (kind === 'month') {
            if (/^[0-9]{2}$/.test(t) || /^[2-9]$/.test(t)) { return true; }
            return /^[a-zA-Z]{3,}$/.test(t) && readMonth(t) !== null;
        }
        if (kind === 'year') { return /^[0-9]{4}$/.test(t) || (/^[0-9]{2}$/.test(t) && t !== '20'); }
        return false;
    }

    /** {blank: true}, {paise: BigInt} or {error} — see WHOLE_DIGITS for `kind`. */
    function readAmount(value, kind) {
        var t = text(value).replace(/\s+/g, '').replace(/,/g, '').replace(/₹/g, '');
        if (!t) { return { blank: true }; }
        var m = /^([0-9]*)(?:\.([0-9]{0,2}))?$/.exec(t);
        if (!m || (!m[1] && !m[2])) { return { error: 'not a number' }; }
        var whole = (m[1] || '').replace(/^0+(?=[0-9])/, '') || '0';
        if (whole.length > WHOLE_DIGITS[kind]) { return { error: 'too large' }; }
        return { paise: BigInt(whole) * 100n + BigInt(((m[2] || '') + '00').slice(0, 2)) };
    }

    /** '1,02,340.00' — Indian grouping, always two decimals, as the paper prints. */
    function formatPaise(paise) {
        var negative = paise < 0n;
        var abs = negative ? -paise : paise;
        var whole = (abs / 100n).toString();
        var frac = (abs % 100n).toString().padStart(2, '0');
        if (whole.length > 3) {
            var head = whole.slice(0, -3), tail = whole.slice(-3);
            head = head.replace(/\B(?=([0-9]{2})+(?![0-9]))/g, ',');
            whole = head + ',' + tail;
        }
        return (negative ? '-' : '') + whole + '.' + frac;
    }

    /**
     * The bill's TOTAL: labour + every part amount, the figure the server works
     * out and saves (`OldBill.update_totals`). Nothing on the form types it.
     * {paise}, {error: 'unreadable'} while any amount cannot be read (no total
     * would be honest), or {error: 'too large'} past the total's own column.
     */
    function billTotal(labour, parts) {
        var amounts = [readAmount(labour, 'labour')], sum = 0n;
        for (var i = 0; i < parts.length; i++) { amounts.push(readAmount(parts[i], 'part')); }
        for (var j = 0; j < amounts.length; j++) {
            if (amounts[j].error) { return { error: 'unreadable' }; }
            if (amounts[j].paise !== undefined) { sum += amounts[j].paise; }
        }
        if (sum >= BigInt('1' + '0'.repeat(WHOLE_DIGITS.total)) * 100n) { return { error: 'too large' }; }
        return { paise: sum };
    }

    /*
     * PART NAME SUGGESTIONS, TAKEN FROM THE JOB LINES ABOVE.
     *
     * The Job Card suggests a JOB from the parts on the card ("Engine Oil" +
     * "replaced"). The paper bill runs the other way — JOB PERFORMED is written
     * first, PART NAME under it — so here the suggestion turns round: a part is
     * a job line with its verb taken off ("Coolant replaced" -> "Coolant").
     *
     * ⚠ THE VERBS ARE THE JOB CARD'S OWN, word for word and in its order
     * (`jobcard_form.html`, "JOB PERFORMED — SUGGESTED FROM THE PARTS"). A test
     * reads both files and fails if they part company.
     */
    var JOB_VERBS = [
        'replaced',
        'removed and installed',
        'refurbished',
        'inspected',
        'repaired'
    ];

    /** 'Coolant' for 'Coolant replaced'; '' for a line that ends in no known verb. */
    function partFromJobLine(line) {
        var t = text(line).replace(/\s+/g, ' ');
        var lower = t.toLowerCase();
        var verbs = JOB_VERBS.slice().sort(function (a, b) { return b.length - a.length; });
        for (var i = 0; i < verbs.length; i++) {
            var tail = ' ' + verbs[i];
            if (lower.length > tail.length && lower.slice(-tail.length) === tail) {
                return t.slice(0, -tail.length).trim();
            }
        }
        return '';
    }

    /** One suggestion per part, first spelling kept, in the order typed. */
    function partSuggestions(lines) {
        var seen = {}, out = [];
        for (var i = 0; i < lines.length; i++) {
            var part = partFromJobLine(lines[i]);
            var key = part.toLowerCase();
            if (part && !seen[key]) { seen[key] = true; out.push(part); }
        }
        return out;
    }

    /*
     * EVERY PART NAME THE BOX OFFERS, in three groups:
     *
     *   1. this bill's own job lines, verb taken off — what this paper says;
     *   2. the warehouse CATEGORY names — a live bill prints a stock part under
     *      its category ("Engine Oil", never "Castrol Edge 5W-30"), and the Excel
     *      bills were written the same way;
     *   3. the Spare Parts master list.
     *
     * One entry per name, compared without case. A name the master lists
     * already hold keeps THEIR spelling even when a job line typed it another
     * way, so the same part is not spelled two ways across a car's history.
     * Suggestions only: nothing typed here is ever added to either list.
     */
    function partOptions(jobLines, categories, spares) {
        return mergeNames(partSuggestions(jobLines || []), categories, spares);
    }

    /** `first`, then the categories, then the master list — once each, lists' spelling. */
    function mergeNames(first, categories, spares) {
        var known = {}, i, name;
        var lists = [categories || [], spares || []];
        for (var l = 0; l < lists.length; l++) {
            for (i = 0; i < lists[l].length; i++) {
                name = text(lists[l][i]).replace(/\s+/g, ' ');
                if (name && !known[name.toLowerCase()]) { known[name.toLowerCase()] = name; }
            }
        }
        var seen = {}, out = [];
        function add(value) {
            value = text(value).replace(/\s+/g, ' ');
            var key = value.toLowerCase();
            if (value && !seen[key]) { seen[key] = true; out.push(known[key] || value); }
        }
        (first || []).forEach(add);
        for (l = 0; l < lists.length; l++) {
            for (i = 0; i < lists[l].length; i++) { add(lists[l][i]); }
        }
        return out;
    }

    /*
     * JOB PERFORMED SUGGESTIONS — the Job Card's own, built the Job Card's way:
     * every verb, in its order, over every part ("Engine Oil replaced" for all
     * parts before any "removed and installed"). The parts are the ones already
     * typed on this bill first, then the categories and the master list, because
     * on the paper the jobs are usually typed BEFORE the parts.
     */
    function jobOptions(billParts, categories, spares) {
        var parts = mergeNames(billParts, categories, spares), out = [];
        JOB_VERBS.forEach(function (verb) {
            parts.forEach(function (part) { out.push(part + ' ' + verb); });
        });
        return out;
    }

    /*
     * What the dropdown shows for what has been typed — at most `limit`, like
     * the Job Card's own endpoints (ten).
     *
     * A suggestion is offered when every typed word appears in it, which is the
     * Job Card's "contains" match for a single word. Those where every typed
     * word STARTS a word come first: typing "c" matches the c in every
     * "replaced", and "Coolant replaced" is the one that was meant.
     */
    function matchOptions(options, query, limit) {
        var words = text(query).toLowerCase().split(/\s+/).filter(Boolean);
        if (!words.length) { return []; }
        var starts = [], contains = [];
        for (var i = 0; i < options.length; i++) {
            var low = options[i].toLowerCase();
            if (!words.every(function (w) { return low.indexOf(w) !== -1; })) { continue; }
            var own = low.split(/\s+/);
            var atStart = words.every(function (w) {
                return own.some(function (o) { return o.indexOf(w) === 0; });
            });
            (atStart ? starts : contains).push(options[i]);
        }
        return starts.concat(contains).slice(0, limit || 10);
    }

    return {
        JOB_VERBS: JOB_VERBS,
        partFromJobLine: partFromJobLine,
        partSuggestions: partSuggestions,
        partOptions: partOptions,
        jobOptions: jobOptions,
        matchOptions: matchOptions,
        readMonth: readMonth,
        readDate: readDate,
        boxIsFull: boxIsFull,
        readAmount: readAmount,
        formatPaise: formatPaise,
        billTotal: billTotal
    };
});
