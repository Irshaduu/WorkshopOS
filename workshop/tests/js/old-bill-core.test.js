/*
 * Tests for old-bill-core.js — what the Old Bill form understood while typing.
 *
 *     node --test "workshop/tests/js/*.test.js"
 *
 * The date and amount cases come from old-bill-cases.json, which the Django
 * suite reads too (test_old_bills.py) — so the browser and the server are held
 * to the same answers, case for case.
 */

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const C = require('../../static/js/old-bill-core.js');
const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, 'old-bill-cases.json'), 'utf8'));

test('every shared DATE case reads the way the server reads it', () => {
    for (const c of CASES.dates) {
        const got = C.readDate(c.day, c.month, c.year, CASES.today);
        const where = JSON.stringify([c.day, c.month, c.year]);
        if (c.error) {
            assert.deepStrictEqual(got, { error: c.error }, where);
        } else {
            assert.deepStrictEqual(got, { iso: c.iso, label: c.label }, where);
        }
    }
});

test('every shared AMOUNT case reads the way the server reads it', () => {
    for (const c of CASES.amounts) {
        const got = C.readAmount(c.text, c.kind);
        const where = JSON.stringify([c.text, c.kind]);
        if (c.error) { assert.deepStrictEqual(got, { error: c.error }, where); }
        else if (c.blank) { assert.deepStrictEqual(got, { blank: true }, where); }
        else { assert.deepStrictEqual(got, { paise: BigInt(c.paise) }, where); }
    }
});

test('a box moves the cursor on only when it cannot take another character', () => {
    // Day: two digits, or a first digit no day can follow.
    assert.strictEqual(C.boxIsFull('day', '10'), true);
    assert.strictEqual(C.boxIsFull('day', '4'), true);
    assert.strictEqual(C.boxIsFull('day', '3'), false);   // 30, 31
    assert.strictEqual(C.boxIsFull('day', '1'), false);   // 10–19
    assert.strictEqual(C.boxIsFull('day', '0'), false);   // 01–09
    // Month: two digits, 2–9, or a name that is one month.
    assert.strictEqual(C.boxIsFull('month', '1'), false); // 10, 11, 12
    assert.strictEqual(C.boxIsFull('month', '6'), true);
    assert.strictEqual(C.boxIsFull('month', '12'), true);
    assert.strictEqual(C.boxIsFull('month', 'ma'), false);
    assert.strictEqual(C.boxIsFull('month', 'apr'), true);
    // Year: two digits, except "20", which is always the start of 2024–2026.
    assert.strictEqual(C.boxIsFull('year', '26'), true);
    assert.strictEqual(C.boxIsFull('year', '20'), false);
    assert.strictEqual(C.boxIsFull('year', '2026'), true);
});

test('Indian grouping, always two decimals, as the paper prints', () => {
    assert.strictEqual(C.formatPaise(3707500n), '37,075.00');
    assert.strictEqual(C.formatPaise(10234050n), '1,02,340.50');
    assert.strictEqual(C.formatPaise(7500n), '75.00');
    assert.strictEqual(C.formatPaise(0n), '0.00');
});

test('the sample bill JB-26-097 works out to its paper total', () => {
    const parts = ['', '', '', '', '2,820.00', '75.00', '11,880.00', '', '', '', ''];
    assert.deepStrictEqual(C.billTotal('22,300.00', parts), { paise: 3707500n });
});

test('a bill with nothing priced yet totals zero, and blanks add nothing', () => {
    assert.deepStrictEqual(C.billTotal('', []), { paise: 0n });
    assert.deepStrictEqual(C.billTotal('', ['', '  ', '75']), { paise: 7500n });
});

test('no total while any amount cannot be read', () => {
    assert.deepStrictEqual(C.billTotal('22300', ['7a5']), { error: 'unreadable' });
    assert.deepStrictEqual(C.billTotal('22,30.0.0', []), { error: 'unreadable' });
    assert.deepStrictEqual(C.billTotal('', ['100000000']), { error: 'unreadable' });   // a part past its column
});

test('a total too large for its column is said, exactly where the server refuses it', () => {
    // 9,999,999,999.99 is the most the total's column holds.
    assert.deepStrictEqual(C.billTotal('9999999999', ['0.99']), { paise: 999999999999n });
    assert.deepStrictEqual(C.billTotal('9999999999', ['99999999']), { error: 'too large' });
});

test('a part name is a job line with its verb taken off', () => {
    assert.strictEqual(C.partFromJobLine('Coolant replaced'), 'Coolant');
    assert.strictEqual(C.partFromJobLine('  Drive belt tensioner   replaced '), 'Drive belt tensioner');
    assert.strictEqual(C.partFromJobLine('Wheel bearing removed and installed'), 'Wheel bearing');
    assert.strictEqual(C.partFromJobLine('Brake Disc REFURBISHED'), 'Brake Disc');
    assert.strictEqual(C.partFromJobLine('Wheel alignment'), '');       // no verb, not a part
    assert.strictEqual(C.partFromJobLine('replaced'), '');               // a verb alone names nothing
});

test('part names offered: this bill first, then categories, then the master list, once each', () => {
    const got = C.partOptions(
        ['Tensioner Torx bolt replaced', 'engine oil replaced', 'Wheel alignment', ''],
        ['Engine Oil', 'Coolant', 'Air Filter'],
        ['Air Filter', 'ABS Sensor', '  Drive   belt ', 'coolant'],
    );
    assert.deepStrictEqual(got, [
        'Tensioner Torx bolt',   // only on this bill
        'Engine Oil',            // this bill's line, in the category's spelling
        'Coolant', 'Air Filter', // the rest of the categories
        'ABS Sensor', 'Drive belt', // the master list, without repeats
    ]);
});

test('job suggestions are the Job Card\'s: every verb in order, this bill\'s parts first', () => {
    const got = C.jobOptions(['Spark plugs', 'coolant', ''], ['Coolant'], ['Air Filter']);
    assert.deepStrictEqual(got.slice(0, 3), ['Spark plugs replaced', 'Coolant replaced', 'Air Filter replaced']);
    assert.strictEqual(got[3], 'Spark plugs removed and installed');
    assert.strictEqual(got.length, 3 * C.JOB_VERBS.length);
    assert.strictEqual(got[got.length - 1], 'Air Filter repaired');
});

test('the dropdown matches every typed word, word starts first, ten at most', () => {
    const options = ['Air Filter replaced', 'Coolant replaced', 'Cabin Filter replaced', 'AC Compressor replaced'];
    // "c" is inside every "replaced"; the names that START with it come first.
    assert.deepStrictEqual(C.matchOptions(options, 'c').slice(0, 3),
                           ['Coolant replaced', 'Cabin Filter replaced', 'AC Compressor replaced']);
    assert.deepStrictEqual(C.matchOptions(options, 'coo rep'), ['Coolant replaced']);
    assert.deepStrictEqual(C.matchOptions(options, 'FILTER'), ['Air Filter replaced', 'Cabin Filter replaced']);
    assert.deepStrictEqual(C.matchOptions(options, '   '), []);
    assert.deepStrictEqual(C.matchOptions(options, 'brake'), []);
    const many = Array.from({ length: 30 }, (_, i) => 'Part ' + i);
    assert.strictEqual(C.matchOptions(many, 'part').length, 10);
});

test('with no job lines and no lists there is nothing to offer', () => {
    assert.deepStrictEqual(C.partOptions([], [], []), []);
    assert.deepStrictEqual(C.partOptions(['Wheel alignment'], undefined, undefined), []);
});

test('the sample bill suggests its parts once each, in the order typed', () => {
    const jobs = ['Drive belt tensioner replaced', 'Tensioner Torx bolt replaced', 'Drive belt replaced',
                  'Convertible top actuator replaced', 'Coolant replaced', 'Spark plugs replaced',
                  'coolant replaced', ''];
    assert.deepStrictEqual(C.partSuggestions(jobs), [
        'Drive belt tensioner', 'Tensioner Torx bolt', 'Drive belt',
        'Convertible top actuator', 'Coolant', 'Spark plugs',
    ]);
});
