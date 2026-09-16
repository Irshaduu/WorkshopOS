/*
 * pricing-core.js — the arithmetic behind the job card's suggested prices.
 *
 * WHAT IT DOES
 * ------------
 * Works out a suggested customer price from a cost and a markup, the markup a
 * price actually carries, and an inventory row's line total. Nothing else: no
 * DOM, no fetch, no globals. The job card template decides WHEN a box may be
 * filled; this file only answers WHAT the number is.
 *
 * THE SERVER NEVER DOES THIS
 * --------------------------
 * A suggestion reaches a bill only when a person presses Save with it on
 * screen. See `workshop/pricing.py` for why the server deliberately holds no
 * copy of this arithmetic — which is also what makes this the ONLY copy, so
 * there is no second implementation for it to drift from.
 *
 * EVERYTHING IS WHOLE NUMBERS — BigInt, never a float
 * ---------------------------------------------------
 * JavaScript's own decimals are binary and slightly wrong, and rounding UP
 * makes "slightly" a whole rupee: `700 * 1.1` is `770.0000000000001`, so
 * `Math.ceil` gives ₹771. Measured, not assumed. So every figure is parsed
 * straight from its text into hundredths (paise for money, hundredths of a
 * unit for a quantity) as a BigInt, and all arithmetic is integer arithmetic.
 * A markup is a whole percent for the same reason.
 *
 * PARSING IS STRICT
 * -----------------
 * `parseFloat("1,000")` is 1, which would fill ₹1.40 into a customer price.
 * Anything that is not a plain number — a comma, an exponent, a sign, a
 * non-ASCII digit, too many digits for the column — parses as null, and null
 * means "no suggestion", never a guess.
 *
 * Loaded as a plain <script> before the job card's own script, deliberately not
 * an ES module — the manifest storage rewrites URLs in CSS but not in JS (the
 * photos-core.js note). Tested by `workshop/tests/js/pricing-core.test.js`:
 *
 *     node --test "workshop/tests/js/*.test.js"
 */
(function (root, factory) {
    'use strict';
    var api = factory();
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;          // node --test
    } else {
        root.PricingCore = api;        // browser
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // The columns these boxes post into. Money is DecimalField(max_digits=10,
    // decimal_places=2) → at most 8 digits before the point; a quantity is
    // max_digits=8 → at most 6. A figure the column cannot hold is one the
    // server would refuse, so it is never parsed and never suggested.
    var MONEY_WHOLE_DIGITS = 8;
    var QTY_WHOLE_DIGITS = 6;
    var MONEY_CEILING = 9999999999n;     // 99,999,999.99 in paise
    var MAX_MARKUP = 999;

    var NUMBER = /^\s*([0-9]*)(?:\.([0-9]*))?\s*$/;

    /**
     * Plain decimal text → BigInt hundredths, or null.
     *
     * Accepts what Django's DecimalField accepts for these boxes: "1400",
     * "1400.5", "1400.50", "1400.", ".5", leading zeros, surrounding spaces.
     * Refuses everything else, including more than two decimals (the column
     * would round or refuse it) and a negative (no price here is negative).
     */
    function parseHundredths(text, wholeDigits) {
        if (typeof text !== 'string') { return null; }
        var m = NUMBER.exec(text);
        if (!m) { return null; }
        var whole = (m[1] || '').replace(/^0+(?=[0-9])/, '');
        var frac = m[2] || '';
        if (!m[1] && !frac) { return null; }             // "", ".", "  "
        if (whole.length > wholeDigits || frac.length > 2) { return null; }
        return BigInt(whole || '0') * 100n + BigInt((frac + '00').slice(0, 2));
    }

    function parseMoney(text) { return parseHundredths(text, MONEY_WHOLE_DIGITS); }
    function parseQty(text) { return parseHundredths(text, QTY_WHOLE_DIGITS); }

    /** A whole percent from 0 to 999, from a number or its text — or null. */
    function parseMarkup(value) {
        var text = (typeof value === 'number') ? String(value) : value;
        if (typeof text !== 'string' || !/^[0-9]{1,3}$/.test(text.trim())) { return null; }
        var n = parseInt(text.trim(), 10);
        return n <= MAX_MARKUP ? n : null;
    }

    /**
     * The suggested price, in paise, ROUNDED UP TO THE WHOLE RUPEE.
     *
     *     cost × (100 + markup) / 100, then up to the next rupee
     *
     * Up, never to nearest (the owners' rule): ₹10.10 becomes ₹11. Done as
     * `ceil(costPaise × (100 + markup) / 10000)` in integers, so ₹700 at 10% is
     * exactly ₹770. Null when the cost is missing or zero (a zero warehouse
     * cost means UNKNOWN, not free), the markup is not a whole 0–999, or the
     * result would not fit the column.
     */
    function suggestPaise(costPaise, markup) {
        if (typeof costPaise !== 'bigint' || costPaise <= 0n) { return null; }
        var m = parseMarkup(markup);
        if (m === null) { return null; }
        var num = costPaise * BigInt(100 + m);
        var rupees = num / 10000n;
        if (num % 10000n !== 0n) { rupees += 1n; }
        var paise = rupees * 100n;
        return paise <= MONEY_CEILING ? paise : null;
    }

    /**
     * The markup a price carries over its cost, as a whole percent ROUNDED
     * DOWN — so the badge never claims more profit than there is, and a
     * "20%" badge is never really 19.8%. Both arguments are BigInts on the
     * SAME scale. Null when either is missing or the cost is not above zero.
     */
    function markupPercent(price, cost) {
        if (typeof price !== 'bigint' || typeof cost !== 'bigint' || cost <= 0n || price < 0n) {
            return null;
        }
        var num = (price - cost) * 100n;
        var q = num / cost;                               // truncates toward zero
        if (num < 0n && num % cost !== 0n) { q -= 1n; }   // …so floor a negative
        return Number(q);
    }

    /**
     * An inventory row's markup when only its TOTAL is known (somebody typed
     * the total, which clears the unit price). Costs are compared per line:
     * total against cost-per-unit × quantity, scaled to one unit of measure.
     */
    function lineMarkupPercent(totalPaise, unitCostPaise, qtyHundredths) {
        if (typeof totalPaise !== 'bigint' || typeof unitCostPaise !== 'bigint' ||
            typeof qtyHundredths !== 'bigint' || qtyHundredths <= 0n) {
            return null;
        }
        return markupPercent(totalPaise * 100n, unitCostPaise * qtyHundredths);
    }

    /**
     * Unit price × quantity in paise, rounded exactly the way the SERVER
     * rounds it — `Decimal.quantize(Decimal('0.01'))` under Python's default
     * ROUND_HALF_EVEN — so the total on screen is the total that is saved
     * (1400.05 × 1.5 is 2100.08; a float `toFixed` says 2100.07). Null when it
     * would not fit the column, which the server refuses with a message.
     */
    function lineTotalPaise(ratePaise, qtyHundredths) {
        if (typeof ratePaise !== 'bigint' || typeof qtyHundredths !== 'bigint' ||
            ratePaise < 0n || qtyHundredths < 0n) {
            return null;
        }
        var n = ratePaise * qtyHundredths;                // in hundredths of a paisa
        var q = n / 100n;
        var r = n % 100n;
        if (r > 50n || (r === 50n && q % 2n === 1n)) { q += 1n; }
        return q <= MONEY_CEILING ? q : null;
    }

    /**
     * The unit price a hand-typed TOTAL works out to, in paise — for DISPLAY
     * ONLY, drawn grey in the Unit Price box and never posted.
     *
     * Rounded HALF-UP, because this is exactly the figure the printed bill puts
     * in its UNIT PRICE column: `invoice.derive_unit_price` divides the total by
     * the quantity and quantises with ROUND_HALF_UP. So the grey number on the
     * job card is the number the customer will read.
     *
     * WHY IT IS NEVER SAVED. The server rebuilds a line's total from a saved
     * unit price (`total = unit × quantity`), and a division rarely survives
     * the trip back: ₹1,000 for 3 is 333.33, and 333.33 × 3 saves ₹999.99; for
     * 7 it is 142.86, which saves ₹1,000.02 — a customer overcharged, with
     * nothing on screen to say so. Measured against Python before this was
     * written. Shown and not posted, the typed total stays the bill.
     */
    function unitFromTotalPaise(totalPaise, qtyHundredths) {
        if (typeof totalPaise !== 'bigint' || typeof qtyHundredths !== 'bigint' ||
            totalPaise < 0n || qtyHundredths <= 0n) {
            return null;
        }
        var num = totalPaise * 100n;          // paise per hundredth of a unit
        var q = num / qtyHundredths;
        var r = num % qtyHundredths;
        if (r * 2n >= qtyHundredths) { q += 1n; }
        return q <= MONEY_CEILING ? q : null;
    }

    /** 'loss' below zero, 'low' below the threshold, otherwise 'ok'. */
    function band(percent, low) {
        if (typeof percent !== 'number' || !isFinite(percent)) { return null; }
        if (percent < 0) { return 'loss'; }
        return percent < low ? 'low' : 'ok';
    }

    /** "40%", "−12%" (a real minus sign), and ">999%" past three digits. */
    function badgeText(percent) {
        if (typeof percent !== 'number' || !isFinite(percent)) { return ''; }
        if (percent > MAX_MARKUP) { return '>' + MAX_MARKUP + '%'; }
        return (percent < 0 ? '−' + (-percent) : String(percent)) + '%';
    }

    /**
     * Paise → the text a money INPUT holds: "1400", "2100.50". Never grouped —
     * "1,400" in a box is refused by the server and read as 1 by parseFloat.
     */
    function inputValue(paise) {
        var whole = paise / 100n;
        var frac = paise % 100n;
        if (frac === 0n) { return whole.toString(); }
        return whole.toString() + '.' + (frac < 10n ? '0' : '') + frac.toString();
    }

    /**
     * Paise → a figure to READ: Indian grouping, paise only when there are
     * any — "1,05,714.50", "1,400". The same rule as the `inr_amount` template
     * filter, so a cost drawn by the server and one written after a pick look
     * identical.
     */
    function displayRupees(paise) {
        var whole = (paise / 100n).toString();
        var frac = paise % 100n;
        if (whole.length > 3) {
            var head = whole.slice(0, -3);
            var tail = whole.slice(-3);
            var parts = [];
            while (head.length > 2) {
                parts.unshift(head.slice(-2));
                head = head.slice(0, -2);
            }
            if (head) { parts.unshift(head); }
            whole = parts.join(',') + ',' + tail;
        }
        if (frac === 0n) { return whole; }
        return whole + '.' + (frac < 10n ? '0' : '') + frac.toString();
    }

    return {
        parseMoney: parseMoney,
        parseQty: parseQty,
        parseMarkup: parseMarkup,
        suggestPaise: suggestPaise,
        markupPercent: markupPercent,
        lineMarkupPercent: lineMarkupPercent,
        lineTotalPaise: lineTotalPaise,
        unitFromTotalPaise: unitFromTotalPaise,
        band: band,
        badgeText: badgeText,
        inputValue: inputValue,
        displayRupees: displayRupees
    };
});
