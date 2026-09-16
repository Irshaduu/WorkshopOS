/*
 * Tests for pricing-core.js — the suggested customer price on a job card.
 *
 *     node --test "workshop/tests/js/*.test.js"
 *
 * Outside the static tree for the reason photos-core.test.js gives: so
 * `collectstatic` never ships a test file.
 *
 * Every rounding expectation here was produced by PYTHON first, not written
 * from the formula — `Decimal.quantize(Decimal('0.01'))` for a line total and
 * `ROUND_CEILING` for a suggested price — because the whole point of the line
 * total is that the browser shows the figure the server will save.
 */

const { test } = require('node:test');
const assert = require('node:assert');

const P = require('../../static/js/pricing-core.js');

const money = (t) => P.parseMoney(t);
const qty = (t) => P.parseQty(t);

/* ------------------------------------------------------------------------- */
/* Parsing — strict, or a typo becomes a price                               */
/* ------------------------------------------------------------------------- */

test('a plain figure parses into paise', () => {
    assert.strictEqual(money('1400'), 140000n);
    assert.strictEqual(money('1400.5'), 140050n);
    assert.strictEqual(money('1400.50'), 140050n);
    assert.strictEqual(money(' 1400 '), 140000n);
    assert.strictEqual(money('1400.'), 140000n);
    assert.strictEqual(money('.5'), 50n);
    assert.strictEqual(money('0001400'), 140000n);   // leading zeros are not digits
});

test('a comma is REFUSED, never read as 1 the way parseFloat reads it', () => {
    // parseFloat("1,000") === 1, which would fill ₹1.40 into a customer price.
    assert.strictEqual(money('1,000'), null);
    assert.strictEqual(money('1,00,000'), null);
});

test('anything that is not a plain positive number is refused', () => {
    for (const bad of ['', ' ', '.', 'abc', '1e3', 'Infinity', 'NaN', '-5', '+5',
                       '12.345', '1 000', '₹1000', '١٢٣', '४०', '0x10']) {
        assert.strictEqual(money(bad), null, JSON.stringify(bad));
    }
    assert.strictEqual(money(undefined), null);
    assert.strictEqual(money(null), null);
    assert.strictEqual(money(1400), null);            // text only — boxes hold text
});

test('a figure too long for the column is refused, as the server would', () => {
    assert.strictEqual(money('99999999.99'), 9999999999n);   // 8 digits: fits
    assert.strictEqual(money('100000000'), null);            // 9 digits: does not
    assert.strictEqual(qty('999999.99'), 99999999n);
    assert.strictEqual(qty('1000000'), null);
});

test('a markup is a whole percent from 0 to 999, from a number or its text', () => {
    assert.strictEqual(P.parseMarkup(40), 40);
    assert.strictEqual(P.parseMarkup('40'), 40);
    assert.strictEqual(P.parseMarkup('0'), 0);
    assert.strictEqual(P.parseMarkup('999'), 999);
    for (const bad of ['1000', '40.5', '-5', '', 'abc', '४०', null, undefined, 40.5, NaN]) {
        assert.strictEqual(P.parseMarkup(bad), null, String(bad));
    }
});

/* ------------------------------------------------------------------------- */
/* The suggested price                                                       */
/* ------------------------------------------------------------------------- */

test('the owners\' own example: ₹1,000 at 40% is ₹1,400', () => {
    assert.strictEqual(P.suggestPaise(money('1000'), 40), 140000n);
});

test('it is exact where a float is not — ₹700 at 10% is ₹770, not ₹771', () => {
    // In JavaScript, 700 * 1.1 === 770.0000000000001 and Math.ceil of that is
    // 771. Measured before this was written; it is why the file uses BigInt.
    assert.strictEqual(700 * 1.1 > 770, true, 'the float trap this guards against');
    assert.strictEqual(P.suggestPaise(money('700'), 10), 77000n);
});

test('it always rounds UP to the whole rupee', () => {
    assert.strictEqual(P.suggestPaise(money('1234.56'), 40), 172900n);   // 1728.384
    assert.strictEqual(P.suggestPaise(money('1057.14'), 40), 148000n);   // 1479.996
    assert.strictEqual(P.suggestPaise(money('10.10'), 0), 1100n);        // ₹10.10 → ₹11
    assert.strictEqual(P.suggestPaise(money('0.01'), 40), 100n);
    assert.strictEqual(P.suggestPaise(money('1'), 40), 200n);            // 1.40 → 2
});

test('no known cost means no suggestion — a zero cost is UNKNOWN, not free', () => {
    assert.strictEqual(P.suggestPaise(0n, 40), null);
    assert.strictEqual(P.suggestPaise(money('0.00'), 40), null);
    assert.strictEqual(P.suggestPaise(null, 40), null);
    assert.strictEqual(P.suggestPaise(money('1,000'), 40), null);
});

test('an unusable markup means no suggestion', () => {
    assert.strictEqual(P.suggestPaise(money('1000'), '40.5'), null);
    assert.strictEqual(P.suggestPaise(money('1000'), 1000), null);
    assert.strictEqual(P.suggestPaise(money('1000'), undefined), null);
});

test('a price the column could not hold is never suggested', () => {
    // 99,999,999.99 at 999% is ₹1,09,90,00,000 — ten digits. The server would
    // refuse it, so the box is left alone instead of being filled with it.
    assert.strictEqual(P.suggestPaise(money('99999999.99'), 999), null);
    // Right at the edge: 71,428,570 × 1.4 is 99,999,998 and fits; one rupee of
    // cost more rounds UP to 10,00,00,000, one past the ceiling.
    assert.strictEqual(P.suggestPaise(money('71428570'), 40), 9999999800n);
    assert.strictEqual(P.suggestPaise(money('71428571'), 40), null);
});

/* ------------------------------------------------------------------------- */
/* The markup a price carries — the badge                                    */
/* ------------------------------------------------------------------------- */

test('the badge reads the real markup and rounds DOWN', () => {
    assert.strictEqual(P.markupPercent(money('1400'), money('1000')), 40);
    assert.strictEqual(P.markupPercent(money('1500'), money('1000')), 50);
    assert.strictEqual(P.markupPercent(money('1198'), money('1000')), 19);   // 19.8
    assert.strictEqual(P.markupPercent(money('1399'), money('1000')), 39);   // 39.9
});

test('a suggested price never reads below the markup it was made at', () => {
    // The price is rounded UP, so it carries at least its markup: ₹1,057.14 at
    // 40% becomes ₹1,480, which is 40.0004%. The badge must say 40, not 39 —
    // otherwise every suggestion would sit one step under its own product's
    // markup, and a 20% product would open yellow.
    for (const [cost, markup] of [['1057.14', 40], ['333.33', 20], ['0.07', 40], ['1234.56', 20]]) {
        const price = P.suggestPaise(money(cost), markup);
        assert.ok(P.markupPercent(price, money(cost)) >= markup, `${cost} at ${markup}%`);
    }
    assert.strictEqual(P.markupPercent(money('1000'), money('1000')), 0);
});

test('below cost floors towards the bigger loss', () => {
    assert.strictEqual(P.markupPercent(money('995'), money('1000')), -1);    // −0.5
    assert.strictEqual(P.markupPercent(money('900'), money('1000')), -10);
    assert.strictEqual(P.markupPercent(0n, money('1000')), -100);           // given away
});

test('no cost, no price, or a nonsense price → no badge', () => {
    assert.strictEqual(P.markupPercent(money('1400'), 0n), null);
    assert.strictEqual(P.markupPercent(null, money('1000')), null);
    assert.strictEqual(P.markupPercent(money('1400'), null), null);
});

test('an inventory row with only a total is measured per line', () => {
    // 10 L at ₹1,000 cost, ₹14,000 total → 40%.
    assert.strictEqual(P.lineMarkupPercent(money('14000'), money('1000'), qty('10')), 40);
    // 1.5 L at ₹1,000 cost, ₹2,100 total → 40%.
    assert.strictEqual(P.lineMarkupPercent(money('2100'), money('1000'), qty('1.5')), 40);
    assert.strictEqual(P.lineMarkupPercent(money('2100'), money('1000'), qty('0')), null);
    assert.strictEqual(P.lineMarkupPercent(money('2100'), money('1000'), null), null);
});

test('the colour bands: red below cost, yellow below 20, green from 20', () => {
    assert.strictEqual(P.band(-1, 20), 'loss');
    assert.strictEqual(P.band(0, 20), 'low');
    assert.strictEqual(P.band(19, 20), 'low');
    assert.strictEqual(P.band(20, 20), 'ok');
    assert.strictEqual(P.band(40, 20), 'ok');
    assert.strictEqual(P.band(null, 20), null);
});

test('the badge text', () => {
    assert.strictEqual(P.badgeText(40), '40%');
    assert.strictEqual(P.badgeText(0), '0%');
    assert.strictEqual(P.badgeText(-12), '−12%');
    assert.strictEqual(P.badgeText(999), '999%');
    assert.strictEqual(P.badgeText(9900), '>999%');
    assert.strictEqual(P.badgeText(null), '');
});

/* ------------------------------------------------------------------------- */
/* The line total — what the server will save                                */
/* ------------------------------------------------------------------------- */

test('unit price × quantity rounds the way the server rounds (half-even)', () => {
    const cases = [
        // [rate, qty, what Python's Decimal.quantize produced]
        ['1400', '10', '14000'],
        ['0.05', '0.5', '0.02'],       // exact half, rounds to even (down)
        ['0.15', '0.5', '0.08'],       // exact half, rounds to even (up)
        ['1400.05', '1.5', '2100.08'], // a float toFixed says 2100.07
        ['1400.25', '1.5', '2100.38'],
        ['1401', '1.15', '1611.15'],
        ['999.99', '0.01', '10'],
        ['1480', '2.5', '3700'],
        ['1057.14', '3', '3171.42'],
    ];
    for (const [rate, q, want] of cases) {
        const got = P.lineTotalPaise(money(rate), qty(q));
        assert.strictEqual(P.inputValue(got), want, `${rate} × ${q}`);
    }
});

test('the grey unit price is the one the printed bill prints (half-up)', () => {
    const cases = [
        // [typed total, qty, what invoice.derive_unit_price produced]
        ['1000', '3', '333.33'],
        ['1000', '7', '142.86'],
        ['3000', '1.5', '2000'],
        ['1000', '6', '166.67'],
        ['0.05', '2', '0.03'],        // an exact half rounds UP here, unlike a saved total
        ['14000', '10', '1400'],
        ['2101.50', '1.5', '1401'],
        ['999.99', '0.01', '99999'],
        ['1', '3', '0.33'],
        ['0', '4', '0'],
    ];
    for (const [total, q, want] of cases) {
        assert.strictEqual(P.inputValue(P.unitFromTotalPaise(money(total), qty(q))), want, `${total} / ${q}`);
    }
});

test('the grey unit price is shown, never saved — this is why', () => {
    // Saved, a divided unit price rebuilds a DIFFERENT total on the server.
    const unit = P.unitFromTotalPaise(money('1000'), qty('7'));        // 142.86
    assert.strictEqual(P.inputValue(P.lineTotalPaise(unit, qty('7'))), '1000.02');
    const third = P.unitFromTotalPaise(money('1000'), qty('3'));       // 333.33
    assert.strictEqual(P.inputValue(P.lineTotalPaise(third, qty('3'))), '999.99');
});

test('no quantity or no total, no grey unit price', () => {
    assert.strictEqual(P.unitFromTotalPaise(money('1000'), qty('0')), null);
    assert.strictEqual(P.unitFromTotalPaise(money('1000'), null), null);
    assert.strictEqual(P.unitFromTotalPaise(null, qty('2')), null);
    assert.strictEqual(P.unitFromTotalPaise(money('1,000'), qty('2')), null);
});

test('a total the column could not hold is not produced', () => {
    assert.strictEqual(P.lineTotalPaise(money('140000'), qty('1000')), null);
    assert.strictEqual(P.lineTotalPaise(null, qty('2')), null);
    assert.strictEqual(P.lineTotalPaise(money('1400'), null), null);
});

/* ------------------------------------------------------------------------- */
/* Writing figures back out                                                  */
/* ------------------------------------------------------------------------- */

test('a box value is never grouped, and drops a .00', () => {
    assert.strictEqual(P.inputValue(140000n), '1400');
    assert.strictEqual(P.inputValue(210050n), '2100.50');
    assert.strictEqual(P.inputValue(210005n), '2100.05');
    assert.strictEqual(P.inputValue(10000000000n), '100000000');   // no commas, ever
    // What goes into a box must read back as the same number.
    for (const paise of [0n, 5n, 140000n, 210050n, 9999999999n]) {
        assert.strictEqual(money(P.inputValue(paise)), paise);
    }
});

test('a figure to read is grouped the Indian way, like the inr_amount filter', () => {
    // Expected strings are what `inr_amount` itself printed for the same values.
    assert.strictEqual(P.displayRupees(140000n), '1,400');
    assert.strictEqual(P.displayRupees(10571450n), '1,05,714.50');
    assert.strictEqual(P.displayRupees(45236780000n), '45,23,67,800');
    assert.strictEqual(P.displayRupees(5n), '0.05');
    assert.strictEqual(P.displayRupees(99900n), '999');
});
