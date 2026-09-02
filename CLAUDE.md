# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

WorkshopOS ("Titan") is a Django 5.2 monolith for a single premium automotive
workshop: job cards, inventory, spare/supplier shops, fleet billing, cashbook,
estimates, photos and owner analytics. Two apps — `workshop` (core business
logic) and `inventory` (stock + supplier shops).

Built for a **low-volume, high-value** workshop — appointment-driven premium
servicing, roughly 50 cars a month, seven staff, two owners. That is why RBAC
needs only three tiers, and why performance work is judged against realistic
load rather than generic "web scale" assumptions.

**PostgreSQL in both development and production.** Development runs against a
**local PostgreSQL** (`localhost:5432`, `titan_db`); production is Railway's own
PostgreSQL in the same project as the app. SQLite survives only for bulk
dummy-data seeding and the test suite — see "Which database am I on?".

⚠ *Development used to run against a hosted Neon instance in Singapore, and
several docs said so long after it stopped being true.* The move to local
Postgres removed the ~3.5 s of per-page network latency those docs warned about
and made `DB_SSLMODE=disable` correct locally. **If a doc mentions Neon, it is
stale — check `.env`.**

**Still pre-go-live.** Neither instance holds a real workshop's books, so don't
describe either as live production data. Deployment: `GO_LIVE_RUNBOOK.md`
(one-time procedure) and `RAILWAY_OPERATIONS.md` (ongoing platform reference).

## How to work here

1. **Fix the code, not the tests.** A failing test — especially a financial or
   security one — means the implementation regressed. Never bypass one.
2. **Every new rule gets a test.** One honest gap: the Django suite executes no
   JavaScript. `node --test "workshop/tests/js/*.test.js"` covers one DOM-free module.
   Everything else in the frontend must be verified by hand in a browser.
3. **Keep docs in sync in the same session.** New model/field, new route, new
   workflow, roadmap item completed → update the owning doc (see the ownership
   map at the end of this file).
4. **State what is true.** Unverified claims — performance numbers with no
   benchmark, counts nobody recounted — are what made these docs drift before.

---

# Deliberate decisions — do NOT "fix" these

Things that look like bugs, were raised as bugs, and are business rules. Each was
explicitly ruled *intended* by the owner. If you are about to correct one of
these, you are about to break the business.

Each entry states the rule, why it holds, and the test that guards it.

## Money & billing

**A part-paid bill books the shortfall as a discount, and is marked `PAID`.**
`update_bill_status` sets `payment_status='PAID'` as soon as `received_amount > 0`
and puts `total_bill_amount − received_amount` into `discount_amount`. A walk-in
customer has exactly one payment event — they pay at pickup, at whatever the
owner verbally agrees — so the unpaid portion *is* the discount. There is no
pay-the-rest-later case for them. Genuine multi-payment relationships are Fleet
Accounts (`BulkPayer`), which run through `bulk_payer_pay` and use `PARTIAL`
correctly. `audit_high_discounts` is the compensating control.
→ `workshop/tests/test_jobcard_views.py` asserts a ₹100 discount on ₹500-of-₹600.
**Do not delete it as "locking in a bug".**

**A large discount is a flat ₹3,500, not a percentage — and it is confirmed
before it happens.** `JobCard.HIGH_DISCOUNT_AMOUNT` is read by
`audit_high_discounts`, the `HIGH_DISCOUNT` alert and the settle dialog, so none
can disagree about where the line is. A proportion answered the wrong question:
30% is ₹1,500 off a ₹5,000 service (a rounding-down at pickup) and also ₹7,000
off a ₹60,000 rebuild (a quarter of a month's margin). Accepted consequence: a
small bill can be discounted to almost nothing silently, because the amount at
stake is genuinely small — the audit page still lists every one. The settle
screen shows the running shortfall on *every* settlement and says what it
becomes; the **confirmation fires only past the threshold**, because confirming
what cannot surprise anyone is how confirmations stop being read. It does not
block.
→ `ALargeDiscountIsConfirmedBeforeItHappensTests`, `TheDiscountAuditListsByAmountTests`

**Labour is ONE charge per job card, not a price per job line.** Work is quoted
whole — the customer is told "₹22,300 for the job" — so `JobCard.labour_amount`
holds the figure, typed once into the Total Labour box, and `JobCardLabourItem`
is a list of what was done with no money on it. `update_totals()` is
`spares + labour_amount`. `JobCardLabourItem.amount` is **dormant**: still on the
table, never written, never read for money.

Four consequences:
- **Saving a job line no longer recomputes the bill**, so `jobcard_create` and
  `jobcard_edit` must call `jobcard.update_totals()` explicitly, or a card whose
  only change was its labour figure keeps its old total forever. The seeder needs
  the same call.
- **Deleting a job line must NOT move money** — removing a typo from the job list
  cannot reduce a customer's bill. (`test_jobcard_properties` asserts the current
  rule; it was inverted when the per-line column stopped being written.)
- **`amount` is dropped from the formset entirely.** It used to render for Floor
  inside a `d-none` cell, and `_floor_locked_data` only rewrote the parts
  prefixes — so a Floor login could POST `labours-0-amount` and rewrite the labour
  charge. A field that does not exist cannot be posted. `labour_amount` needs the
  opposite treatment: it lives on the *card*, so `_floor_locked_data` pins it and
  its return value binds `JobCardForm` as well as the formsets.
- **`blank=True`, and empty means zero.** Plenty of cards are parts-only.
  `clean_labour_amount` turns empty into `Decimal('0')` (the column is NOT NULL,
  so cleaning to None would be an IntegrityError) and refuses a negative outright
  rather than clamping.
→ `TheLabourChargeLivesOnTheCardTests`, `LabourPrintsAsOneSubtotalTests`

**An unlocked edit that moves a SETTLED card's bill must fix the payment state —
and the two routes are fixed differently.** `_reconcile_settled_bill()` in
`views/jobcard.py`. The Financial Lock exists because editing a settled card is a
real need, but nothing followed the money afterwards.
- A **`PAID` walk-in** keeps its old `discount_amount`, so the Profit page read
  revenue off the new total while `received_amount` never moved. The discount is
  **recomputed** — that is the shortfall-is-the-discount rule applied to the new
  total, and a large jump trips the HIGH_DISCOUNT alert, which is the
  compensating control for exactly this.
- A **`BULK_PAID` fleet card** is the opposite: a fleet genuinely does pay later,
  so the extra is owed, not discounted. It drops back to **PARTIAL**, because
  `bulk_payer_pay` only cascades over PENDING/PARTIAL and the difference was
  otherwise uncollectable forever.
- **A bill that shrank below what was received is left alone in both cases** —
  that is an overpayment, not a shortfall, and inventing a refund would be
  guessing.
→ `EditingASettledBillKeepsThePaymentHonestTests`

**Every typed rupee amount goes through `workshop/money.py`, and the bound is
READ from the column.** Three failures, one rule: a figure too large for
`max_digits` is **stored by SQLite** (silently violating the declared precision)
and **rejected by PostgreSQL** with `numeric field overflow` — a 500 from a fat
finger. `Infinity` and `NaN` both parse as valid `Decimal`s, and they break a
bare `amount > 0` guard in **two different ways** — worth being precise about,
because the difference decides how the failure shows up. `Infinity` is genuinely
`> 0`, so the guard agrees with it and it is **written**, poisoning every
aggregate that touches the column. `NaN` never gets that far: in Python's
`decimal`, an *ordered* comparison against NaN (`< > <= >=`) raises
`InvalidOperation` — only `==` returns False quietly, and float NaN does not
behave this way — so the guard raises outside whatever `try` wrapped the parsing
and the page **500s**. One corrupts, one crashes; `parse_money` refuses both
before either can happen. `fit_text()` is the same story for strings — an
oversized note is another SQLite-accepts / Postgres-500s split, and is **trimmed
rather than crashed**.

**The four payment screens were wired to it late (2026-08-21).** Settling a
bill, paying a Fleet Account, paying a spare shop and paying a Supplies Shop each
carried a hand-rolled `try: Decimal(...)` plus a sign check, so the rule above
held everywhere except where money actually moves.
→ `workshop/tests/test_money_guards.py`

⚠ **AND EVERY CALLER MUST CHECK `<= 0` ITSELF — `parse_money` REFUSES A ZERO
BEFORE IT QUANTISES, WHICH IS NOT THE SAME THING** (found 2026-08-31, six call
sites, all of them wrong). The order inside the function is: reject NaN and
Infinity, reject out of range, reject `value < 0 or value == 0`, **then**
quantise to the column's two decimals. So `0.004` is genuinely greater than
zero, passes everything, and comes back as **`0.00`**.

What that does depends on the column, and both outcomes are bad:

- **A `CheckConstraint amount > 0` turns it into a 500.** Three models carry
  one — `CashbookEntry`, `SpareShopPayment`, `SupplierPayment` — plus
  `OwnerWithdrawal`, so writing `0.00` is an `IntegrityError` and the person
  gets an error page instead of "Enter a valid amount".
- **`BulkPaymentHistory` has NO such constraint**, so the fleet screen simply
  **wrote the row**: a ₹0 payment in the ledger, cascading nothing, with a
  history entry somebody then has to reverse.

The browser cannot catch it either — every one of those screens guards with
`parseFloat(amount) <= 0`, which `0.004` also passes.

`parse_money` is deliberately **not** changed to quantise first: rounding a
figure UP into validity would be the function saving a number nobody typed,
which is the rule the whole module exists for. The caller decides, in one line:
`if amount is None or amount <= 0:`. **All six now do.**

**`JobCard.paid_date` is when a bill was actually settled.** Set only when
`payment_status` becomes `PAID`/`BULK_PAID`, cleared when a payment is undone.
Paid Bills filters and sorts on it, never `updated_at` — that is `auto_now=True`
and changes on *any* save, so an old paid bill resurfaced under "Today" the moment
someone edited it for an unrelated reason.

**Financial Lock covers `PAID` and `BULK_PAID` alike**, enforced on both sides:
JS disables the fields and requires a confirm() to unlock; `jobcard_edit` rejects
the POST unless the hidden `financial_unlock` field is `"true"`. Don't remove
either half — the client-side lock alone is bypassed by a raw POST.

## Spare parts — the two routes

**A job-card spare's route is stored in `source`, never inferred. Do not
reintroduce name matching.** A part reaches a car either from a spare shop
(`source='SHOP'` — ordering workflow and shop ledger apply) or off the warehouse
shelf (`source='INVENTORY'` — `item` FK set, ordering fields meaningless).

Before this column the route was guessed from a NULL `shop` plus a
case-insensitive match of `spare_part_name` against `Item.name` — and the guess
was made *differently* in `inventory/signals.py` than in `analysis_engine.py`. A
part bought from a shop whose name happened to equal a stock product was deducted
from the warehouse by one rule while correctly billed as a shop purchase by the
other, so the shelf count drifted down until a restock bill papered over it.

**Every consumer reads `source`** — the stock signals, `analysis_engine.py`,
Stock History, the master-list rename.
→ `ShopPurchaseNeverMovesStockTests`, `DoubleCountRuleTests`

**`JobCardSpareItem.unit_price` is the workshop's COST, and its SHAPE differs by
route: a shop line's LINE TOTAL, a warehouse draw's cost PER UNIT.** Putting a
*customer* price in it on either route would make the margin report compute
revenue − revenue = zero.

- **Shop side is a line total.** The workshop enters what it was billed, not a
  rate — Office copies the figure off the spare shop's own bill. Multiplying it
  by the row's quantity turned 5,000 typed on a row of 2 into ₹10,000 owed, money
  nobody was billed. **A shop row's quantity no longer moves any money at all**;
  it is a description of what was bought, and it still prints on the invoice.
- **Warehouse side is per unit, and must not be "made consistent".** It is a
  weighted average of what the shelf paid, written by `JobCardSpareItem.save()`
  and rewritten by the date-ordered replay in `inventory/costing.py` — derived
  from the shelf, never typed — so a draw's cost is still `× quantity`.
- **`analysis_engine.SPARE_COST` is the one expression that knows which is
  which** (a `Case/When` on `source` over `SHOP_LINE_COST` and
  `WAREHOUSE_LINE_COST`) and **nothing may re-derive it.** It had been hand-rolled
  in five places — the engine, `SpareShop.update_totals()`, and three aggregates
  in `views/spare_shop.py` — five chances to fix one and leave four, and they
  would have disagreed exactly where it hurts: a shop's own page and the Profit
  page quoting different debts for the same rows. `models.py` imports it locally
  because `analysis_engine` imports `models`.
→ `test_a_shop_line_costs_what_was_typed_not_that_times_quantity`

**An inventory row's cost is DERIVED, not frozen.** The replay in
`inventory/costing.py` is **date-ordered**, so a draw is priced by the receipts
preceding *its own date* and a later-dated bill cannot reach back. Freezing broke
the workshop's actual rhythm — a Supplies Shop delivers, keeps its own book, and
the bill is only keyed when the collector comes at month end, so a month of draws
recorded no cost at all. `recompute_average_cost` rewrites any draw whose stored
cost disagrees with the replay. Only two things move a past draw and both should:
a bill **backdated to before it**, or an existing bill **corrected**. Nothing
customer-facing moves — that is `total_price`, never touched here.
→ `test_a_later_dated_bill_never_disturbs_an_earlier_draw`

**`JobCardSpareItem.customer_rate` is INPUT ONLY.** It backs the optional "Unit
Price" box on an inventory row (customer price per unit) and is never back-filled
from `total_price ÷ quantity`, so a null honestly means "nobody entered a rate".
When it *is* set, `total_price = customer_rate × quantity` is enforced on save, so
editing 7 L down to 4 L recomputes the bill. Staff usually skip the box and type
the total, so it must never be required. "Customer Price" is the UI label for
`total_price`, not a third field.

**A spare's shop can change, so BOTH ledgers must be refreshed.**
`JobCardSpareItem.save()` snapshots the previous `shop_id` and refreshes both — it
used to refresh only the new one, so moving a spare from A to B left A still
counting a row it no longer owned, and clearing the dropdown stranded the debt
entirely. The two job-card views need the same guard separately, because they
resolve the shop with `.update()` (which skips `save()`): they add the pre-edit
`spare.shop_id` to `shops_to_update`, a set of **ids**, not objects.
→ `MovingASpareBetweenShopsTests`

**An ARCHIVED spare shop stays attached to what was already bought from it.**
`_resolvable_shops()` resolves active shops **plus any archived one these rows
already point at**, and `_shop_options()` puts that archived shop back in the
dropdown so it round-trips. Without both halves the select rendered with nothing
marked, the browser posted a blank, the FK was cleared, and the purchase silently
disappeared from the shop's ledger — an unrelated edit was enough to erase ₹2,000
of debt. Archiving still hides a shop from cards that never used it.
→ `ArchivedShopKeepsItsDebtTests`

**Every unassigned spare is created through `_build_unassigned_spare()`, which
validates.** Bounds come from the columns (`unit_price` max_digits=10, `quantity`
max_digits=8); the name is truncated to the column width rather than crashing; an
archived shop is refused. The rules live in one helper rather than a view
precisely so a second "add" screen cannot inherit the holes by copy-paste — and
there is one, the Unassigned Hub's own Add a Purchase form. Its **shop select is
required**, because a row with no job card *and* no shop is filtered out of the
Hub, missing from every ledger, and unreachable by the only delete there is.
→ `AddUnassignedValidationTests`, `AddingFromTheHubTests`

**An unassigned spare can be deleted, and only from the Unassigned Hub.**
`spare_shop_delete_unassigned` is scoped to `job_card__isnull=True`: a spare
already fitted to a car is removed from that car's own Spare Parts section, so
every row has exactly one screen that owns deleting it. Permanent, and written to
`DeletionLog` under `ENTITY_UNASSIGNED_SPARE`.

**UNASSIGNED SPARES is open to FLOOR, add-only — and the price is stripped on the
SERVER.** The mechanic takes delivery of the part, so letting them record it is
the only way the shop ledger is not a day behind; but Floor is shown cost nowhere
else in this app. `unassigned_spares_hub` is `@staff_required`;
`unassigned_spare_edit` and `spare_shop_delete_unassigned` stay
`@office_required`. In `unassigned_spare_add`, a non-Office user gets
**`PRICE_NOT_SUPPLIED`** passed instead of `unit_price` being read at all, so a
crafted POST carrying a price writes nothing. Hiding the box is presentation;
this is the control.
- **An unpriced row stores NULL, never 0** — zero says the shop gave the part
  away and would settle the ledger at a figure nobody agreed.
  `SpareShop.update_totals()` coalesces NULL to 0, so it adds nothing to the
  balance until Office fills the figure in. Blank in Office's own price box means
  the same thing, on both the add and the edit path.
- **An ARCHIVED shop's rows stay listed, stay editable, and keep their shop** —
  same rule as `_resolvable_shops()`, same reason: archiving must never hide what
  is owed.
→ `test_a_crafted_price_from_floor_is_ignored`, `workshop/tests/test_unassigned_spares.py`

**"Ordered For" (`original_vehicle_info`) is a NOTE, not a link to a car.** Free
text, no picker, no FK — at the moment somebody types it the car often has no job
card to point at, and half the point is being able to write "Audi A4 — the white
one". It moves no money and joins no table. Trimmed to 255 rather than refused
(the SQLite-accepts / Postgres-rejects split again); clearing it stores NULL;
Floor may write it, since the mechanic takes delivery and it is not cost.
→ `OrderedForSaysWhichCarThePartIsForTests`

**A part cannot arrive before it was ordered, and the rule lives in
`workshop/spare_dates.py`.** `pair_problem(ordered, received)` is the one
implementation; `_clean_spare_dates` calls it rather than restating it. Three
things worth knowing: **half a pair is never wrong** (ordered-and-not-yet-arrived
is the normal mid-workflow state); a **future** date is refused, because it is far
more often a mistyped year than a plan; and the error attaches to `received_date`
so the mark lands on the box being corrected. A row marked DELETE is not argued
with.

The browser runs the same rule as you type — the chip turns red and one short
line appears in the panel. **Keep the two implementations word-for-word
identical**: the browser copy exists only to save the round trip, and the moment
it says something different from the refusal it causes it is worse than not being
there. Its date arithmetic is string comparison on the ISO values, and `todayISO()`
is built from LOCAL parts — never `toISOString()`, which converts to UTC and so
reports yesterday for the whole of an IST morning. The panel's Done button greys
out while the pair is wrong; that is **not a lock** — the panel still closes on
Escape and on an outside tap, because a popover whose only exit is conditional on
its contents is a trap.
→ `workshop/tests/test_spare_dates.py`

**A SPARE ROW WITH CONTENT BUT NO NAME IS REFUSED, NOT DROPPED.**
`spare_part_name` is `blank=True` and the blank-row sweep keyed on the name alone,
so a row carrying dates, a shop, a status and both prices but no name was thrown
away on save with nothing said. An entirely empty row is still dropped in the
browser and never reaches the server, so the refusal only fires on a row with real
content. Status is deliberately **not** counted as content — it defaults to
PENDING and is never blank, so it would make every untouched row look filled in.

**A SPARE's STATUS IS DERIVED FROM ITS DATES, by one rule**: `received` present →
RECEIVED, else `ordered` present → ORDERED, else PENDING. The case needing no
clause falls straight out — both dates present and Ordered edited must not jump,
and it does not, because received is still there. `originalStatus` is updated
*before* the value, so clearing both dates drops to Pending quietly instead of
interrupting with the backward-change dialog about a move nobody made. Delegated
on `document`, because a per-element version works on saved rows and silently does
nothing on every row added by "+ Add Spare".
→ `test_the_status_is_derived_from_the_dates_by_one_rule`

**A SPARE-SHOP PAYMENT IS DATED BY THE DAY THE MONEY MOVED, and that date is
typed.** `SpareShopPayment` carried only `created_at` (`auto_now_add`) while its
sibling `inventory.SupplierPayment` has had a `date` column since day one — so
the two ledgers, which are deliberately one screen, disagreed about what a
payment date even is. A shop's collector comes at month end and the payment is
often keyed the following week — and the keystroke date is what every window on
this page filtered and ordered by. So a payment made on the 30th and keyed on
the 3rd fell out of Last Month and turned up in This Month, with no route to
correct it. The same defect `CashbookEntry.date` exists to stop.

Four things about it:

- **The window follows `date`; the BALANCE follows nothing.** Every
  `created_at__date` filter on the shop detail and print views moved over in the
  same edit, because a column nothing reads is worse than no column — it looks
  fixed. What the shop is owed is still every purchase against every payment
  whatever filter is on: a debt is not a period.
- **`created_at` stays and is still written.** It is the audit trail and it
  breaks ties inside a day, which is why the ordering is `['-date',
  '-created_at']` rather than `date` alone — two payments back-dated to the same
  day still read in the order they were entered.
- **Lower stakes than the Cashbook, and that is why it survived so long.** A
  payment settles a debt that was already expensed when the part reached a car,
  so nothing here reaches the Profit page. The blast radius is reporting: this
  shop's own page, and its printed history.
- **Done BEFORE go-live deliberately.** Migration `0071` backfills existing rows
  from `created_at`, which is an approximation by construction — the keystroke
  date is precisely what the column exists to stop trusting. Doing it while
  nothing but demo data is being approximated is the whole point; after go-live
  it would bake a guess into the workshop's real books for ever.

The date box is the **Cashbook's own control**, values copied rather than
approximated — 46px calendar glyph, invisible `<input type="date">` over it,
amber and spelled out the moment it is not today, `showPicker()` for desktop
Chrome. The two are the same control asking the same question, and a box that
changed shape between two screens opened in one sitting reads as two different
products. The Pay confirmation repeats the date **only when it is not today**,
on the settle-dialog reasoning: confirming what cannot surprise anyone is how
confirmations stop being read.
→ `APaymentIsDatedByTheDayTheMoneyMovedTests`

**THE SUPPLIES SHOP SIDE CARRIES THE SAME RULE, and its control is a DIFFERENT
SHAPE on purpose.** `inventory.SupplierPayment` had the column since day one and
nothing ever wrote to it — no input on the form, nothing read in
`add_shop_payment` — so every supplier payment fell back to
`default=timezone.now` and was keystroke-stamped exactly as the spare-shop one
used to be. Closed in the same pass; it was the worse of the two by workflow,
since this is the side whose collector comes round *weekly or monthly*.

Everything downstream already read `date` — `Meta.ordering` is
`['-date', '-created_at']`, both list views order by it, the history partial
prints it — so the whole defect was the one missing input. The view now reads
`posted_date()` and refuses `is_future()` before writing the row.

⚠ **It does NOT copy the 46px calendar glyph on `add_payment.html`, and that is
not drift.** That page is stacked full-width `form-control-lg` fields, and a
glyph dropped into that column would be the one control that does not match its
neighbours. **What is copied is the behaviour**: capped at today, amber with the
day spelled out the moment it is not today. No confirmation modal — the native
input is plainly legible at full width there, unlike a collapsed glyph.
→ `ASupplierPaymentIsDatedByTheDayTheMoneyMovedTests`

⚠ **THE SHOP PAGE'S OWN INLINE FORM WAS MISSED ENTIRELY, and it is the door
people actually use.** The fix above landed on `add_payment.html`; the "Record a
Payment" row on `supplier_shop_detail` posts through a HIDDEN form carrying
amount, method and note, so no date ever reached the view and every payment made
there fell straight back to `default=timezone.now` — the keystroke, the exact
thing the column exists to stop trusting. `add_shop_payment` had read
`posted_date()` all along, which is why it survived: **a view test passes either
way, so the test has to go through the FORM.**
→ `TheShopPageOwnPayFormCarriesTheDateTests`

**ALL THREE PAYMENT FORMS ARE ONE CONTROL NOW — LITERALLY ONE, NOT THREE COPIES
KEPT IN STEP.** Spare shop, Supplies Shop and Fleet. All three say **"Record a
Payment"** (they said "Make a Bulk Payment", "Record Payment" and nothing, for
one act on screens an owner opens in one sitting), and since **2026-08-28** all
three render the same `.rpay-*` markup over one declaration in
**`static/css/style.css`** — the shared stylesheet `base.html` links on every
page. It had been three near-copies of the same form in three templates, which
is the shape a rule drifts out of, and they had already drifted three ways:

| | before | after |
|---|---|---|
| **spare shop** | 309px tall on a 375px phone, row wrapped to **three** ragged lines | 159px |
| **Supplies Shop** | 285px, the same three lines | 159px |
| **Fleet Account** | 397px of content in a 343px box — `flex` with no wrap **and no scroller**, so the row squeezed and the Pay button's right edge landed on the viewport edge | 159px |

The date glyph was also top-aligned against the wrapped fields, sitting 24px
below the box beside it; every control now bottom-aligns on one line.

**THE ROW NEVER WRAPS AND NEVER SQUEEZES — IT SCROLLS SIDEWAYS**, at every
width, the same single behaviour the Unassigned Hub's add form settled on and
for the same reason: one shape beats a layout that rearranges itself between
the tablet it is filled in on and the laptop it is checked on. Every field
carries a **fixed** width so a wide screen cannot stretch the row back into a
shape that wraps; only the Note grows, and it can only grow, because
`flex-wrap: nowrap` leaves no wrap to fall back into. Measured on the spare
shop's 502px row: 1280 and 820 hide nothing (the Note takes the slack at
298px), 640 hides 5px, 375 hides 271px.

**THE PAY BUTTON IS THE LAST THING IN THE SCROLLER, NOT PINNED BESIDE IT.** It
was pinned for one revision on the reasoning that the action must always be
reachable; the owner's call was that it scrolls with the row, and the row is
better for it — the strip reads in the order it is filled (when, how much, how,
why, **PAY**), and a bright red or green sliver at the right edge is a better
"there is more this way" cue than the gradient fade that used to sit there.
**That fade is gone with it**: laid over a coloured button at the end of the
scroll it would dim the one control that must not be dimmed. `min-width: 0` on
the scroll wrapper is still load-bearing — a flex item defaults to
`min-width: auto`, so without it the wrapper grows to the row's full width and
the card overflows the page instead of scrolling inside itself.

**RED PAYS A SHOP, GREEN TAKES A FLEET'S MONEY.** The only thing that differs
between the three forms, and it differs because the direction of the cash does:
paying a shop is money OUT, a fleet paying us is money IN. It is the Profit
page's own rule ("MONEY IN IS GREEN, MONEY OUT IS RED") applied to the button
that moves it, so an owner reading a shop ledger and a fleet ledger in one
sitting meets one vocabulary instead of a blue button that says nothing on
either. Both colours come from **one pair of custom properties**
(`--rpay-btn-a/b`), so a variant sets two values rather than restating the
gradient and the focus ring.

⚠ **The button carries NO DROP SHADOW**, at rest or on hover, on the owner's
instruction — which puts it in line with the Job Card's submit, recorded above
as carrying none either. A saturated red or green block on a white card needs
no help being found. **Hover is a `filter: brightness()` change, not a lift**:
a translate with nothing under it reads as the button coming loose.

**THE CHIP IN THE HEADING IS THE ACCOUNT'S NAME, AND NOTHING ELSE.** It carried
the balance beside the name for one revision and the figure came back out: the
dark stat bar directly above already states it in the largest type on the page
("BALANCE OWED", "Pending Balance", the fleet's "Balance" box), so printing it
again a few pixels below said one fact twice and put a loud red number next to
the only control on the card that should be pulling the eye. What the heading
needed was the half that bar does *not* answer from inside this card: **which**
account the Pay button is about to settle. It is **not uppercased** — a shop is
called "Fluid manjeri", not "FLUID MANJERI"; a caps treatment invented for the
four-letter word "OWED" makes a real proper noun harder to read — and it sits
on neutral ground rather than the old red wash, because with no money in it
there is nothing to warn about. It truncates rather than wraps, so the heading
is one line whatever the account is called, and the fleet needs no branch on
the sign of its balance. The footnote carries the thing the stat bar cannot
say: that a payment is allocated **oldest-first**, true of all three waterfalls
and previously written only on the spare shop's.

**ONE PAYMENT-METHOD LIST FOR THE WHOLE APP — `💵 Cash`, `📱 UPI`, `💳 Card`,
`🏦 Bank Transfer`, glyph for glyph, on ALL EIGHT selects.** The invoice's
Settle Bill dialog, both Cashbook forms, both Supplies Shop forms, the spare
shop's and the Fleet Account's. Four values (`CASH` / `UPI` / `CARD` /
`TRANSFER`) had drifted into **five spellings**: "Transfer", "Bank" and "Bank
Transfer" for one thing, "UPI", "UPI / GPay" and "UPI / QR Code" for another —
which on screens an owner opens in one sitting reads as different options
rather than one vocabulary. The glyph is what makes the row scannable at a
glance on the Floor tablet, so it belongs on all of them or none.

⚠ **What is deliberately NOT uniform is which one is selected first.** A
customer bill is settled by **UPI**, a shop is paid in **cash**, and a fleet
settles by **UPI**, so each list opens on what actually happens on that screen.
The fleet marks `UPI` as `selected` rather than reordering its options, so the
ORDER is identical everywhere and only the DEFAULT follows the screen.

The unrendered `bulk_payments_partial.html` carries the same four labels. It is
reachable from no view and no `{% include %}`, and was updated anyway so that
reviving it cannot quietly reintroduce a sixth spelling.

**A FLEET PAYMENT CAN CARRY A NOTE — `0073`, the last of the three ledgers to
get one.** `SpareShopPayment.note` and `inventory.SupplierPayment.note` have
existed since those models were written, so the shared control drew a Note box
on two screens out of three — and the one it skipped takes the workshop's
**largest single receipts**. A fleet collector hands over six figures against
several months of cars, and a cheque number or "Aug + Sep" on that row is the
only thing that later says which months it covered. Same column as its two
siblings character for character (`CharField(255)`, blank, null), asserted by a
test that reads all three widths rather than hard-coding one.

The box was deliberately left **off** for a revision rather than rendered over a
column that did not exist — an input whose value is silently dropped is the same
defect as a column nothing reads, and it looks fixed. Three things travel with
it: the view runs the note through **`fit_text`** (the SQLite-accepts /
Postgres-500s split, on the one screen where money is about to move — trimmed,
never crashed); **blank stores NULL**, because nobody wrote a note is a
different fact from somebody writing nothing; and it is **rendered back** in the
payment-history panel, only when there is one.
→ `AFleetPaymentCanCarryANoteTests`

**The box has NO container and no "Date" caption.** A bordered, filled 46px box
made the date look like a third input beside Amount and Method, when the whole
point is that it is almost always right and should cost nothing. It is the glyph
alone — still a 44px target, still amber with the day spelled out the moment it
is not today. A calendar glyph does not need a caption saying it is a date. The
same reasoning now covers the **₹** inside the Amount box: it is `aria-hidden`
decoration and the currency rides in the input's own `aria-label`, because
printing the symbol in the caption *and* in the box is one fact twice on a row
already scrolling for width.

⚠ **NO TRANSITION ON ITS COLOUR, and that is the recorded rule rather than
taste.** The amber IS the state, and a running transition outranks everything in
the cascade — so while it is in flight the computed colour is still the OLD one,
which is both a lie to the eye and unmeasurable in any tool that is not painting
frames. Caught exactly that way here: the first measurement read slate on a
back-dated box and only turned amber once the transition was disabled. Hover has
none either; on a glyph it needs none. `!important` is no longer needed on the
amber, because all that is left is `color` on an element carrying no Bootstrap
utilities.

**ALL THREE CONFIRMATIONS REPEAT THE DATE WHEN IT IS NOT TODAY**, and only two
of them used to. The Supplies Shop's never did, so the one form whose collector
comes round weekly could file a back-dated payment with nothing on screen naming
the month it lands in. Restating "today" on every payment is how a confirmation
stops being read — the settle dialog's own reasoning.

⚠ **THE CARD CARRIES A SLOW TRAVELLING LIGHT ON ITS BORDER, and it is held to
the Job Card's rule rather than exempted from it.** That page records "this is
the ONLY looping animation" because an idle shimmer is noise on a screen staff
work all day and costs battery on the Floor tablet. This one is the same
`--jc-orbit` technique at a fraction of the contrast, and it earns its place
four ways: **the card renders only when money is owed** (all three templates
gate it, and a settled shop shows "All Clear" instead), so it is not permanent
furniture; it is one ~1.5px pseudo-element; at **3.2s** it reads as a light
going round rather than as an edge that might be a rendering artifact; and it
quickens to 1.7s on `:focus-within`. `prefers-reduced-motion` drops the motion
and keeps the ring. (It shipped at 7s for a day and was too slow to register as
deliberate — the owner asked for it faster.)

**Its progressive enhancement is THREE-way, and the middle case is the one that
is easy to get wrong.** No `mask-composite` → the static inset ring.
`mask-composite` but no registered `@property` → **`var(--rpay-orbit, 0deg)`
falls back**, so the gradient still paints and the keyframe flips *discretely*
between 0deg and 360deg, which render identically, so the jump is invisible.
Both → the angle interpolates. **Without that `0deg` fallback the whole
`background` is invalid at computed-value time**, and since the `@supports`
block clears the static shadow the card would end up with no ring at all.


## Warehouse stock & costing

**Warehouse stock is allowed to go NEGATIVE. The old `Greatest(…, ZERO)` clamp is
gone and must not come back.** A job card records a part the mechanic has
*already physically taken*, so refusing or truncating that record does not put the
part back on the shelf — it only stops a mechanic mid-shift and makes the system
disagree with reality. The clamp never prevented an overdraw, it destroyed the
evidence of one: drawing 5 from a shelf of 2 stored 0 instead of −3, so when the
missing supplier bill arrived (+10) the count landed on 10 instead of 7 and three
units were invented, permanently and silently. A negative balance is self-healing
(−3 + 10 = 7) and is the signal that a Supplies Shop bill is missing.

**Negative is not "Low Stock".** Low means buy more; negative means a bill is
missing. The Low Stock page reports negatives as a separate amber **"stock
discrepancy"** banner, and `out_of_stock` counts `== 0` rather than `<= 0` so the
two counts are disjoint — one overdrawn product used to be reported as two
problems.
→ `NegativeStockTests`

**Warehouse cost is a weighted average, not FIFO — and it is always a full
replay.** FIFO was costed out and both routes total the same over the stock's
life; they disagree only about which month the cost lands in. The average won
because stock may go negative (FIFO has no layer to draw from) and because restock
bills are editable (FIFO re-costs every consumption that drew from the changed
layer). Per-batch cost is still recorded forever on `SupplierRestockItem`, so real
FIFO can be reconstructed later — this choice forecloses nothing. There is
deliberately **no incremental update path**: a moving average is path-dependent
and cannot be un-averaged, so a fast implementation plus a correcting one would be
two versions of one number free to disagree. Receipts move the average; draws do
not.

**A warehouse draw with no cost basis stores NULL, never 0.** `Item.avg_cost == 0`
means the cost is *unknown* — opening stock counted onto the shelf before any
supplier bill exists, or a product whose only restock bill was deleted — not that
the part was free. Storing 0 reported those parts as pure profit.
`analysis_engine.uncosted_draw_count()` counts such draws so the Profit page can
say so out loud. **Expect this on go-live day** until the first restock bill for
each product is entered.

**A Supplies Shop bill's DISCOUNT is part of what the stock cost, and its DATE
changes the average.** Four rules, all in `inventory/`:
- The discount is apportioned **pro-rata across the bill's lines by value** —
  `SupplierRestockItem.effective_unit_price`, which costing uses;
  `per_unit_price` stays gross for display. Without it, `avg_cost` came from gross
  prices while the Profit page expensed the discounted amount, so one purchase
  carried two costs.
- A discount **above its bill total is dropped and reported**, never applied: it
  made the bill negative, so the supplier appeared to owe the workshop and the
  Supplies Shops expense went negative, *raising* profit from a mistyped zero.
  `get_effective_amount` is floored at zero as a second line of defence.
- `update_totals()` **re-costs the bill's items itself** when a discount exists —
  the total is the apportionment denominator, is written with `.update()` (no
  signal), and is only known *after* the lines save, so a line's own post_save
  would divide by a stale or zero total.
- A `SupplierRestockBill` pre/post_save pair **re-costs when `bill_date` or
  `discount_amount` changes**, since neither lives on a line.
→ `inventory/test_supplier_costing.py`

**Stock moves only via signals.** Restock bills add, job-card draws remove. There
is **no manual stock-number editing anywhere** — Low Stock is read-only. Keep any
new stock-affecting change signal-driven rather than mutating `Item.current_stock`
in a view.

**Item creation happens only through Supplier → Add Product**
(`add_shop_catalog_item`), which requires an Average Stock threshold. A product is
one shared `Item` (unique per `category`+`name`) linked to shops via
`ShopCatalogItem` — the same product across shops is that one Item. A catalog
entry can be **deactivated**: it stays listed (greyed) and drops out of restock
bills. That exclusion is enforced **server-side** in
`shop_restock_bill`/`edit_restock_bill` via `_active_catalog_items()`, not just in
the picker template — any view writing `SupplierRestockItem` rows must re-validate
ids against the shop's active catalog, because those rows move real stock.

**`remove_shop_catalog_item` deactivates instead of deleting** when the shop has
restock-bill history (a hard delete would alter historical bill totals) **or the
product still holds stock** (stock is signal-only, so deleting would silently
destroy a countable quantity). Only a zero-stock, no-history orphan Item is
deleted — and, like every permanent delete, it writes
`DeletionLog.record(ENTITY_INVENTORY_ITEM, …)` first, inside the same atomic block.

**`average_stock` means "how many we normally keep in stock"**, not an alert
threshold — Low Stock fires below **25%** of it. Don't relabel the field as a
threshold in the UI; the two numbers are different by design.

**Category names dedupe on `__iexact` in both `add_category` and
`edit_category`.** Duplicates aren't cosmetic: `add_shop_catalog_item` resolves a
category with `get_or_create(name__iexact=…)`, which raises
`MultipleObjectsReturned` as soon as two spellings coexist. `Category.name` has no
DB-level `unique=True` (adding it needs a dedupe migration first), so the view
guards are the only protection. **Delete is allowed only while the category holds
no products** (`Item.category` is `PROTECT`).

**Stock History is a live query over `JobCardSpareItem`**, not the dormant
`ConsumptionRecord` model, and adds no signals. Both views filter
`job_card__is_deleted=False` and flag entries whose `spare_part_name` matches no
`Item` as **"not from stock"**. Rows are capped at `HISTORY_ROW_CAP` rather than
paginated, so the day-grouped layout is never split.
## Salary & advances

**Salary months have THREE states, following the workshop's own rhythm.** A month
is settled in the first days of the *next* one and the cash is handed over
immediately, so:

| State | Meaning | What is allowed |
|---|---|---|
| **open** | not yet settled | settle it |
| **locked** | settled, still the most recent | correctable via "Edit this settlement" in the ⋮ menu |
| **closed** | a newer month has since been settled | no edit, no delete, for anyone including owners |

Both the lock and the closure are enforced **in the view**, not just the template:
`salary_payment_form` refuses a POST without `settlement_unlock` and refuses a
closed month outright; `salary_payment_delete` refuses a closed month on the GET
as well, so its confirmation page never renders.

The locked fields use **`readonly`, never `disabled`**: a disabled input is not
submitted, and the settlement loop skips any staff member whose `leave_days` key
is absent — so disabling would silently write no line for anybody.

**Closure is a STORED one-way flag (`SalaryPayment.superseded`), never a computed
"is this the latest?"** The computed version looked tidy and was a ratchet that
turned both ways: deleting the newest settlement handed the frontier back to the
month before it, so the entire history could be walked backwards one delete at a
time — observed doing exactly that, 13 settled months down to 10. `superseded` is
set on every earlier month when a month is settled and is never cleared. Closure
is keyed to being superseded rather than to a date, deliberately: a rule like
"July closes once August opens" closes a month the instant it is settled whenever
settlement runs late, punishing exactly the month that was hardest to get right.
→ `ASettledMonthIsLockedTests`, `OnlyTheMostRecentSettlementCanBeChangedTests`,
`test_the_history_cannot_be_walked_backwards_by_deleting`

**A SETTLED month is a closed set of people; an UNSETTLED month is the roster.**
Both the settlement screen and its POST loop used to walk
`Mechanic.objects.filter(is_active=True)` regardless, so a staff member hired
*after* a month was settled appeared on it priced at **today's** salary, with a
live "Pay now" figure that was never paid — and re-saving the newest settlement
would have written that figure as a real line. Stored data was never wrong
(`salary_expense` reads `SalaryPaymentLine` only); the **page** was wrong, on a
screen an owner reads to decide what to pay.

The GET builds its rows from `payment.lines` when a settlement exists, the POST
skips any staff member with no existing line, and the template gates on
**`row.salary_used`, never `row.staff.current_salary`**. Reading a settled month
from its own lines also fixes the mirror defect for free — retiring someone used
to erase them from a month they were genuinely paid in. Adding somebody to a past
month is deliberately not an edit: delete the settlement and settle again.
→ `ASettledMonthIsAClosedSetOfPeopleTests`

**A month keeps the salary it was FIRST settled at, and there is no way to edit
it.** Salaries are revised at the same month boundary the previous month is
settled on, so whichever was done first used to decide the answer. The rule is:
**settle the finished month, then apply the raise.** `salary_used` is frozen at
the first settlement and every later save reuses it, so re-saving a month to fix
leave days can never reprice it. To settle at a different figure, delete the
settlement and settle again — Owner-only, logged. A crafted `salary_<pk>` POST
field is ignored.
→ `AMonthKeepsTheSalaryItWasSettledAtTests`

**A month cannot be SETTLED while someone handed an advance would get no
settlement line.** `salary_payment_form` writes a line only for staff who are
active *and* have `current_salary` set, and `salary_expense()` stops counting a
month's advances as loose the moment the month is settled — so an advance
belonging to anyone else was counted in **neither** place and settling dropped
that cash off the Profit page permanently. Neither state is exotic: the home page
has a whole "needs a salary" list, and staff leave. `_unsettleable_staff()` blocks
and names them. It fires **only** on staff who actually received money that month.

**An advance cannot be recorded into a settled month — blocked, not detected.** A
detector used to catch this afterwards and flag the month for re-settling, but it
nagged from another screen days later and, by existing, invited people back into
reopening a closed month. The message is **role-aware**, because deleting a
settlement is Owner-only: Office is told to ask an owner, an owner is told to
delete it themselves. Both are offered the second route — record it in the current
month with a note — which is the only route once the month is closed.
→ `AnAdvanceCannotEnterASettledMonthTests`

**AND IT CANNOT LEAVE ONE EITHER — a settled month's advances are FROZEN in
BOTH directions.** Only the entering half above was ever enforced. The bin in
the staff history modal called `salary_advance_delete`, which had no check at
all, so a settled — even a **closed** — month's advance could be removed with
one tap on a screen the settlement lock had otherwise shut.

It is the worse direction of the two. The paid `SalaryPaymentLine.advance_used`
keeps claiming money nothing records, and what follows depends on the month:

- **The most recent settlement is a real cash loss.** Re-saving it sums the
  advances afresh, so `advance_used` drops to zero and the net jumps by exactly
  the amount already handed over. Measured: a ₹3,000 advance deleted out of a
  settled month took the net from ₹17,000 to ₹20,000 on a ₹20,000 salary — the
  workshop paying cash it had already given.
- **A closed month's mismatch is permanent**, because that settlement can never
  be re-saved to notice it.

Three things carry it: the refusal is in the **view**, since the bin is
client-side; the message names a route the reader can actually take, which is
**three** branches rather than the add path's two (an owner is told to delete
the settlement, Office to ask one, and a **closed** month is told there is no
route — sending an owner at `salary_payment_delete` there would land them on a
button that refuses them on the GET); and `_mark_locked` flags the frozen rows
in **one query** so the delete is not offered at all, on the rule the audit menu
already follows — a door somebody can see but not open is worse than no door. An
open month's advance still deletes exactly as before.

**THE ROW'S ACTION IS A ⋮ MENU, AND EVERY ROW HAS THE SAME TRIGGER.** The delete
was a red bin pinned to the row — the loudest object in a list whose whole job is
to be read, on the one action that cannot be undone. A settled row first lost it
for a bare lock glyph, and that was worse in a way worth recording: **a lock says
"you cannot" without saying why**, and why is the only part anybody can act on.
So both rows carry a ⋮ and the MENU carries the difference — "Delete advance" in
red, or a disabled item plus the sentence naming the month that is in the way
("July 2026 is settled — this advance is part of that month's pay"). The rows
read as one list rather than two kinds of thing.

⚠ **The menu is armed in JS with `strategy: 'fixed'`, and that is load-bearing.**
The modal body is `overflow-y: auto`, and an absolutely-positioned menu inside a
scroller is **clipped** — invisibly, and only on the rows near its edges, which
is the trap CLAUDE.md already records twice. Popper escapes it on the fixed
strategy, which can only be set through `popperConfig` **in JavaScript** — so
`armAdvanceMenus()` runs after the fragment lands rather than leaving Bootstrap's
delegated handler to create each instance with the default absolute strategy.
`data-bs-display="static"` is **not** the fix; it drops Popper and is clipped
just the same. Measured at 375px: menu [112, 344] against a button right edge of
343, inside a modal spanning [8, 367].

⚠ **The menu is `max-width`ed as well as `min-width`ed.** The locked branch
carries a sentence, and left to grow it reached 352px inside a 370px modal and
hung off the left edge.
→ `AnAdvanceCannotLEAVEASettledMonthEitherTests`

**EVERY ADVANCE HOLDER MUST BE ON THE FORM THAT SETTLES THEM.**
`_unsettleable_staff` catches the two **standing** reasons somebody receives no
settlement line — no salary, retired. The **situational** one was open, and a
stale browser tab produces it on an ordinary working day: the settle form is up
while the office types leave days for seven people, somebody is hired and handed
an advance meanwhile, and the submitted payload carries no `leave_days_<pk>` box
for them. The loop skips anyone whose key is absent, so they get no line — and
their advance is now inside a settled month, which `salary_expense` excludes
from its loose-advance pass. The cash lands in **neither** place and drops off
the Profit page permanently.

Refused rather than papered over: writing them a line would price it at
**today's** salary with leave days nobody entered, which is the defect
`ASettledMonthIsAClosedSetOfPeopleTests` pins down. Reloading the page is the
whole remedy, and the message says so by name.

⚠ **Scoped to the FIRST settlement, and that scope is the point.** The harm is
the *transition* — settling is what moves the month out of the loose-advance
pass. On a month already settled the cash is already counted or already lost and
re-saving changes neither, so blocking there would refuse an ordinary correction
over a state the re-save did not cause and cannot fix. Nothing new can be
stranded either way, now that an advance can neither enter a settled month nor
leave one.
→ `EveryAdvanceHolderIsOnTheFormThatSettlesThemTests`

**The advance date box refuses BOTH bad dates while they are being picked, not
after the form is submitted.** `salary_advance_add` has always refused a
forward-dated advance and one dated into a settled month — but only once the
whole form had been filled in and sent, which is the settle screen's own "say it
before the button" rule broken one screen over. Two marks, one box:

- **Capped at today** with `max`, the same rule the Cashbook's own date control
  follows. Cash cannot have been handed over on a day that has not arrived.
- **A SETTLED month turns the box red and names it** — "July 2026 is already
  settled, so an advance can't be dated into it" — and disables Save. The months
  ride over as `json_script`, never interpolated into markup, and they are the
  query the year list is already built from, so it costs nothing.

Both are presentation; **the view stays the control**. The comparison is done on
the `'YYYY-MM'` the input's value already starts with — no `Date` object, so no
timezone can shift it a day and therefore a month.

⚠ **That comment block cost a real defect on the way in.** It was written as a
`{# … #}` spread over four lines, which stops being a comment — four lines of
developer prose rendered inside the Give an Advance modal, over the date box.
`test_template_comments.py` catches exactly this and was simply not run. **Run
it after touching any template.**
→ `TheAdvanceDateBoxCannotOfferTomorrowTests`

**Overtime is one amount per person per month, added to the net.** Only a few
staff have any, so it is a single figure entered at settlement rather than an
hours-and-rate calculation. Stored on `SalaryPaymentLine.overtime_amount` and
folded into `net_amount`, so the wage cost the Profit page reads (`net + advance`)
includes it with no change to `salary_expense()`.

⚠ **AN OVERTIME FIGURE THAT CANNOT BE USED IS REFUSED, NOT ZEROED — this
REVERSES what this file said until 2026-08-28**, on the owner's decision. It read
"junk input falls back to zero", and the fallback was the defect. **`5,000` typed
with a comma is the case that decided it**: `Decimal` cannot read it, so the
settlement saved ₹0, underpaid by ₹5,000, and the screen showed the right number
the whole time, because the running total is computed in the browser with
`parseFloat`. Silent, and in the direction that shorts the staff member. It is
now the leave-days rule applied to the other typed box on the same form — refused
outright rather than clamped, because a fallback saves a number nobody typed.

Four things carry it:
- **The split is NOTHING TYPED versus SOMETHING UNUSABLE**, and both halves are
  load-bearing. An absent key is the ordinary case (the box only exists on rows
  the form drew) and an empty one is somebody clearing it; both mean no overtime
  and must stay ₹0, or an untouched settlement would refuse itself.
- **It is parsed in the guard, not at write time**, beside the leave-days check,
  and the write loop reads the parsed value rather than the box a second time.
- **Both guards report before returning**, so a form wrong in both places is
  corrected in one pass rather than one round trip per mistake.
- **The bound in the message is READ from the column and FLOORED to whole
  rupees.** The true ceiling is 99,999,999.99, and `:,.0f` rounds that UP to
  100,000,000 — a figure the guard itself rejects, so the message would have
  named a bound that does not work. Caught by measuring the live refusal, not by
  reading the code.

*Not fixed, and knowingly:* an overtime near that ceiling overflows the
`net_amount` it is added to (same `numeric(10,2)`). It predates this guard, needs
an authenticated user typing a figure no workshop has, and answering it means
per-row conditional bounds.
→ `OvertimeIsAddedToThePayTests`

**Retiring a staff member warns about their unsettled advances, at the moment it
happens.** Retiring someone who still holds advances is legitimate, but the
settle-guard then refuses that month until they are reactivated. Control Hub is
where the click happens and Salary & Advance is where it bites, so without a word
at the click the owner got a green tick and Office hit a wall days later.
`_unsettled_advance_total()` counts only months with no `SalaryPayment`. **The
warning never blocks.**

**`leave_days` is bounded, and the settlement month is validated.** `-10` produced
a net *above* the salary (a negative deduction pays more) and `400` produced a
large negative — both now rejected outright rather than clamped, because a clamp
saves a number nobody typed. The month used to come straight off the URL, so
`/salary-advance/payment/2099/12/` created a settlement that then counted as a
settled month forever.

## Fleet Accounts (`BulkPayer`)

The UI says **"Fleet Account"**; the model, fields and URLs all say `BulkPayer`.
Don't rename them to match the copy. `BULK_PAID` displays as **"Fleet Paid"**.

**A FLEET ACCOUNT CAN BE RENAMED, AND THAT IS SAFE FOR A REASON WORTH KNOWING
BEFORE COPYING IT ANYWHERE ELSE.** `bulk_payer_edit` is one `UPDATE` with no
propagation step, because **everything points AT the account by ForeignKey** —
`JobCard.bulk_payer`, `BulkPaymentHistory.bulk_payer`, and the Deep Analysis
fleet section, which groups by the FK id and pulls the name through the join.
So the account page, the picker, the fleet report and the **"Fleet · <name>"
chip on a printed invoice** all follow with nothing to keep in step.

⚠ **That is the OPPOSITE of a brand, model, spare or concern**, which are free
text copied onto every job card and therefore need `master_data.py` to carry a
new spelling across the history. Do not reason from one to the other.

Two things deliberately keep the OLD name: a `DeletionLog` snapshot and a
`Notification` body. Both are frozen records of what was true when written.

It mirrors `spare_shop_edit` on the sibling model, and the two halves of that
are both load-bearing: **`__iexact`** because the column's `unique=True` is
case-sensitive, so "Acme Fleet" and "acme fleet" are both insertable and the
picker would list one account twice; and **`.exclude(pk=pk)`** because without
it the model-level uniqueness check fires before the view runs and refuses the
account its own name back — so fixing the capitalisation of the only account of
that name would be impossible. The name is `fit_text`-trimmed to its 150-char
column rather than 500ing on Postgres.

The ⋮ menu is gated to **Office and Owner**, matching the view's own
`@office_required` — Office creates these accounts and settles them. **Delete
keeps its tighter Owner-only gate inside that menu.** Nothing was widened:
every fleet view is `@office_required`, so this only made visible a door Office
could already open.
→ `RenamingAFleetAccountReachesEveryScreenTests`

**A Fleet Account holding unsettled job cards cannot be ARCHIVED, and an archived
one takes no new cards, no new payments and no reversals.** Archiving used to be
unguarded and hid the account from every screen at once: `bulk_payer_detail` 404s
on an archived payer, the picker drops it, `pending_payments_list` already
excludes any card carrying a `bulk_payer`, and `update_bill_status` refuses a
fleet card with "settle it from that account's page" — a page that no longer
opened. One click made real debt unreachable by every route. `bulk_payer_delete`
now refuses while any PENDING/PARTIAL card is attached and names them;
`move_jobcard_to_bulk`, `bulk_payer_pay` and `bulk_payment_history_delete` all
require an active account. Blocking rather than opening a back door keeps one
rule: **money owed is always reachable from exactly one screen.**
→ `ArchivingAFleetAccountCannotStrandItsDebtTests`

**A Fleet payment may only be reversed while its effects are still intact —
newest first.** `bulk_payment_history_delete` restored job balances and advance
credit through two `max(0, …)` clamps, which silently absorbed the difference
whenever a *later* payment had already spent this one's leftover credit. The view
now pre-flights both clamp conditions under the same locks and refuses, naming
which payment to reverse first.

**The invariant to assert in any new fleet test:**
`Σ(card.received_amount) + advance_balance == Σ(history.amount)`.
→ `ReversingAFleetPaymentOutOfOrderIsRefusedTests`

**THAT REVERSAL IS CONFIRMED IN THE SUPPLIES SHOP'S OWN DIALOG** — 3rem glyph,
"Are you sure?", the amount in red inside a muted line, then pill Cancel and
Confirm. An owner settles a spare shop, a Supplies Shop and a Fleet Account in
one sitting, and a confirmation that changes shape between them reads as three
different products. The **mechanism** stays this page's own
`.bd-confirm-overlay` rather than becoming the inventory app's Bootstrap modal,
so the existing show/hide and click-outside handlers are untouched; only the
contents of the box changed.

⚠ **THE ONE THING THAT DIFFERS IS THE REASON BOX, and it is kept on purpose.**
The Supplies Shop's confirmation has none. A fleet reversal takes back the
largest single receipt the workshop handles, `bulk_payment_history_delete` reads
`reason`, and `DeletionLog` stores it — it is the only field that later says why
the money moved back.

⚠ **BECAUSE THE REASON BOX MUST SIT INSIDE THE FORM THAT POSTS IT, CANCEL SITS
INSIDE THAT FORM TOO — AND A BARE `<button>` IN A FORM SUBMITS IT.** Left at the
browser default, pressing **Cancel** on "Are you sure?" would reverse the
payment: the loudest possible failure, on the one control whose whole job is to
let somebody back out. `type="button"` is the entire defence, so it is asserted
rather than trusted.

It replaced a broken layout, and the cause is worth keeping. `.bd-confirm-box
.btn-group` is `display: flex` with the default `align-items: stretch`, and the
whole `<form>` was one of its two children — so the form grew to hold a stacked
reason input plus a button, and Cancel, a single-line button, was **stretched to
match it**, rendering as a tall empty box beside a reason field that had climbed
above the red button it belongs under. `.btn-group` on this page is a
two-BUTTON row; nothing taller than a button may be a child of it. The Rename
overlay beside it had always had this right — the input outside the row, the
form wrapping both.

**The menu item reads "Delete this Payment", not "Delete & Reverse".** The old
label named the mechanism rather than the thing, and *reverse* is already this
section's word for undoing a payment out of order — so it read as a second,
different action.
→ `CancelOnTheReversalDialogCannotReverseAnythingTests`

**A FLEET PAYMENT IS DATED BY THE DAY THE MONEY MOVED — the third and last
ledger to get the column, and the one where it mattered most.**
`inventory.SupplierPayment` has had `date` since day one and `SpareShopPayment`
gained it in `0071`; `BulkPaymentHistory` carried only `created_at`, the
keystroke. A fleet collector comes round and the office keys the receipt when it
gets to it, and these are the **largest single receipts the workshop takes**.

Nothing filtered fleet payments by date before, so the defect was invisible —
which is exactly why it survived two passes that fixed its siblings. The moment
any cash figure is cut by period it would file a six-figure receipt in the wrong
month, and Cash Tracking does precisely that.

Four things, all mirroring `0071`: the migration **backfills from `created_at`**,
an approximation by construction, which is why it belongs **before go-live**;
`created_at` stays as the audit trail and breaks ties inside a day
(`ordering = ['-date', '-created_at']`); the detail view's own explicit
`order_by` had to move too, or the column would have been one nothing reads,
which is worse than no column because it looks fixed; and **the balance follows
nothing** — what an account owes is not a period, so a heavily back-dated
payment must not change it.

The control is the **spare shop's own** — 46px calendar glyph, invisible
`<input type="date">` over it, capped at today, amber and spelled out when it is
not today — because this is an inline row like that one, not a stacked page. The
Pay confirmation repeats the date **only when it is not today**.
→ `AFleetPaymentIsDatedByTheDayTheMoneyMovedTests`

**A job card can't be removed from a Fleet Account once it has
`received_amount > 0`.** Blocked, not auto-reversed: that money may be part of a
lump payment shared with other cards in the same cascade, so there is no clean
single amount to claw back. Reverse the specific `BulkPaymentHistory` entry first.

**`advance_balance` banks any surplus** when a lump payment exceeds what is owed,
and is pooled into the next payment before distributing — so `total_balance` can
legitimately go negative (in credit).

**Cascade payments** (Fleet and Spare Shop alike) use `select_for_update()` inside
`transaction.atomic()`, oldest-first, distributing until exhausted:
PENDING → PARTIAL → PAID. Only `BulkPaymentHistory` stores a JSON snapshot for
reversal; Spare Shop payment history does not.

## Cashbook

**The Cashbook is ONE stream, and every row behind the total is reachable.** A
single chronological list with `All / Out / In` chips over it, one search box, one
pager at `PAGE_SIZE = 45`. It used to be an expenses list beside an income list —
two totals, two add forms, two of every control — for a ledger whose income side
is used a handful of times a month. Each list was also sliced at a 300-row cap
while the totals above came from the full queryset, so a busy period printed a
figure that could not be added up from what was on screen.

**The totals follow the date window and the search but NOT the type chip** — a
chip is a way of reading the period, not a different period, and moving the
headline when one is tapped would make the expenses appear to vanish from a period
they are still part of. Totals and both chip counts come from **one** aggregate,
so they can never disagree.
→ `TheLedgerIsOneSearchableStreamTests`, `ALongCashbookPeriodStaysReadableTests`

**The Cashbook is ~98% expenses, and the page is weighted for that.** Income is
scrap, black oil and the like. Money Out leads the headline and is the largest
figure on the page; the add form opens on Money Out; the income card recedes to
grey reading "nothing came in — normal" on the many periods with none.

**THE HEADLINE IS TWO FIGURES. There is no Net card.** The workshop does not work
out a cashbook net, and the netting off belongs to the owner's Analysis section. A
figure labelled "Net" beside an expense total invites being read as profit — which
is the Profit page's job and a different calculation. Removing the card moves no
money and hides no data: `analysis_engine.cashbook_income()` and
`cashbook_expense()` aggregate the entries themselves. `cashbook_totals['net']` is
still computed in the view.
→ `BothSidesAreCollectedEvenThoughOnlyTwoAreShownTests`

**A Cashbook category snaps to the spelling already in use.** The Profit page
breaks the cashbook down with `values('category')` and the category is free text
with no picker, so "Electricity", "electricity" and "ELECTRICITY" were three lines
for one real cost — the total stayed right, the breakdown an owner reads to see
*where* money went did not. There is no master list for these, so the entries
already recorded **are** the list: first spelling wins. The row being edited is
excluded from the check, so deliberately re-casing the only entry of its kind
still works. The name box offers those spellings as a `<datalist>`, putting the
rule where it applies rather than after it; it is a suggestion, not a constraint.
Skipped on the AJAX path, since the datalist sits outside the swapped regions.

**Wage-looking categories are flagged, never filtered.** Free-text categories mean
a keyword filter would hide real money, so the Profit page shows a "wages may be
counted twice" warning and lets the owner move the entry.

**A Cashbook entry is dated by the day the money moved, and that date is
editable.** `CashbookEntry.date` has always existed and driven every filter, but
**no form rendered a date input and neither view read one**, so every entry was
stamped with the day it was typed and a crafted POST carrying a date was ignored.
A month-end expense keyed the following week landed in the wrong month on the
Profit page permanently, because the edit form could not move it either. Both
views take `date` through `posted_date()`, falling back to today on anything
unparseable. (`default=timezone.now` on a `DateField` **is** safe here —
`DateField.to_python` converts the aware datetime to `TIME_ZONE` before taking
`.date()`, so it lands on the correct IST calendar day.)

**That rule now lives in `workshop/money_dates.py`, not in this view.** It was
written here first as `_entry_date`, which is exactly the shape a second copy
gets made from — and one was needed the moment the spare-shop payment form
turned out to have the same defect. Two implementations of "which day is this
money filed under" would be two answers free to disagree, and they would
disagree at a **month boundary**, which is the only place anybody would notice.
`posted_date()` and `is_future()` are kept **separate** on purpose: the fallback
is about input that cannot be read, the refusal about input that reads fine and
is wrong, and only the caller knows what each should say.
→ `CashbookEntriesAreDatedByTheDayTheMoneyMovedTests`,
`BothLedgersDateMoneyByOneRuleTests`

**NOTHING THAT MOVES MONEY IS DATED FORWARD, AND THE LAST TWO HOLES WERE CLOSED
2026-08-30.** `is_future()` guarded both Cashbook forms, all three payment
screens and the salary advance; `spare_dates.pair_problem()` carried the same
refusal for an ordered/received pair. Two typed dates had never been wired to
it, and both were found by auditing the **About page's own claim** that "no
date can be in the future" — which was false when it was written.

- **`JobCard.admitted_date`** was the expensive one. `analysis_engine` dates a
  card's WHOLE LIFE on it — revenue and BOTH parts costs — so a card typed 2027
  for 2026 lifts one entire job out of the month that earned it and then
  **hides** it, because This Month and This Year end on a calendar boundary the
  card sits past. `clean_admitted_date` on `JobCardForm`.
- **`SupplierRestockBill.bill_date`** was two defects in one line. The edit form
  is the **only** door (the create path takes no date and falls to the column
  default) and it assigned the raw POST string onto a `DateField`: garbage
  reached Postgres as a `DataError` — a 500, and **not** caught by that view's
  `except ValueError` — and a forward date was accepted. A forward date also
  breaks `inventory/costing.py`, whose date-ordered replay would sort the bill
  after every real draw and give a cost basis to none of them.

⚠ **A FORWARD-DATED CARD WAS PUT TO THE OWNER FIRST, AND IT IS NOT WANTED
(2026-08-30).** The workshop is appointment-driven, so a card opened for a car
arriving next week was the one plausible reading — and `resolve_window`'s
"never cut off a forward-dated record" comment reads like evidence they exist
deliberately. **It is not**: that line makes All Time honest *if* a mistyped
card exists, and it only half-works, since a 2027 card still falls outside This
Year. A card is opened when the car is admitted.

Both **refuse rather than clamp**, the rule everywhere a value is typed here —
a fallback saves a value nobody typed, and filing a 2027 card under today is
the same defect one month closer. The widget `max` on each is **presentation
only**; it is set in `JobCardForm.__init__` rather than `Meta.widgets`, because
an attribute declared there is evaluated once at import and a long-running
server would cap the box at the day it booted.

⚠ **`Estimate.date` is deliberately still open.** An estimate is connected to
nothing — no ledger, no stock, no line in `analysis_engine` — so it is the one
typed date that moves no money, which is why the About page now says
**"nothing that moves money can be dated ahead of today"** rather than
restating a blanket claim that would go false again.
→ `workshop/tests/test_future_dates.py`

**The date box is small, first, and silent only while it is right.** Almost every
entry is dated today, so the field that is nearly always correct is a 46px
calendar glyph with the real `<input type="date">` invisible on top of it — one
tap opens the OS picker on every platform. Two things stop that being a trap: the
moment the date is not today the box turns amber and spells the day out, and the
add confirmation repeats the whole entry before a rupee is written. Desktop Chrome
opens a date picker only from the calendar glyph, which the overlay hides, so the
click handler calls `showPicker()`; on mobile the tap has already opened it and
the second call throws, which is caught.

**Income mis-keyed as an expense can be corrected in place.** It lands on the
*wrong side* of the Profit equation — a double-sized error. `entry_type` is
honoured on edit **only when a valid one is posted**, so a payload without it
keeps what the entry already has rather than silently flipping it.

**`payment_method` is validated against the list, on both the add and the edit**
(2026-08-31). `entry_type` was checked and this was not, so a crafted POST wrote
whatever it liked into a 20-character column: the row then prints the raw code
where the label should be, and the search's method matching — which maps a typed
word onto the CODES it knows — can never find it again. Anything unrecognised
falls back to `CASH`, the rule the fleet and withdrawal pickers already follow.

**Both money guards are `<= 0`, not just `is None`.** See the `parse_money` rule
under "Money & billing": `CashbookEntry` carries a `CheckConstraint amount > 0`,
so `0.004` quantising to `0.00` was a 500 on the two commonest write paths in
the ledger.

## Owner withdrawals

**TAKING PROFIT OUT IS NOT AN EXPENSE, AND `OwnerWithdrawal` APPEARS IN EXACTLY
ONE FIGURE IN THE WHOLE ENGINE.** Profit is what is *available* to take; taking
it cannot reduce it. Put it anywhere inside `build_profit_report` and the error
**compounds**, which is what makes this the one rule in the section worth
shouting: profit falls, so the page reports less left to distribute, over money
that has already been distributed, and the next distribution is decided from
the smaller figure.

It IS real cash out of the drawer, so it belongs in `cash_position()`'s
money-out list, dated by `date`. That is the whole footprint on the money math
— **two lines in `analysis_engine.py`**, the other being `_DATE_STREAMS`, so
All Time can reach a withdrawal older than every other stream.
→ `AWithdrawalIsNotAnExpenseTests`, `ItIsCashOutAndSaysSoOnceTests`

**THE TABLE EXISTS BECAUSE THERE WAS NOWHERE CORRECT FOR THE MONEY TO GO.** The
Cashbook is an expense ledger and `cashbook_expense()` feeds the profit equation
as General Cashbook, so an owner recording "₹50,000 — Owner" there quietly cut
reported profit by ₹50,000. The likeliest place for that money to land was the
one place that breaks the figure. **This is a correctness fix wearing a
feature's clothes**, which is why the sentence *"Not a business expense — profit
does not change"* is printed under the box the amount is typed into rather than
in a heading somebody has already scrolled past.

⚠ *Considered and NOT done:* a Cashbook flag for owner-looking categories, the
way `_shoplike_cashbook_count` flags shop-looking ones. A third warning on the
Profit page is real noise, the Cashbook is Office-visible and Office does not
record owner draws, and the section's existence is the fix. Revisit only if one
actually turns up.

**ONE PAGE, NO PER-OWNER DRILL-DOWN.** With two owners the comparison *is* the
question — what have we each taken — and a page per owner answers half of it at
a time. The history below narrows instead, with both cards still on screen.

**THE FILTER IS A CHIP ROW, AND THE OWNER CARDS ARE DISPLAY ONLY.** The cards
were links that also held the filter state, so one object reported a figure AND
carried a filter — two jobs needing two active states to say so. It is now the
**Cashbook's own chip row**, character for character: one chip per option, each
with its count, the active one taking its own colour. That replaced a "show
everyone" link which rendered *only while a filter was on*, so the way back was
visible, the way in was not, and neither said who else there was.

**IT SITS ON THE HISTORY HEADING'S OWN LINE, at its right-hand end** — the
section's name on the left, who it is showing on the right. Stacked, it spent a
whole line on three small controls and put the filter a scroll away from the
heading it modifies. Two things came with the move: the heading dropped the
owner's name (it read "{name} · {period}" while a filter was on, with the
active chip saying the same name six pixels to the right), and that is also
what makes the two fit — measured at 1280, heading 129px and chips 289px on one
768px row.

⚠ **BELOW 576px IT TAKES A SECOND LINE, and the first reason is arithmetic.**
At 375px the row has 343px against a 157px heading and 289px of chips — 103px
short, and nothing but dropping the counts closes a gap that size. The break is
declared at the app's own 576px rather than at the ~460px where they genuinely
stop fitting, because below it the chips also stretch full width and take a
42px touch height instead of 34px. **A thumb is the reason**, and 42px beats
sharing a line by a few pixels.

⚠ **EVERY CHIP AND EVERY PAGER LINK ENDS IN `#wdHistory`.** A chip is a LINK,
so tapping one is a full navigation and the browser lands at the TOP of a page
whose history is ~536px down — the reader is thrown back to the hero every time
they change who they are looking at. The fragment lands them on the row they
tapped instead. **No script, no fetch, and no scroll position to restore**,
which is the whole reason it is a fragment rather than the AJAX the list pages
use: there is nothing here that can get out of step. `scroll-margin-top` is
`calc(var(--sticky-top) + 12px)`, and that one declaration covers both layouts
— `--sticky-top` is the fixed bar's height on a laptop and 0px on a phone,
where the bar is at the bottom. Measured: the anchor lands `.wd-hist` 12px
below the chrome.

Nothing about the cards or the hero changes when a chip is tapped — they always
report the whole window — so landing past them loses the reader nothing.

**ONE COLOUR PER OWNER, decided in the view and used in three places** — the
card, the chip and every row of theirs in the list, so a list of two people
reads as two people without anybody reading a name. `OWNER_TINTS` is assigned
by POSITION in `owner_accounts()` (ordered by displayed name), so it is stable
between renders and a third owner gets a colour for free.

**THE OWNER CARD IS FILLED WITH IT — DARK BLUE AND DARK VIOLET** (`#1e3a8a`,
`#5b21b6`), on the owner's instruction (2026-08-31). It was a 3px rail on a
white card, which is enough to tell two ROWS apart and not enough to make two
people the subject of the page; under the black headline these read as the two
halves of one answer. Three things travel with it:

- ⚠ **THE DEPTH COMES FROM ONE VALUE, never a second hex.** The card lays a
  `rgba(255,255,255,.12) → rgba(0,0,0,.14)` gradient OVER `var(--tint)`, so a
  third owner needs one colour rather than a matched light/dark pair somebody
  has to keep in step. 12% is the ceiling: at the lightest corner `#1e3a8a`
  measures 7.4:1 against the white figure on it and `#5b21b6` 6.8:1.
- ⚠ **THE BLUE IS NAVY, NOT THE APP'S OWN `#2563eb`.** Every button and active
  pill in the system is that blue, so a card filled with it read as the one
  thing on the page to press — when it is purely a figure.
- The row rail went 3px → **4px** in the same edit: two dark colours need a
  little more of themselves to be told apart at a glance than a violet and a
  cyan did.

⚠ **NO OWNER COLOUR MAY BE RED OR GREEN.** Both are spoken for app-wide as the
DIRECTION of money and this page prints a red amount on every row, so an owner
who happened to be red would read as the urgent one. It shipped for a minute
with `#c2410c` in the palette — **caught by the test, not by eye**, which is
the point of having it.
→ `test_no_owner_colour_is_red_or_green`,
`test_each_owner_keeps_one_colour_across_the_card_the_chip_and_the_rows`

⚠ **THE TWO TOTALS ARE PRINTED AND NEVER NETTED.** What a gap between two
owners *means* depends on the partnership split, and this system does not hold
one — so the page prints both honestly and the owners do the reading. Exactly
the rule "what we owe and what we hold sit together and are never netted"
follows one page over. A test asserts the difference appears nowhere.

**An owner with nothing in the window still gets a card, at ₹0.** Honest here in
a way it is not on the fleet table: an owner exists for the whole period, so
"took nothing" is a fact about them, where a fleet account's ₹0 would be a claim
about a period it was not in. A missing card reads as a missing owner.

⚠ **AND SO DOES SOMEBODY WHO HAS SINCE LEFT `owner_accounts()` — the headline
is the SUM OF THE CARDS.** The cards were built from that query alone while the
list below prints every row in the window, so an owner the query stops
returning is money the list shows and the total does not count: **the hero
disagreeing with the rows underneath it, silently**, which is the one thing a
money page may never do. `owner_accounts()` filters `is_active`, so deactivating
an account is the whole of what it takes. Measured before the fix, with one of
two owners deactivated: hero ₹6,07,500 over a list of sixteen rows totalling
₹11,50,000.

Any owner id present in the window's own aggregate is appended to the card list,
so the hero, both chip counts and the rows are one aggregate by construction —
the Cashbook's rule. Three details: the extra query fires **only when there is a
stray**, they are **appended** so a real owner's position and therefore colour
cannot move, and a stray is **listed but not offered in the picker**
(`owner_choices`, not `cards`), because `withdrawal_add` validates against
`owner_accounts()` and would refuse it — an option that can only ever be
refused is a door somebody can see and cannot open.

**THE PROFIT PAGE'S DATE VOCABULARY, NOT THE DAY-TO-DAY LISTS'.** Owner money is
taken a handful of times a month, so Today and This Week would return an empty
page nearly every time — which reads as a broken screen rather than a quiet
period. `engine.resolve_period` is called directly, so there is one
implementation of the window and **All Time comes free**.

**WHICH OWNER IS VALIDATED AGAINST THE OWNER LIST, never merely read.** Hiding a
name from a `<select>` is presentation; `owner_accounts().filter(pk=…)` is the
control. Without it a crafted POST could file a withdrawal against the Floor
account, where it would sit on a page that role cannot open, attributed to
somebody who never took the money.

⚠ **THE AMOUNT IS CHECKED `<= 0` AFTER `parse_money`** — the app-wide rule
recorded under "Money & billing", found here first. `OwnerWithdrawal` carries a
`CheckConstraint amount > 0`, so a sub-paisa figure quantising to `0.00` was a
500; the visible amount box is not inside a form either, so its `min="1"` never
runs and only the view can refuse it.

**`decorators.owner_accounts()` is the ONE answer to "who are the owners?"**,
read by this page and by `notifications._recipients`. ⚠ The either-or
(`is_superuser` **or** the Owner group) is load-bearing and is the same one
`is_owner` uses: a reseeded database routinely leaves both owners superuser with
an **empty** Owner group until somebody runs `sync_owner_identity --yes`, and
group membership alone went dark that way on two demo deployments.

**DELIBERATELY NO EDIT.** Every other ledger has one because a row keyed on the
wrong day would otherwise be stuck in the wrong month for good — but that
argument assumes a role that *cannot* delete it. This section is Owner-only end
to end, delete is always available, and re-adding takes one line of the form.
One fewer surface, and every correction lands in Deletion History rather than
silently overwriting what was there. For the same reason there is **no
`delete_window` guard**: that rule escalates an *Office* delete to an owner, so
here it could never refuse anybody, and calling it would be a check that reads
like a control.

**IT IS ON THE SYSTEM MAP, IN TEL.03, WITH EXACTLY ONE ARROW.** It was the
only section drawn nowhere on the sheet. The card sits at the foot of the
telemetry zone under ESTIMATE HISTORY — that zone is "Boards & History" and this
page is largely a history list, but it is the one card there that moves money,
so it wears the OUT accent rather than the zone's data blue.

The single arrow to **CASH TRACKING** is the "appears in exactly one figure"
rule drawn: no line to Profit, to any expense, or to the trunk, because there
is nothing there to draw. ⚠ It runs down the OUTER margin at x=1404, not the
1000–1030 lane between CASHLINK and AUDIT: a straight drop is impossible
(PROFIT sits directly above the target) and that inner lane already carries the
expense trunk in the same coral — `check_system_map.py` measured the two 7px
apart for 122px and refused it, which is exactly what check 5 is for.

**No go-live opening balance, and the page says so rather than guessing.**
Whatever the owners took before the system existed is not in it, so a
year-to-date figure is short for year one — the same shape as opening stock. It
self-corrects after one full year, and an opening figure is one more number
nobody can check.

**THERE IS NO SECTION THEME — THIS PAGE SITS ON THE APP'S OWN GROUND, AND
THAT REVERSES WHAT THIS FILE SAID UNTIL 2026-08-31, on the owner's
instruction.** It read **THE THEME IS THE GROUND, AND NOTHING ELSE**: this
section is about the OWNERS rather than about the workshop — the only one that
is — so it got a room of its own, and a room is the colour of its WALLS, which
was `body.wd-page` at **`#e7eeea`**, an off-green, with the furniture
untouched.

The owner's call is that it does not get a room. An owner opens this page in
the same sitting as the Cashbook and the Profit page, and a ground that
changes between them reads as a different **product** rather than as a
different room — which is precisely the failure the violet repaint was
reverted for the day before, arriving by a quieter door. `body_class` and the
ground declaration are both gone; the page inherits `--color-bg` like every
other screen.

**What tells this section apart was never the wall colour.** It is the two
OWNER TINTS — navy and violet, the only place in the app where two people are
the subject — and they are untouched.

⚠ **The green survived TWO earlier challenges, which is why it needs saying
that neither of them was this one.** A **dark** green ground was asked for
(2026-08-31) and refused on the CONTRAST: every card here is white with a
hairline border and every secondary label is grey, so a dark ground leaves the
borders invisible and the labels unreadable, and each then needs a colour of
its own — which is the repaint below arriving by a different door. What the
green got instead was one step deeper, bounded and measured: the muted
subtitle (`#64748b`) reads **4.1:1** on `#e7eeea` and drops to **3.7:1** by
`#dde7e0`. That reasoning is still correct and is why no *darker* ground may
be reintroduced either. The contrast the request was reaching for went into
the **headline**, which is black, and stays.

⚠ **IT SHIPPED FOR AN HOUR AS A REPAINT AND THAT WAS THE FIRST MISTAKE.**
Violet was pushed onto the pills, the headings, every owner name, every
border, the kebab hover and the shared payment card's own rail. The section
stopped looking like a different room and started looking like a different
product — the owner's verdict was immediate. The rule written then was **a
section theme in this app is one background declaration**; the rule now is
that **a section theme in this app is NOTHING**. The green was the last
surviving piece of that repaint, and it failed for the same reason on a longer
timescale.

⚠ **So the ONE olive value left is `.wd-title i` (`#4d7c0f`), the page-title
glyph.** It is a leftover of the removed theme rather than a decision, and it
was left alone rather than swept up with the ground, because nothing on this
page depends on it and the app has no single convention for a title glyph's
colour. Recolour it only if asked.

⚠ **THE HERO IS THE DARK SLAB, AND THAT REVERSES WHAT THIS FILE SAID UNTIL
2026-08-31, on the owner's decision.** It read that the weight had to come from
a rail and a lifted shadow, "never from a dark fill", having gone dark slab →
plain white box → rail. The owner's call is that it goes back, and the reason
is one this page could not see from inside itself: **every other section header
in the system is this exact slab** — the spare shop's, the Supplies Shop's,
both shop details, all of them `linear-gradient(135deg, #1e293b, #0f172a)` at a
16px radius. An owner reads those in one sitting, so a section whose headline
is the only white one reads as a different product, which is the failure the
theme note above is about.

The values are **copied from `.detail-header`** rather than approximated, the
same rule the date glyph follows, and the label and count take that header's
own `rgba(255,255,255,.55)`. The olive rail went with the white fill: a slab
needs no rail to be found.

**The form is the shared `.rpay-*` control, RED because the money is going
OUT** — the Profit page's own colour rule applied to the button that moves it.
Four things are specific to it:

- ⚠ **IT CARRIES THE TRAVELLING LIGHT, LIKE THE OTHER THREE — reversing
  `.rpay-card-still` the day after it was written** (2026-08-31, the owner's
  call; the brief was that this border did not move the way the spare shop's
  and the Supplies Shop's do). The argument for stopping it was real and is
  worth keeping in view: the light earns its place on those three on one stated
  condition, that they *render only when money is owed*, and this card is on
  screen every time the page opens. The owner's answer is that **the four cards
  being one control outranks the exemption**, and that this page is opened a
  handful of times a month rather than worked in all day — which is the case
  the Job Card's "only looping animation" rule was written against.

  `.rpay-card-still` stays in `style.css`. It is the honest way to stop the
  light if that judgement flips back, and it is one class on one line.
- **WHICH OWNER IS A FIELD IN THE ROW, between the date and the amount.** It
  spent a revision as a select styled into the heading chip, where it read as a
  LABEL — nothing said it could be changed. In the row it wears the same
  `.rpay-select` as Method, under its own caption.

  ⚠ **THE WIDTH IT COSTS IS PAID OUT OF THE NOTE, never out of the button.**
  Measured at 1280, six controls at their shared sizes need **834px against
  734px of laptop**. The row scrolls sideways rather than wrapping — but the
  Record button is the LAST thing in that scroller, so any overflow lands on
  the one control that must never be hard to reach. The Note is optional and is
  the only field that grows, so its basis is the cheapest thing to shrink:
  `.rpay-f-owner` **124px** and `.rpay-f-note` **92px** bring the row to exactly
  734, **nothing hidden** — measured again after the widening: row 734px,
  scroller 734px, hidden 0. **Both overrides are page-scoped; the three payment
  templates are untouched.**

  Its caption is **TAKEN BY, not OWNER**. Over a page called Owner Withdrawals,
  inside a card that says Record a Withdrawal, "Owner" was the section's own
  name for the third time, and it is not what the field asks.

  ⚠ **AND IT READS AS A QUESTION UNTIL IT IS ANSWERED.** "Who?" rendered in the
  same weight and colour as a chosen name, so the one field the figures can
  never catch afterwards looked filled in. `#wdOwner:invalid` — the select is
  `required` and empty, so the STATE is the selector and nothing has to be kept
  in step in script — turns it grey; `#wdOwner option` puts the normal colour
  back, or that grey is inherited into the open list on some browsers.
- **NOTHING IS PRESELECTED, and the heading chip ECHOES what is.** Whoever
  opens the page is not necessarily whoever took the money, and a picker that
  opens on a name files it against that name for anybody who does not look —
  the one field on the row the figures themselves can never catch afterwards.
  So the select opens on `Who?` and both the browser and the view refuse
  without it.

  The chip is then a **mirror, never a second control**: `.rpay-owed` is where
  the other three cards name the account about to be paid, directly over the
  button that pays it, so the name is under the eye at the moment of pressing —
  which a 108px select at the far left of a scrolling row is not. It is a
  `<span>` driven one-way from the select, because two controls setting one
  value is how they start disagreeing.

  ⚠ **It is ABSENT until there is something to say**, not a placeholder
  announcing that nothing has been chosen — that is a second telling of what
  the picker two inches away already says with the word "Who?". It ships
  `hidden` and empty, so a page whose script never arrived shows nothing rather
  than a stale name.

  **It wears that owner's own colour**, read off the chosen `<option>`'s
  `data-tint` — the same value their card, their chip and every row of theirs
  carry, so the name over the Record button is recognisably that person before
  it is read. The attribute is how the script gets the colour without a second
  copy of the palette; `.rpay-owed` carries no Bootstrap utility, so a plain
  inline style wins on it and the "an `!important` utility beats an inline
  style" trap does not apply here.
- **The Note carries NO placeholder here.** "What it was for" under a caption
  reading NOTE (OPTIONAL) is the label restated in quieter type, which is the
  rule the job card's vehicle and customer boxes already follow.
- **THE FOOTNOTE IS SMALLER ON A PHONE** — 0.68rem below 576px against the
  shared 0.74rem, which takes it from three lines to two on a 375px screen. It
  is the only one of the four cards whose footnote is two sentences of standing
  explanation rather than one short line, and it sits directly above the
  history. **Page-scoped**: the three payment cards keep the shared size. The
  sentence itself is untouched — it is the most important one on the page, and
  making it quieter after the first reading is not the same as shortening it.

⚠ **`.wd-list` MUST NOT BE `overflow: hidden`.** Every row holds a ⋮ dropdown,
and this is the clipping trap the traps section records — invisible, and only
*sometimes*: with several rows the menu flips upward and stays inside the box,
so it looks perfectly correct. Measured with ONE row, **33px of a 44px menu was
cut off**, putting the only delete there is out of reach on exactly the list
that has one thing to delete. The corners are rounded on the rows.
→ `TheHistoryListCanAlwaysBeActedOnTests` asserts the declaration, because
nothing in the Django suite executes CSS.

## Master data

**Master data dedupes on `__iexact`, and there is exactly ONE rename
implementation — `workshop/master_data.py`.**

The models' `unique=True` is *case-sensitive*, so "Toyota" and "toyota" were both
insertable, and `ConcernSolution` had no uniqueness at all. Every duplicate then
showed twice in autocomplete. The job-card auto-learn path had always deduped with
`__iexact`; the four Master Lists *forms* were the manual entry points that did
not, and now carry the check — excluding the row being edited, so re-saving an
unchanged name is never blocked.

A spare or concern could be renamed from **two** screens — Master Lists and Data
Cleanup — and they were two implementations of one rule, so the same edit meant
different things depending on which page you opened. Both now call
`rename_spare()` / `rename_concern()`.

Three properties of a merge: **the surviving entry's spelling wins** (so list and
history can never disagree); it is scoped to `source=SHOP`, because the rename
uses `.update()` and firing no signals is only safe for rows that move no stock —
relabelling a warehouse draw would desync it from the `Item` it is FK'd to; and it
is **not cleanly undoable**, since renaming back relabels every row now carrying
the surviving name.
→ `RenamingAMasterEntryMeansTheSameThingFromBothScreensTests`,
`MergingAMasterEntryNeverMovesMoneyOrStockTests`

**A merge is CONFIRMED first; a plain rename is not.** The gate fires **only on a
collision** — a rename that matches nothing stays one POST, because confirming
what cannot surprise anyone is how confirmations stop being read. `merge_preview()`
reads the **same** `*_rename_target()` helpers `rename_*` uses to decide; two
lookups of "does this collide" would be two answers free to disagree, and they
would disagree exactly where it matters. A brand merge additionally discloses the
models it will carry across and the ones it will **drop** as duplicates — a second
permanent delete hidden inside the first — via `brand_merge_model_split()`,
likewise shared with the code that performs it. Both screens gate it, or the
silent merge just moves to whichever door is open.
→ `AMergeIsConfirmedBeforeItHappensTests`

*Considered and rejected:* **blocking delete on an in-use entry.** Usage
effectively never returns to zero (the job-card delete guard forbids deleting a
card that carries spares), so `used > 0` is a one-way door — every name ever typed
would become permanently unremovable. It also guards nothing: a master-list delete
touches no job card, no bill and no report, is logged, and auto-learn restores the
entry the moment someone types it again.

**Renaming a BRAND or MODEL reaches the job cards too.** Reports group by
`JobCard.brand_name` / `model_name` — free text on the card — so a brand recorded
as "Toyta" was a permanent second brand in the insights, and correcting the master
list changed nothing. Spares and concerns had propagated since day one; brands and
models never did. A brand merge carries the dying brand's **models** across,
dropping any whose name already exists under the survivor (`CarModel` is
`unique_together('brand','name')`). A model rename is **scoped to its brand**:
Toyota's "Corola" and another make's are different cars.

**The master list decides how its own entries are spelled — BOTH the brand and
the model.** `model_name` had no normalisation while `brand_name` and
`registration_number` did, so 'corolla' and 'COROLLA' were two models everywhere
they were counted. It is deliberately **not** title-cased the way `brand_name` is
— that turns 'i20' into 'I20' and 'CR-V' into 'Cr-V'. `JobCard.clean()` collapses
whitespace and then snaps to the master list's own spelling when that brand
already has the model recorded; anything genuinely new stays exactly as typed.

**The BRAND gets the same snap, and title-casing is only its fallback.**
`.title()` is right for 'toyota' and wrong for every acronym marque: **'BMW' was
stored as 'Bmw' on every card in the system**, so the master list said BMW while
Car Profiles, the brand chart and the Vehicles insight — all of which group by
this free-text column — said Bmw. The fix is the model's own rule applied one
field over: title-case, then let the curated list overrule it. A marque the list
has never heard of is still tidied to 'Koenigsegg'.

**`Estimate.clean()` carries the identical pair**, because the two documents are
opened days apart for the same car and a quotation spelling a marque differently
from the bill that follows reads as two different products.
→ `TheMasterListDecidesHowABrandIsSpelledTests`
→ `RenamingABrandOrModelReachesTheJobCardsTests`,
`TheMasterListDecidesHowItsOwnEntriesAreSpelledTests`

**Deleting a master-list entry cannot touch history.** Brand / model / spare /
concern names live on job cards as free text, never as a FK, so removing one
changes no bill, no ledger and no report, and auto-learn re-creates the name the
next time someone types it. The delete shows a confirmation carrying the usage
count and writes `DeletionLog.ENTITY_MASTER_DATA`. When the entry is still in use
the page steers towards **merging instead**.
→ `MasterDataDeleteTouchesNoHistoryTests` — pins this down so the day someone
converts one of these to a ForeignKey it fails loudly instead of a delete quietly
cascading a car's history away.

**Brand and model deletes are disclosed and logged.** Deleting a brand CASCADEs
every model under it — the largest permanent delete in the app — and the confirm
page used to say only "this will also delete all car models", never how many or
which, while nothing was written to `DeletionLog`.

**Brand / model / spare / concern are free text, not FKs to the master lists.**
`CarBrand`, `CarModel`, `SparePart` and `ConcernSolution` exist as reference
tables, but `JobCard.brand_name`, `JobCardSpareItem.spare_part_name` etc. are
`CharField`s filled by autocomplete. A deliberate trade for data-entry speed on
the shop floor. The mitigation is normalisation on save, not converting them to
ForeignKeys.

**One deliberate exception: a warehouse draw is FK-backed by `item`.** Inventory
products are a closed set by construction — they exist only because someone
created them through Supplier → Add Product — so there is no data-entry speed to
protect, and a great deal of correctness to gain. Spare-shop rows stay free text.

## Owner Analysis & Reports

Two pages. **`/analysis/` — Profit**: `Total Turnover − Total Expenses = Profit`
for one date window, with the equation shown literally on screen, and then that
same profit decomposed by **what earned it**. Owners read it to decide **profit
distribution**, so keep it plain — no drill-downs, and a new card earns its
place only by removing one. Filters are This Month / Last Month / This Year /
Last Year / All Time / Custom, deliberately *not* the Today/This Week vocabulary
of the day-to-day lists — profit isn't a daily number.
**`/analysis/insights/` — Deep Analysis**: everything else, one AJAX-loaded
section at a time.

**All money math lives in `workshop/analysis_engine.py`, never in the views or
templates** — pure functions taking a date window, so the arithmetic is testable
without a request. Views resolve the window, call the engine, and render.

**THE DOUBLE-COUNT RULE — the thing most likely to get "fixed" into a bug.** A
part is charged **exactly once, at the moment it is fitted to a car**:
- `source='SHOP'` → bought from a spare shop for that job → the **Spare Shops**
  expense, `unit_price` as typed (the shop's line total).
- `source='INVENTORY'` → taken off the warehouse shelf → the **Inventory Used**
  expense, at the shelf's weighted-average cost.

The two routes partition the spare rows exactly, so every rupee of parts cost
lands in one bucket: none lost, none doubled. A `source='SHOP'` row with **no
shop recorded** is surfaced as its own "Other Spare Purchases" line rather than
silently dropped.

⚠ **THE SECOND HELPING TO GUARD AGAINST IS THE RESTOCK BILL.** Buying stock
turns cash (or a promise to pay) into goods on a shelf — it is not a cost until
the goods are used. `supplier_billed()` reports what was billed and **must never
be added alongside the draw cost**: that charges one delivery twice, ~₹6.9L
against the seeded data.

**This reversed on 2026-08-25, on the owner's decision.** Until then the BILL
was the expense and the draw was excluded. Two things were wrong with that:

- **The other parts route never worked that way.** `spare_shop_expense` is dated
  by `job_card__admitted_date` and counts only rows attached to a card — a shop
  part is expensed when it is FITTED, and `unassigned_spare_purchases` exists
  precisely to hold back the ones that are not. The warehouse route was the odd
  one out, so the workshop had two parts routes on two different bases.
- **It made monthly profit lumpy for no reason an owner could act on.** A month
  with a big delivery carried the whole bill; the months that consumed it looked
  rich. July 2026 read ₹5,36,500 where the work done that month earned
  ₹4,33,500.

The trade, accepted knowingly: profit now leans on `avg_cost` being right, so
**`uncosted_draw_count()` is load-bearing rather than decorative** — a draw with
no cost is charged ₹0 and pushes profit UP. It is drawn as a warning on the page
for that reason. Expect it on go-live day.
→ `DoubleCountRuleTests` — if it fails, the workshop is being charged twice.

**A SUPPLIES SHOP BILL IS FLOORED AT ZERO, and the expression is
`SUPPLIER_BILL_COST` — one declaration, three former copies.** A discount larger
than the bill makes `total − discount` negative, and a negative EXPENSE *raises*
reported profit. `SupplierRestockBill.get_effective_amount` always floored it;
`inventory_expense`, `monthly_series` and `_insight_shops` each hand-rolled the
subtraction and did not, so the model and the page disagreed about the same bill.
Two ways a bill reaches that state even though the forms reject it:
`update_bill_discount` validated only `discount >= 0` (closed in the same edit),
and `update_totals()` recomputes `total_amount` from the lines **without
re-checking the discount**, so deleting a line from a discounted bill can push
the discount above the new total.
→ `ASupplierDiscountCannotRaiseProfitTests`,
`EveryDoorIntoADiscountEnforcesTheSameRuleTests`

**Revenue is `total_bill_amount − discount_amount`.** A discount is money never
earned, not an expense; for a settled card this equals `received_amount` exactly.

**THREE DATES, AND ONLY ONE OF THEM IS AN EXPENSE.** A Supplies Shop delivers,
the workshop pays in instalments over the following months, and mechanics draw
the stock down throughout — so one delivery carries three different dates:

| Date | What it does |
|---|---|
| **bill date** | raises the shelf and the payable; sets `avg_cost` via the date-ordered replay. **Not an expense.** |
| **draw date** | **the expense** — `warehouse_drawn_spare_cost`, at shelf cost |
| **payment date** | moves the payable **only**; never touches profit |

The last row is the one that looks wrong and is right: paying a supplier turns a
liability into cash out. It changes what you owe, not what you earned.
**`SupplierPayment` appears nowhere in `analysis_engine.py`, and it must stay
that way**; the same holds for spare-shop payments.

**Both parts lines NAME their basis** — "Parts taken off the warehouse shelf"
and "Parts bought per job, not payments" — because both shops are paid in
instalments and both have a payment screen of their own, so a ledger showing a
different figure that month invites exactly the wrong reading.
→ `ThreeDatesThreeJobsTests`

**A JOB CARD'S WHOLE LIFE LANDS ON ITS `admitted_date`.** A car admitted in
June, completed in July and paid in August sits entirely in **June** — revenue
and BOTH parts costs, because `_live_spares` dates by `job_card__admitted_date`.
`completed_date` is read by the Completed list and `paid_date` by Paid Bills;
**neither is read by the Profit page.** That is what keeps a month's margin
internally consistent: a job's revenue and that job's parts cost never land in
different months. Verified against the data — 3 July cards completed in August
kept their revenue in July.

Consequence worth knowing: a card admitted on the 30th and still being worked on
keeps ADDING to that month as parts are typed in, so a month is not final while
cards admitted in it are still open. Bounded and visible (the Live Report lists
open cards), and currently zero on this data — revisit only if settling profit in
the first days of a month starts catching open cards.

**A NEW PURCHASE NEVER CHANGES A PAST MONTH.** `inventory/costing.py` replays
receipts in **date order**, so every draw is priced by the bills preceding *its
own* date. Two shops at different prices, or one shop raising its price, only
move draws from that day forward. Demonstrated against live data: a Feb draw at
₹1,000 was untouched by a March delivery at ₹1,500 (which correctly blended the
average to ₹1,312.50 for April draws), and moved only when a forgotten bill was
**backdated to before it** — which is the workshop learning what those goods
actually cost, and is the workshop's real rhythm.
→ `test_a_second_delivery_before_the_first_is_paid_keeps_both_straight`,
`inventory/test_supplier_costing.py`

**WHAT WE OWE AND WHAT WE HOLD SIT TOGETHER, and are never netted.** The
owner's question, in their words: *"we have to pay Supplies Shops ₹1,00,000,
but we have ₹1,20,000 worth of stock in the workshop."* Both figures existed
and lived on two different pages, so the comparison could not be made. "Stock
on the shelf" is now the fifth tile in Position Right Now — full width, its own
rail colour, directly under the supplies-shops payable.

⚠ **There is no accounting identity between them, so no net is computed.** The
payable covers every unpaid bill whether or not those goods are still on the
shelf; the shelf holds goods from bills long since paid. Printed side by side
they answer the real question — is the debt backed by goods we still hold — and
the owner does that reading, not the page. The tile says **"at what it cost"**,
because valuing the shelf at retail would put an unearned margin into a balance
figure.

**Unknown cost on an `Item` is `avg_cost == 0`, NOT NULL** (`default=0,
null=False`), so an `isnull` filter matches nothing and would value opening
stock that has never had a supplier bill at ₹0 — worthless rather than unknown.
Those products are excluded and **counted on the tile**, because a shelf that
reads low with nothing saying why is worse than either. **Expect a count on
go-live day.** Negative stock is left negative: it means a bill is missing.
`warehouse_stock_value()` is in the engine and read by both the tile and the
Inventory section, so one shelf cannot have two values.
→ `WhatWeOweAndWhatWeHoldSitTogetherTests`

⚠ **`SUPPLIER_BILL_COST` had FIVE hand-rolled copies, not three.** The audit
that consolidated `inventory_expense`, `monthly_series` and `_insight_shops`
missed two, and both were in `inventory/`:

- **`SupplierShop.update_totals()`** — an underwater bill *subtracted* from the
  shop's balance, so real debt on its other bills read smaller than it is, and
  `deactivate_supplier_shop` (which reads this figure) would let a shop the
  workshop still owes be archived.
- **The payment WATERFALL** in `views_suppliers.py`, twice — the supplier page
  allocates payments across bills oldest-first, and the cumulative total it
  allocates against was un-floored **while `get_effective_amount`, used for the
  per-bill figure in the same loop, has always floored it.** The two halves of
  one calculation disagreed about the same bill. Measured: a negative amount in
  the running sum shifts the allocation for every bill after it and marks a
  bill nobody has paid for as **COVERED** — the ledger telling the workshop a
  live debt is settled.

Both now import the declaration. **Before adding a sixth: the expression is
`SUPPLIER_BILL_COST`, and `inventory/` may import it from `workshop`.**
→ `test_the_shops_own_BALANCE_uses_the_same_floor`,
`test_the_payment_WATERFALL_floors_it_too`

**THE PROFIT PAGE STATES THE SAME PROFIT TWICE, AND THE SECOND ONE LANDS ON THE
FIRST WITH NOTHING IN BETWEEN.** The equation is streams of money out; "What
Earned The Profit" is the owner's own view — asked what the workshop earns from,
the answer was **labour, spare-parts commission, inventory commission, cashbook
income**, less the running costs:

```
LABOUR + SPARE PARTS MARGIN + INVENTORY MARGIN + CASHBOOK INCOME
    (less discounts given)            = GROSS EARNINGS
less SALARY and GENERAL CASHBOOK      = THE SAME PROFIT
```

⚠ **THERE IS NO RECONCILING LINE, AND ITS ABSENCE IS THE POINT.** This card
first shipped beside an equation on a *different* basis — bills vs draws — so a
"stock movement" row sat at the bottom converting between them. It reconciled to
the rupee, and the owner's verdict was **"I am more confused now."** A page that
has to explain itself to itself is a page nobody trusts. The fix was to pick one
basis, not to word the bridge better. **If a third row ever reappears in
`spend`, the two bases have drifted apart and that is the bug.**

Three things that make the identity close, each of which was an easy miss:
- **The discount is its own line.** It is given on the whole bill, so it belongs
  to neither the labour line nor either margin. Shown only when there is some.
- **`unattributed_spare_expense` is NOT deducted again.** `parts_trading` costs
  every `SOURCE_SHOP` row whether or not a shop was named, so it is already
  inside the shop margin. The equation splits it out; this absorbs it.
- **Every shared figure is handed in**, never re-queried — a breakdown that
  looked up its own salary could disagree with the equation directly above it.
→ `TheProfitIsAlsoSaidTheOwnersWayTests`

**CASH TRACKING SITS ABOVE THE EQUATION, AND IS DRAWN AS A DIFFERENT KIND OF
OBJECT SO THE TWO CAN NEVER BE ADDED TOGETHER.** `cash_position()` in
`analysis_engine.py`: money in and money out for the window, by the day each
rupee actually moved. It is first on the page because owners read it more often
than they read profit.

**Three traps, each of which would break it silently:**

- **A fleet card's `received_amount` is CUMULATIVE.** Summing job cards for
  fleet money counts a card's whole life on the day it finally closed — a
  ₹1,10,000 card collected over three months landing entirely in the third —
  and counts it again against the payment that closed it. Fleet cash comes from
  **`BulkPaymentHistory`**, one row per payment. So the walk-in half is
  `payment_status='PAID'` **only**: including `BULK_PAID` counts every fleet
  rupee twice.
- **Wages are dated by the SALARY MONTH**, not the day they were handed over.
  Settlement happens at month end or the 1st or 2nd of the next — it straddles
  the boundary — so the settlement date would put one wage bill in different
  months depending on which side of midnight somebody pressed a button. The
  salary month never moves, the owners already think of August's wages as
  August's cost, and `salary_expense` has always filtered `SalaryPayment.month`.
  Accepted consequence: August's wages show in August though the cash left in
  early September — a constant one-month shift that repeats every month, so it
  never accumulates.
- **It REUSES `salary_expense`, it does not restate it.** That function already
  carries the guard that an advance inside a settled month is not counted twice,
  once inside its settlement and again as a loose advance.

**It is NEVER called a balance.** There is no opening cash figure anywhere in
this system, so what can honestly be reported is the CHANGE over the window,
never the position. An owner who reads "in the account", checks the bank and
sees something else stops believing the whole app. `is_balance` is returned as
`False` to say so out loud.

**And the last line is not labelled "Net"** — the Cashbook's own Net card was
removed for exactly this reason, and here the thing it would be misread as sits
directly below it. The direction is said in **words** ("More came in than went
out"), decided in the engine with a positive magnitude, the rule
`financial_position` already follows.

**It renders ABOVE the `has_data` gate**, which is `turnover != 0 or
expense_total != 0` — both PROFIT figures. A month whose only activity was
paying off old bills moves real cash and touches neither, so inside the gate the
page would say "Nothing recorded" while the drawer emptied.

**THE TWO HALVES ARE TWO COLUMNS, AND EACH RAIL SITS ON ITS OWN HALF'S LEFT,
at every width.** It labels the block it introduces. The red spent one revision
on the card's RIGHT edge, which put it a column's width from the rows it
described — the eye had to travel past the Money Out figures to reach the
colour naming them. On the left, **one declaration serves both layouts**: side
by side they are two matched bars, and stacked they become a single left rail
that turns red exactly where Money Out begins. There is no media-query swap
left to contradict itself.

`align-items: stretch` is doing real work: it makes the two rails the same
length whatever each half holds, so they read as a pair rather than as one bar
that ran out.

**The breakpoint is 640px, the app's own phone line** — the nav bar moves to
the bottom at the same width, so "mobile" means one thing across the whole app.
It also clears the content: the longest line ("Rent, power, consumables" plus
its figure) needs ~250px, and 640px leaves each column ~275px. Measured at
1024 / 768 / 375: two 352px columns, two 329px columns, then stacked with the
red starting 240px down.

**A rule sits under the heading**, because the card carries two independent
halves under one title and without it the title reads as the first line of the
left-hand one. Each column heading carries its own hairline for the same
reason, one level down.

**A CARD SUBTITLE ON THIS PAGE IS CONTEXT INFO, AND IS DRAWN AS ONE** — an
info glyph and muted type, **no box**. A bordered chip was tried and dropped:
the glyph alone already says "this is a note", and the border made a second
object competing with the card's own frame on a page that already carries a
dashed card, solid cards and an equation panel. Every one answers
*what am I looking at*: which basis, which period, what it is NOT. As plain
grey type at the end of a heading that reads as decoration and gets skipped,
which is how a card came to claim something untrue for months. **The glyph is a
real element in the markup, never a font codepoint in CSS `content`**: an icon
stylesheet that failed to arrive would leave a blank box exactly where the
explanation should be.
→ `test_every_card_subtitle_is_marked_as_context_info`

**TURNOVER AND EXPENSES ARE `col-md-6`, NOT `col-lg-6`.** At `lg` the two
cards went side by side only past 992px, so a **tablet stacked them while a
laptop did not** — the one place on this page where the two devices disagreed,
on a page all three form factors read. Everything else already switches at or
below tablet width: the cash columns at 640, the position tiles and the
equation at 576. At 768px each card gets ~369px against a ~175px hint beside a
~75px figure.

**"GENERAL CASHBOOK" IS "CASHBOOK EXPENSE",** because `Cashbook Income` sits
four rows above it in the same card. One ledger, two directions, and the two
names have to say so. Renamed in **both** places the engine prints it — the
equation's expense line and the earnings card — since it is one figure.

⚠ **"MONEY SPENT" ON THE EXPENSES CARD WAS FALSE, and it is the spend/paid
collision again.** Only **one** of its four lines is cash: General Cashbook.
Spare Shops is the cost of parts fitted, dated by the job card, while the shops
are settled in instalments months later; Inventory Used is stock drawn at
weighted-average cost, bought and paid for on an earlier bill; Salary & Advance
is the wage bill for the salary MONTH, whose settlement cash leaves in the
first days of the next one. It became untenable the moment Cash Tracking landed
directly above it — two adjacent cards, one saying "Money moved" and one "Money
spent", over figures on entirely different bases and differing by lakhs. It now
reads **"What the work cost, not cash out"**.

**Turnover's "Money earned" was NOT false and changed anyway.** Revenue is
earned rather than received, so the word was right — but beside a cash card it
invites being read as "came in", so it names its basis: **"Billed for work
done, paid or not"**. The other three subtitles were checked and were accurate
as they stood.
→ `test_the_EXPENSES_card_does_not_claim_the_money_left_the_drawer`

**The dashed border is `2px #cbd5e1`, the card is SQUARE while every card below
it is 16px-rounded, and both values matter.**
`--color-border` is `#e2e8f0` against an `#f3f4f6` page — invisible at 1px — so
the card meant to read as a DIFFERENT KIND OF OBJECT from the solid profit
cards below read as no object at all, which is the whole safety of putting cash
on this page.

**ITS SIDE PADDING IS 10px, NOT THE 18px EVERY SOLID CARD ON THE PAGE USES,
BECAUSE THIS CARD HAS TWO LEFT EDGES.** `.pf-cash-col` carries a coloured
rail, and a rail is itself an edge marker — so at 18px the dashed border and
the green rail ran parallel down the whole card with a strip of dead ground
between them, which on a phone is 5% of the width for the card's entire
height. Measured at 375px before the fix: card at x=16, rail at x=36.

**10px is chosen against the column's own 14px rather than picked by eye**:
the rail must sit CLOSER to the block it introduces than to the border it is
not part of, or it reads as floating between the two. 12px was tried and is
the ambiguous case — 12 outside against 14 inside. It is **symmetric**,
because the title rule and the money-moved rule span the full content box, so
an asymmetric card would sit visibly off-centre inside its own border. The
outer edge is untouched, so the card still lines up with every card below it;
only the inside got tighter.

**The period is said ONCE, in the card title.** Both column headings carried
"AUGUST 2026" while the page header and the active filter pill already state
it: four tellings of one fact, and the two longest were competing with the
totals beside them for the same line. The headings are now just MONEY IN and
MONEY OUT.

**Two disclosures ride on it**, both flagged and never filtered: an unsettled
salary month (the wage line is then only advances — ₹9,000 against ₹1,24,000 on
the demo data), and cashbook expenses whose free-text category reads like a shop
payment, which would be counted twice.
→ `CashIsTrackedSeparatelyFromProfitTests`

**THE PAID BILLS GRAND TOTAL WAS REMOVED IN THE SAME CHANGE**, because this
replaced it. It summed `received_amount` over cards that reached fully-settled
status in the window — exact for a walk-in, who pays once at pickup, and wrong
for a fleet three ways at once: a card closed this month carried its whole
cumulative receipt, a `PARTIAL` card holding real cash appeared nowhere, and
banked advance credit appeared nowhere. A ₹1,20,000 fleet payment could report
there as ₹20,000. **The row COUNT stays** — how many bills are in the list is a
fact about the list, not a business figure — and it is no longer an RBAC rule at
all, since neither role now sees a money total.
→ `test_the_page_carries_no_money_total_for_anyone`

**The Expenses card carries NO footnote about warehouse stock, and needs none.**
It used to read *"Parts worth ₹1,88,000 came off warehouse stock and are not
charged here."* True, and it should never have needed saying: it existed only
because a third of the parts fitted had their cost in none of the four lines.
Both halves now charge parts when they are fitted, so there is no gap to
explain.
→ `TheExpenseListNeedsNoFootnoteTests`

**Nothing else earns money.** `total_bill_amount` is `Σ spares.total_price +
labour_amount` and nothing else — no GST, no service charge, no consumables
line — so those four streams (plus the discount, which reduces them) are the
complete income side. Verified against the model, not assumed.

**THE PAGE CARRIES NO DRILL-DOWNS, and it was carrying two.** Both left, to
**different** places, and that difference is the rule:

- **General Cashbook category list → Deep Analysis → Cashbook.** Free-text
  categories have no ceiling, so it carried a collapsed tail and a "Show all"
  button between the owner and the position tiles. It existed nowhere else —
  the Cashbook page lists *entries* and has never totalled them by category —
  so it became a whole section rather than a truncated card.
- **Salary & Advance card → nowhere; the module already owns it.** Four rows
  explaining one expense line. `/salary-advance/` owns settlements, advances
  and per-person history, and the amber banner already links there by name, so
  a ninth insight section would have been a thinner second copy of it.

Keeping one and deleting the other would have been the page applying its own
rule to whichever card somebody noticed.

⚠ **TWO THINGS WERE KEPT OUT OF THOSE CARDS, and each for its own reason:**
- **The wage double-count warning stays on the Profit page.** Everything else
  in that card was detail; that line says the profit figure above it may be
  counting the wage bill twice. **A warning that changes what the headline
  means lives beside the headline.**
- **The wage cost's composition moved into the expense line's own hint.**
  Salary is the only expense line here whose composition is not self-evident
  and which reads like a double count: it is **net + advances**. An owner
  seeing ₹1,24,000 here against a ₹1,15,000 settlement has to be able to tell
  the ₹9,000 gap is advances already handed out, not an error. It **replaced**
  "1 month settled" — a count of months, which said nothing about the figure
  beside it — so it costs no extra line, and it only renders on a fully settled
  month that actually had advances. An unsettled month keeps the warning
  instead, which is the bigger fact.
→ `ThePageCarriesNoDrillDownsTests`, `TheCashbookBreakdownLivesInDeepAnalysisTests`

**The "Where It Went" donut was REMOVED.** It plotted `expense_lines`, which
the Expenses card already prints with a share percentage *and* a proportional
bar per line — the same four numbers drawn twice.

**THE WAREHOUSE-STOCK NOTE LIVES INSIDE THE EXPENSES CARD, under the total it
explains.** It sat at the foot of the page, several screens below that total:
a reader who wondered had stopped reading, and one who got that far was no
longer asking. The question it answers is real, not pedantic — roughly a third
of the parts fitted come off the shelf and their cost is in none of the four
expense lines, so the honest reading of the total with nothing said is that
expenses are short by that amount and the profit above them is overstated.
(They are not: that stock was paid for on a Supplies Shop bill, and charging it
again is the double-count rule being broken — ~₹9.8M against the seeded data.)
→ `TheWarehouseNoteSitsUnderTheFigureItExplainsTests`

**MONEY IN IS GREEN, MONEY OUT IS RED, AND THE COLOUR SITS ON THE AMOUNT.** The
earnings card's right-hand column reads straight down. It is on the amount and
not the label because the two cost rows already carry theirs there — colouring
the label above would make the two halves of one card disagree about where
colour lives. Full-strength `--color-success`, not a tint: at low opacity green
reads as disabled text rather than as a colour.

Two deliberate exceptions: **Gross Earnings is not green** (a structural
waypoint, and with green above and below it there would be nothing for the eye
to land on), and a **discount stays red** even though it sits in the earning
half — it is coloured for the direction it goes, not the half it lives in.
→ `MoneyInIsGreenMoneyOutIsRedTests`

**THE EARNINGS CARD'S SUBTITLE NAMES THE FIGURE.** It read "Same profit, by
what earned it" — what the card does, written so it only parses once you
already know. It now prints the profit itself ("The same ₹4,81,500, broken
down"), which the reader can match against the hero without being told. Its
glyph is `text-primary` like every other card title: **green is reserved on
this page for money that IS profit** — the hero, and this card's last row.

⚠ **There is no "Running costs" heading over the deductions, and it was removed
rather than reworded.** Two of the three rows are running costs and the third
is a timing adjustment that goes **either way** — on a month that drew the
shelf down it is a PLUS. A heading must be true of every row under it, and any
wording covering all three is either wrong on that row or vague enough to say
nothing. The − and + signs carry it, and the two rules (Gross Earnings, then
Profit) give the block its shape.

⚠ **Never park retired copy in a CSS comment here.** `<style>` is served to the
browser, so a phrase quoted in one is still on the page — which is how a test
asserting the old subtitle was gone kept failing after it had been changed.
**The same is true of a `//` comment in an inline `<script>`**, and it bit
again on Owner Withdrawals: comments explaining that "Show everyone" and "No
owner chosen" had been removed put both phrases straight back into the
response. `{% comment %}` is the safe place for that note — Django strips it
before anything is sent.

**Wages come from Salary & Advance, never the Cashbook.** Wage cost for a settled
month is `net_amount + advance_used` (an advance is cash already out; the
settlement pays the remainder), plus loose advances in months not yet settled.

**AN UNSETTLED MONTH'S WAGES ARE NOT IN THE PROFIT, AND THE PAGE HAS TO SAY SO —
this fires on the DEFAULT view, every month.** A salary month is settled in the
first days of the *next* one, so for the whole of any month "This Month" contains
a month with no settlement. Measured on 25 Aug 2026: ₹4,90,577 profit at a 44.4%
margin with the salary line reading ₹0, against a real wage bill of about
₹1,20,000 a month — a third of the profit missing, and all the page said was
"0 month(s) settled", which is the count of what IS in the figure and therefore
says nothing about what is missing from it.

`unsettled_months()` names them and an amber banner sits under the equation.
**Nothing is estimated** — a wage figure nobody paid inside the profit equation
is how this page would go from *incomplete* to *wrong*, the same rule that reports
an uncosted warehouse draw as unknown rather than as ₹0. Two bounds keep it from
becoming noise: never a **future** month (This Year runs to 31 December), and
never a month **before** the workshop's first salary activity (All Time reaches
back to the earliest record).
→ `UnsettledWagesAreNamedNotHiddenTests`

**"ALL TIME" IS ANCHORED BY EVERY STREAM, SALARY INCLUDED.** `_stream_bounds()`
takes five MINs. It used to take three — job cards, cashbook, restock bills — and
a salary month is dated the 1st while the earliest job card fell on the 17th, so
the window opened on the 17th, that month's settlement sat outside it, and All
Time reported the wage bill **₹1,22,167 short** while claiming to cover
everything. Any stream this list forgets is money the widest filter cannot see.
→ `AllTimeReachesEverySalaryMonthTests`

**Every stream is dated by its own natural date**, so a period never mixes bases.
`monthly_series()` must always total to `build_profit_report()` — asserted in
`ConsistencyTests`, so the chart can never contradict the headline.

**`fleet_due` IS CUT FROM `receivable`'S OWN POPULATION BY `receivable`'S OWN
EXPRESSION.** The page labels it "Of that, fleet accounts" directly under
`receivable`, so it claims to be a *slice* of the figure above it. It was
`Sum(BulkPayer.total_billed_amount − total_paid_amount)`, which differs twice
over: those stored totals are **gross of discount** (`update_totals` sums
`total_bill_amount` alone) and they span **every** card on the account including
settled ones, while `receivable` is net of discount over unsettled cards only.
The two agree only while no fleet card carries a discount — the first one that
does would have the page claiming a slice bigger than the whole. Still
deliberately **not** filtered by `is_trashed`, for the original reason:
`receivable` has no such filter, and a balance must not depend on whether
somebody tidied a list.
→ `TheFleetLineIsASliceOfTheLineAboveItTests`

**EVERY BALANCE ON "POSITION RIGHT NOW" CAN GO NEGATIVE, AND THE SIGN IS TURNED
INTO WORDS IN THE ENGINE.** A spare shop paid ahead of its purchases is in
*credit*, not owed ₹-7,65,938 — which is what the tile printed, and reads as a
broken figure rather than a real position. `financial_position()` returns
`tiles`, each with a label, a **positive** magnitude and a direction
(`in` / `out` / `credit`), so the template prints what it is handed. Deciding
what a minus sign means is arithmetic, and arithmetic does not live in a
template.
→ `ABalanceThatWentTheOtherWayIsSaidInWordsTests`

**AN UNASSIGNED SHOP PURCHASE IS DISCLOSED, AND EXPENSED ONLY WHEN IT REACHES A
CAR.** These are parts ordered from a spare shop for one car, not used on it,
and kept on the shelf for the next car that needs it. The shop is owed for them
either way; returning one is the only other exit, and that deletes the row.
`SpareShop.update_totals()` counts them — so they sit inside "We owe spare
shops" — while `spare_shop_expense` filters `job_card__isnull=False` and leaves
them out. **The arithmetic was right and the page said nothing**, so it showed a
debt with no cost anywhere behind it.

Leaving them out matches every other spare-shop purchase, which is dated by
`job_card__admitted_date` so a part's cost sits in the month of the revenue it
helped earn.

> ⚠ **Do not "fix" this by counting them — the alternative is worse than it
> looks.** Nothing in the app attaches an unassigned row to a job card
> (`unassigned_spare_edit` never writes `job_card`), so a part is fitted by
> typing it onto the card and deleting the unassigned row. Expensing it while it
> waits would therefore make a **past month's profit change** on the day
> somebody fits the part: the August expense leaves with the deleted row and
> reappears in September. A settled month moving weeks later is far worse than a
> cost arriving a month late.

**The wording on screen is "not yet fitted", never "not counted".** The first
draft read "not counted as an expense in any period", which says the money
vanished and would send somebody hunting a bug that is not there.

*Not to be confused with go-live opening balances* — those are a separate
one-time exercise for Supplies Shops and warehouse stock, done on go-live day,
and they do not come through this table.
→ `UnassignedShopPurchasesAreDisclosedTests`

**THE "VS PREVIOUS" CHIP COMPARES LIKE WITH LIKE, and `comparison_window()` is
the one place that decides.** `this_month` and `this_year` resolve to the WHOLE
calendar month/year so the header is honest and a forward-dated card is never
outside the window — but the data only reaches today, and comparing that against
a **full** previous period compared 8 months against 12. The page read "−8.5% vs
previous" for 2026 while turnover per trading day was running ~11% **ahead**. A
number that says *down* on a growing workshop, on the page profit distribution is
decided from, is the worst thing this section could do.

Four rules:
- An **incomplete** period is measured only as far as it has data (`read_to`) and
  compared against the **same days** of the period before — labelled "vs same
  period last year" / "vs same days last month". The headline still covers the
  full window; only the two figures being compared are trimmed.
- A **finished calendar** period compares against the previous **calendar**
  period, never "the same number of days earlier". July is 31 days, so the
  day-count version put Last Month at 31 May – 30 June; 2024 being a leap year put
  Last Year at 2 Jan – 31 Dec and quietly dropped New Year's Day.
- **No comparison at all** when there is nothing honest to compare against — All
  Time (its window already starts at the first record) and any previous window
  reaching back **past** the first record, which is only partly covered. Last Year
  read "7.1× vs previous" against a 2024 the system holds five months of: true
  arithmetic, and not a fact about the workshop. Clears itself as history
  accumulates.
- The date arithmetic is **clamped**. `prev_start = prev_end − span` raised
  OverflowError on a custom range starting near year 1 — a mis-keyed year in a
  date box is enough — and a 500 on the profit page is not an acceptable answer
  to a typo.

A percentage past 300% is written as a **multiple** (`19.4×`): a true 1,838.9%
carried to one decimal reads as a broken figure rather than a good month.
→ `AnIncompletePeriodIsComparedLikeForLikeTests`

**NEITHER PAYABLE IS FILTERED BY ITS ARCHIVE FLAG EITHER, and both archive views
refuse a shop that still owes.** `payable_spare` filtered `is_trashed=False` and
`payable_supplier` filtered `is_active=True`, and nothing else counted that money
— so archiving a shop the workshop owed ₹50,000 removed the ₹50,000 from the
only screen that reports it. Worse than the fleet version was, because a
vanishing **payable** *raises* reported profit rather than understating a debt.
Fixed on both sides at once and both halves are load-bearing: the filter is gone
so an already-archived shop still counts, and `spare_shop_delete` /
`deactivate_supplier_shop` now block on an outstanding balance — the same guard
`bulk_payer_delete` carries, keeping the one rule that **money owed is always
reachable from exactly one screen**. A shop paid *ahead* archives normally; a
credit is not a debt.
→ `ArchivingAShopCannotHideWhatIsOwedTests`

## Deep Analysis — the eight insight sections

Mechanics · Spare Parts · Inventory · Vehicles · Fleet · Shops · Cashbook ·
Operations. Lazy-loaded one at a time; `INSIGHT_SECTIONS` in `analysis_views.py`
is the one list that defines them, and the Profit page's Deep Analysis link
builds its subtitle from it rather than naming them a second time.

**ONE WORD, ONE MEANING — across BOTH pages.** Four different figures were all
called "Profit" on two screens an owner reads in one sitting. The vocabulary:

| Word | Means | Where |
|---|---|---|
| **Profit** | the bottom line, after every expense | the Profit page, and nowhere else in Analysis |
| **Gross profit** | revenue − parts cost, no overhead off it | car profiles, Mechanics |
| **Margin** | parts sold − parts cost (no labour either) | Spare Parts, Inventory, Shops |

`test_it_is_never_called_plain_profit` already fixed the car profile; its
neighbours had drifted, and Mechanics is the *identical* calculation so it takes
the identical words.
→ `OneWordOneMeaningAcrossBothPagesTests` scans the section templates, so a
section added later cannot quietly reintroduce a fourth meaning.

**"PAID" MEANS CASH AND ONLY CASH; what a part COST is "spend".** A second
word-collision, on the same two pages and worse than the Profit one because the
two figures are *meant* to differ. The Spare Parts section labelled its COST
tile **"Paid to shops"** while the Shops section labels actual cash out **"Paid
to spare shops"** — on the demo data ₹1,85,000 against ₹6,00,000. One word, two
meanings, two figures, on a screen an owner scrolls in one sitting. The Shops
section's own footnote *defines* Paid as cash, so the page contradicted its own
glossary. The Profit page carried it too, in the earnings card's
`paid`/`paid_word` fields, which rendered "− ₹1,85,000 paid to shops".

Shops are settled in instalments, so what was bought and what was paid rarely
land in one month — **the word is the only thing telling them apart.** The tile
is "Shop spend", the earnings hint reads "spent at shops", and the engine field
is `cost`/`cost_word`, because calling the variable `paid` is what produced the
label.
→ `test_PAID_means_cash_and_SPEND_means_cost_in_every_section` scans the section
templates, so only the Shops section may label a figure "Paid".

**AND THERE IS A THIRD WORD — "BILLED" — BECAUSE THE SHOPS SECTION'S TWO HALVES
ARE NOT ON ONE BASIS.** The same collision one level down, and the one an owner
hits when two sections refuse to reconcile. A **spare-shop** row is a COST: the
part goes straight onto a car, it is dated by that job, and it is exactly what
the Profit page charges — "spend" is right. A **Supplies Shop** row is a
PURCHASE: it puts goods on the warehouse SHELF, which raises the shelf and the
payable and **is charged nowhere**, because the cost lands later, on the day a
mechanic draws the part. That is the "THREE DATES, AND ONLY ONE OF THEM IS AN
EXPENSE" rule showing up in a label.

Both halves said **"spend"**, under a footnote defining spend as *"the figure
the Profit page charges"* — so the supplies tile made a promise the figure does
not keep. Measured on the demo data: ₹85,000 billed against ₹1,88,000 of stock
actually used and charged, two numbers the page said were the same kind of
number. There is no arithmetic that reconciles them, and an owner reading
"Charged ₹3.06L" in Inventory beside "Supplies spend ₹85,000" here had no route
to find that out.

Four things carry it, and the variable name is one of them:
- The tile is **"Supplies billed"** with the basis under it ("onto the shelf, by
  bill date"), and **every one of the four tiles now names its own basis** — a
  tile that has to be reconciled against another screen must say what it is.
- The engine field is **`billed`**, never `spend`, and the total is
  `supplier_billed`. Calling the variable `spend` is what produced the label,
  exactly as calling one `paid` did a level up.
- The footnote defines **all three words** and says where the supplies cost
  actually lives (the Inventory section's "Stock used").
- The section subtitle dropped **"by spend"**, which claimed one basis for both
  halves in four characters.
→ `test_a_SUPPLIES_figure_is_never_called_SPEND` asserts the PROPERTY first — a
bill raised in the window with nothing drawn against it is reported in full and
charged ₹0 — then the label, so the word cannot drift back.

**EVERY MONEY TILE NAMES ITS OWN BASIS, IN ALL THREE SECTIONS — the Shops
section got this and the other two did not.** The rule was written for Shops
when "Supplies spend" turned out to be claiming a basis it did not have, and
it is stated there as *a tile that has to be reconciled against another screen
must say what it is*. Inventory and Spare Parts were never given it, and the
confusion it exists to stop turned up exactly as predicted: **"Stock used" was
read as the supplies BILL for those parts.** It is not — it is `SPARE_COST`
over warehouse draws, a weighted average of what the shelf paid, dated by
`job_card__admitted_date`. The bill is a different figure on a different basis
and sits one section over as "Supplies billed".

Markup and the inline style are copied from `shops.html` character for
character, so three sections cannot drift into three shapes.

⚠ **"BILLED to customers", never "PAID by customers."** `revenue` is
`Sum(total_price)` — what was charged, settled or not. It also keeps these
tiles clear of `test_PAID_means_cash_and_SPEND_means_cost_in_every_section`,
which reserves "Paid" for the Shops section. (That scanner matches a bare
`<div class="k">`, so a basis line carrying a `style` attribute is invisible
to it — the word is right on its own merits, not because the test forced it.)

⚠ **ONLY A TILE THAT RECONCILES AGAINST ANOTHER SCREEN GETS ONE — Margin and
Margin % do NOT.** They are derived on the spot from the two tiles beside them,
both on screen and both now carrying their own basis, so explaining them was
the rule applied past its own edge and cost two of every four lines added. They
were given one for a revision and it came back out on the owner's call.

⚠ **If one is ever restored there, make it a PHRASE, never notation.** The
Margin % line shipped briefly as `margin ÷ charged` — the only sublabel on the
page written as a symbol, beside a tile reading "charged **less** stock used".
The figure is `profit / revenue`, which on the demo data is **40.6% where the
markup (`profit / cost`) is 68.8%**, so if it is ever spelled out it has to
name the denominator in words.

**A BASIS IS ONE CLASS, `.ia-stat .b` in `insights.html`** — 0.6rem/#94a3b8
against the label's 0.66rem/#64748b, so it reads as a footnote to the figure
rather than a second label competing with it. It replaced nine copies of the
same inline style across four section templates, on the `.rpay-*` precedent.

**Count tiles get nothing** — "Parts fitted", "Shops used", and the Mechanics,
Vehicles and Operations sections. They reconcile against no other screen.

⚠ `totals.lines` is `Count('id')`, so **"Parts fitted" counts ROWS, not
units**: a row of 4 litres is 1. Loose rather than wrong, and left alone.

**BOTH PART ROUTES DISCLOSE AN UNCOSTED PART, and only one used to.**
`SPARE_COST` costs a NULL `unit_price` at ₹0 on either route, so on either one a
part with no price reads as **free** and pushes profit UP by exactly that much —
the one way these pages can be wrong without looking wrong. `uncosted_draw_count`
filtered `source=INVENTORY`, so the warehouse half was counted and warned about
while an uncosted **shop** part was silent. Measured on the demo data: a single
unpriced shop row left July's Spare Shops expense ₹1,000 short and its profit
₹1,000 high, while the page reported "0 uncosted".

`uncosted_shop_count()` is its twin and both are rendered — on the Profit page
and in their own insight sections. **The wording differs because the remedy
does**: a warehouse draw needs a Supplies Shop bill to establish a cost, a shop
row just needs somebody to key what the shop charged. A NULL here is not a
fault — `unassigned_spare_add` stores NULL rather than 0 when Floor records a
part, because zero would say the shop gave it away — so the count is a queue,
not an error.
→ `BothPartRoutesDiscloseAnUncostedPartTests`

**THE TWO SPARE ROUTES ARE TWO SECTIONS, not two tables in one.** They were one
merged table until 2026-08-25; splitting the tables fixed most of it and left
the **headline** merged, so a per-job trading margin was still being averaged
against a shelf margin that depends on `avg_cost` being right. Four reasons,
and they apply to any new section listing parts:
- A SHOP part has a shop, an ordering state and a **per-job payable**; a
  warehouse draw came off the shelf, and whatever is owed for filling that shelf
  belongs to a bill, not to this car. Only the first is chaseable per job.
- The COST columns are **not the same kind of number** — a shop line's cost is
  the line total as typed, a draw's is a weighted average × quantity. `SPARE_COST`
  gets each right; printing them in one column invites dividing one by a quantity
  that does not price it.
- **QUANTITY means different things.** A draw's quantity is what left the shelf;
  a shop row's moves no money at all. It is shown for stock and deliberately
  **left off** the shop table.
- **The owner already splits them.** Asked what the workshop earns from, the
  answer named inventory commission and spare-parts commission as two things —
  and the Profit page's earnings card now does too, so both pages describe the
  business the same way.

Both sections read `engine.parts_trading`, so the two sides partition the spare
rows exactly and a margin quoted here cannot disagree with the same margin on
the Profit page.
→ `TheTwoSpareRoutesAreTwoSectionsTests`

⚠ **The Spares glyph was `bi-tools`** — the JOB PERFORMED icon, so the section
that *buys* parts wore the icon of the section that *fits* them, the same
mistake CLAUDE.md records fixing on the Spare Shops pages. It survived because
`SparePartsWearsOneGlyphTests` scans templates and `INSIGHT_SECTIONS` is Python.

**A "MOST USED" CHART IS ITS OWN QUERY, never a re-sort of the table above it.**
The merged section built its movers list from the fifteen rows it had already
cut by **profit**, so a cheap part fitted to every car could not appear in a
chart of what moves unless it also happened to be a top earner — the chart
answered "which of the top earners is used most" under a heading saying
something else.
→ `TheMostUsedChartIsItsOwnQuestionTests`

**THE INVENTORY SECTION VALUES THE SHELF, and unknown cost is `avg_cost == 0`,
NOT NULL.** `Item.avg_cost` is `default=0, null=False`, so an `isnull` filter
matches nothing and would quietly value opening stock that has never had a
supplier bill at ₹0 — reporting it as *worthless* rather than as *unknown*.
Those products are excluded and **counted** instead, the rule
`uncosted_draw_count()` follows. Negative stock is left negative: it is allowed
by design and means a Supplies Shop bill is missing, so flooring it deletes the
signal. It is a **position**, the only figure in the section the date filter
does not touch, and the tile says so.
→ `TheShelfIsValuedHonestlyOrNotAtAllTests`

**A SHOP PURCHASE WITH NO SHOP IS NAMED ON THE SPARE PARTS SECTION.** It is
inside that section's cost — every `SOURCE_SHOP` row is — and it is *not* inside
the Profit page's Spare Shops line, which splits it out as "Other Spare
Purchases". Without the count on screen the two pages quote different
spare-shop costs for one period and nothing says why.

**A WAREHOUSE ROW IS GROUPED BY ITS `item` FK, NEVER BY `spare_part_name`.**
That column is a **snapshot** taken when the part was drawn and is not rewritten
when the product is renamed (`save()` only fills it when blank), so grouping by
it splits one product's history into two rows the day somebody corrects a
spelling. The shop side has no FK and is grouped by `Lower(spare_part_name)` —
but the row is **displayed** from `Min(spare_part_name)`, a real stored spelling.
Displaying the lowered key re-title-cased is what turned 'DOT 4' into 'Dot 4'.

**EVERY JOB CARD IS ACCOUNTED FOR IN "HOW CUSTOMERS PAID".** The table excluded
any card with no `payment_method`, so its Jobs column added to less than the job
count with nothing saying why — 13 of 150 in the demo data. Two kinds have none:
a **fleet card** (the method sits on the fleet payment) and a card **nobody has
settled yet**. Each is named as its own row, and only when there is one.
→ `EveryJobCardIsAccountedForInHowCustomersPaidTests`

**THE FLEET SECTION'S "BALANCE NOW" IS CUT LIKE `receivable`, NOT FROM STORED
TOTALS** — the same defect as the Profit page's fleet line, one screen over, and
worse there because a **net** "Billed" column sat beside a **gross** balance.
`advance_balance` is netted off and the sign is said in words: an account paid
ahead reads "in credit", never as a minus. It had been computed and never
rendered at all.
→ `TheFleetBalanceIsCutTheSameWayTheReceivableIsTests`

**AN ACCOUNT THAT OWES BUT BROUGHT NO CARS IN IS STILL LISTED.** `rows` is built
from job cards IN THE WINDOW while "Balance now" is live and spans the account's
whole history — so a quiet account vanished from the table, taking its debt off
the only screen that lists fleet balances while the Profit page's fleet line
still counted it. Its activity columns print **dashes, not ₹0**: zero billed at
100% collected reads as "billed nothing and collected it all", a claim about a
period the account was not in. Not filtered by `is_trashed`, for the reason
`receivable` is not.
→ `AFleetAccountThatOwesIsAlwaysListedTests`

**THE FLEET SECTION SHOWS ONLY FLEET FIGURES, and it did not.** The largest
number on a card headed *Fleet* was **walk-in revenue** — ₹7.48L of business the
section is not about. The two walk-in boxes existed only as the other half of a
comparison, and once the fleet boxes carry their **share** the comparison is
already made in the place the reader is looking: *"8.7% of ₹33L car bills"* says
everything the second box said. Three boxes remain — how much of the work is
fleet, what it earned, how many accounts — and the denominator is printed, so
walk-in is the visible remainder rather than a competing headline. The account
count sits last and says *"active now, not filtered"*, because it is the only
figure here the date range does not touch.

⚠ The share is **"of car bills"**, not "of turnover". The denominator is fleet +
walk-in revenue; Turnover on the Profit page also carries cashbook income, so the
other wording would be a share of a figure it was not divided by — arithmetic
right, word wrong.
→ `TheFleetBoxesReadAsOneSplitTests`

**THE SHOPS SECTION SELECTS BY `source=SHOP`, not by "has a shop".** A draw
carries no shop today, so the two pick the same rows — but one is the rule and
the other is a coincidence of the data. Parts **not yet fitted** are disclosed on
the row, because they are inside "Owed now" and cannot be inside "Spent".
→ `TheShopsSectionSelectsByRouteNotByCoincidenceTests`

**SPEND AND PAID ARE TWO QUESTIONS, and this section answers the per-shop
one.** "Spend" is what the work cost — the figure the equation charges. "Paid"
is cash that actually left against these shops' ledgers, on their own instalment
rhythm. Neither affects the other and **neither belongs in the profit
equation**: profit and cash differ by five things at once (stock bought but
unused, stock used but bought earlier, bills unpaid, bills paid from earlier
periods, customer bills unpaid), so subtracting one from the other gives a
number that is not anything.

⚠ **THE WORKSHOP-WIDE cash answer now lives on the PROFIT PAGE, and that
reverses what this file used to say.** It read "the cash one lives HERE, not on
the Profit page", and the page carried a pointer to Position Right Now instead
of a number — on the reasoning that any cash figure printed beside profit
invites the incomplete arithmetic above. **The owner's call overruled it: they
read cash more often than they read profit**, so keeping it two taps away was
costing more than the risk. The risk did not go away; it is answered by making
the two impossible to confuse rather than by separating them — see "CASH
TRACKING" below. This section is unchanged and still the per-shop view.

⚠ **BOTH SIDES ARE DATED BY THE DAY THE MONEY MOVED, AND THEY STILL STAY TWO
FIGURES — the reason changed rather than went away.** This rule used to read
"they are dated differently": `SupplierPayment.date` was a real date the office
set while **`SpareShopPayment` had no date column at all**, so one combined
"paid to all shops" total would have meant two things at once. A test asserted
that absence deliberately, **so that the day the column landed the choice got
revisited rather than drifting**. It landed; this is the revisit.

Both now read `date`, never `created_at`. They stay two figures because **a
spare shop and a Supplies Shop are two different trades on two different
instalment rhythms**, which is how the whole Shops section is already split.
Combining them is a product decision for the owner, not a consequence of the
basis lining up.

The tripwire was replaced with the stronger assertion, not deleted: a payment
back-dated out of the window must drop out of BOTH figures. Every payment these
tests create is keystroke-stamped *now*, so a filter quietly left on
`created_at` counts all of them and fails.
→ `test_both_sides_are_cut_by_the_day_the_money_moved`,
`test_the_two_sides_stay_two_figures_even_on_one_basis`

*Considered and rejected:* **a "total company debt" tile.** `payable_total` is
computed in `financial_position()` and deliberately NOT rendered. Spare +
supplies payable is not the whole debt — wages owed for an unsettled month are
not tracked as a payable anywhere — so a figure labelled "total debt" would
quietly exclude the largest monthly obligation the workshop has. Two honest
tiles beat one incomplete total.

*Also considered and rejected:* **a dedicated shop-money section.** Everything it
would carry already exists: what was paid this period is in Shops, what is still
owed is in Position Right Now, and what left the shelf is the Inventory Used
expense line plus the Inventory section. A fourth screen would be a thinner copy
of three.

**"GROSS PROFIT" on a car profile is GROSS, and the word is the whole safety of
it.** `revenue − parts cost` — before wages, rent, power and every other overhead,
because this workshop attributes none of those to a car: labour is quoted whole
with no hours recorded, so there is nothing to apportion by. Measured over the
current data it reads ~45% where the business actually makes ~32%, and that gap
*widens* as payroll grows. "Profit" was refused as a label; *gross* does the
warning. `analysis_engine.build_profit_report` remains the one true profit figure.
→ `test_it_is_never_called_plain_profit`

Four rules hold it up:
- **BOTH part routes are costed, and that is NOT the double-count rule being
  broken.** That rule governs the workshop-wide Profit page. The question here is
  different — what did *this car* cost us — and a part off the shelf cost what the
  shelf paid for it. Nothing is added to a total that already contains the restock
  bills.
- **`SPARE_COST` is imported from `analysis_engine`, never restated.**
- **It says so when its cost side is incomplete.** `SPARE_COST` counts a missing
  `unit_price` as ₹0, so an uncosted part reads as *free* and pushes the figure
  UP — the one way it can be wrong without looking wrong. The count is aggregated
  alongside and printed as a quiet caveat; a fully-costed car says nothing.
- **Owner only, and not computed at all for anyone else** — `None` from the view,
  so the two aggregates never run and the template gates on the value rather than
  a second role check free to fall out of step.
## Customer documents — invoice & estimate

**`workshop/invoice.py` owns BOTH documents. Do not fork it, and do not "unify"
the two places they deliberately differ.** `build_invoice()` and
`build_estimate()` share `effective_quantity`, `derive_unit_price`, `PartLine`,
`JobLine` and the `MIN_JOB_ROWS`/`MIN_PART_ROWS` padding. The estimate is handed
over first and the invoice follows it for the same car, so where they agree they
must agree exactly. `views/billing.py` and `views/estimate.py` resolve the record
and render; neither contains any arithmetic.

**The printed invoice is NOT a transcription of the job card.** Four departures,
each deliberate:

1. **Both spare routes print in ONE "PART NAME" list.** The Job Card *edits* them
   as two sections because a draw has no shop and no ordering workflow, but a
   customer has no interest in which shelf a part came off. One list, insertion
   order, one subtotal.
2. **A warehouse draw is billed under its CATEGORY, never its product.**
   `Item.name` is the branded SKU the workshop buys; `Category.name` is what it
   is. Naming the brand on a document the workshop hands out also publishes its
   supply chain. Shop spares keep their free-text name.
   → **Consequence for go-live: the taxonomy must be Category = generic part,
   Item = branded SKU.** The demo seed is the other way round and would print
   "Fluids" on a bill — that is the seed file being wrong, not the rule.
3. **Labour prints its descriptions and one SUBTOTAL, never per-line amounts.**
   Splitting a ₹2,500 job into five numbers invites a line-by-line negotiation
   about work that was quoted whole.
4. **A blank QTY is ONE for the money, and a single part prints NEITHER a quantity
   NOR a unit price.** Staff routinely leave the box empty for a single part, so
   blank has to resolve to 1 somewhere. But the workshop writes a quantity down
   only when there is more than one of something, and on a row of one **the unit
   price IS the amount** — printing it is the same figure twice in adjacent
   columns. QTY and UNIT PRICE are the *breakdown* of the amount; with one unit
   there is nothing to break down.

**The two cells travel together and are decided ONCE**, by an `itemised` flag in
`build_invoice` — a row either reads "qty × unit = amount" or reads just the
amount, and it can never say a quantity it does not price or price a quantity it
does not say. `derive_unit_price` still holds the arithmetic and is tested on its
own. Compared **numerically** (the column stores two decimals, so a string test
would itemise every row somebody typed rather than left blank) and only against
exactly one: **0.5 litres is not a single anything** and still itemises in full.
Zero and negative fold in with blank.
→ `OnePrintsAsNothingTests` — also pins the two properties a customer could catch
by hand: whenever both are printed they multiply back to the amount, and a free
part still prints ₹0.00 while an unpriced one prints nothing.

**The UNIT PRICE column is always DERIVED** as `total_price ÷ quantity`, never
read from a stored field: `JobCardSpareItem.unit_price` is the workshop's *cost*
and printing it would put the margin on every part into the customer's hand.
Deriving also gives the identical answer where `customer_rate` is set, so one rule
covers both routes.

**A part with no price prints an empty cell while one given away prints ₹0.00.**
`PartLine.priced` exists so a truthiness check cannot collapse the two.

**Two columns diverge between the documents, and both follow from one fact: a bill
records work that happened, an estimate describes work that has not.**

| | Invoice | Estimate |
|---|---|---|
| **QTY** | blank stays blank; a typed **1 is hidden** — on a bill, one is the figure this workshop never writes down | blank stays blank; a typed **1 prints** — somebody chose to put it in front of the customer |
| **UNIT PRICE** | **derived** on any row that itemises, nothing on a row of one | printed **only when `customer_rate` was entered**, whatever the quantity — deriving would present the workshop's arithmetic as a quoted rate |

Both still count a blank as 1 in the arithmetic. `PartLine` carries both
`quantity` (the money) and `display_quantity` (the cell), and **neither document
sets them equal**. Nothing can carry an estimate's figures onto a job card — the
card is typed fresh — so the two can never contradict each other on one car.
→ `TheEstimatePrintsWhatSomebodyTypedTests`, `TheEstimatePrintsLikeTheBillTests`

**The PAID box is a receipt stamp, not a line of the bill.** A settled bill prints
a small green box under TOTAL carrying what was actually received; an unsettled one
prints nothing there, not an empty box and not a zero.
- **`settlement()` in `workshop/invoice.py` decides, not the template.** A
  template asking `received_amount > 0` or comparing it to the total would invent
  a second definition of settled — and would be wrong on the commonest case, since
  a part-paid walk-in is marked PAID with the shortfall booked as a discount. So
  the comparison would print nothing on exactly the bills most worth stamping.
- **Settled is `payment_status in ('PAID', 'BULK_PAID')`. PARTIAL is deliberately
  excluded** — for a walk-in it never occurs, and for a fleet card it means money
  is still owed.
- **It prints the received amount and nothing else.** Not the discount — that is
  the workshop's own write-off, agreed verbally, and printing it invites a
  negotiation about a figure the customer was never quoted. Not a balance either.
- **The label is "PAID" / "FLEET PAID"**, not `get_payment_status_display()`,
  which reads "Fully Paid" — written for the office screens, and beside ₹37,000 on
  a ₹40,820 bill it puts two claims on one page.
- The box sits **outside** the table: those two totals rows are the bill's
  arithmetic, and a third would also widen the totals block's `break-inside:
  avoid`.
→ `ThePaidStampAppearsOnlyOnceSettledTests` — note its assertions run against
`_sheet()`, because `.paid-box` is also a stylesheet rule and a whole-page search
finds it on every render.

**The invoice page loads NOTHING from a third party.** No CDN CSS, no CDN JS, no
icon font — everything inline, the modal is a native `<dialog>`, the icons are
inline SVG. A framework reset shipping upstream could move a column on a
customer's bill, and a workshop printing on a dropped connection got an unstyled
page.

*Since 2026-08-21 the whole app is third-party-free* (see `static/vendor/`), so
this is no longer the one page that is. **The rule still stands on its own terms
and is stricter:** the invoice loads nothing from ANY origin, including ours —
inline, not merely self-hosted — because a bill must print identically whatever
the network is doing, and because the sheet must carry no reference a stylesheet
edit could turn into a fetch.

**Assert that on FETCHES, not on the string "http".** A blunt
`assertNotIn('http://', html)` breaks the moment an SVG goes in, because every SVG
declares `xmlns="http://www.w3.org/2000/svg"` — an XML namespace *identifier*, a
name shaped like a URL that no browser ever resolves. The test checks what
actually causes a request: no `cdn.`, no `<link`, no `@import`, no `url(http`,
every `src`/`href` same-origin, every absolute URL one of the namespace
declarations. (`src=` is legitimately present — the page loads its own
`js/sound.js` off `/static/`, which is this server. The *printed sheet* carries no
reference at all, asserted separately.)

**The screen controls live OUTSIDE the `.sheet` element entirely**, not merely
`display:none` in print. `NothingInteractiveLivesOnThePaperTests` asserts the
sheet contains no `<button>`, `<a>`, `<form>`, `<input>`, `<script>` or
`<dialog>`, because a CSS-only rule is one stylesheet edit from printing.

**The template is standalone** (does not extend `base.html`), so it **must**
render the `messages` block itself — that is not the double-render `base.html`
forbids. It previously rendered none, so "Billing updated" was never shown on the
one page where money is actually settled.

**The invoice is one A4 sheet on screen as well as on paper — narrow screens SCALE
it, they do not reflow it.** A bill that rearranged itself to fit would stop being
a preview of what prints. `fitSheet()` applies `transform: scale()` and sets the
wrapper's height to match; `@media print` clears the transform.
- The wrapper's height is set by the same function that watches it resize, so the
  `ResizeObserver` must compare **width only** or it calls itself forever.
- Both `window.resize` and the observer are attached deliberately — some browsers
  report a rotation through only one.
- Pagination is pure CSS: `thead { display: table-header-group }` repeats the
  column headings, `tr { break-inside: avoid }` stops a row splitting, and
  SUBTOTAL/TOTAL sit in their own `<tbody class="totals">` rather than a
  `<tfoot>`, **which would have repeated them at the foot of every page.**

**The toolbar breaks into TWO CHOSEN rows on a phone.** `.bar-spacer` becomes
`flex: 0 0 100%; height: 0` below 640px — a full-width line break — so row 1 is
*where you came from and what state this bill is in* and row 2 is *the three
things you can do*, in equal columns. `flex: 1 1 0` with `min-width: max-content`
makes them equal when they fit and wrap **intact** when they do not: no label is
ever truncated, which on a row of verbs is the difference between a button and a
guess. The rule is scoped `.bar .btn`, not `.btn` — the same class is the dialogs'
button. "Print / Save PDF" sheds its second half into a `.btn-print-long` span.
→ Consequence: the full wording is no longer one contiguous string, so
`test_the_controls_are_all_marked_no_print` checks that button by its **action**
(`window.print()`) — "Print" alone also matches `@media print` in the stylesheet.

**`estimate_print.html` carries the identical block**, and that is the point rather
than a copy-paste slip: the two screens are opened days apart by the same person,
and a toolbar that rearranges itself between them reads as two different products.

**The letterhead is the owner's own PNG, inlined as a data URI, from ONE include**
(`workshop/includes/_brand_mark.html`). Five things are load-bearing:
- **A `data:` URI, never `<img src="/static/...">`.** Anything fetched can fail to
  arrive, and a bill that prints without its letterhead is worse than one that
  never had it. A static path would render identically in development and then 404
  on a deploy that missed `collectstatic`.
- **A raster is safe HERE because it out-resolves the paper** — 1323px across 56mm
  is 600 DPI, twice what a 300 DPI print consumes.
  `test_the_artwork_is_dense_enough_to_print` fails below 500.
- **The supplied file needed three fixes**, all invisible on screen and obvious on
  paper: cropped to its ink (the canvas carried padding), background lifted from
  253-grey to pure white, resampled to 600 DPI. Encoded as a **16-colour palette**
  — 130KB truecolour became 38.6KB, which is why a three-colour mark can be
  inlined at all.
  ⚠ **Never hand-edit the base64.** To replace the mark, redo those four steps
  from the owner's original artwork with Pillow and re-inline the result. The
  one-off script that produced the current file is **not in the repo** — only
  `scratchpad/build_app_icons.py`, which does the equivalent job for the app
  icons and is the working model to copy.
- **Sized by WIDTH (56mm), height `auto`**, measured against the workshop's own
  running bill (a 55.6 × 13.3mm lockup). Height stays `auto` so the ratio can only
  come from the file.
- **One include, both documents.**
→ `BothDocumentsCarryTheSameLetterheadTests`

*A traced SVG was tried twice and rejected.* The first was 98% anti-aliasing
noise. The second was genuinely clean and still lost, and that is the part worth
keeping: **a trace approximates letterforms by construction** — it rendered at
3.73:1 against the artwork's true 4.40:1, a 15% vertical stretch. **Greys in a
two-colour logo are the tell for the first failure; a ratio that disagrees with
the source is the tell for the second.**

## Estimates

**An ESTIMATE is connected to NOTHING, and that isolation is the feature.**
`Estimate` / `EstimateJobLine` / `EstimatePartLine` are read by five views and one
printing function, and by nothing else — no job card, no warehouse stock, no
ledger, no line in `analysis_engine.py`. Money on an estimate is a *proposal*: a
quote that moved stock or entered the Profit page would be the workshop counting
work it has not done.

Three consequences:
- **The part name is free text and matches nothing on purpose** — quoting
  "Castrol Edge 5W-30" must not deduct the shelf.
- **`EstimatePartLine.customer_rate` / `.amount` are named the OPPOSITE way round
  from `JobCardSpareItem`**, deliberately. There, `unit_price` is COST and
  `total_price` is what the customer pays. An estimate has no cost side at all —
  every figure on it is a quoted price — so the per-unit field reuses the one
  `JobCardSpareItem` name that already means exactly that. **Nothing here may ever
  be read as a cost.**
- **Deleting one writes NO `DeletionLog` row.** The only place the section departs
  from the app's deletion model, and it is a decision: `DeletionLog.record()` is
  the origin of `RECORD_DELETED`, which is CRITICAL and pushes to both owners'
  phones. An estimate is a draft expected to be rewritten and discarded, and
  buzzing two phones over housekeeping is how a critical alert stops being read.
  Logging-without-notifying was rejected because it means weakening the choke
  point that keeps the other ten entity types correct.
→ `AnEstimateIsConnectedToNothingTests`,
`test_quoting_a_stock_product_moves_no_warehouse_stock`

**There is no delete button — clearing the name IS the delete.** A ✕ beside every
row is a one-tap way to lose work on a tablet, and a quote is typed in a hurry.
`BlankRowIsNoRowFormSet` marks a row DELETE when it is blank, **and additionally
whenever a STORED row has lost its name — even if its figures are still there.**
That last part is the whole gesture. A **new** row carrying figures with no name is
still refused, because there it is a slip rather than an erasure.
→ `test_clearing_the_name_deletes_a_PRICED_stored_line`,
`test_a_priced_NEW_row_with_no_name_is_still_refused`

**A blank row is not a row — and the fix has to run BEFORE `super().clean()`.**
Everything on a quote is optional, so a line someone typed into and then cleared
must not become "This field is required".

⚠ **The ordering is load-bearing and cost an hour to find.**
`BaseModelFormSet.clean()` calls `validate_unique()`, which reads
`self.deleted_forms` — and that property **caches** its answer in
`_deleted_form_indexes` on first access. Marking the rows after `super().clean()`
marks them too late; the cache is already built from the unmarked forms and
`deleted_forms` stays empty forever. The failure is worse than a no-op:
`_post_clean` excludes a blank value on a not-required field from model
validation, so the emptied row raises no error either — it is simply **saved**,
printing an unnamed line on a customer's document.
→ `test_clearing_an_existing_line_removes_it_instead_of_erroring`

**The part-price suggestion is a PLACEHOLDER, and never anything more.**
`spare_price_hint` in `views/autocomplete.py` puts the average customer price over
the last 5 billings of that name into the Unit Price box's *placeholder*. Never
written into the field, never posted — so the worst case when the endpoint is
slow, wrong or down is grey text nobody uses. **A price on a document handed to a
customer must be something a person decided.** Three rules: it is the **customer
price**, derived with the printed document's own `derive_unit_price` rule and
never `JobCardSpareItem.unit_price` (which would quote every part at cost); it
reads **job cards only, never past estimates**, or one optimistic quote would
drift the suggestion upward forever with nothing real underneath it; and a part
with no history returns `found: false` rather than zero, because "never sold" and
"it is free" are different answers. It is `@office_required`, not
`@staff_required` like its neighbours — Floor is shown no prices anywhere.

**Two date filters, not the eight the day-to-day lists carry.** Paid Bills /
Completed / Cashbook sort a stream of daily activity. A workshop writes a handful
of quotes a month and looks them up months later, so six of those eight would
return an empty page most of the time — which reads as a broken screen, not an
empty period. This Year (default) or All Time, as two pills rather than a dropdown.
An unrecognised `?filter=` falls back to This Year rather than silently widening.

**A native `<datalist>` for part names, not the Job Card's fetch autocomplete.**
The master spare list is ~200 entries and a datalist needs no wiring — so a row
added *after* page load gets the same suggestions with nothing to re-initialise.
That is the whole point: the three formset-cloning traps all live in per-element
wiring. For the same reason `estimate.js` is **pure event delegation**, and its
blank rows live in `<template>` elements rather than hidden `<div>`s — a
template's contents are a detached fragment that `querySelectorAll` cannot reach,
so the `__prefix__` placeholder can never be picked up by a document-wide sweep.
Removing a row **ticks DELETE and hides it, never removes the node**: Django reads
a formset by contiguous index.

**A list row survives every combination of blank fields.** Two rules: the
**headline is whatever identifies the car best** — brand + model, else the
registration, else the estimate number — so there is always exactly one big line;
and **nothing blank is announced**, so a missing customer prints nothing rather
than "No customer name". The registration shares the headline line rather than
sitting under it, which is what keeps every row two lines tall at every width. A
quote with no figures prints **"Not priced"**, never ₹0.00.

**A money box must not fight the person typing into it.** `_tidy_money_initial`:
a field arriving with `0` turns the first keystroke into `08500`; one arriving
with `8500.00` puts two zeros and a point between the caret and the next digit.
Display only — real paise are kept (`1250.50`), because dropping those changes the
number rather than tidying it. Bound forms are deliberately untouched:
`BoundField.value()` reads submitted data, not `initial`, so a rejected POST shows
exactly what was typed.

**The header keeps its action beside the title at every width — the row must never
become a column.** Switching `.est-header-top` to `flex-direction: column` gives a
phone a full-width "New Estimate" button on its own line and pushes the first card
below the fold. The title shrinks instead (`.est-title-word` is a separate element
precisely so it, and not the count pill or the button, is what truncates), and the
description sits **outside** that flex row.
→ `test_the_header_puts_the_action_beside_the_title_not_under_it`

*This is the opposite call from the Spare Shop header, and the difference is real:*
there the action is a short fixed button against a title the page controls; there
it is a variable count badge against a name the customer chose.

## Settling — "what is still unfilled"

**`workshop/settlement.py` is the one implementation, read at two moments** — "you
are about to skip this" by the settle dialog, "you skipped this" by the Live
Report's *Billed but not filled* container. A second copy would drift exactly
where it matters: a card the dialog waved through turning up on the chase list, or
the reverse. `unfilled(jobcard)` returns the grouped structure both surfaces draw.

Settling is the last thing that happens to a job card and the only irreversible
one: the moment a figure is typed the card is PAID, the shortfall becomes a
permanent discount, and the Financial Lock stands between the card and anyone
correcting it. Four rules:

1. **It never blocks.** Both remaining buttons go forward. The workshop settles at
   the counter with the customer standing there, and a checklist that refused to
   let them pay would be worked around inside a week — by not opening this screen
   until afterwards, which loses the check entirely.
2. **It is not rendered at all when there is nothing to say**, and the settle
   button reads *its absence from the DOM* to decide. A dialog that appears on
   every settlement, most of them fine, is one people learn to dismiss without
   reading.
3. **A warehouse draw is never chased for a shop's fields.** A draw came off the
   shelf already fitted, so it has no shop, no order and no arrival, and its
   `status` column is meaningless. The one check spanning both routes is the
   customer price, because that is the figure that bills whichever shelf the part
   came off.
4. **A card with no job lines is NOT nagged about labour.** ₹0 labour is correct on
   a parts-only bill; the gap is reported only when work was *recorded* and left
   unpriced.

**"Settle without completing" was REMOVED.** A walk-in has one payment event and
it happens at pickup, so by the time anybody is on this screen the car is going
out; settling while leaving the card open says the workshop still holds a car it
does not, and that card then sits on the home board and in every "in workshop"
count. It traps nobody: completing a card is the one action here that is **not**
one-way (Undo Completion is in the ⋮ menu on Completed).
→ `test_there_is_no_way_to_settle_and_leave_the_car_on_the_board`

**An uncompleted card is kept apart from the list, with its own button.** It is
not one more unfilled box — it is a contradiction (money taken for a car the board
still shows as being worked on) and the only item fixable from this screen.
"Complete & settle" posts `complete_card=true`, read by `update_bill_status`. That
runs **before** the money moves and outside any condition on it, so a card that is
genuinely finished stays marked finished even if the settlement then fails; and
`JobCard.mark_completed()` is a **no-op on an already-completed card**,
deliberately, because `completed_date` is what the Completed list filters and sorts
on and a re-settlement weeks later must not restamp the day the car was handed
over. That method is the one implementation, shared with the Completed button.
→ `workshop/tests/test_settlement_preflight.py`

**ONE GAP, ONE BOX — the box holds the thing AND what is wrong with it.** A row
reads **"Castrol Edge 5W-30 — no customer price"**, in one bordered box. It used to
be the name on one line with small red chips beneath, under a section heading
carrying a count: one part missing one figure cost three lines and five elements,
and a car with a concern and two parts was a fourteen-line block.

- **The phrases live in `settlement.MISSING` and are DERIVED from the chip
  labels.** The labels are still a gap's identity — `count` counts them, the tests
  name them — so a second hand-written list would be one vocabulary twice, free to
  drift into a screen that chases "Shop Price" here and "no supplier price" there.
  `PartGap.missing`, `ConcernGap.missing` and `Unfilled.card_missing` are the only
  things either template prints.
- **A concern says "not fixed", and its STATUS is gone from the module.** PENDING
  vs WORKING is a real distinction while the car is on the floor; the moment it has
  been billed and driven away "Working" is a claim about the present that is not
  true. What is true, and the only thing anyone can act on, is that it was never
  marked fixed.
- **The section headings went with the chips.** Each row carries the icon of the
  job-card section it belongs to instead. A capped section still prints its exact
  remainder, so the visible rows plus "+N more" are the true total.
- **The tint INVERTS between the two surfaces' chips and these boxes**,
  deliberately: a chip is white-on-red because it sits on the section's red wash;
  these boxes sit on a white card, so a faint red ground separates them and the red
  is spent on the words.
- **The phrase WRAPS, it never truncates.** The phrase being readable is the whole
  point of the row.
- **It is a CHECKLIST, not prose — no sentences, no tinted boxes.** An earlier
  build explained each gap in a sentence: every sentence was true and the whole
  thing was four paragraphs deep, which on the one screen where somebody is
  standing at a counter with a customer is the same as saying nothing.
  → `test_a_chip_is_a_label_not_a_sentence` keeps prose out.

The two templates stay separate **markup** (the invoice loads nothing from
anywhere and carries its stylesheet inline, so an include would still declare the
classes twice) but never separate **rules**.
→ `test_the_dialog_prints_the_phrases_the_module_names`

**The two spare DATES are ONE gap.** A part is finished when it has been ordered
*and* received, so half-filled is still incomplete; which of the two is missing is
answered by opening the date panel.

**The dialog is AMBER for a question and RED for a warning.** An uncompleted card
on its own is a contradiction worth pausing on, and the button beside it fixes the
one thing wrong — amber. The moment anything is actually *unfilled* the frame turns
red, because settling closes the door on correcting it. `readiness['is_critical']`
decides in Python, so the frame and the body cannot disagree; red outranks amber.
Neither state blocks.
→ Assert on the rendered `<dialog>` tag — `.pf-critical` is also a rule in the
invoice's own inline stylesheet, present on every render.

**Three buttons, and the DOM order serves both layouts at once, so it must not be
shuffled**: left-to-right weakest→strongest on a laptop (Cancel · Open job card ·
the action), and on a phone a two-column grid with the action hoisted by `order`
to a full-width row at the TOP under the thumb.

## Photos

**PHOTOS are a SEPARATE SUBSYSTEM that the rest of the app does not know exists —
and that isolation is the feature, exactly as it is for an Estimate.** Three
surfaces: car photos on a saved job card, a box per Spare Parts row, and a
read-only box on Spare Shop → Purchase History.

**Nothing points AT a photo.** No column on `JobCard` or `JobCardSpareItem`, no
money, no stock, no ledger, nothing in `analysis_engine.py` and nothing in
`invoice.py` — so a photo cannot reach a customer's bill. Photos upload
**independently of the form POST**, so storage being slow, down or entirely
unconfigured cannot block a job card from saving. With no credentials the box is
not rendered, the endpoints answer 503, and every other thing on the form behaves
identically.
→ `TheSectionIsCompletelyOptionalTests`

⚠ **`settlement.py` must NEVER chase a missing photo.** Turning "no photos" into a
settlement gap would paint every ordinary card red on the Live Report and in the
settle dialog, which is the opposite of optional.

**Cloudflare R2, not a Railway Volume, and the deciding factor was BACKUP.**
Railway sells no object storage — only Volumes, a disk bolted to one service. A
Volume would work and `backup_db` cannot see it, so photos would be the only data
in this system with no backup at all, and they are evidence in a
pre-existing-damage dispute. R2 is free at this workshop's volume (~1.8 GB/year
against a 10 GB tier), has zero egress, and survives a change of host. Postgres
BYTEA was rejected on the backups: 14 retained `pg_dump`s of 1.8 GB/year makes
restores unusable.

**The bytes never touch Django.** The browser PUTs straight to R2 on a presigned
URL and GETs the same way, so an upload on bad shop wifi never occupies a gunicorn
worker and the `no-store` middleware never forces a re-fetch. `workshop/photos.py`
signs with stdlib `hmac`/`hashlib` rather than `boto3`, and `presign()`
deliberately takes every input as an argument and reads no settings — that is what
lets it be pinned to **AWS's published known-answer vector**
(`test_it_matches_the_published_aws_example`). Get the signing wrong and uploads
403 with an opaque browser error; there is no other way to catch it without a live
bucket.

⚠ **The bucket needs a CORS policy** (PUT + GET from the app's origin,
`content-type` allowed) or every upload fails in a way that reads exactly like a
signing bug.

**FOUR endpoints, not three: sign and commit are separate, and the ordering is
load-bearing.** The obvious design writes the row first — and a browser closed
mid-upload then leaves a row pointing at an object that does not exist, which the
gallery draws as a broken image nobody can explain or remove. Signing first
inverts the failure: **a row always means a real photo**, and the cost is an
orphaned object when a commit never lands, invisible to everyone and collected by
`sweep_photo_blobs`. The server mints the UUID at sign time because the storage
key is derived from it. **The limit is re-checked inside the commit transaction**,
because a burst has several in flight at once.

**Deleting the row and deleting the blob are deliberately separated.** A DELETE to
R2 is a network call and this codebase does not put those on the request path; if
the bucket is unreachable a photo must still vanish from the app the instant
somebody deletes it. `OrphanedPhotoBlob` is written in the same transaction as the
delete, so a key cannot be lost between the two, and a re-run of the sweep is
harmless.

⚠ **The queueing is a `post_delete` SIGNAL on `JobCardPhoto`, never the view.** It
lived in the delete endpoint first, which covers exactly one of the ways a photo
row can vanish. Every other way is a CASCADE — removing a spare row, deleting a
job card, `purge_business_data`, `purge_old_photos` — and a cascade fires no view,
so those objects were orphaned in the bucket permanently. Django skips its
fast-delete path for a model carrying a post_delete receiver, so the signal fires
for querysets and cascades alike.
→ `test_a_CASCADE_queues_the_object_too`,
`test_a_bulk_queryset_delete_queues_every_object`

**The STORAGE KEY is a UUID and the DOWNLOAD NAME is readable — two different
strings, on purpose.** `download_name()` gives a saved copy the car, plate, job
card number and date, carried by `Content-Disposition` on the signed URL. The key
stays `<uuid>.jpg` for three reasons, each a defect avoided: it is derived from
the primary key, so building it from the registration would orphan every photo of
a car the moment somebody corrected a typo in its plate; two photos of one car on
one job card would collide; and a readable key is a **guessable** key, which on a
bucket left public is enumerable by anyone who knows a registration number.
`_filename_safe()` collapses anything that is not a letter, digit, dash or dot — a
slash in a part name would read as a directory separator on the way into somebody's
phone.

**The freeze mirrors the FINANCIAL LOCK, and is keyed on the CARD'S PAYMENT
STATUS, never on which page the request came from.** A settled card's photos can
be looked at and not changed — money and evidence stop moving together. Purchase
History carries no Financial Lock, so a page-based check would leave that door
open. Consequence: because a settled card's photos cannot be deleted at all, the
only delete there is removes a mis-shot from an open card — housekeeping — so it
writes **no `DeletionLog` row**, on the Estimates reasoning.

⚠ **The box is a `<div role="button">`, never a `<button>`.** The Financial Lock
disables everything matching `input, select, textarea, button` inside the form, so
a real button would go dead on a settled card — killing **viewing** as well as
adding, on exactly the cards whose photos matter most. Whether photos may be ADDED
arrives separately as `data-can-edit`. The overlays live **outside** the `<form>`
for the same reason.
→ `test_the_box_is_not_a_button_so_the_financial_lock_cannot_kill_viewing`

**Capture is ONE TAP and there is no review.** It halves the taps on a ten-photo
walk-around, and moves the whole risk into the upload: a frame is **held in memory
until the server confirms it**, one retry is spent automatically, a *refusal*
(limit full, bill settled) is never retried, and anything still broken becomes a
**visible** failed item plus a `beforeunload` warning. A photo may never disappear
silently. The flash, the count bump and a shutter click (`sound.js`'s `shutter`
tone) are the capture feedback.

**The shutter click is not an outcome tone, and that is what keeps it safe in a
burst.** The rule used to be "no tone per shot" outright, rejecting a `success`
chime on every frame of a ten-photo walk-around — ten confirmations is the noise
that teaches people to stop hearing the tones that matter elsewhere in the app.
`shutter` sits outside that vocabulary on purpose: it fires at the moment of
capture, before the upload even starts, so — like a physical camera, which clicks
on every frame whether or not the shot comes out — it claims nothing about the
result. The tone that actually reports a result is still `error`, still fires
once per genuine failure rather than once per shot, and is unchanged by this.
→ `test_a_photo_that_never_uploads_becomes_VISIBLY_failed`

**THE LIGHTBOX CAPTION READS TOP-DOWN: where you are, which car, then when and
who.** "1 of 4" leads and is the ONLY one of the three given any weight,
because it is the only line that CHANGES as you swipe — it sat under the date
in small grey type, which is where the eye arrives last. The car and the date
share a SINGLE declaration and sit flush together: they are two halves of one
quiet caption, and a rule they both match cannot drift apart. The one gap in
the block is under the position, separating what stands out from what does
not. A single photo prints no position at all — "1 of 1" is a fact about
nothing, the same rule the job card's own quantity follows.

**The car comes from the SERVER, as one `subject` label for the whole gallery.**
The overlay is included on two screens and only one of them knows the car: the
job card form has it on screen, while Purchase History is a shop's page where a
row's car is whatever card that spare hangs off. Asking the page would be two
answers free to disagree. It is sent once rather than on every photo — every
photo in one gallery belongs to the same card by construction, so per-photo it
would be the same string ten times over the wire.
→ `TheLightboxSaysWhichCarTests`

**The box shows a COUNT and never a thumbnail** — which is what lets the feature
skip thumbnail generation, a second object per photo, and any server-side image
processing at all. **The limit is never printed until it is hit** — a "3/10" badge
invites filling it. **The gallery is newest-first**, which is what makes
unreviewed capture safe. **The lightbox image is a plain `<img>` with nothing
layered over it**, which is what makes long-press "Save image" work for free on
iOS and Android; an overlay or a `pointer-events` trick would take that away.

**A NEW card, and a NEW spare row, offer no box** — neither has a primary key to
attach a photo to. The card's row says "save the job card first"; a spare row
leaves its cell EMPTY, but the cell itself always renders, or every column to its
right shifts by one on that row. This is the real workflow rather than a
workaround: nobody photographs a car while typing its registration.

**The per-row counts are ANNOTATED onto the formset queryset**
(`SourceScopedSpareFormSet.get_queryset`), never counted in the template — a
rebuild in the live data carries 91 spares.

**PURCHASE HISTORY IS VIEW ONLY** — no camera, no delete, and no box at all on a
row with no photos. Recording a part is the floor's job and it happens on the job
card; a second door into changing evidence is how two screens start disagreeing.
Both halves have to agree for editing to be offered — the server's `can_edit` AND
the box's `data-can-edit`. **Only the first is a control**; the second is
presentation, and that is honest here because the person may already add that
photo from the job card.

**Retention is `purge_old_photos`, and it is NOT scheduled by default.** The
owner's rule is that complaints stop after a year, and the arithmetic works out:
1.8 GB in, 1.8 GB out, plateauing around 2 GB inside a free 10 GB for ever. **It
skips cards still `PENDING` or `PARTIAL`, whatever their age** — a year-old unpaid
bill is the one case where "no complaints after a year" is false by construction,
because an unpaid bill *is* an open argument and those photos are the evidence in
it. Age is from `taken_at`, not the card's date, so a photo added late to an old
card still gets its own full year. No `DeletionLog` rows: several hundred CRITICAL
pushes a month is how a critical alert stops being read.
## Auth, RBAC & security ("Steel Gate")

**Two lockouts, different units.**
- `AccountLockout` is the primary: **5 failures locks that one account** for 15
  minutes.
- `FailedAttempt` is the backstop, counting by direct `REMOTE_ADDR`
  (X-Forwarded-For is intentionally ignored to prevent spoofed-IP bypass), at
  **`IP_FAILURE_LIMIT = 20`**.

The IP threshold was raised from 5 because the unit was wrong for this business:
the laptop, the tablet and both owners' phones leave through one connection, so
five fumbled attempts on the Floor tablet locked the owners out of their own
devices. **Don't lower it back** — per-account lockout is what actually stops a
guessing attack; the IP gate only catches a spray across many accounts.
→ Tests touching either must clear `FailedAttempt.objects.all()` in `setUp` to
avoid cross-test contamination.

**Login is one view behind one door.** `/login/` reads `Sign In` / `Identifier`
and names no roles. `/admin-login/` survives as a `RedirectView` with
`query_string=True` — never deleted, because the owners have it bookmarked, the
name is still reversed, and dropping the query string would strand an old
bookmark's `?next=`.

There used to be two faces, a blue "Staff Sign In" and a red "Admin Sign In" on
one view. They **gated nothing**, since either accepted any role; what they did
was publish the org chart to anyone who typed the address, and the staff face
named the lower tiers in its placeholder. Consequences worth keeping:
- **`Forgot?` moved onto the one door**, where it belongs — it used to render only
  on the owner face while the nav bar links to `/login/`, so an owner arriving the
  ordinary way had no recovery route on screen at all.
- **All three RBAC decorators use `login_url='/login/'`** — Owner and Office pages
  used to bounce anonymous visitors to `/admin-login/`, which is how probing an
  owner URL revealed the second door.

⚠ **Obscurity is not a control and must not be treated as one.** The controls are
the password, the two lockouts, HTTPS and the RBAC decorators. This only stops the
front door drawing a map. Note what it deliberately does *not* hide: the lockout
message still confirms an account exists after five tries, which is a documented
trade in `login_view`.

**Sign in with username, email, or mobile.** `resolve_user_by_identifier` tries
each in that order and **fails closed** if more than one account matches.

**An OWNER account is nameable only by its email address at the sign-in form.**
The mobile branch accepts the last ten digits, so the workshop's *published* phone
— website, business cards, Google Maps — was a valid owner identifier, and a
first-name username is barely better. Being nameable costs twice at this form and
nowhere else: it is where guessing happens, and it is where five wrong tries lock
the account, so anyone who could name an owner could lock that owner out on
demand. Three things are load-bearing:

1. **The refusal must also be enforced at the `authenticate()` call.**
   `login_view` passes `username=account.username if account else ''` and **never
   the raw input** — Django's `ModelBackend` looks accounts up *by username*, so
   the old fallback would have handed the refused text straight to the backend and
   signed the owner in on it.
   → `test_a_refused_identifier_cannot_authenticate_by_the_back_door` asserts this
   **with the correct password**; with a wrong one it passes whether or not the
   hole is open. Timing is unchanged: `''` matches nothing and ModelBackend still
   hashes a dummy password on a miss.
2. **The reset flow is deliberately NOT narrowed.** It answers identically whether
   or not an account exists, carries its own throttles, and delivers only to the
   address already on file — so a username there hands an attacker nothing, while
   refusing it would strand an owner who remembers their username but not which
   address is on the account. *Recovery paths should be generous about identifying
   you; authentication paths should not.*
3. **An owner with no email is exempt**, or the rule would be a permanent lockout
   with no way back. Only an owner can clear an owner's email, so it is not a lever
   an attacker can pull. Several older fixtures create owners with no email and
   therefore still sign in by username entirely legitimately —
   `test_sign_in_by_mobile_reads_the_database` says so in its docstring rather than
   leaving it to look like an oversight.
→ `OwnersSignInByEmailOnlyTests`

**The trade, stated plainly:** an owner who types their username gets "Invalid
credentials", indistinguishable from a wrong password — the message cannot say
more without confirming the account exists. **Tell the owners this out loud.**

**A password reset clears `AccountLockout`, and must keep doing so.** Owners
cannot be unlocked from Control Hub (`manage_reset_password` refuses them by
design), so the emailed code is a locked-out owner's only self-service route back
— and it dead-ended: the lock is keyed to the account, not the password, so the
owner read "Password changed, sign in with your new password", did so, and was
answered "This account is locked". That reads as the reset having failed, and the
obvious next move burns the 3-per-hour code budget until `RESET_CODE_LIMIT` alarms
**both** owners over somebody correctly recovering their own account.

The IP backstop is deliberately **not** cleared: its message names the network
rather than the account so it never contradicts the reset, it clears itself on the
same timer, and wiping it would erase the record of a spray against every other
account behind that connection.
→ `test_a_locked_out_owner_can_sign_in_straight_after_resetting`,
`test_the_reset_does_not_wipe_the_network_wide_failure_count`

**RBAC decorators return 403, not a login redirect, for signed-in users.**
Anonymous visitors still get the sign-in page with `?next=`, validated by
`_safe_next` against open redirects. A signed-in user who simply lacks the role
gets `PermissionDenied` → `templates/403.html`. Both used to redirect to a login
form, so an Office user opening an Owner page saw a sign-in screen *while already
signed in*. A test asserting 302 for an authenticated wrong-role user is asserting
the old bug.

**Owner accounts are `is_superuser=True` but `is_staff=False`.** That pairing is
deliberate. `is_superuser` is what every RBAC decorator and the `has_group`
template filter check, so owners keep full authority inside the app. `is_staff`
gates **only** the Django admin site, and `/admin/` bypasses the protections this
app is built around: a delete there writes no `DeletionLog`, the Financial Lock
does not apply, and archive-don't-delete is not honoured. `sync_owner_identity`
re-asserts this on every run. If you genuinely need admin, `createsuperuser` a
separate account and delete it after. **Consequence: with no `is_staff` accounts,
`/admin/` is unenterable by anyone — intended.**

⚠ **Never use `is_staff` as a workshop role check.** It means "can log into Django
admin", nothing more. It was once used to gate the Invoice link, which hid billing
from the Office role whose job it is — while `invoice_view` itself is
`@office_required`. **Template gates must mirror their view's decorator.**
→ `InvoiceLinkVisibilityTests`

**The whole Control Hub (`/manage/`) is Owner-only** — accounts, staff roster and
security alike. It was `@office_required` while the drawer only ever offered it to
owners, so Office could not see it but could reach it by URL and create logins or
reset passwords. Owner accounts are never managed *from* this panel: reset, delete
and unlock each refuse them, because owner credentials are changed at
`/change-password/` or recovered by emailed code.

**`manage_unlock_account` lets an owner lift a lockout immediately.** Five wrong
attempts locks a staff account for 15 minutes, which is right against guessing and
wrong when a mechanic fat-fingers their password mid-shift. The button renders only
while an account is actually locked.

**Creating a login is all-or-nothing.** `create_user()` used to run *before*
`Group.objects.get(name=role)`, so a missing group row 500'd the panel **having
already created the account** — a login with no group at all: invisible in Control
Hub (which lists strictly by group), able to sign in, then 403'd by every
decorator. A ghost nobody could see in order to delete it. The group is resolved
first and the create runs inside `transaction.atomic()`.

**Usernames dedupe with `__iexact`.** Django's is case-sensitive, so "Office" and
"office" were two logins — and sign-in matches exactly, so whoever typed the wrong
case just got "invalid credentials".

**Deleting a login and changing a staff password notify the other owner.**
Creating one always did; the two actions that actually revoke or hand over access
were silent.

**`salary_set_amount`'s `next` goes through `auth_views._safe_next`** — it used to
go straight to `redirect()`, an open redirect.

### Password recovery

**A hand-built 6-digit emailed code, not Django's `PasswordResetView` link.** The
link flow is less code and better tested, and it was the original plan. It was
rejected for one reason: **on iOS an installed PWA has its own cookie jar,
separate from the browser.** A link tapped in the mail app opens in Safari and
completes the reset *there*, so the owner returns to the installed app still
signed out. Android is better but not guaranteed either. A 6-digit code has no
such dependency — the reset finishes in the same session that requested it, on
every OS. **The owners read this on iPhones.** If you are about to "simplify" this
into `PasswordResetView`, you are about to break the flow on the exact device it
was built for.
→ `PasswordResetOTP`, `workshop/tests/test_password_reset.py`

**TOTP was considered and rejected.** TOTP is a *second factor*; a reset is a
*recovery channel*, and the two fail in opposite directions — TOTP proves you hold
the device, which is worthless exactly when the device is what was lost. That
matters more here because **owners cannot reset each other**, so email is the only
self-service route an owner has. The fallback that suggestion was really reaching
for is the shell password reset in `GO_LIVE_RUNBOOK.md` §5.3.

**The code is in the email *subject* line on purpose.** iOS and Android both show
the subject in the notification banner, so the owner reads it without opening the
mail app. The trade — briefly visible on a lock screen — is deliberate.

**The throttle is TOLD to the visitor, and that is not a regression of the
non-disclosure rule.** There are two limits, treated differently:
- `PasswordResetOTP.throttle_reason` is keyed to the **account** and stays silent
  — reporting it would answer "does this account exist and can it reset?", which
  is the entire reason step 1 has one generic reply.
- `_own_request_throttle` is keyed to the **browser session** and is reported in
  full, because it describes what this visitor just did and is identical for a real
  account and an invented one. Same two numbers (60s / 3 per hour), so in the
  ordinary one-owner-one-phone case the message shown *is* the rule applied. It is
  not a security control; clearing cookies resets it.
→ `test_the_visible_throttle_is_not_an_existence_oracle` — if that fails, the
message has started leaking account existence.
→ `test_throttled_request_sends_nothing_and_says_so` is the current assertion; an
older test asserted the opposite. **Don't restore it.**

**A reset code that failed to send is deleted, not retired.** `throttle_reason`
counts rows by `created_at` regardless of `used_at`, so a retired-but-present row
still spent the hourly budget: three failed sends exhausted it and flipped the
honest "could not send" error into the generic "code sent" reply, so the app
reported two contradictory things about one outage.

**Step 2 echoes the submitted code back into the field; it must keep doing so.**
Every rejection there except a spent code is about the *password*, and dropping the
six digits sent the owner back to the mail app on a phone for a mistake they had
already fixed. The code is single-use, expiring, and already in their inbox, so
echoing it reveals nothing. The two password fields are deliberately not echoed.

**Change Password (`/change-password/`, Owner-only) has NO link in the UI, and is
not dead code.** It is the **handover path**: an owner gets a temp password
verbally, signs in, replaces it — so go-live does not depend on SMTP being
configured. Owners otherwise sign out and use Forgot Password. Deleting it means
handover requires working email on the day.
→ `test_change_password.py` asserts both the absent link and the live route.

**Owner identity lives in the database, not `.env`.** Adding a third owner or
changing an address needs no code change and no deploy —
`sync_owner_identity`, `set_owner_email`.

### Signed-out pages

The login page and both recovery steps extend `workshop/auth/base_auth.html`. A
page overrides only its accent colour and its copy; the layout, input styling and
submit guard are shared. Light theme, no imagery — the wordmark and a 3px red
hairline carry the brand.

The views pass `AUTH_PAGE` (`hide_chrome=True`), which suppresses the nav bar
**and** the PWA install banner. A signed-out page owns the whole viewport, and
prompting someone to install the app before they have proved they can get into it
is premature.

⚠ **Every auth form must keep `js-auth-form` / `js-auth-submit`.** The guard in
`base_auth.html` blocks a second submit while one is in flight — the staff form
previously had none, so the button could be pressed repeatedly, each press another
POST and each wrong one spending part of the five-attempt lockout budget. The
`dataset.submitting` flag does the work, **not `disabled`**: a button disabled
inside its own submit handler still lets a queued Enter keypress through in some
browsers.

## Notifications — one catalogue, one entry point

The whole event list is **`workshop/notifications.py`**. Add an event to `EVENTS`,
then call `notify()` from the single place it happens — **never**
`Notification.objects.create()` in a view. There are **16 call sites across 8
modules**; that file is the only way to answer "what does this thing notify
about?" without grepping.

`EVENTS` holds **14 events — 11 CRITICAL, 3 INFO**, all Owner-audience.

**Severity is a tier, not decoration: CRITICAL sends a Web Push, INFO only lands
in the feed.** Keep the critical list short — a phone that buzzes for routine
activity stops being read for the things that matter.

⚠ **GETTING IN ALWAYS PUSHES — ALL THREE. This REVERSES what this file said
until 2026-08-29**, on the owner's decision. It read *"an OFFICE or FLOOR
sign-in pushes; an OWNER sign-in does not"*, with `LOGIN` at INFO on the
reasoning that an owner signing in is routine and `notify()` excludes the actor
anyway, so it would only announce the co-owner's ordinary working day.

What overruled it: **an owner account is the highest-privilege thing in this
system, and a sign-in on one with a stolen password reached no phone at all.**
`PASSWORD_RESET` pushes, but only if the intruder went through the reset flow —
somebody who simply has the password raised nothing. `LOGIN`, `STAFF_LOGIN` and
`ACCOUNT_LOCKED` are now all CRITICAL.

**Volume is what makes it safe, and it is the same argument that already
justified `STAFF_LOGIN`:** `SESSION_COOKIE_AGE` is 40 days, so a signed-in phone
STAYS signed in and this fires on a genuinely new session — a new device, a
cleared cookie jar, 40 days elapsed. Roughly one or two a month across two
owners. The actor is still excluded, so what arrives is always *somebody signed
into the other account*, which is the thing worth knowing.

**The two events stay split**, because the tier was never all the split carried:
the titles differ (`Owner signed in` / `Staff signed in`) and a staff alert
leads its **detail** with the ROLE, which is what says whether that account can
see money. They share a glyph on purpose — one act about two kinds of account,
and a second visual difference would read as two unrelated events.

⚠ **THE IP IS DELIBERATELY OFF BOTH SIGN-IN EVENTS AND ON ALL FOUR SECURITY
ONES.** Every device in this workshop leaves through **one** connection — the
laptop, the tablet and both owners' phones, which is the same fact
`IP_FAILURE_LIMIT` was raised for — so on a routine sign-in the address is
near-constant and carries almost no information, while the DEVICE is the thing
that would look wrong. It was also a third of the body's length on the two
events that fire most often. Control Hub → Security, which the alert links to,
still lists both per session. A lockout or a reset attack is the opposite case:
there the address is the evidence, so it stays.

⚠ **`_role_name` returns the BARE role, always — and the suppression it used to
carry was a live defect the moment `detail` landed.** It was `_role_label`,
returning `" (Office)"` and, when the username already equalled the role, `''`.
That existed because the body was one string (`"{username}{role} signed in"`)
which rendered **"Floor (Floor) signed in"** — and both staff logins in this
workshop are named after their role. With the role moved into its own field it
is printed nowhere near the username, so there is no duplication left to avoid,
and the suppression started reporting the only two staff accounts that exist as
**"No role"**. Caught in review before it shipped.
→ `test_an_account_named_after_its_role_still_reports_that_role`

**`Notification.actor_label` suppresses the actor when the body already opens
with their name.** The row prints the actor on its quiet second line, and on the
two sign-in events the actor IS the subject, so the row said "Floor (Floor) ·
Chrome on Windows PC" over "Staff signed in · Floor" — the same name twice, on
the events that fire most often. A general rule on the model rather than a
per-event exception in the template. Everywhere else the actor is precisely the
fact the body does *not* carry: which owner deleted the payment, who created the
login.
→ `workshop/tests/test_staff_login_alert.py`

⚠ **A SECURITY ALERT SAYS WHAT DID *NOT* HAPPEN, AND "CHANGE THE PASSWORD" WAS
THE WRONG INSTRUCTION.** Both reset-code alerts ended *"if this was not you,
change the password."* Two things wrong with it, and the second is worse than
the first.

It **alarms**: the whole content of those two events is that the defences
worked — the throttle held, or the code died unused — and the copy read like a
break-in. And it is **not the remedy**: that attack goes through the RESET flow,
so the password was never exposed and rotating it stops nothing. An owner who
followed the instruction would have spent their evening on a change that
addressed no part of what happened.

The shape now is **what happened → what it did NOT do → the mild advice**:

> `Sahad — reset code guessed wrong 5 times`
> From 103.21.44.9. The code is dead and nothing on the account changed. Worth
> making sure the password is a strong one.

"Nothing on the account changed" is the sentence that does the work. The
password advice that IS worth giving is about **strength, not rotation**, and it
is phrased as a statement rather than an instruction, so it cannot be mistaken
for something that must be done tonight.

⚠ **`PASSWORD_RESET` is deliberately NOT softened** — it is the one of the three
where something actually changed, and it says so plainly. It carries no
instruction either, for a different reason: it reaches the *other* owner (the
actor is excluded), whose useful next move is to look at the signed-in devices,
which is where the link already goes.

**`ACCOUNT_LOCKED` was already right** and only got shorter (135 → 77
characters). It still states that the remedy expires, which is the rule.

**`DeletionLog.record()` is the deletion hook.** Every permanent delete funnels
through it, so one call covers all twelve entity types and any added later. Don't
scatter equivalent `notify()` calls into individual delete views.

**Owners only, and the actor never hears about their own action.** Floor gets
nothing — a notification a mechanic can't act on trains everyone to ignore the
bell. The bell in `base.html` is Owner-gated to match; widen the gate and the
audience together or you get a bell that can never fill.

**Audience is resolved by `is_superuser` OR group membership — the same
either-or `has_group`/`owner_required` use everywhere else, not group
membership alone.** A reseeded or freshly copied database routinely leaves
both owner accounts superuser with an empty `Owner` group until someone
re-runs `sync_owner_identity --yes` (see "Which database am I on?"), and
group-only resolution went dark for that entire window — twice, in practice,
on two different demo deployments — reaching nobody while `notify()` reported
success. `is_superuser` is the bit nothing resets, so checking it here too
means a skipped sync degrades nothing. `sync_owner_identity` is still worth
running — it closes `/admin/` and keeps the mobile number current — it is
just no longer a precondition for the bell to work.

**Abusing the password-reset form tells BOTH owners.** `RESET_CODE_LIMIT` and
`RESET_CODE_ATTEMPTS_SPENT` are CRITICAL and are the only events raised with **no
actor**, so they reach both owners including the one targeted: there is no
signed-in person to exclude, the account holder is who can act, and the other owner
is the corroboration. Three things are load-bearing:
- **Only the HOURLY limit fires** — the 60-second cooldown is a double-tapped
  button. `PasswordResetOTP.throttle_kind()` is the single lookup behind both the
  message and the alert.
- **`recently_raised()` de-dupes to one per account per hour.** The form needs no
  login, so without it anyone knowing an owner's username could buzz both phones
  until the alert stopped being read — *that*, not the reset, is the attack.
- **The visitor's response must not change by a single byte.**
  → `test_raising_an_alert_changes_nothing_the_visitor_can_see` compares the
  rendered pages for a real and an invented username.

**A password reset raises `PASSWORD_RESET` to the *other* owner.** A reset also
terminates every session, so the real owner was signed out everywhere with no
message, which reads as the app misbehaving. `actor=user` excludes whoever
performed it: a genuine owner needs no telling, and an intruder should not receive
the warning about themselves.

**Read rows are swept after `RETENTION_DAYS` (14); unread are kept forever.** This
table is a feed, not an archive — the permanent record lives in `DeletionLog`, the
audit pages and the ledgers.

**Fanned out per recipient**, so the unread count is one indexed query. **No FK to
the subject** — most events announce a *deletion*, and a FK would cascade the
notification away with the thing it was about; `object_type`/`object_id` plus a
frozen label in `body` is the same discipline as `DeletionLog.snapshot`.

**`notify()` swallows its own errors** so a malformed body can't fail a payment.
That promise stops at database errors inside an atomic block: the surrounding
transaction is already doomed and shouldn't be rescued.

**A NOTIFICATION IS THREE STRINGS, AND EACH ANSWERS A DIFFERENT QUESTION.**

| | example | where it lives |
|---|---|---|
| **`body`** | `Biljo · ₹1,00,000 payment deleted` | the loud line |
| **`title`** | `Record deleted` | the category, from `EVENTS` |
| **`detail`** | `Spare-Shop Payment` | the context, beside the category |

⚠ **`body` IS A COMPLETE STATEMENT ENDING IN WHAT HAPPENED.** Subject first,
verb last, understandable with nothing under it read at all. That is the test
to apply to a new one: *if the reader saw only this line, would they know what
occurred?* `Biljo · ₹1,00,000 payment deleted` passes; `Spare-Shop Payment ·
₹1,00,000 → Biljo` does not — it names a thing and leaves the reader to infer
the event from a glyph.

⚠ **`detail` EXISTS SO THE STATEMENT CAN STAY SHORT.** The device a sign-in
came from, the kind of record deleted, the remedy for a lockout, the percentage
behind a discount — all real, none of it worth the loud line. Before the column
(`0074`) every one of those was crammed into `body`, which is what made
headlines wrap to three lines on a phone. **Nothing that decides what the row
MEANS may live here** — it is read second, or not at all.

**The loud line carries what DIFFERS between rows.** It used to carry the
`title`, which is identical on every row of its kind — nine consecutive "Record
permanently deleted" headlines with the actual fact underneath in smaller,
greyer type. The eye landed on the least useful line on the row.

Both surfaces use the same order, so the two cannot teach different habits:

* a **feed row** draws the **glyph** where the title would go, `body` loud,
  then `title · detail · actor` quiet underneath;
* a **push** puts `body` in its bold line and `title · detail` under it. It
  used to send the CATEGORY as the bold line, which is what a lock screen shows
  first — so nine alerts in a row opened with "Record deleted" in bold and the
  ₹1,00,000 in the small type below.

⚠ **`RECORD_DELETED`'s statement is built from `entity_label`, which is why
FOUR labels lead with the subject** (`{name} · ₹{amount} payment`, not
`₹{amount} → {name}`). The arrow form produced "₹1,00,000 → Biljo deleted",
an arrow pointing at a verb. Deletion History prints the same string and reads
better for it — that page is scanned by *who the money went to*, and every one
of those rows used to open with a rupee sign.

**A glyph per event, declared in `EVENTS` beside its severity.** Shape is
IDENTITY, colour is SEVERITY — one colour system, so red can only ever mean
"this one matters", and which *kind* of thing happened is carried entirely by a
glyph that needs no colour to be told apart. A read row drains its colour and
keeps its shape. `glyph_for()` answers an unknown key with a neutral default,
which is load-bearing: a row is kept for a fortnight and `event` is plain text,
so the feed must draw a key this file no longer knows rather than 500.

**Money anywhere on the row goes through `:,.0f`, like everywhere else in the
app.**
`HIGH_DISCOUNT` printed the bare Decimal — "₹5500.00 off ₹20500.00", the only
two figures in the whole feed without separators and with paise nobody asked
for.

**`DeletionLog.record` builds the one body assembled from parts, and it must
say each fact ONCE.** It printed the record type twice (the label usually opens
with it) and the amount twice in two spellings, because **7 of the 18
`record()` call sites already put the amount in their own label**. Both guards
read what the LABEL carries rather than a list of which call sites do what, so
a nineteenth cannot reintroduce either.
→ `TheDeletedRecordBodySaysEachFactOnceTests`

**The bell opens a floating panel, fetched lazily** from `/notifications/panel/`.
The bell is on every owner page, so baking ten rows plus their actors into every
response would cost a join on pages that have nothing to do with notifications;
only the unread *count* rides in the context processor. The panel caps at
`PANEL_SIZE`; the badge caps at `99+`.

⚠ **IT IS WARMED ON APPROACH, NOT ON LOAD, AND REOPENING FETCHES NOTHING.**
`openPanel()` used to call `loadPanel(true)` — a forced refetch on **every**
open, so the panel showed "Loading…" every single time including the second
time in ten seconds. Two changes, and the split between them is the point:
prefetching on page load would put a request and a query on every page an owner
opens, which is the exact cost this panel is lazy in order to avoid, so the
warm-up hangs off `pointerenter`/`touchstart` on the bell — free until the
control is about to be used, and worth the ~100–300ms between a hand arriving
and a click resolving. What is already rendered is then reused unless the badge
has moved or it has gone stale (`STALE_MS`). Measured: **0 requests on load, 1
on approach, rows already in the DOM at the instant of the click, 0 on
reopen.** The clock is deliberately **not** stamped on a failed fetch, or an
error would be cached for 45 seconds.

**Row markup lives in one partial** (`notifications/_row.html`), shared by the
panel and the full feed, so "read" cannot come to look like two different things.
Read state is carried by four signals — the glyph tile's fill, the row
background, the headline's weight, and the trailing tick — not a dot alone,
which is easy to miss on a phone.

⚠ **The left accent rail and the "IMPORTANT" pill are GONE, and that is the
same rule the settle dialog follows.** With a coloured tile 10px away, the rail
was a fifth telling of one bit and the pill a sixth; confirming what cannot
surprise anyone is how a signal stops being read. **A read row's headline stops
at `#475569`, not `--color-text-muted`** — the muted tone was fine while that
line held a category nobody re-reads, and it now holds the fact itself.

**The state marker rides the headline's own line, and the age sits beside it.**
As its own column the marker cost 25px of a 341px row — 7% of the width,
permanently, off the one line anybody reads — to hold an 8px dot. The age moved
onto that line for the same reason it replaced the absolute stamp: it is one or
two characters and there is always room.

**The age is `short_ago`, the feed's own wording** — `now` / `12m` / `5h` /
`3d` / `17 Aug`. `28 Aug, 11:59 p.m.` answered a question nobody asks of a
notification; what an owner wants is *this morning or last week*. ⚠ **Nothing
on the scale exceeds six characters, and "Yesterday" was tried and reverted** —
it shares a flex line with the headline, so those nine characters came straight
off the line being read, enough to wrap a body that otherwise fitted. Same
compact vocabulary as the Live Report's `_age_label()`.

**Measured across the whole catalogue, one row per event: 107px per row →
81px average on a 375px phone and 64px on a laptop**, three cramped lines
becoming two comfortable ones (headline 0.9rem where title/body/meta had been
0.85/0.8/0.71rem — three sizes within 2px of each other, which is no hierarchy
at all). The rows that still run long are the two that should: a lockout and a
spent reset code, both of which carry a remedy.

### A notification's URL is permanent

**The fix for a bad one is to make that URL work — not to repoint the next
alert.** A `Notification` stores its `url` in a column and keeps it forever, so
repointing the event changes nothing for every alert already sent.

`SALARY_ADVANCE` once pointed at an AJAX fragment that extends no base template;
repointing it left every earlier alert arriving at an unstyled wall of rows with
no nav and no way back. The view now serves a **full page** on navigation and the
bare fragment only when `X-Requested-With: XMLHttpRequest` is present. **The
fragment is the opt-in branch**, deliberately: lose the header and the modal shows
a whole page inside itself, which is untidy; the other way round puts a naked
fragment in front of an owner.
→ `TheStaffAdvancePageOpensAsAPageTests`

**General rule: before changing a notification's `url`, ask what happens to the
ones already sent.**

**Check the destination actually *contains* the subject.** `ACCOUNT_LOCKED` once
pointed every lockout at Control Hub → Accounts, which lists Office and Floor only,
while `manage_unlock_account` refuses owner accounts by design — so a locked
*owner* opened a page that did not contain the account and offered nothing to
press. It is now routed by role. `ACCOUNT_ARCHIVED` for a Supplies Shop pointed at
`supplier_shop_list`, which filters `is_active=True` — the one page guaranteed
*not* to contain the shop the notification is about. Its spare-shop and fleet twins
already pointed at their archived lists.
→ Follow the URL and assert the subject's name is on the page it reaches;
comparing against a `reverse()` proves nothing about whether the destination shows
the thing. **Both of those bugs were found by reading, and nothing enforced the
rule** — `EveryNotificationLandsOnItsSubjectTests` now does, by fetching each
destination as an owner and looking at the rendered page.

⚠ **Match CASE-INSENSITIVELY.** The Security section renders an owner row as
`{{ s.user.username|upper }}`, so a case-sensitive check reports a false miss on
`LOGIN`, `PASSWORD_RESET` and both reset-code events — the four this exists to
protect. Cost twenty minutes chasing four phantom failures.

⚠ **`USER_DELETED` is the one destination that CANNOT hold its subject**, and
that is not a defect to fix: the login is gone by the time anyone taps, and a
deleted login writes no `DeletionLog` row to point at. Control Hub → Accounts,
which shows who can still sign in, is the most useful page available; the name
rides in the notification's own headline instead.

**If the remedy an event describes EXPIRES, the body has to say so.** A lockout
lasts 15 minutes and a notification is permanent, so an owner reading "Unlock it
from Control Hub → Accounts" an hour later found an ordinary account list and
reasonably concluded the alert was lying. The button is right to disappear; the
body was wrong to describe a permanent remedy.

**That salary-advance page answers THREE questions and then stops** — who is this,
how much just now, how much this month. The first build added a staff-role line, a
month-grouped history list and a row-cap notice, all correct and all in the way;
the history already lives in the ⋮ modal one tap away. Four rules: the figures are
**stacked at every width** (the owners' phones straddle any sensible breakpoint,
and side-by-side reads as a *comparison* when the questions are a sequence); the
notification carries **`?advance=<pk>`** so the exact advance is named, and without
it the newest stands in with the label changing to "Latest advance"; the month
total follows **the advance's own month**, not today's; and the total is
aggregated **in the database**, never summed from what is rendered.

## Web Push — a delivery layer, never a source of truth

`workshop/push.py` sends; `workshop/views/push.py` is the HTTP surface;
`PushSubscription` is one row per **device**, not per user.

⚠ **`sw.js` is served from the origin root by a Django view, not `/static/`.** A
service worker can only control pages at or below its own path, so WhiteNoise
serving it at `/static/sw.js` would silently limit its scope to `/static/` and it
would never receive a push for the app. The view also sends
`Service-Worker-Allowed: /` and `Cache-Control: no-store` (a cached worker means a
fix ships and nobody gets it).

**Nothing waits on the network.** `queue_push()` hands off to a background thread
via `transaction.on_commit` — so a rolled-back action never announces itself, and
saving a payment doesn't pay for two ~200 ms HTTPS calls. The thread opens and
closes its own DB connection.

**Push failing must never affect the feed.** Missing VAPID keys, a dead push
service, zero subscribers — all no-ops. `notify()` guards the push call separately
from the row write so a push problem can't even change its *return value*.

**404/410 from the push service means that endpoint is permanently gone** — the
row is deleted, not retried. Other errors are counted and dropped after
`MAX_FAILURES`.

⚠ **THE PANEL SAYS SO WHEN THIS DEVICE IS NOT SUBSCRIBED.** Turning push on
was reachable only through the 34px struck-through bell in the panel header —
an unlabelled icon inside a panel, which is not a control anybody finds by
accident. Measured in the development database: **one owner has two subscribed
devices and the other has none**, so half the owners had never received a
single CRITICAL alert and no screen said so.

`#notifPushCta` is one line, amber, and rendered **only in the state that needs
acting on** — it disappears the moment alerts are on, the sticky save button's
own rule, so it can never become furniture. It is not the "Alerts on this
device" card that was removed for spending three lines of prose on a binary,
and it is the same action as the toggle rather than a second one.

It also carries the **reason** when push is unavailable, which previously had
nowhere to be shown at all: on iOS, Push exists only for an installed app, so
an owner in plain Safari met a dead struck-through bell whose explanation lived
in a `title` attribute that a phone cannot display. In that state the strip is
`disabled` — a statement, not an offer.

⚠ **A PUSH CARRIES NO `tag`, AND THE CONSTANT ONE IT USED TO CARRY WAS
DELETING ALERTS OFF THE LOCK SCREEN.** `sw.js` passed `tag: 'workshopos'` with
a comment claiming it "collapses repeats of the same event". A tag does not
work that way — it is a **replace key**, and one constant value meant **every
push replaced the one before it**. Two deletions a minute apart showed as one;
a staff sign-in landing after a ₹1,00,000 record deletion wiped that deletion
off the lock screen before anybody read it. The feed row survived either way,
which is exactly why it could go unnoticed.

Only CRITICAL events reach the push path — money moved unexpectedly, something
destroyed, someone got in — and **none of them supersedes any other**, so there
is nothing here a later alert is entitled to replace. Untagged notifications
stack, which at a handful a day is the right trade: seeing two is recoverable,
missing one is not. `renotify` went with it; it is only meaningful alongside a
tag.

**iOS only delivers push to an app added to the Home Screen.** In a plain Safari
tab `PushManager` is simply absent. `static/js/notifications.js` detects this and
says "Add this app to your Home Screen first" — without that the button just looks
broken on the exact device the owners use.

**The service worker is registered on EVERY page load, and `sw.js` has a `fetch`
handler that caches nothing.** Registration used to live only inside `enablePush()`
in `notifications.js`, which runs when an *owner* taps "turn alerts on" — so on an
ordinary page load there was no worker at all, and Office and Floor had no bell and
therefore no route to ever register one. Chrome fires `beforeinstallprompt` only
for a page with a registered worker **that has a fetch handler**, so the install
banner could appear on iOS only. Registration lives in `script.js` because it runs
on more than one page, and `register()` is idempotent.

⚠ **The fetch handler caches nothing and must not start.** All it does is pass
requests through and answer a *navigation* that fails with a plain inline "no
connection" page, so bad workshop wifi reads as an explanation rather than a broken
app.
→ `ServiceWorkerRouteTests`, `TheAppRegistersItsWorkerOnEveryPageTests`

⚠ **Registration, install state and push subscriptions are all per-origin**, so
every device has to re-enable push after a change of host or domain.

**Push is optional in every environment.** A deploy with no VAPID keys is valid
and degrades quietly.

## Outbound network calls

**The app makes exactly TWO kinds, both optional and neither on the request
path:** the password-reset email, and Web Push. There is no SMS or chat
integration and none is to be added. Push is a delivery layer over the existing
`Notification` rows, not a parallel system.

**Mail leaves over Resend's HTTPS API in production, not SMTP.** Railway blocks
outbound SMTP on every plan below Pro (ports 25/465/587/2525). Since Django routes
every `send_mail()` through `EMAIL_BACKEND` and this app has exactly **one** call
site (`auth_views.py`), swapping that setting moves the mail onto HTTPS with no
change to the flow, the throttles or the tests. Written against stdlib
`urllib.request` rather than `requests` or the `resend` SDK — re-adding a
dependency to send single-digit emails per year is a poor trade. The SMTP block in
`base.py` stays, because development and any host that permits SMTP still use it.

⚠ **Verify the sending domain on a SUBDOMAIN** (`mail.formuladservice.in`) —
SPF/DKIM at the root can disturb mail for the business domain itself, which
carries the public WordPress site.

## Middleware & search engines

**A signed-in page is `no-store`, so Back cannot un-log-out.** Logging out flushes
the session, so the next *request* is bounced — but Back never makes a request. It
restores the page from the browser's back/forward cache, fully rendered: the
dashboard, a customer's bill, the Profit page, on a laptop now in somebody else's
hands. Nothing server-side can undo that after the page has been sent.
`NoStoreMiddleware` is scoped to authenticated responses (it reads `request.user`,
so it must stay after `AuthenticationMiddleware`); static assets never reach it
because WhiteNoise returns them earlier. **Accepted cost:** Back re-fetches instead
of restoring instantly.

**The app tells search engines to stay out in two ways, and they cover different
crawlers.** `robots.txt` (a `TemplateView` in `urls.py`) carries `Disallow: /`, and
`NoIndexMiddleware` sets `X-Robots-Tag: noindex, nofollow` on every response. **Not
redundancy**: a crawler that obeys `Disallow` never fetches the page and so never
sees the header, so `Disallow` stops well-behaved bots while the header is what
de-indexes a URL that got in anyway. The middleware is deliberately not a `<meta>`
tag — the printed invoice, the printed estimate and the four signed-out auth pages
are standalone templates that do not extend `base.html`, and a fifth would be added
one day with nothing failing. **Neither is a security control**; every page worth
protecting is behind a login.

**`SessionTrackingMiddleware`** updates `UserSession` (device / IP / last-activity)
on every authenticated request, throttled to a 5-minute cooldown per session.
Owners can remotely terminate any active session from the management dashboard.

**`GZipMiddleware` is on, and it sits BELOW WhiteNoise.** The two facts above
combine into a bill: pages are large (the job card form renders 211 KB, most of it
the inline CSS and JS the frontend deliberately keeps in the template) and
`no-store` means every navigation re-sends all of it. Railway's proxy does not
compress. It gzips to 55 KB — 26% — with the cashbook at 22% and the dashboard at
24%. Below WhiteNoise on purpose: WhiteNoise short-circuits static requests, so
from there they never reach this middleware, which is right because it already
serves its own pre-compressed `.gz`/`.br`.

**On BREACH — the preconditions DO exist here, and two Django defences cover
them.** `?q=` is reflected on Completed, Paid Bills, Car Profiles and Estimates,
all of which also carry a CSRF token, which is the classic setup. Both defences
were verified rather than assumed: the CSRF token is **re-masked on every render**
(three renders, three different tokens), so there is no stable secret for a
compression-length oracle to walk a byte at a time; and
`GZipMiddleware.max_random_bytes = 100` pads every response with a random-length
gzip filename field, added by Django for exactly this reason. No other secret
lives in a response body — the session id is an HttpOnly cookie. **Revisit if a
page ever renders a long-lived token into its HTML.**
→ `ThePagesAreCompressedOnTheWayOutTests` asserts the BEHAVIOUR, not the
MIDDLEWARE list: a settings test would pass while the middleware sat in a position
where it never saw a response.

## Deletion model — two verbs

**Accounts that other records point to** — Spare Shops, Fleet Accounts, Supplier
Shops, Mechanics — are **deactivated (archived), never hard-deleted** (that would
CASCADE-destroy their financial ledgers). The flag name differs by model
(`is_trashed` on SpareShop/BulkPayer, `is_active` on SupplierShop/Mechanic) —
internal only. They drop out of active lists and dropdowns and reactivate from a
per-module **Archived** list.

**ARCHIVING IS REFUSED WHILE THE ACCOUNT STILL OWES OR IS OWED** — all three of
`bulk_payer_delete`, `spare_shop_delete` and `deactivate_supplier_shop`. One rule:
**money owed is always reachable from exactly one screen**, and archiving used to
hide the account from every list *and* drop its balance out of the Profit page at
the same time. Blocking rather than opening a back door is the deliberate call
(see the Fleet Accounts section). A balance in **credit** does not block — it is
not a debt, and refusing would trap an overpaid account with no purchases left to
come.

**Transactions & records** — Job Cards, Fleet/Shop/Supplier payments, Restock
bills, Cashbook entries — are **permanently deleted**, but every delete first
writes a snapshot via `DeletionLog.record(...)` to the Owner-only, read-only
**Deletion History** (`/deletion-history/`). There is deliberately **no restore** —
reviving stale financial data corrupts running balances.

**EVERY LOGGED DELETE POSTS A REASON, AND THE REASON IS OPTIONAL.** All 13
`DeletionLog.record()` call sites read `request.POST.get('reason', '')` and the
column has always stored it — but four dialogs never rendered the input, so a
Fleet payment reversal, a spare-shop payment reversal, a Supplies Shop payment,
a restock bill and a salary advance all reached the Owner's Deletion History
blank on the one field that says *why the money moved back*. Closed 2026-08-28;
no view changed and no migration was needed.

⚠ **It is deliberately NOT mandatory, on any of them.** The compensating control
is already stronger than a required box: `DeletionLog.record()` stores who, when,
what, how much and a full `snapshot`, and raises **`RECORD_DELETED` (CRITICAL)**,
which pushes to both owners' phones within seconds and links straight to the
record. In a seven-person workshop with two owners who deal with customers
personally, *ask them* beats a text box that a required field turns into "a" or
"." — and a required field people defeat is worse than an optional one, because
the log then contains noise that looks like signal. It is the settle dialog's own
rule ("it never blocks", "confirming what cannot surprise anyone is how
confirmations stop being read") applied one screen over. What was genuinely
missing was PREVENTION rather than a better audit field, and that is the Office
delete window below — a boundary nobody can type around.

**OFFICE CORRECTS A RECENT MISTAKE; AN OWNER TAKES ANYTHING OLDER.**
`workshop/delete_window.py`, `OFFICE_DELETE_WINDOW_DAYS = 7`. Six money
deletes are `@office_required` — fleet payment, spare-shop payment, Supplies
Shop payment, restock bill, cashbook entry, salary advance — so Office could
remove a six-month-old fleet payment exactly as easily as one keyed this
morning. Those are two different acts:

  * recorded an hour ago → a **correction**: frequent, cheap, and the money is
    still fresh in everybody's head;
  * recorded six weeks ago → **anomalous**: that period has been reported on,
    an owner has read the Profit page against it, and a shop's balance was
    settled on it.

An **escalation, never a wall** — no approval queue, no second sign-off, no new
mechanism. The owners already exist, already hold the role, and are already the
people `RECORD_DELETED` alerts within seconds.

⚠ **MEASURED ON `created_at`, NEVER ON THE MONEY DATE — this is the half that
would silently break the workflow it protects.** Every covered model carries
both columns and they answer different questions. Back-dating is *normal* here:
a Supplies Shop delivers, keeps its own book, and the bill is keyed only when
the collector comes at month end, which is why the Cashbook, both shop payment
forms and the fleet payment form each have a date box. On the money date,
Office would key a bill back-dated six weeks, mistype it, and be refused
permission to delete their own typo thirty seconds later. `created_at` asks the
right question — how long has this been sitting in the books — and it is why
those columns were kept when the money dates landed.

⚠ **THE CONTROL IS STILL OFFERED, AND THE REFUSAL NAMES THE ROUTE.** Hiding the
button would say "you cannot" without saying why — the rule the frozen-advance
⋮ menu already follows — and would additionally say something false, that the
record cannot be deleted at all, when an owner can. So the POST is refused and
the message carries the row, its age, the rule and who to ask: *"This ₹100,000
payment was recorded 40 days ago. Office can delete something recorded in the
last 7 days — ask an owner to remove this one."* The window is read from the
one constant, so the number on screen can never disagree with the number
enforced.

Three things deliberately **not** covered:
- **`jobcard_delete`** — already refuses a card carrying spares, labour or a
  received payment, so a deletable card holds no money. A window there is
  friction buying nothing.
- **`salary_payment_delete`** — `@owner_required` already.
- **Housekeeping** (master data, unassigned spares) — no money moves, and
  auto-learn restores a master-list name the next time somebody types it.

Two consequences accepted knowingly. On the **salary advance** the window sits
*after* the settled-month branch, because that one refuses everybody including
an owner and names the settlement in the way — the stronger rule and the better
message wherever they overlap. And a **fleet reversal must go newest-first**, so
if any payment in that chain is past the window the owner does the whole chain
rather than Office starting it; that escalates more often here than anywhere
else, which is the right way round for the largest receipts the workshop takes.

**7 is a dial, not a law** — one constant, and the messages follow it.
→ `workshop/tests/test_delete_window.py`

**Job-card delete guard:** a card carrying spares, labour, or a received payment
**cannot** be deleted. A deletable card holds no spares, so no stock is affected.

**Financial-transaction deletes reverse their effect** (restore job-card balances /
warehouse stock) inside the same atomic block, then log + hard-delete.

**`is_deleted` (JobCard) is a dormant column** — still filtered on for
compatibility, no longer written.

**There is deliberately no delete for staff, only deactivate.** Changing someone's
`role` is an in-place field update on the same row, never a delete-and-recreate —
that is what keeps `lead_mechanic` on old job cards intact.

## Roles & visibility

Three Django auth Groups: **Owner**, **Office**, **Floor**. `decorators.py`
defines `owner_required`, `office_required`, `staff_required`. Superusers pass
every check. Use these on any new view instead of rolling custom permission checks.

**WHO THE CUSTOMER IS is Office and Owner only; the INTERNAL NOTE is open to
everybody.** The workshop identifies a car by its registration because Owner 1
deals with customers personally, so a mechanic never needs to know whose car it is.
The note stays open because it is about the CAR ("noise only when cold", "do not
wash") and the mechanic is usually who finds out. The section is **named
differently for each** — "Customer & Notes" for Office and Owner, "Workshop Note"
for Floor — because a heading reading "Customer Details" over a box that says
nothing about the customer is the page misdescribing itself.

Three things are load-bearing:
- **The two fields are simply NOT RENDERED for Floor**, which is safe on a
  ModelForm (an absent field leaves the stored value alone) and **would not be in a
  formset** (an absent formset field saves as blank and wipes the row).
- **A crafted POST is answered separately.** Hiding a box is presentation;
  `_floor_locked_data` pinning the stored value is the control. Both directions
  matter — a payload can invent a customer *or* erase one, and only pinning
  (rather than dropping the key) stops the second.
- **`_price_locked_data` was renamed `_floor_locked_data`.** The rule it enforces
  was never about money: *a field Floor cannot see on any screen must be a field
  Floor cannot post from any screen.* `OFFICE_ONLY_CARD_FIELDS` names the two.
→ `WhoTheCustomerIsIsOfficeOnlyTests`

**Floor may not set prices, and that is enforced on the SERVER.** The template
hides prices from Floor but still renders the inputs inside a `d-none` cell — it
has to, or a mechanic saving the card would blank what Office entered. That left
the rule as UI-only: a Floor login POSTing `total_price=1` turned a ₹5,000 bill
into ₹1. `_floor_locked_data()` rewrites every posted `unit_price` /
`total_price` / `customer_rate` with the value already stored (blank for a new row)
before the formsets are bound.

⚠ **Do not "simplify" it by deleting the keys instead** — an absent formset field
saves as empty and wipes the price, the exact failure the rendered-but-hidden
inputs exist to prevent.

**PAID BILLS is Office-visible with a 7-day window; the HIGH DISCOUNT AUDIT is
not.** Office settles bills, so it needs to look one up. The window is enforced in
`paid_bills_list`, **not** by hiding the filter dropdown — `?filter=all` is one URL
edit away. Office sees per-card amounts in full. **There is no grand total on that page
any more, for either role** — see "Cash Tracking" above for why it went and
what replaced it — so this is now purely a window rule. `audit_high_discounts`
stays **`@owner_required`** — it reads as what the workshop settled for against
what it billed, the compensating control for the shortfall-as-discount rule. Its
entry in the ⋮ menu is gated to match, because a door Office can see but not open
is worse than no door.
→ `workshop/tests/test_paid_bills_rbac.py`

**FLOOR may put a card on hold and mark it completed. It may not UNDO a
completion.** Both buttons were rendered for Floor while the views were
`@office_required`, so pressing either gave a mechanic a 403 on the one screen they
use all day. Neither moves money, and a hold is reversed by the same button.
`undo_completed` is deliberately **not** widened — it can put a second active card
on the floor for one registration and has to answer that rule when it does.
→ `workshop/tests/test_floor_board.py`

**The Live Report is Office and Owner only, whole page.** Everything on it is
supplier names, ordering state and money-side gaps, none of which Floor is shown
anywhere else. The nav pill was always gated `is_owner or is_office`, so the
template gate and the decorator now agree.

**The read-only job card (`/jobcards/<pk>/`) is Office and Owner only.** The reason
is the LAYOUT rather than the secrecy: line 2 runs mileage, mechanic, customer and
phone number together with no captions, and every part sets the workshop's COST
beside the customer's price. Removing two of four values from an unlabelled line
does not produce a safe page, it produces a confusing one. **Floor loses nothing**
— the dashboard car card's live-details drawer is these same four lists.

**Inventory RBAC:** Floor sees only the main list, **Low Stock** (read-only) and
**Stock History**. Everything else — Manage/Category, Add Product, restock,
catalog, payments — is `@office_required`. "Manage Database" is a **read-only
Category browser**.

**Unassigned Spares is Floor's only door into the Spare Shops section** (add-only,
no prices). `/spare-shops/` is already in `DRAWER_SECTION_PREFIXES`, so that link
lights the Manage button with no change there.
---

# UI conventions

## Devices

Every screen is used on **three** form factors, one per role:

| Device | Role | Consequence |
|---|---|---|
| **laptop** | Office | the reading/settling surface |
| **tablet** | Floor | ~44px touch targets, no hover |
| **mobile** | Owners | Analysis and Deletion History are read here |

Design responsively — a desktop-only table, or a layout that overflows
horizontally on a phone, is a defect, not a cosmetic issue.

**A PAGE TITLE NAMES THE PAGE AND NEVER THE PRODUCT.** In the installed app
the window title is `manifest.name` + `" - "` + the document `<title>`, so a
page that appends the brand itself gets it twice — the Profit page read
**"Formula D — Diagnose & Service - Profit — August 2026 — WorkshopOS"**, with
the internal codename in the loudest chrome the owners ever see, on the page
they read most. Six pages did it (both Analysis pages, Spare Shops, three
Salary ones) and the other ~60 did not.

Two halves, and each is the other's reason:
- **`manifest.json`'s `name` is `Formula D`**, not the tagline. It is the
  prefix on *every* page, so a descriptor there is paid for once per screen
  forever. The tagline still lives in the manifest's `description`, and
  `short_name` was already `Formula D`.
  ⚠ **An installed app CACHES its manifest** — the launcher and window keep
  the old name until it is removed and re-added. A name change looking like
  it did not apply is the cache, not the edit.
- **No `{% block title %}` appends the brand.** `test_password_reset` already
  asserted that a reset email must not say "WorkshopOS", with a docstring
  claiming the word "appears nowhere in the UI" — which those six titles had
  quietly made false. It is true now.

One dead template still carries it: `inventory/home.html` (`Inventory |
WorkshopOS`), reachable from no view. Left alone rather than swept, so that
deleting it stays a separate decision.

**`base.html` defines the light-mode CSS variables (`--color-*`) and renders
Django messages ONCE for all pages.** Never re-render `{% if messages %}` in a
child template — it double-prints and loses the error/success styling. (The
standalone print templates are the exception: they do not extend `base.html`, so
they must render it themselves.)

**`.main-content` is capped at 800px**, so the form is the same width on every
device — a table wider than that hides the same number of pixels on a 1280px
laptop as on an 820px tablet.

⚠ **`{% with %}` scope ends at `{% endwith %}`, and a dead variable evaluates
FALSE rather than raising.** Anything owner-gated that lives *after* `{% endwith %}`
in `base.html` must use `request.user|has_group:"Owner"`, not the `is_owner`
variable — a stale `{% if is_owner %}` there silently evaluated false, which is how
the notification panel's JavaScript went missing once.

⚠ **Django's `{# … #}` comment is single-line only.** Spread one across two lines
and it stops being a comment — the text renders on the page. Ten of these once
shipped and put paragraphs of developer commentary inside the nav bar and the login
forms, with every functional test still green, because tests assert on specific
strings and nothing was reading what the page actually *said*. Use
`{% comment %} … {% endcomment %}` for anything spanning lines.
→ `workshop/tests/test_template_comments.py` scans every template statically.

## Navigation — one bar, one drawer

**A 3px bar at the top reports that something is on its way, because the installed
app has no chrome to borrow.** `manifest.json` declares `"display": "standalone"`,
which removes the address bar and the tab spinner — so in the installed app a tap
was answered with *nothing at all* until the new page painted, and every page here
is a full server-rendered navigation over a `no-store` response, which is a real
round trip. `navProgress` in `script.js`, `.nav-progress` in `base.html`.

Five things are load-bearing:

- **A navigation paints AT ONCE; an in-page update has to EARN it.** Half the list
  screens never navigate — their filters are `href="#"`, fetching a partial and
  calling `pushState`, so the URL changes while the document never unloads. Those
  call `navProgress.begin()`, which paints only if the work outlasts
  **`THRESHOLD_MS = 250`**. Measured 22–37ms against the real database, so on the
  shop laptop nothing appears at all; on an owner's phone, where the same fetch is
  a real round trip, it does. **That threshold is the whole reason this is not
  noise.**
- **It never reaches the end.** Nothing here knows how far along a request is, so
  it eases towards 90% and waits. A bar that completes and then sits there has lied.
- **It only ever STARTS on a navigation.** The page it reports on replaces the
  document, so the bar leaves with the page that created it — there is no
  completion path to get wrong. A 15s safety timer covers a navigation that never
  happens.
- **`transform` only**, so it cannot reflow the page it is describing;
  `prefers-reduced-motion` keeps the bar and drops the creep, because the
  information is the point.
- **It must not fire on things that do not navigate.** Verified: `data-bs-toggle`
  (the drawer and every ⋮ menu), `#` anchors, `target`, `download`, cross-origin,
  the same URL, and **a `confirm()` the person cancelled** — that last one matters,
  since eleven templates ask through `confirm()`, mostly as an
  `onsubmit="return confirm(…)"` attribute. It is delegated on
  `document` in the BUBBLE phase, so the guards that refuse a submit in CAPTURE
  (the Financial Lock, the inventory quantity check) never reach it.

⚠ Three templates confirm through a Bootstrap modal that then calls
`formToSubmit.submit()`. **Programmatic `.submit()` fires no submit event**, so
those show no bar. Known and left alone.

There is exactly **one** nav: a fixed bar in `base.html` plus a Bootstrap
off-canvas drawer (`#appDrawer`) behind the Manage/Menu button. There used to be a
second, divergent mobile bottom nav; it was deleted because the two menus listed
different things. **Don't add a second nav** — a new destination goes in the
drawer, in the section it belongs to.

**The top bar carries a different set per role:**
- **Owner / Office** — Admin · Completed · **Live** · Alerts · Manage. The bell is
  Owner-only.
- **Floor** — Floor · New · Inventory · Menu.

*"Live" is `live_report`.* It was called "Report" and that was wrong twice over:
the page is the state of the workshop *right now* and carries no money at all,
while the drawer's "Analysis & Reports" is the profit page and genuinely is a
report — two entries a thumb's width apart, both saying "report", meaning opposite
things.

⚠ **There is no `+ New` on the Owner/Office bar, on purpose** — Floor creates most
job cards, and Owner/Office reach the form from the `+ New` button in the home
page's own header. So **the only `{% url 'jobcard_create' %}` in `base.html` is the
Floor tab**: if that button ever leaves the dashboard header, Owner and Office lose
every navigation route to a new card.

**On phones (≤640px) that same bar renders at the BOTTOM.** It is the one element,
repositioned in a media query — not a second nav. The top edge is the hardest place
on a phone for a thumb. Five things move with it and each is wired to `--nav-h` so
they cannot drift apart: `.main-content`'s offset (top margin → `body`'s
`padding-bottom`), the notification panel (opens **upward**), the PWA install
banner (sits on top of the bar, z-index below it), `--sticky-top` (0 on a phone,
`--nav-h` elsewhere), and the safe-area inset for the iPhone home indicator.

**`--nav-h` is the single source of truth for bar height; `--sticky-top` for where
a sticky page header rests.** Change the variables, not the individual margins — a
hard-coded `top: 60px` on two job-card headers is exactly how they ended up with an
empty strip above them when the bar moved.

⚠ **The bar must carry Bootstrap's `fixed-top` class even in the phone layout,
where it paints at the bottom.** Load-bearing, not cosmetic: Bootstrap's scrollbar
helper only pads elements matching `.fixed-top` when the drawer locks body scroll,
and without it the bar jumps sideways by the scrollbar width on open. Swapping in
`.fixed-bottom` is **not** the fix — Bootstrap's `bottom: 0` would combine with our
own `top: 0` and stretch the bar down the whole viewport. For the same reason
`body` uses `overflow-y: scroll` **without** `scrollbar-gutter: stable`; the two
together double-count the scrollbar.

**Phone tabs are equal-width columns, and separation comes from the container's
`gap` — never from padding on the tabs.** `flex-basis: 0` sizes the *content* box
and padding is added on top of the equal share, so one padded tab beside the bell's
unpadded wrapper came out 4px wider than its neighbours. `max-width: 96px` stops a
landscape phone rendering 150px slabs. The bell gains a label ("Alerts") on the tab
bar only — an unlabelled tab among labelled ones sits its glyph ~7px lower, which
reads as a misalignment.

**Every pill that can become icon-only carries an `aria-label`.** Keep that pairing.

**Drawer items are role-filtered in the template to match each view's decorator.**
If you change a view's RBAC decorator, update its drawer entry in the same edit.

**The Manage pill's highlight is a LIST in Python, not a chain of `{% if %}`.**
`DRAWER_SECTION_PREFIXES` in `templatetags/custom_filters.py`, with an
`is_drawer_section` filter. It used to be ten inline `p|slice` comparisons and had
quietly fallen two sections behind — a missing entry in a ten-clause boolean is
invisible.
→ `test_every_drawer_destination_lights_the_manage_button` scrapes the drawer's own
links and asserts every one is covered, so the next section added fails loudly.

**ABOUT is the LAST drawer entry, Owner-only, and it CARRIES NO LINKS.** It sits
under the drawer label **Guide** (not "Help" — the page is a tour of what exists,
not a place to get unstuck) and wears **`bi-info-circle`**, the outline style the
rest of the drawer uses. It was `bi-compass`, which promised navigation from the
one page in the app that deliberately offers none.
`/about/` — a static, query-free tour of what is in the system: the generated
system map as its header, then every section in short plain English.

Four things about it, each a decision rather than a default:

- **Owner-only**, matching `@owner_required` on the view. It describes Profit,
  Cash Tracking, Deletion History and both shop ledgers, and a tour of doors a
  role cannot open is the same defect as rendering one — the rule the audit
  menu and the frozen-advance ⋮ already follow.
- **No links, no buttons, no forms anywhere in the body.** The brief was
  "scroll and read all". A page of shortcuts into other sections is a second
  menu, and the drawer it was opened from is the menu.
  → `test_it_carries_no_links_at_all` scopes to the page's own `<section>`
  blocks, so `base.html`'s nav and logout form are not counted.
- **The map is an `{% include %}` of a GENERATED partial**, never a pasted
  `<svg>` — see the SYSTEM_MAP entry in the doc ownership map for why.
- **The map is ONE fixed drawing at every width.** It does not reflow, and on
  a phone it is genuinely tiny — the owner's explicit call, with pinch-zoom as
  the answer. That only works because `base.html`'s viewport meta sets no
  `user-scalable=no` and no `maximum-scale`; **do not add either.**

  ⚠ **THE ZOOM HINT SHOWS AT EVERY WIDTH, and the 900px gate it used to carry
  was wrong from the day it was written** (corrected 2026-08-31, after the
  owner asked where the hint had gone — it had never been lost, it was simply
  hidden on the screen they were reading). It was written as "shown only where
  it is true", on the assumption that the map is only small on a phone. **It is
  never full size anywhere**: this page sits inside `.main-content`, capped at
  800px app-wide, so the 1414px drawing renders at **768px — 54% — on a 1280px
  laptop and on every screen wider than that**, which puts an 8.8px card title
  at 4.77px. The hint was hidden on exactly the widths where somebody is most
  likely to be reading it and least likely to think of zooming a page.

  It is ONE string at all widths rather than a phone copy and a laptop copy to
  keep in step, and it names the gesture only where there is one: *"Zoom in on
  any part of the map — pinch on a phone."* One line at 375px, measured.
- **The page is FULLY DARK, and it is the only one.** It shipped as a dark map
  on the app's light surface, and the seam was the loudest edge on screen —
  the eye landed on the join rather than on the drawing. The whole page now
  sits on the map's own ground (`body.about-dark`), and the six family rails
  are the map's own DARK flow colours, so the header's legend is a key to
  everything under it. **Nothing else in the app follows it**: this is the one
  screen that carries no form and no money and exists to be looked at. The
  map frame is **square** (`border-radius: 0`), because the schematic inside
  it is built entirely on 90-degree corners.
- **A LATE SUPPLIES BILL IS TOLD AS TWO HALVES, AND SHIPPING ONE WITHOUT THE
  OTHER IS THE DEFECT.** The card read *"Bills are usually keyed long after
  the goods arrive, and that is fine"*, which reads as advice to leave the
  paperwork. The first half is genuinely a feature and worth saying warmly:
  the shelf may go negative, the mechanic still takes the part and writes it
  on the card, and a bill dated to the delivery day fills the count back up
  **and** back-costs every draw since.

  ⚠ **The second half is the price of waiting, and it is SMALLER than it
  looks — getting that wrong argues for changing a routine the figures do
  not need changed.** `JobCardSpareItem.save()` snapshots `Item.avg_cost`
  onto the draw and only leaves `unit_price` NULL when that average is **0**.
  So on a product bought regularly a late bill does **not** make the parts
  free: they are costed at what the shelf last paid, and the replay corrects
  them when the bill lands. The ₹0 case is a product **no bill has ever
  costed** — opening stock, a first purchase, or one whose only bill was
  deleted — and there `uncosted_draw_count` puts a banner on the Profit page
  saying it "makes this profit look higher than it is". The card names both
  cases separately; a first draft claimed the ₹0 one for everything.
- **The prose is written for two owners on a phone, not for this file.**
  "Keyed", "enforced on the server", "refused outright", "deliberately" are
  right here and wrong there. Contractions are fine; a word the reader has to
  translate is not.
- **It says "the system", never the product name.** WorkshopOS and Titan are
  the owner's own words for it and are kept out of the page's prose — and off
  the map's title block, which reads SYSTEM MAP.
  → `test_it_does_not_use_the_owner_s_own_names_for_the_system`
- **Every card on the map is described somewhere on the page.** The map is the
  page in a drawing, so a box on the sheet with nothing said about it is a
  gap. Nine families, ~43 cards.
  → `test_it_covers_every_area_the_map_draws`

  ⚠ A reflowing HTML version was built first and rejected. Measured: the A4
  sheet's 8.8px card titles render at **2.34px** on a 375px phone. That is the
  known, accepted cost, not an oversight — do not "fix" it by making the map
  responsive, which would un-make it as the printed sheet.
→ `workshop/tests/test_about.py`

**Logout is confirmed, and there is exactly one logout control in the whole app.**
The drawer button is a `data-bs-toggle="modal"` trigger; the POST form lives in
`#logoutConfirmModal`, which sits **outside** the off-canvas — a modal nested
inside one inherits its stacking context and opens behind the backdrop. Verified
layering: modal 1055 > modal-backdrop 1050 > offcanvas 1045 > offcanvas-backdrop
1040.
→ `LogoutConfirmationTests` asserts the page contains exactly one
`action="/logout/"`.

**A panel that covers the screen has no way out.** Both the drawer and the
notification sheet were effectively full-screen takeovers on a phone; both close on
a backdrop tap, and neither left anywhere to put a thumb. The drawer is
`clamp(240px, 70vw, 340px)`; the sheet leaves **exactly 25vh of live backdrop
above**, expressed as a subtraction from 75vh rather than a bare `66vh` so it stays
a quarter of the screen as `--nav-h` or the safe-area inset change.

*The drawer's width and its type size are one decision, not two.* The longest label
("Analysis & Reports") renders 138px at 1.02rem, and the row spends 108px on
padding / icon tile / gaps / chevron — so **246px is the width at which the last
label stops fitting on one line**. 70vw clears it from 360px up; the 240px floor
stops a 320px screen wrapping. Grow the type or shrink the width past that and rows
start wrapping.

## Card list grids — six lists, two breakpoints

`row-cards` in `base.html` owns Completed, Pending Bills, Paid Bills, Job Cards and
the High Discount Audit; `.cp-grid` on Car Profiles keeps its own declaration
because it is CSS grid rather than Bootstrap columns. **The numbers must never
differ.**

| Width | Columns |
|---|---|
| < 560px | 1 |
| 560–799px | 2 |
| ≥ 800px | 3 |

- **800px is where `.main-content` reaches its `max-width` and stops growing**, so
  from there up nothing about a card changes. Bootstrap's `lg` (992px) had been
  holding these at two-up for 192px after the container had already stopped
  changing — the nearest tier, not the right number.
- **It must not start lower**: the plate and the payment badge stop fitting on one
  line at about a 236px card, and a few cards wrapping while the rest do not is the
  raggedness `.del-vehicle-name`'s `min-height` exists to prevent.
- **560px** was already Car Profiles' own two-up point while the others waited for
  `md` (768) — so an iPad Mini showed Car Profiles two across and Completed one
  across, same screen, same minute, two answers.

**The cards carry a bare `col-12` and no responsive `col-*`.** Leaving
`col-md-6 col-lg-4` on them would be two rules describing one grid, agreeing today
and free to disagree the first time either is touched.

**A four-up rule above 1400px would make cards narrower on the biggest screen than
three columns are on a tablet**, because `.cp-page`'s own `max-width: 1400px` is
dead inside an 800px `.main-content`. Widening the container is the only thing that
would earn a fourth, and that is a decision about the whole app.
→ `workshop/tests/test_card_list_grid.py`

## Job Card form

**Every section announces itself the same way** — `.jc-sec-head`: a tinted glyph
tile, the name, the action on the right. Six sections share one heading shape where
there had been six hand-rolled flex rows, and the Customer block had **no heading at
all**.

⚠ **Read the band's colour values off `.jc-sec-head` itself, never off this file.**
It is one flat neutral. A six-step ramp (each section a step darker) was built and
rejected: **the sections are not a scale of anything** — a car's concerns are not
"more" than its vehicle details — so six shades invited being read as a ranking,
and the darkest drew the eye hardest at the bottom of the form where the least
urgent sections live. A control on the band must not be tuned to one band colour.
The symbol keeps a tile so it stays an object rather than dissolving into the band.

**The read-only twin copies all of it** — `.dv-sec-head` in `jobcard_detail.html`
is the same values, and `test_the_section_band_is_the_forms_own_colour` compares
the two rules so neither can move alone.

⚠ *Trap that test records:* `.jc-sec-head` is re-used further down the stylesheet by
the locked-record palette, so a selector match on `endswith` finds the wrong rule
and reads as the band having changed colour when it has not.

**Below 576px the Add button gives up its WORD, not the section its NAME.**
Icon-only it is 44×44 (`min-width` as well as `min-height` — a target is only as
big as its smaller side) and every one carries an `aria-label`.

**An EMPTY box wears a hairline; a CHANGED box wears an amber edge.** Two marks,
two different facts, and **neither may move the page**.

- **Every empty box is marked except those carrying `jc-optional`**, and the
  exemption is declared on the **widget in `forms.py`**, not as a list of names in
  a template script — one mechanism, sitting where somebody adding a field will see
  it. Exempt: Customer Name, Contact Number, the Internal note, and a SHOP spare's
  Qty (nothing refuses a save without it).
- **The two spare DATES are NOT exempt, and are marked as a PAIR** — a spare is
  finished when it has been ordered *and* received. The mark sits on the **chip**,
  because that is what is on screen; the two inputs inside the panel are swept like
  any other box, which is what says *which* is missing once it is open. One control
  cannot carry two facts, so it does not try to.
  → `ADatePairIsOnlyDoneWhenBothAreInTests`
- **The INVENTORY quantity is NOT exempt while the spare one is**, and that
  asymmetry is the rule working: the same word carries two different obligations —
  a warehouse draw is refused without a quantity, because that is the number leaving
  the shelf — so the mark follows the obligation, not the label.
  → `test_an_inventory_quantity_is_still_marked_when_a_spare_one_is_not`
- **It is border COLOUR only.** That restraint is why it can be applied this
  widely: an ordinary edit carries dozens of marks, and at any louder weight that is
  a page-long alarm. A border *width*, padding or margin would reflow the parts
  tables as you type.
- **It is NOT the error state.** `.jc-row-invalid` paints a row's background and is
  what a refused save looks like.
- **A settled card wears none** — the lock disables every box, and an empty box on
  a closed card is nothing anybody will fill. Done as `.jc-empty:disabled` in CSS
  deliberately, because the lock is applied on a `setTimeout(…, 100)` and script
  reading that state would race it.
- **The amber `.jc-changed` edge is `box-shadow: inset`**, painted inside the box
  the browser already laid out. Three marks hang off one class on the body
  (`jc-dirty`) so they cannot disagree: that edge, the **sticky header turning
  amber**, and a note on the Save button — plus a `beforeunload` prompt.
  **The header tint is the signal that carries, not the pill**: on a 375px phone
  the title was already truncating, and adding a pill to that flex row cut it to
  "Editing:" and nothing. So the **wording is held back until 576px** and a
  background colour, which occupies no width at all, does the job below it.
- **`dirty` is cleared only on a submit that was not prevented.** The Financial
  Lock and the Inventory guard both cancel, and clearing the warning on a submit
  that never left would drop it on the one card still needing it. Two places fill
  boxes in script and therefore fire no event — `importSpare()` and the colour
  picker — and both call `window.jcFormTouched()`.
→ `workshop/tests/test_jobcard_form_ux.py`

**The blank-row DELETE flags are RECOMPUTED on every submit, not latched.** The
four passes that mark an empty concern / spare / draw / job for deletion only ever
set `checked = true`, and a submit can be cancelled *after* they have run — the
Financial Lock's own handler does exactly that. So a row left blank on a refused
attempt stayed marked, and typing into that row and saving dropped what had just
been typed. They assign `checked = !value.trim()`.

**A warehouse draw with no quantity is refused in the browser by a SCRIPT guard,
never by `required`.** `InventoryDrawForm.clean` already refuses it on the server
and that stays the real rule; this only saves the round trip. `required` cannot
express "only once a product has been picked" and breaks badly twice here: it
blocks the **submit event**, and the handler that marks blank rows for deletion
lives in that event — so a card carrying one untouched blank row would refuse to
submit with nothing on screen — and a `required` control the browser cannot focus
(`#empty-inventory-form` is in the document, inside `d-none`) makes Chrome abandon
the submit **silently**. The guard runs on `document` in the **capture** phase and
calls `stopPropagation()`.

**A refused save says so, names what, and keeps what was typed — and the list is
built in PYTHON.** `_collect_problems` in `views/jobcard.py`. The error summary
used to enumerate four formsets by hand, and Inventory was the fifth — so a draw
saved with a blank Qty was refused with **no banner, no message and no sound**,
which from the front is indistinguishable from the Save button doing nothing.
Three rules: the list is assembled in the view, so a new section cannot be forgotten
in markup; each row is named by **what it holds** (`row_label()`), because
"Inventory item 7" means counting rows; and a `messages.error` is raised, which is
what makes the banner appear and plays the error tone.

- **The visible product box is re-rendered from the POSTED choice, never from
  `instance.spare_part_name`.** That box is not a form field — it posts nothing,
  and the hidden `item` pk is the row's whole identity — so on a rejected save a NEW
  row came back with the pk intact and the box empty, looking untouched, and got
  filled in a second time.
- **There is ONE `_form_context()` for every render of the form.** Building it
  closed a live data-loss path: the duplicate-registration refusal passed **no
  `spare_shops`**, so every spare row's shop `<select>` re-rendered holding only
  "-- Shop --". Correct the registration, press save, and each select posts blank,
  the FK is cleared, and the purchase disappears off that shop's ledger. Needing
  nothing unusual — only a customer bringing a car back before the last card on it
  was closed.
→ `ARefusedSaveSaysWhatIsWrongTests`

**The two routes are edited as two sections over ONE formset model.**
`JobCardSpareFormSet` and `JobCardInventoryFormSet` are both inline formsets on
`JobCardSpareItem`, prefixes `spares` and `inventory`; each scopes itself to its own
`source` in `get_queryset()` and stamps `source` in `save_new()`
(`SourceScopedSpareFormSet`). **`source` is deliberately not an editable field** —
moving a row between routes would have to move warehouse stock and a shop-ledger
balance at the same time.

Two consequences: every job-card POST must carry the `inventory-*` management form
(the template always renders it, so a payload without it is malformed), and the
shop-resolution pass filters to `SOURCE_SHOP`, because it reads `shop_name` as a
posted pk and a draw has none.

**The Inventory product is picked, never typed** — the visible search box has no
`name` attribute and posts nothing; the hidden `item` field carries the choice.

**The Inventory picker SEARCHES categories and never OFFERS them.** Typing "Engine
Oil" returns the products inside that category, and every row it returns is a real
`Item` with a real pk. Both halves are load-bearing: a person thinks in the generic
term, because that is the word the customer uses *and the word the bill prints*, so
matching `Item.name` alone meant searching "Engine Oil" returned nothing — and the
obvious next move is to create a **product** called "Engine Oil", which puts a
generic name on the shelf as a fake SKU. What the job card must store is the
branded SKU, because that is what moves stock and carries the cost. **The category
can lead you to the product; it can never be the answer.** `distinct()` is required
— an OR across the category join offers a product matching on both its own name and
its category's twice.
→ `test_searching_a_category_returns_the_products_inside_it`,
`test_a_category_is_never_itself_an_option`

⚠ **The placeholder "Search by product or type (e.g. Engine Oil)" is load-bearing.**
It is the only place that rule is now stated to the person typing.
→ `test_the_inventory_box_still_says_it_searches_by_type`

**Stock crosses the wire already formatted** — `clean_qty`, the same filter every
other quantity goes through, so one product cannot read "38" on one screen and
"38.00" on another.

**The stock line under the box reserves its height whether or not it has text.** It
was an empty `div`, so choosing a product wrote a line into it and the row — with
everything below it — jumped, which on a tablet means the box you were aiming at has
moved by the time your finger lands.

**"38 in stock" is shown while PICKING and not afterwards.** The count answers one
question — is there enough on the shelf to take — asked at the moment of choosing
and never again. On a card reopened weeks later it is a number about TODAY's shelf
beside a part fitted long ago, once per row. `stock_display` returns `''` for a row
with a pk; the picker still writes the line the instant a product is chosen. **The
picker's own suggestions do not print it either** — a dropdown row carries the
product and its category and nothing else. The count belongs in exactly ONE place.

**The Inventory table carries NO `align-middle`, unlike Spare Parts.** The Item
cell is taller than its neighbours because it holds that stock line, so centring
every cell vertically lifted the Item box above the Qty and price boxes beside it by
half the line's height. `.inventory-table > tbody > tr > td { vertical-align: top; }`
starts all four at the same y and lets the stock line hang below.

**BOTH price boxes on a Spare Parts row hold the LINE TOTAL, and on a row of more
than one they both say "total".** Six things are load-bearing:
- **Both are `<span>`s with no name.** They post nothing, compute nothing and store
  nothing; they are driven off the Qty box already in the row.
- **INSIDE the boxes, absolutely positioned on the left**, so the mark reads at the
  left edge with the typed value at the right. Both are `text-end`, so the left half
  is space the value never occupies and neither mark costs any height. **Anything
  that appears below a control moves every row under it.** `padding-left` is applied
  only while a mark is there, and it is what stops a big figure sliding underneath —
  a long value makes the input SCROLL rather than paint over its own padding.
- **Both appear together, under one condition: a quantity that is not ONE.** On a
  row of one, "total" is true of every box on the page and says nothing.
- **Scoped by field NAME (`spares-…-quantity`), so the Inventory section is
  untouched** — a draw's Unit Price genuinely IS per unit there.
- **Pure delegation, no per-element wiring**, so a row added by "+ Add Spare" works
  with nothing re-initialised. `refreshRowTotals()` rides the same three sweeps the
  date chips do plus the per-keystroke `input` path, because typing the Qty is
  exactly when the marks are needed.
- **Floor is shown neither**, because Floor is shown no prices at all.
→ `BothPriceBoxesAreLineTotalsTests` — searches the *tbody*, never the whole page,
where the stylesheet declares the same class names.

**The two spare DATES share one cell, as a CHIP reading `22/07 – 29/07` that opens
a small panel.** They were two full-width columns costing ~357px of a table that
already scrolls sideways, and they are blank on most rows because
`spare_autofill.js` fills them from the Status dropdown. A missing half prints an
ellipsis (`22/07 – …`) so the chip always says which date you have; neither prints
"Add dates".

⚠ **The panel is `position: fixed`, and that is two decisions in one.** It is out of
flow, so opening it cannot move a row (table, row and page heights are identical
open and shut). And it is the only position that **escapes the clip**: the panel
sits in a `<td>` inside `.table-responsive`, which is `overflow-x: auto`, and an
absolutely-positioned panel in there is cut off invisibly and only sometimes.

Three things travel with it: the **inputs are unchanged form fields** with their
names, inside the form — a hidden input still submits its value, so only where the
boxes are *shown* changed; everything is **delegated off `document`**; and every
button in it is **`type="button"`** — a bare `<button>` inside a form submits it, so
one wrong and looking at a date saves the card.

**Column order is safe to change**, because every script touching these rows
resolves fields by a row-scoped `querySelector` on the field NAME, never by cell
position. What is **not** safe is dropping a cell: an absent formset field saves as
blank.

⚠ **`#empty-spare-form` must be reordered in the same edit** — it is cloned by
script.js and would otherwise lay an added row one column adrift of its header, with
nothing in the browser to say so.
→ `test_the_added_row_template_matches_the_live_rows`

**On the parts tables the row you are in is NAMED by a sticky number and LIT by a
focus tint** — two marks, two questions.

- **The LIGHT is what actually prevents the mistake.** The failure is rarely "wrong
  table" — it is off-by-one, catching the row above or below, because rows are 55px
  tall and every box looks like every other. `:focus-within` lights the whole row
  across every column. It costs no width, no height and **no JavaScript**, which is
  why an added row has it for free. It is **blue** because amber already means "you
  changed this" and red means "this was refused".
- **The NUMBER is the handle for the horizontal scroll** — 34px pinned left, so row
  7 is still row 7 with its name off screen. 34px of table width, **0px of row
  height**, because a sticky cell is laid out in its row like any other.

*A truncated NAME was the obvious alternative and is wrong on this workshop's
data*: real cards carry "Front Lower Control Arm LH" directly above the "RH", and
"Front Brake Pad Set (Brembo)" above the "Rear". Any column narrow enough to afford
prints "Front Lower…" on both — worse than printing nothing, because it looks like
an answer. **A number cannot collide with another number.**

Three things are load-bearing: the number is a **bare cell — no input, no name, no
stored value**, so anyone auditing the money can skip it; it is **re-derived from
the DOM** by `renumberRows()`, never incremented from a counter, because its whole
job is to agree with what is on screen; and **both clone templates carry the cell**.
A hidden row **keeps its number rather than closing the gap** — rows are never
removed, so a position is stable, and renumbering under somebody mid-edit would be
the mark undermining itself.
→ `TheRowYouAreInIsNamedAndLitTests`

**Customer Details is FOLDED SHUT**, because this workshop mostly does not record
one. Most job cards carry no name and no number, and three permanently empty boxes
between Vehicle Details and Customer Concerns are three boxes everybody scrolls
past. Nothing was removed and nothing made harder.

It is a **native `<details>`**, not a JavaScript panel: nothing to wire, keyboard
and screen-reader behaviour for free. The load-bearing fact is that **a closed
`<details>` still SUBMITS the inputs inside it** — `display: none` has never stopped
a form control posting.
→ `test_a_closed_section_still_saves_what_is_typed_into_it` — if it fails, every
customer name in the workshop is being wiped on save.

**It opens itself whenever there is anything to see**: a card that HAS a name, a
number or a note renders open, and so does one whose refused save put an error on
one of those fields — otherwise the message hides behind a summary nobody thought
to click, and the page says "not saved" while showing nothing wrong.

⚠ **THERE IS NO "OPTIONAL" PILL ON EITHER FOLD any more, removed on the owner's
instruction — the fold already says it.** A section that ships CLOSED, on a form
where every other section is open, is one nobody is being asked to fill; the pill
said the same thing a second time in the loudest treatment on the band, competing
with the section's own name for a line that already truncates at 375px. The
`.jc-fold-note` rule went with it, so a copy cannot come back by accident. Nothing
about the fields changed — none of them was ever required.

**The internal note is a TEXTAREA that grows.** `rows=1`, because most cards carry
no note. Built as progressive enhancement: the CSS declares a draggable one-row
textarea, and `autoGrow()` sets `overflow: hidden`, drops `resize` and sizes the box
only once it runs — a page whose script never arrived is never left with a box that
clips its own text.

⚠ **A textarea inside a CLOSED `<details>` is `display: none`, so `scrollHeight`
reads 0** and sizing it there collapses the box the moment the fold is opened —
hence the `offsetParent` guard and the `toggle` listener.

**The note is unprintable by construction** — `invoice.py` and the invoice template
both read named fields, so a column nobody references cannot print.
→ `test_the_internal_note_never_reaches_the_customer` keeps it so against the day
somebody adds a generic field loop.

It is **not** price-locked, so Floor may write one — that is the point of the box.

**It is the one control on the form at `font-weight: 400`.** `.form-control`
sets 500 on every control, which is right for the short data fields it was
written for — a registration, a figure, a name — where the weight is what
lifts the value off its label. It is wrong for the one box holding SENTENCES: a
two-line note at 500 reads as shouted, and it is the only free text on the page.
Scoped to `.jc-grow`, which only the note carries, so nothing else moved.

**"Job Performed" is suggested from the parts already on THIS card.** Nearly every
job line is a part on the same card plus a verb — "Engine Oil replaced" — so the
source is the card's own two parts sections, not a master list. Four rules:
- **A native `<datalist>`**, so a job row added *after* page load gets the same list
  with nothing re-initialised.
- **A warehouse draw is offered by its CATEGORY, never its branded SKU**, through
  `invoice.item_display_name`. That split was made for this: both strings end up on
  ONE document, so a job line naming the brand beside a part line naming the
  category is the invoice contradicting itself.
- **The list is rebuilt on FOCUS of a Job Performed box**, delegated on `document`
  — the one moment it is about to be used and therefore the one moment it has to be
  current, needing no event from the picker or the autocomplete.
  `data-category` starts EMPTY on `#empty-inventory-form`, so a cloned row can never
  inherit the previous row's category.
- **The verbs exist in exactly one place**, ordered by the owner's own measurement
  (replaced ~70%, then removed-and-installed, refurbished, inspected, repaired). The
  order is load-bearing: a datalist keeps document order, so opening it cold shows
  one "replaced" line per part before any variant of anything.
→ `workshop/tests/test_job_line_suggestions.py` asserts everything the server owes
the script — nothing in this suite executes JavaScript.

**The vehicle and customer boxes carry NO placeholder.** Every one sits under a
label that already names it, so the hint restated the label in quieter type — a
second line of text per box on the longest form in the app. Both the Job Card and
the Estimate strip them **in `__init__` from one list**, so the two forms cannot
drift. The placeholders that survive earn it by saying something a label cannot:
the Inventory picker's "or type" and the money boxes' currency.
→ `test_the_vehicle_and_customer_boxes_carry_no_placeholder`

**Surviving placeholders are drawn quietly** on both forms — colour and size only,
so no box changes height. **One exception, told apart by the ATTRIBUTE:** the
estimate's unit-price box carries `avg: 1064` when the part has sales history, and
`.estimate-rate[placeholder^="avg"]::placeholder` keeps that one italic and darker.
The selector works because `el.placeholder = …` reflects onto the attribute, so the
rule follows the script with nothing to keep in step.

**A LOCKED job card has to LOOK locked.** The form grew a soft-surface palette that
painted every control the same colour a `:disabled` control was painted, so on a
settled card the lock disabled every field and none of them looked any different —
the banner said LOCKED while the form under it looked ready to type into. Locked is
now its own palette (cooler fill, visible border, muted text, `not-allowed`),
deliberately further from the live state than the live state is from hover, and
`[readonly]` gets the same treatment.

The state is keyed on the form's own **`data-locked`**, read in CSS rather than
script because the lock is applied on a `setTimeout(…, 100)`. It is restated on
every section heading as the **word** "LOCKED", not an icon-font glyph — a codepoint
depends on the icon stylesheet having arrived, and this is not the screen to take
that bet on. (The argument was originally about the CDN; the font is self-hosted
now, but a stylesheet that fails to load still paints a blank box where the state
should be, and a word cannot fail that way.)
→ `ALockedRecordLooksLockedTests`

⚠ Those rules re-use `.jc-sec-head` / `.jc-sec-icon`, so they are declared at the
FOOT of the stylesheet; a copy earlier in the file is what several tests find first
when they split on a selector.

**The submit button is AMBER on an edit and GREEN on a create, and neither carries a
shadow.** `btn-primary` blue put the one control that matters most into a page that
is mostly blue. A deeper navy was tried and rejected — it solved the problem by
being a *darker blue*. **Amber is the only colour on this page already about your
changes**, so the button that commits them wearing it is the page agreeing with
itself. Amber forces DARK text and that is not optional: white on `#f59e0b`
measures 2.2:1, `#1e293b` on it measures 6.81:1. Both colours come from **one
`--jc-action`** read by the big button and the sticky one.

**The feedback is built for a FINGER, not a pointer.** These sections are worked on
the Floor tablet, where hover is wrong twice over — see the traps section. Every
hover rule is behind `@media (hover: hover)`; what a finger gets is `:active` as a
real squash (`scale(.94)` plus a filled background, not the token 0.97 a pointer
would need, because it has to read at arm's length). The browser's grey tap flash is
replaced via `-webkit-tap-highlight-color`, or it fights the `:active` paint and
lands a beat later.

**An added row announces itself.** The "+ Add" button is at the top of its section
and the new row lands at the bottom of a list that may already be below the fold, so
the only evidence of a tap was a scrollbar changing length. The row flashes (a
`background-color` keyframe — paint, so nothing moves) and is scrolled into view
with `block: 'nearest'`, which scrolls nothing when it is already visible.

**While there is unsaved work, a light TRAVELS THE BUTTON'S BORDER**, and a second
light sweeps across a pressed one. Three rules keep them safe:
- **Fire the sweep from a class on `pointerdown`, never `:active`** — a tap releases
  in about 80ms and takes `:active` with it.
- **The `--jc-orbit` ANGLE turns, not the element** — rotating the element would
  turn the ring with it and skew a wide rectangle. It is confined to the border by a
  `padding` + two-mask pair. **WHITE**, not amber, so one gradient serves both the
  amber edit button and the green create button.
- **Progressive enhancement**: the ring needs `mask-composite` and a registered
  `@property`, so a still white 2px inset outline is declared unconditionally and the
  `@supports` block clears it where the ring can be drawn. An old browser loses the
  animation and still says "unsaved".

*A pulsing glow was tried first and replaced:* a pulse changes the button's apparent
SIZE, so the eye keeps being pulled back to something growing and shrinking; a light
running the edge is movement with no change of weight.

**This is the ONLY looping animation on the page.** An idle shimmer is noise on a
screen staff work all day and costs battery on the tablet; this one is temporary, the
person can end it, and it stops the moment the card is saved.

**Everything animates `transform`, `box-shadow` or `background-color`** — composited
or paint-only, so a control reacting to a press can never nudge the form under the
finger aiming at it. `prefers-reduced-motion` drops both motions and **keeps the
colour**, because the colour is the feedback.

**One press makes one job card**: the button goes to "Saving…" and then disables, and
`disabled` is set in a **`setTimeout(0)`, never inline**. The button carries no
`name`, so dropping it from the payload costs nothing.

**Add buttons and the date chip are 38px, and 44px under `@media (hover: none)`** —
keyed on input method rather than a width breakpoint, because it is the finger that
decides how big a target must be and the Floor tablet is wider than plenty of
laptops.

**The sticky save button is INSIDE the form, and is absent until there is something
to save.**
- **Inside the `<form>`, which is an integrity matter, not a layout one.** The
  Financial Lock disables controls with `form.querySelectorAll(…)`, so a floating
  button outside the form would be the one control the lock never reached — a
  settled, locked job card, saveable from a button in the corner.
- **It is not there unless the card is dirty**, so it is never in the way of anybody
  with nothing to save and can never be pressed pointlessly.
- **It clears the phone's bottom nav** — `calc(var(--nav-h) + env(safe-area-inset-bottom) + 16px)`,
  both variables, so it follows the bar if that ever changes. Stacking is **1020 —
  under the nav (1030) and under the date panel (1035)**: it must never cover
  navigation, and never cover a popover somebody opened deliberately. Both doors are
  disabled together on submit, or a second tap posts the card twice.
→ `TheStickySaveTests`

**The car's colour is a RAIL, not a wash.** A full-page tint at the same 8% alpha
the other screens use sat behind every section for several screens — a lot of colour
for a fact the header rail and the colour dot already state. What remains is
`.jc-head::before`, one strip at the top, driven by `--jc-accent`. The shared picker
**dispatches `carcolour:change`** rather than letting each page reach into it —
setting `.value` in script fires no event, and the Estimate uses the identical
control and wants none of this.

**One car-colour palette, one picker.** `CAR_COLOR_CHOICES` and `CAR_COLOR_HEX` at
module level in `models.py`; `workshop/includes/_car_color_picker.html` is the single
swatch control, used by the Job Card and the Estimate. A second copy would be ~100
lines of markup, CSS and JS plus fifteen hex values free to drift, and a Grey job
card printing a different grey from a Grey estimate is invisible until the two are
side by side. **The estimate's colour is not printed on the quotation** — it is the
stripe down each history row, and the customer already knows what colour their car
is.

**Two exceptions travel with the colour everywhere it is worn** (job card, Car
Profiles, Live Report): a WHITE car's rail is outlined (`inset` box-shadow) or it
vanishes against the card, and a car with **no colour recorded gets a hatched rail
and NO wash at all** — a slate tint would say "this car is grey", which is a
different fact from "nobody wrote it down".

⚠ **`jobcard_form.html` closes its `<form>` before its wrappers.** Two `</div>`s
once sat above the submit block: the HTML parser pops `<form>` when an ancestor
`<div>` closes, so the Save button became a **sibling** of the form. It still
submitted — the parser's form-element pointer associates a control created while a
form is open — and *that* is what made it a trap rather than a bug: nothing looked
wrong, while `form.querySelectorAll(...)` silently skipped everything past that
point.
→ `TheFormIsWellFormedTests`

## Dashboard & board screens

**THE BOARD NARROWS TO ONE MECHANIC, AND THE HEADING MUST NOT FOLLOW IT.** A
scrolling chip row over the cards — `All 10 · Amlah 3 · Hijaz 3 · Sabith 3 ·
Unassigned 1` — filtering the board to one person's cars.

It exists because `_floor_by_mechanic` has grouped the floor by mechanic on the
Live Report for months and that page is **`@office_required`**, so the people
actually holding the cars had no way to see which ones were theirs. Not a
duplicate of it either: that board is a read-only list of concerns for deciding
what to say next, these are the working cards with the ⋮ menu on them.

⚠ **"IN WORKSHOP" READS `floor_count`, NEVER `page_obj.paginator.count`.** Those
were the same number only for as long as nothing could narrow this board. Read
off the pager it prints **"3 IN WORKSHOP" while ten cars are in the workshop** —
the one figure on the page that would then be flatly untrue. The Live Report
keeps its own `floor_count` apart for exactly this reason.

**It is also what makes the filter safe to LEAVE ON, which is the question that
decided the persistence rule.** The filter rides in the URL (`?mechanic=`) like
every other filter in this app, so it survives a refresh, Back and the pager —
and the argument against that is real: this is the home page and the Floor
tablet is shared, so somebody filters to Amlah, walks off, and the next person
sees three cars of ten. The answer is that the page contradicts a stale filter
out loud — the heading still says ten, over three cards, with a lit chip between
them saying whose three they are. A filter that silently reset would be the
confusing one: you tap a name, the tablet sleeps, you wake it and you are
looking at everyone again with nothing saying why.

**Four rules about what is on the row, all falling out of ONE aggregate** in
`_floor_chips()` — which is also the only list of valid `?mechanic=` values, so
a chip and the filter it applies cannot disagree:

- **A mechanic holding no car gets no chip** — `_floor_by_mechanic`'s own rule,
  and a `Shafeeq 0` chip is a door onto an empty board.
- **The counts sum to All**, the unassigned group included, so the row can never
  quietly lose a car. Asserted, because the failure is invisible: the row still
  looks right, it just stops accounting for one.
- **Unassigned is last, is the only chip carrying a colour, and appears only
  when a car is in it** — it is the one entry asking for a decision rather than
  reporting a fact, so it takes the red the Live Report already gives its "Not
  assigned" group.
- **Ordered by NAME, never by count.** By count a chip moves out from under the
  thumb reaching for it every time a car changes hands.

⚠ **A key that names no chip falls back to All, and validating against the CHIPS
rather than the staff roster is what makes the stale case and the crafted case
one rule.** Filter to Amlah, let somebody complete his last car, come back to
the same URL — there is no Amlah chip any more, so the board falls back instead
of rendering empty under a filter that no longer exists. Same fallback the
Estimates list gives an unrecognised `?filter=`.

⚠ **`.order_by()` on that aggregate is load-bearing, not tidying.** The board is
ordered `-updated_at`, and an ordering field on a `values().annotate()` joins the
GROUP BY — which returns one row per (mechanic, timestamp) and counts every car
as **1**. Cleared explicitly so a later edit to the board's ordering cannot
silently break the counts.

⚠ **THE CHIP IS THE PROFIT PAGE'S ROW IN BEHAVIOUR AND DELIBERATELY NOT IN ITS
CLOTHES.** That row is a 999px Inter pill; this is the only page in the app
wearing the pit-board look, where every control is Barlow Condensed, uppercase
and cut to a **6px** corner (`.btn-report`) and every small figure sits in a
**4px** block (`.age-pill`, `.reg-badge`). A rounded Inter pill here would be the
one object on the screen that came from somewhere else. What IS copied is the
behaviour: one line, scrolls sideways at every width, never wraps. Measured —
450px of chips, so nothing is hidden from 768px up and 131px slides at 375px,
with the page body itself not scrolling.

The active fill is **one custom property** (`--tint`, defaulting to the page's
own `--pit-track`), so Unassigned overrides one value rather than restating the
declaration — the Owner Withdrawals chip's own mechanism. The count resets
`letter-spacing`, which is not tidying: the chip tracks its uppercase at 0.5px
and digits inherit it, so "10" rendered with a gap down the middle.

⚠ **THE LABEL IS `0.78rem` BECAUSE THAT IS `.mechanic-tag` — the same person's
name at the same size whether it is in the filter or on the card under it.** It
shipped at 0.85rem, and measuring the page's own scale is what settled it: at
375px that was **13.6px, the largest secondary element on the screen** — over the
plate (12.2), over the name on the card (12.5), and 1.6px over the `+ NEW`
button (12.0), which is the primary action. **A filter is chrome and must not
out-size what it filters.** At 0.78rem the chip is 33.5px against that button's
36.8px on a pointer, so the loudest control on the header row is the one that
should be. Condensed is narrower than the card's Barlow at equal px, so the chip
also reads a shade lighter than the name it matches — the right way round.

⚠ **THE TOUCH HEIGHT IS 38px, AND THAT IS THE 44px RULE READ PROPERLY RATHER
THAN RELAXED — this reverses what this file said for a day.** It stood at 44px
on the argument that a thumb needs 44px whatever the type is doing. The right
rule is narrower: **44px is for a control where a MISS COSTS YOU SOMETHING.**
The card's ⋮ keeps it, and the reason is already on the record here — a near
miss there opens the job card. Mis-tapping a filter chip costs one more tap,
with the result instantly visible in the lit chip and the board under it;
nothing is destroyed and nothing navigates.

The measurement is what settled it. The chip's natural content height is
**37.4px** (16px padding + 2.7px border + 18.7px line-height), so `min-height:
44px` was adding **6.6px of pure forced air** — and it left the filter the
joint-tallest control on the phone, level with that ⋮ and **taller than the
`+ NEW` button (33.5px)**, which is the primary action. Same failure as the type
size, one dimension over: the filter outsizing what it filters. At 38px it sits
with the "View" bar (38.7px), under the ⋮, and still clears **WCAG 2.5.8 Target
Size (Minimum, AA) — 24×24 — on both axes at 38×63**. The pointer case never had
a `min-height` and is untouched at 33.5px.

⚠ **Measuring one chip's natural height by zeroing its own `min-height` reads
the WRONG NUMBER.** These are flex items in a `align-items: stretch` row, so a
single chip just stretches back to the tallest sibling and reports no change at
all — it looked like `min-height` was doing nothing. Zero it on **every** chip
to see the real content height.

⚠ **THE SECOND PASS WAS PADDING, NOT TYPE, AND TAKING THE CHIP APART IS WHAT
SAID SO.** At 375px an 82.6px "AMLAH 3" was **32.9px of label and 5.5px of
digit — 46% content** — against 25.6px of its own side padding (31% of the
chip), plus another 10px wrapped around the digit alone. So the row read airy
while the type was already right. Trimming horizontal air is free here because
**the smaller side is the one that binds**: the chip is ~73px wide against a
44px minimum, so only `min-height` is load-bearing and it is untouched.

Measured at 375px across both passes: chip **82.6 → 72.6px**, row **474 → 399px**,
overflow **135 → 56px**, and **4 of the 5 chips now sit fully on screen where 3
did**. The fifth peeking past the edge is the scroll cue — and with the red one
last, it is also what says Unassigned is there at all. 1280 and 820 are
unchanged at 33.5px with nothing hidden.

⚠ **Do not chase all five onto a 375px screen.** It needs ~11px more off each
chip, which puts the text against the border — and the row scrolls by design
anyway: this workshop has five job-card-eligible staff, so a busy day is seven
chips and no tightening fits those. Scrolling keeps the row **one line at 46px
whatever the roster does**, which is why it is not the Owner Withdrawals chip
row's wrap: that one has three chips and a fixed ceiling, this one grows with
the staff list and would be three stacked rows above the cars.

⚠ **EVERY RED RULE ON THE UNASSIGNED CHIP IS SCOPED `:not(.is-active)`, AND
THAT IS THE DOCUMENT-ORDER TRAP, CAUGHT IN PRODUCTION USE RATHER THAN IN
REVIEW.** `.pit-crew-chip.is-unassigned` and `.pit-crew-chip.is-active` are both
**two classes** — equal specificity, so the winner is document order, and the
unassigned block sits after the active one. Selected, the chip therefore took
`color: #dc2626` back over the white it had just been given and rendered dark
red on the red fill: **1.28:1**, which is the word disappearing. The badge went
the same way (both selectors are three), and the hover was worse — at three it
outranks `.is-active` outright. Scoping to `:not(.is-active)` states what is
actually meant, that the red type is the UNSELECTED marking, so it no longer
depends on where in the file the block sits.

⚠ **THE SELECTED FILL IS `#dc2626`, NOT THE PAGE'S OWN `--pit-red` (#ef4444),
AND THE BADGE DARKENS THERE WHERE IT LIGHTENS EVERYWHERE ELSE.** Both are
arithmetic, not taste. `--pit-red` is decoration elsewhere on this page (the
header hairline) and carries no text; this fill carries white at 13.6px bold,
where it measures only **3.77:1** against the 4.5:1 that size needs — `#dc2626`
measures **4.83:1**. And `.is-active .n` lays white at 20% over the fill, which
on navy gives a lighter block at **9.6:1** but on red composites to
rgb(227,81,81) — moving the block *towards* the white digit on it, leaving
**3.78:1**. Darkening gives **7.1:1**. One overlay cannot serve both: a black
overlay on navy leaves the badge indistinguishable from the fill.
→ `test_the_selected_unassigned_chip_keeps_its_white_type`,
`test_the_selected_red_fill_is_the_measured_one`

⚠ **VERIFYING A COLOUR RULE MEANS MEASURING THE STATE THAT CHANGED, NOT THE
ONE THAT DID NOT.** The 1.28:1 shipped past a browser check that measured the
chip's computed colours *while it was idle* and only eyeballed the selected one
in a screenshot. Two further traps sit behind it: this page declares its CSS
**inline**, so fetching fresh markup and injecting it into an already-loaded
page styles it with the STALE stylesheet — the page has to be reloaded before
the new rule exists at all; and `.form-control`-style transitions mean a
computed colour read mid-flight is the OLD one, so set `transition: none`
before reading. Both cost a measurement that read as correct.

**The lit chip is scrolled into view on load**, because every chip is a link and
a full navigation resets the scroller to the left — so on a phone, where 131px
of the row is off-screen, selecting Unassigned left nothing on screen saying
what the board was showing. It nudges `scrollLeft` on the row itself, never
`scrollIntoView`, which walks up and scrolls every scrollable ancestor including
the document. Measured: 375px scrolls 134px with the page's own `scrollX` still
0; 820 and 1280 do nothing at all.

**Two things deliberately do NOT follow the chip.** "Completed today" counts a
different population (cars that left today) — a mechanic filter is a way of
reading the floor, not another workshop. And the row is **not drawn at all on an
empty workshop**, since "ALL 0" over the empty state is a control with nothing to
control.

**Cost: +2 queries, flat** — one COUNT for the unfiltered floor, one aggregate
for the chips, both on the existing `(is_deleted, completed, -updated_at)` index.
→ `workshop/tests/test_dashboard_crew_filter.py`

**The dashboard car card is worked with a THUMB.**
- **The car's colour is stated twice, not three times.** The 10px stripe and the 8%
  wash; the 20% coloured halo was the weakest of the three and the only one that
  read as a rendering artifact — a red glow around a white card looks like something
  failing to paint. It appears only under a POINTER now.
- **Hover is behind `@media (hover: hover)`**, for the sticking reason above.
- **`:active` does something.** It was an empty rule with the comment "Feedback
  removed to prevent blinking", so tapping a card gave nothing at all on the one
  device where it is always tapped. The blinking came from *moving* the card; a
  press that changes only paint cannot blink.
- **The hold dot no longer BLINKS.** A 2s infinite loop per held card on the screen
  the workshop looks at most. Nothing is lost — "on hold" was already said three
  times over (the pill's word, its red ground, the dot's colour).
- **The ⋮ is 34px, and 44px under `@media (hover: none)`** — it sits beside the
  card's own click area, so a near miss opened the job card instead of the menu.

**The card says the progress ONCE loudly.** "1/1" was the second heaviest thing on
the card, with **DONE** under it, and the ring beside it saying the same thing again
as a percentage — three tellings, one shouted. The ratio stays, because "2 of 5" is
the fact a percentage rounds away, but small and with `tabular-nums` so it cannot
change width as it counts up. **DONE is gone**: it labelled a number that already
reads as a proportion.

**The ring is TWO colours and carries a tinted DISC.** It once ran red under 30%,
amber to 60%, blue to 99%, green at 100% — so a perfectly normal morning with three
cars just admitted read as three warnings. The colour was encoding **progress**
while being decorated like **urgency**, and progress is not urgent: a car admitted
two hours ago has done nothing yet and that is correct. It was also wrong in both
directions at once, because the ring knows nothing about age (that is the pill
beside it). So **green means finished, one blue means under way**, and how far along
it is is the ARC. Two colours can be told apart at a glance on a moving tablet; four
cannot.

The **disc** fixes the other half: at 0% there is no arc at all, so the indicator
was a hollow grey circle with a number in it, which reads as something that failed
to load. A body at every value makes it a badge that fills rather than a ring that is
missing.

**The ring's track is THINNER than its arc** (2.25 against 3.5), not just lighter —
both at 3px was two arcs of equal weight told apart by colour alone. Declared in
CSS, **never as a `stroke-width` attribute**: an attribute is a presentation
attribute and loses to any stylesheet rule, so leaving both would be two numbers for
one line.

**`.car-name` WRAPS to two lines instead of truncating**, clamped at two. "Land
Rover Range Rover Sport" arriving as "Land Rover Range Ro…" is the card failing at
the one job it has.

**The dashboard wraps NATURALLY and Completed RESERVES the second line, and the
difference is the layout, not taste.** The dashboard is a single-column list, where
a taller card has nothing beside it to look short against. Completed is a
three-across grid of self-sized cards, so one wrapped name would draw a row of three
different heights — hence `min-height: 2.5em` on `.del-vehicle-name`.

**The state is a DOT at the end of the car's name.** No ACTIVE / HOLD pill: the word
was true of nearly every card on a board of cars *currently in the workshop*, so it
distinguished nothing; the only card it mattered on is the held one, and the colour
already says that. The dot is part of the name's own text run, so on a name that
wraps it lands at the end of the SECOND line; it is preceded by **`&nbsp;`** so it
binds to the last word and can never be left alone on a line the two-line clamp then
hides. `role="img"` + `aria-label` carries the state the word used to.

**The name is BIGGER on a phone than on a laptop** — which looks backwards and is
not. It used to *shrink* because it was sharing the line with the pill and the ⋮ and
losing to both. Removing the pill gave the line ~77px back, and this is the screen
read at arm's length while walking.

**The live-details drawer sheds its boxes below 640px, and ONLY below 640px.** Four
sections and ten rows made a drawer ~600px tall on a 375px phone — a whole screen
for one car — of which ~150px was section heading bars, with each section
additionally inside a white card with its own border on a panel that already has
one. **Nothing is removed and nothing reworded** — sections, counts, status icons
and the "+N more" tails all stay. What goes is the furniture. Above 640px it is
untouched: a wide drawer has room for boxes that help the eye find a section across
a long line.

⚠ **`line-height: 1` on the title** is what actually shrank it — that row is as tall
as its tallest child and the glyph is the tallest, so at the inherited 1.5 a 10px
label occupied 20px.

**The bar says "View" / "Hide".** It spans the whole card, sits directly under the
car it belongs to and carries a chevron that turns; "View Live Details" was three
words explaining a control that explains itself, on every card in a list of
forty-five. The sentence moved to `aria-label`, which the JS keeps in step.

## Live Report

**"BILLED BUT NOT FILLED" leads the page.** Every other box is about work in
progress, where an empty box is a task nobody has got to yet. These cards have been
billed: the money moved, the card went PAID, the shortfall became a permanent
discount, and the Financial Lock now stands between the card and anyone correcting
it. **An empty box on one of those is a hole in the books.**

- **BILLED is `PAID`, `BULK_PAID` and `PARTIAL`.** PARTIAL never happens to a
  walk-in, so every one here is a Fleet card that has been invoiced and is still
  being collected. It wears amber rather than the settled green, because money is
  still owed.
- **The narrowing is in the DATABASE and the detail in Python, kept in step
  deliberately.** `_billed_but_unfilled()` is an index lookup in front of
  `settlement.unfilled`, never a second opinion — every clause mirrors a check in
  it, `Trim` included, and `Coalesce` runs first because `TRIM(NULL)` is NULL and a
  card that never had a mileage would otherwise match nothing. The view still drops
  any card whose computed gaps come back empty, so a drift can only ever show
  **fewer** cards — never an empty red box, which is how an owner learns to stop
  reading a warning.
- **`count` is in GAPS, not rows** — a spare missing four things is four problems,
  and that number is what says whether this is a typo or a card nobody filled in.
- **Paginated, not windowed by date.** It is a queue to be worked down; the heading
  carries the true total, and nothing is hidden behind a filter that would have to
  be widened to find the oldest and worst cards.
- **Each car is its own CARD** — a hairline between rows ran a list of four together
  as one wall of red.
→ `workshop/tests/test_billed_but_not_filled.py`

**The operations board ignores every query parameter.** It answers "what is the
state of the workshop right now", and a half-filtered answer to that is worse than
no answer.

⚠ **The "Not assigned" group's position is decided in Python, never by
`order_by('lead_mechanic__name')`.** PostgreSQL sorts NULL last on an ascending sort
and SQLite sorts it first, so a database ordering would put that group at a
different end of the page in the tests than in production.

**A mechanic holding no car is not listed** — every name on the board has work under
it, which is what keeps it short.

**THE FLOOR BOARD IS LAST ON THE PAGE, UNDER ITS OWN "FLOOR" HEADING — moved
2026-09-02 on the owner's instruction, from second.** It is by far the longest
block here: one panel per mechanic, every open concern under every car. Sitting
above the parts boxes it pushed all three of them off the first screen, so the
two lists that are *scanned* were below the one that is *read*. "Billed but not
filled" still leads, for its own reason, and the parts boxes keep their green →
amber → red order.

⚠ **It needed a HEADING, not just a move.** `<h6 class="lr-group">Spares</h6>`
opens a group that nothing closes — there is no wrapper and no second heading —
so a box dropped after the three parts boxes with no heading of its own reads as
a fourth kind of spare, to the eye and to a screen reader alike. The heading is
the thing that ends the Spares group. Adding a fourth `lr-group` would be the
same trap one box further on.

⚠ **THAT HEADING READS "STILL TO DO" AND THE BOX UNDER IT STILL READS "ON THE
FLOOR" — TWO LEVELS SAYING TWO THINGS, WHICH IS THE WHOLE POINT.** It shipped
for an hour as "Floor" over "On the floor", one fact twice. The heading names
the WORK, because since the concerns landed that is what the box is for; the
box title names its ROWS.

⚠ **THE ROWS ARE CARS, AND THAT IS WHY THE BOX TITLE CANNOT BE ABOUT CONCERNS.**
The count badge is `floor_count` — the rule every box here follows is that the
count is the rows beneath it. "Pending Concerns · 10" was proposed and would
read as ten concerns when it is ten CARS, on a board carrying many more
concerns than that.

⚠ **AND "PENDING" IS SPOKEN FOR.** `JobCardConcern.status` is
PENDING / WORKING / FIXED, and this box deliberately lists **both** unfixed
states — the red disc and the amber clock. Naming the section for one of the
two statuses it contains is the "ONE WORD, ONE MEANING" rule broken on the
page that draws the distinction. "Still to do" covers both, and covers the
"All concerns fixed" car too, which is itself an action: nobody has closed it.

**Mechanics are PANELS — two to a row from 800px up, one below it.** A bare
column with a rule beside it read as clutter: a rule is only as tall as its column, so three mechanics
holding three, two and one car drew three vertical lines of three different
lengths. **A filled panel has no length to disagree about.**

⚠ **THEY USED TO READ FOUR ACROSS ON A LAPTOP, AND THAT WENT WHEN THE BOARD
STARTED CARRYING THE WORK ITSELF (2026-09-02).** The rhythm — four names to a
row, three on a tablet, two on a phone — was an explicit owner instruction, so
it is recorded here as reversed rather than quietly dropped. What overruled it
is that concern text is a customer's own SENTENCE and the grid had nowhere to
put one: measured off this page's box model, `.main-content` caps at 800px →
768px of content → `.lr-box` inner 739px → four columns at 177px → `.lr-crew`
161px → inside `.lr-car`, past its border, its 6px rail and 9px of body
padding, **135px of text width**. "Wheel alignment and balancing required"
wraps to two lines there, three concerns make a wall of narrow text, and every
panel is a different height again — the thing the paragraph above records
having already fixed once.

**TWO is where it settled, and two is as far as it goes.** Measured with every
car on the demo floor filled from the workshop's own concern list, 25 rows:

| columns | panel | the box | rows that wrap |
|---|---|---|---|
| 1 | 739px | 1747px | 0 of 25 |
| **2** | **364px** | **1048px** | **0 of 25** |
| 3 | 240px | 865px | 13 of 25 |

Two costs **nothing** — not one concern wraps — and takes 700px of scrolling
off a board somebody reads standing up. The longest concern the master list
holds is "Wheel alignment and balancing required", 252px rendered against
301px of text width in a two-column panel: 49px spare.

⚠ **The breakpoint is the app's own 800, not the width where it actually
breaks.** Going down at two columns: 768px still clean, 700px wraps one row,
640px wraps four — so anything in 768–800 is safe, and 800 is already in the
system (`.main-content`'s max-width, and the card-list grid's top
breakpoint). **There is no separate laptop and tablet answer here**: the
container stops growing at 800, so at 1280, 1024 and 820 the panel is the
identical 364px.

**EVERY CAR CARRIES THE CONCERNS STILL OPEN ON IT — this box is where the next
instruction is given, not just a list of who is holding what.** The owner's
workflow in their own words: *finish this car's vibration, then tell him the
periodic service because those parts are here, then move him to his second
car.* Only Office and the owners command that work — they are the ones
tracking which parts have arrived — so until the board carried the work list
they were holding the whole floor's in their heads, opening one job card at a
time. **The CONCERN is the row and the car is only its heading.**

Six rules:
- **UNFIXED only, and the fixed ones are COUNTED** ("3 done"). A finished job
  is not a decision anybody has left to make, and the count is what says how
  close the car is to being closed.
- **WORKING sorts above PENDING inside a car** — what the mechanic is on right
  now, then what is queued behind it. That is the order the sentence is spoken
  in. Amber; PENDING is red.

  ⚠ **EVERY ROW IS THE SAME WEIGHT, and the under-way one was BOLD for a
  revision** (removed 2026-09-02, the owner's call). The clock already differs
  from the disc in shape *and* in colour, so bold was a third telling of one
  bit — in the loudest treatment on the block, spent on something two marks
  6px away had already said. `.lr-concern--working` now carries **no
  declaration at all**; it stays in the markup as the state's name in the DOM
  and is what the test reads. Do not sweep it as dead CSS — there is no CSS to
  sweep.
- **The traffic light loses its third lamp.** Green never appears on a concern
  row, because a fixed concern is not listed.

  ⚠ **THE TWO MARKS ARE DIFFERENT SHAPES, AND THE PAIR IS COPIED FROM
  `jobcard_detail.html` RATHER THAN INVENTED** — `bi-clock-history` at
  `#d97706` for under way, a 9px `#dc2626` disc for not started, character for
  character the values `.dv-ico-going` and `.dv-ico-pending` already carry.
  That page is what every row on this board OPENS, so a concern that looked
  one way here and another way one tap later would read as two different
  states. Shape rather than colour alone is also what makes the state survive
  greyscale. Both marks sit in one 15px box (`.lr-concern-mark`), or a 9px
  disc and a 13px glyph start their text 4px apart and the list has a ragged
  left edge.
- **A car whose every concern is fixed says "All concerns fixed"** — that is
  itself an action, since nobody has closed the card — while a car with **no
  concerns at all says nothing**. Nobody wrote one down is a different fact
  from every one being fixed.
- **`FLOOR_CONCERN_ROW_CAP` is 8 and names its remainder.** It happens to equal
  `UNFILLED_ROW_CAP`; they are two rules, not one. Every row here is a decision
  an owner is about to make, so the cap is a guard against one card flooding
  the board, never a window.
- **It costs no query per car** — `prefetch_related('concerns')` on the floor
  queryset, split in Python by `_attach_floor_concerns`. Asserted as the
  invariant (one car and five cars cost the same), never as a magic number.

⚠ **`.lr-car-body` needs `flex: 1`, and that is load-bearing rather than
tidying.** A flex item with no grow sizes to max-content, so the body used to
be exactly as wide as its longest line — fine while that was the car's name,
and a ragged edge the moment the concern block's dashed rule started stopping
wherever the longest sentence happened to end. Measured: nine cars, nine rules,
all 697px.

⚠ **NO PARTS-READINESS CHIP ON THE CAR, on the owner's decision (2026-09-02).**
It was offered and declined: a per-car chip reading *Ready / N on the way / N
not ordered*, cut from the same rows the two containers below already list, so
the owner would not have to join the parts state to the car in their head. The
owner's call is concerns only. **If it is revisited, note the limit that made
it car-level in the first place**: `JobCardConcern` carries no link to a
`JobCardSpareItem`, so nothing in the schema can say which part belongs to
which concern, and adding one means a field Floor has to fill on every spare
row.

**Only a SHOP part is ever chased.** A warehouse draw came off the shelf already
fitted, so its `status` column means nothing; listing one as waiting would send
somebody after a part that is already on the car. Rows on a completed or deleted card
are out too, as are spares with no job card — every row here opens a job card.

**"RECEIVED (LAST 5 DAYS)" IS THE ONE BOX ON THE PAGE THAT IS NOT A LIST
OF WORK.** Shop parts received in the last `RECEIVED_WINDOW_DAYS`, green,
sitting above "On the way" — so the three parts boxes run green, amber, red
down the page: the lifecycle backwards, most-finished first, which is the order
the two that were there already established.

Everything else on this page is something to act on — fill this in, give this
instruction, chase this, order this. **A part that has arrived needs nothing
done to it**, and most of what this box shows is already on the car.

⚠ **IT IS BUILT EXACTLY LIKE THE TWO BOXES BELOW IT — same head, same row, NO
SUBTITLE — and that is the owner's instruction rather than a default.** It
shipped for one revision with a note under the heading (*"Nothing to chase here
— most of these are already on the car"*) and a per-row arrival age, on the
reasoning that a reference list drawn like four action lists reads as a fifth
thing to worry about. The owner's call is that **the headline carries it**: the
window is said once, in the heading, and one shape across the three parts boxes
beats three shapes explaining themselves. The heading interpolates
`RECEIVED_WINDOW_DAYS`, so the number on screen cannot drift from the number
enforced.
→ `test_its_rows_are_built_exactly_like_the_two_boxes_below_it` asserts the row
shape against "On the way" rather than against a list of class names, so the
age chip cannot come back by accident.

⚠ **THE WINDOW IS LOAD-BEARING, NOT A TIDY-UP.** Nearly every shop spare on a
live card is already RECEIVED — **43 of 45** on the development data — so
unwindowed this box would be longer than the rest of the page put together.
**5 days is the owner's own number** and the reasoning is theirs: arrivals are
tracked physically or the mechanic says so, and this exists only for looking
one up again afterwards. Long enough to be useful, short enough to still be
news.

Two details. It is the only parts box ordered **newest first**, because it is
not a queue to work down. And a RECEIVED row with **no `received_date` simply
falls outside the window** rather than being special-cased: nothing can say when
it arrived, so nothing here can honestly report it.

⚠ A missing shop is **not** called out here the way the amber box calls it out.
There it means the ledger has nowhere to land on a part still outstanding; here
the part has arrived and the box asks for nothing.

⚠ **A PARTS-BOX VARIANT IS FIVE RULES, NOT ONE.** Square corners
(`border-radius: 0`, one shared rule naming every variant), the title colour,
the count pill, the row hairline and the row hover. The green box shipped for a
revision carrying only the background and border — so it was **rounded where
its neighbours are square**, its rows had no separators, and its heading and
count rendered in the default slate while amber's and red's are coloured. It
read as a different KIND of object on a page whose whole point is that the
colour is the first thing the eye lands on. Nothing in the Django suite
executes CSS, so the declarations are asserted directly.
→ `WhatLandedRecentlyIsListedApartTests`,
`test_it_is_drawn_as_the_same_kind_of_box_as_its_neighbours`

**The live-details card is FOUR sections** — Customer Concerns, Job Performed,
Inventory Items, Spare Parts — in the order the work happens. The last two used to be
one "Parts" list, and splitting them is what makes the badges mean something: only a
bought-in part has an ordering state anyone can act on. So two sections carry a badge
and two carry a bullet, which is the honest split rather than an inconsistency. **The
printed invoice still merges both routes into one PART NAME list** — a customer has no
interest in which shelf a part came off, an owner reading the floor does.

**An empty section is omitted entirely** rather than printing "none" — four headings
with two apologies under them, on every card, is noise multiplied by the length of
the list.

**There is ONE age wording on the page** — `New`, `1d`, `213d`, from `_age_label()`.
There were briefly two, and the same fact worded two ways on one screen invites being
read as two different facts. Day zero is **New**, not "Today", because the line
answers how long the car has been here.

**In the lists the STATUS leads the row and the wording follows.** What is scanned is
state, not prose: a column of badges all starting at the same x reads in one sweep.
**`.status-badge`'s `min-width` is what holds that column straight.**

**The badges are ONE traffic light** — red not started, amber under way, green done.
`.status-working` and `.status-ordered` therefore **share a single declaration**, as
do `.status-fixed` and `.status-received`: each pair means the same thing about a
different kind of row, and two hand-written ambers would drift apart.

**"Not assigned" is RED** in both halves of the page, because it is the one label
asking for a decision, and a colour that meant urgent above and neutral below would
mean nothing.

**A capped section names its remainder, and the cap lives in the VIEW.**
`HOME_SECTION_ROW_CAP` (25) and `UNFILLED_ROW_CAP` (8), both through one shared
`_capped()`.

⚠ **Never `|slice:":10"` in the template**: a cap in the markup and a remainder
computed from a constant are two versions of one rule, free to disagree — and they
would disagree as a "+3 more" beside eleven visible rows.

⚠ **The two boards were briefly two `_capped()` functions of the same name in one
module, and the later silently shadowed the earlier** — so the home board capped at
the Live Report's 10 while every comment said 25, with nothing on screen to show it,
because the remainder line stayed arithmetically correct. One function, an explicit
cap per call site.
→ `test_the_two_boards_do_not_share_one_cap_by_accident`

Capping is safe on these lists and would not be on a money list: no total sits above
the rows for the hidden ones to fall out of, the exact number left is printed rather
than implied, the section heading still reports the true total, and every hidden row
is on the job card the row already opens.

## Car Profiles

**THE LIST LEADS WITH THE MOST RECENT ACTIVITY, not the most recent
ADMISSION.** It ordered on `Max(admitted_date)` alone, so a car admitted in
June, finished in July and settled in August sat below one admitted in July and
untouched since. Everything that happens to a car after it arrives — being
completed, being settled — is activity, and the list an owner opens to find
*the car we were just dealing with* has to say so. `last_activity` is
`Greatest` over the three date columns.

⚠ **EVERY ARGUMENT TO `Greatest` IS COALESCED, and that is a cross-database
correctness matter rather than tidiness.** On PostgreSQL `GREATEST` ignores
NULLs and returns the largest non-null; on **SQLite — which the whole test suite
runs on — it returns NULL if ANY argument is null.** A car with no
`completed_date` would sort correctly in production and drop out of the ordering
entirely under test, or the reverse. `admitted_date` is non-null on every card,
so it is the floor under all three.

⚠ **`TruncDate`, never `Cast(… DateField)`, for `paid_date`.** It is the one
DateTimeField of the three and it is stored UTC, so a cast takes the UTC
calendar day — which for anything settled after 18:30 IST is **yesterday**.
`TruncDate` converts to `TIME_ZONE` first, the same thing a `__date` lookup
does.

**`-latest_id` breaks ties**, because most cars share a date with several others
and the order inside a day would otherwise be whatever the database returns —
which differs between PostgreSQL and SQLite, so the list would not even be
stable between production and the tests. Same lesson the Completed list learned.

**The card prints the date it is SORTED by**, not the admitted date. Printing
one beside an ordering by the other puts the dates on screen out of order, which
reads as a broken list rather than as two different facts.
→ `CarProfilesLeadWithTheMostRecentActivityTests`

**The totals come from the DATABASE, not the page.** A single aggregate over the
whole history — with a pager, anything summed from the page would quietly start
describing "this page" while labelled "this car". **"Billed to date" is
`total_bill_amount − discount_amount`, the Profit page's own definition of revenue**,
because a second definition of "what this customer has paid us" is the one an owner
would end up quoting at the counter.

**The headline figure is "Total billed"** — deliberately not "Total spent", which is
the customer's side of the same number and is wrong on exactly the cars that matter,
since an unpaid bill has been billed and not spent. When there is an unpaid part the
"Still owed" tile appears beside it.

**The header is one row from 768px up, two rows below it.** On a phone the title and
a search box with a five-word placeholder compete for ~360px and both lose; above
768px there is room for both, and giving the search its own line there would push the
first card down for nothing.

**The search box is deliberately the SAME control as Completed's** — the values are
copied, not approximated. Those two pages are opened one after the other all day and
a search box that changes shape between them reads as two different products. If
Completed is restyled, restyle this with it.
→ `TheSearchLooksLikeCompletedsTests` fails either way round.

**Three things the page deliberately does NOT carry:**
- **The colour is worn, not written** — "Red" printed beside a red bar is the same
  fact twice.
- **No first-concern preview in each visit row** — it was the only free-text line in
  the list, so it made every row a different height, and a history is scanned for
  *when* and *how much*.
- **No Invoice button on a visit row** — the job card it opens carries its own, so
  it was a second door to the same place, costing a column of width on a phone and
  needing its own z-index to stay clickable above the row-wide link.

**A VISIT ROW IS TWO LINES AND THREE TYPE TIERS — anchor, fact, quiet.** It had
**six font sizes inside a 4.3px range** (10.08 / 10.88 / 11.2 / 11.84 / 13.76 /
14.4px) across four weights, with ten separate numbers in it. Six sizes that
close is not a hierarchy; it is six things asking for the same glance. Same
failure the notification feed records fixing — "three sizes within 2px of each
other, which is no hierarchy at all" — at twice the count and twice the range.

| tier | | carries |
|---|---|---|
| **anchor** | 0.92rem / 700 / dark | the DATE, and the AMOUNT |
| **fact** | 0.76rem / 500 / muted | the detail line, and the stay |
| **quiet** | 0.66rem | the badge, the #N tile, the margin |

⚠ **THE TWO ANCHORS ARE IDENTICAL, NOT NEARLY IDENTICAL.** They were 14.4/700
against 13.76/800 — two-thirds of a pixel and one weight step apart, which is the
worst kind of difference: visibly not the same, with nothing said by the
difference. Matched, they read as one pair spanning the row, so the eye crosses
left-to-right in one move. The phone override that shrank only the amount to
0.86rem is gone for the same reason. **Adding a fourth size is how the six came
back last time.**

**THE DATE LEADS THE ROW AND THE BILL NUMBER DOES NOT.** `bill_number` is what
the workshop reads out on the phone — a lookup key, not a scan key — and it was
drawn as the headline in the largest type while the DATE sat in the *quietest*,
so reading a car's history meant landing on the one string you were not looking
for, four rows running. The anchor line is now **when · how long · what state ·
how much**, the four things this list is actually scanned for; the bill number,
mechanic and mileage drop to the detail line, read once you have found the row.
The link moved onto the date and carries an `aria-label` naming the card, since
"12 Aug 2026" alone is thin link text.

**HOW LONG THE CAR WAS HERE sits beside the day it arrived, and the words come
from `_time_in_workshop()`** — imported from `views/jobcard.py`, never restated.
The read-only card prints the same figure and the two screens are opened seconds
apart on one card, so a second copy of that subtraction would be free to
disagree exactly there. It brings four edge cases with it: no admitted date, a
completion dated *before* the admission (prints nothing, never "−3 days"), the
singular, and an OPEN card counting to `localdate()` rather than a UTC today.

It carries a **clock glyph, not a middot**: "2 days" dropped into a run of facts
reads as "2 days ago", which on an old visit is a wildly different number. A real
element, never an icon-font codepoint — a stylesheet that failed to arrive would
otherwise take the meaning with it. An open card reads "12 days in" in the amber
the "On the floor" badge beside it already wears, with **no transition**, the
status-colour rule.

⚠ **THE DETAIL LINE'S SEPARATORS ARE DRAWN AS TRAILING `::after` MARKS, and that
is a wrap fix rather than a style choice.** Written into the markup as a "· "
PREFIX the middot travels with the item after it, so the moment the line wraps
the new line OPENS with a separator and the fact reads as a fragment that fell
off. Measured at 320px, and at 375–412px while the stay was still on that line.
Trailing, the middot stays at the end of the line it belongs to, where it reads
as "continues below". `:not(:last-child)`, not `+ span::before`: the template
renders no span for a value it does not have, so a stray separator is not
expressible either way — but only the trailing form also survives a wrap.

Measured at 390px after the pass: **3 sizes, 2 weights, rows 88px → 68px**, no
wrap at 375 or above, nothing hidden and nothing removed. A car on the floor is
the one row that still takes a third line, for its two badges — the exception
that deserves the space.
→ `EveryVisitSaysHowLongTheCarWasHereTests`

**The car wears its own colour — the SAME wash `.lr-car` uses, at the identical
alpha.** Copying the alpha rather than picking a new one is the point: a car you can
see has to look the same on every screen that shows it. One extra rule the Live Report
does not need: the hero's stat tiles sit *on* the wash, so they carry their own
`rgba(255,255,255,.72)` ground or they take the tint twice.

**Tile widths are PROPORTIONAL, and the two fixed ones are fixed for a reason**: a
visit count and a date cannot vary in width, so they get fixed widths and stop taking
a money-sized box for a small fact. The money tiles flex because their width *is* a
function of the data. Sizing every tile to its contents is the tempting version and
the wrong one — a car billed ₹500 and one billed ₹1,25,000 would lay the row out
differently, so the boxes move between cars.

⚠ **The list template must read the context name the view actually passes.** It read
`search_query`, a name this view has never passed — so the search box came back empty
after every search *and* the pagination links carried the same dead name, meaning page
2 of a search silently returned page 2 of every car in the workshop.

## Read-only job card

**DATA WITH NO LABELS.** The layout the owner drew, rebuilt 2026-08-28 into
**one answer card** over the four lists:

```
🟩 Audi A4                                              ⋮
   [KL 10 AA 1000]  (JB-26-001)
   10021 km · 👤 Amlah
   ( customer name   contact )
   note
   ₹22,000                                          [ PAID ]
   ─────────────────────────────────────────────────────────
   ADMITTED        COMPLETED        SETTLED
   01/01/2026      20/01/2026       09/03/2026
   🛡 Settled — locked against editing

Customer Concerns | Job Performed | Inventory Items | Spare Parts
```

**ONE CARD WHERE THERE WERE THREE.** Identity, a date card, and a money line at
the very foot of the page — each with its own border, shadow and radius. On a
375×667 phone the first two alone were 190px before a single concern, and the
total was thirty rows further down: **the most important figure on the page was
the hardest one to reach.** The card now answers *which car, what it costs, where
it is* without scrolling, and the four lists below it are pure detail.

**The row order is the owner's own**, given as a list: car + ⋮ / plate + job card
number / mileage + mechanic / customer / note / money / dates. Three things moved
and each was asked for — the **car gets row 1 to itself** (it used to share the
line with the plate, the dates and two filled buttons, and at 375px it was the
thing that lost); the **job card number** joined row 2, because `bill_number` is
what the workshop reads out on the phone and the one screen dedicated to a single
card never printed it; and the **customer took a line of its own**, because the
row above is about the car, this is about a person, and on a phone the two ran
together and wrapped anyway.

**A car with NO brand or model wears its plate ONCE.** The registration becomes
the row-1 headline in that case, because there has to be something to call the
car, and the chip on row 2 is then dropped rather than repeating it a line
below. The job card number still prints — that is a different fact. It is the
money line's own rule applied to the identity.

**THREE DATES — `admitted_date`, `completed_date`, `paid_date`.** All three are
always drawn, with a dash where nothing has happened yet: a fixed structure is
what makes the page learnable, the same rule that keeps an empty section drawn
rather than omitted, and a column that came and went would move the other two
between one card and the next.

They are **LABELLED, while nothing else on the page is, and that is not an
inconsistency.** Every other unlabelled value here is unambiguous because it is
the only one of its kind on its line; three dates of the same shape side by side
are the one place *position carries the meaning* could not carry it. They were a
bare range in the heading before this, which said neither which was which nor
that a third existed.

⚠ **THE THIRD ONE IS "SETTLED", NEVER "BILLED".** `paid_date` is written when the
money is taken, and *billed* already means the opposite thing one screen over —
Deep Analysis calls a Supplies Shop purchase "billed" precisely BECAUSE it is not
yet a cost. There is no bill-issue date to point at either: `bill_number` is
assigned on the card's first save, so a "Billed" stage would either restate the
admitted date or quietly mean settled.

⚠ **DO NOT "MEASURE" THIS DECISION AGAINST SEEDED DATA — the answer is baked in.**
The settled column was dropped for a day on the evidence that 149 of 150 settled
demo cards were settled on their completion day. That figure described the
seeder, not the workshop: **all three seeders write `paid_date` from
`completed_date`** (`seed_dummy_data.py:680` and `:725`, `seed_meeting_data.py:429`),
the fleet path included. The real basis is the business rule — a **walk-in** has
exactly one payment event and it happens at pickup, so settled repeats the
handover day; a **fleet** collector comes round weeks or months later against
several months of cars, and those are the largest single receipts the workshop
takes. That is the case the column exists for, and it is exactly the case the
seeders flatten.

**THE COUNTER APPEARS ONLY WHILE THE CAR IS STILL HERE.** `_time_in_workshop()`
prints "12 days in" under the dates on an open card, in amber. On a finished card
both dates are printed an inch apart and the subtraction is trivial; on an open
one there is no second date to subtract from, so the counter is **the only way to
know** — that is the rule, not decoration that happens to be hidden sometimes.
The view owns the words: a template cannot get "Same day" and "1 day" right, and
the singular is exactly the case a naive `{{ n }} days` gets wrong on the
commonest short job there is. A completion dated **before** the admission prints
no gap at all — "−3 days" would make the page look like the broken thing rather
than the data, and all three real dates still show, so the mistake stays visible.

**Every date is read from its OWN column**, never inferred from the one before
it, so a card that reached a state out of order — a fleet card settled before
anybody pressed Completed — still prints honestly.

**EVERY SECTION CARRIES ITS OWN SUBTOTAL, and the three of them add up to the
bill EXACTLY.** `update_totals()` is `Σ spares.total_price + labour_amount` over
both routes, so Job Performed + Inventory Items + Spare Parts total the figure in
the answer card. That is the whole optimisation: the bill stops being something
you take on trust. It costs **no query** — both parts figures are summed in the
view off the very rows printed underneath, because a second aggregate is free to
disagree with the rows above it.

Three things travel with it: **labour sits in the section HEADING**, never on a
job line, because labour is one charge on the card and a figure beside each
description invites a line-by-line negotiation about work quoted whole; the
spares subtotal is **`total_price`, the customer side**, since totalling
`unit_price` would put a figure in the heading the bill does not contain; and a
section worth nothing **prints no zero**.

⚠ **EVERY ROW IN ALL FOUR LISTS CARRIES A STATUS MARK, and this reverses what
this file said for one revision.** The tick was pulled from Job Performed and
Inventory Items on the argument that a mark hard-coded to green says nothing —
a job line has no state to be in, and a warehouse draw came off the shelf
already fitted, so its `status` column is meaningless. **The owner's call
overruled it**, and the reason is the one the argument missed: the mark is not
only a STATUS, it is the row's **left anchor**. Without it, Job Performed read
as a bare wall of sentences rather than a list of things that were done, and
the two lists that kept theirs no longer lined up with the two that had lost
them.

**What survives from that pass is the WEIGHT, not the removal.** The tick was a
saturated `#16a34a` at 1.02rem and was the loudest thing on every row — nine of
them on an ordinary card, so a list of twenty read as twenty alarms. It is a
lighter green at 0.9rem now, while the two marks that DO want attention keep
their full strength: **red not started, amber under way**. The traffic light
still means exactly what it means on the Live Report and in the dashboard
drawer; only the one you expect to see is quiet.
→ `test_every_row_in_all_four_lists_carries_a_status_mark`,
`test_the_expected_mark_is_quieter_than_the_two_that_want_attention`

**THE PRICE IS GREEN AND ALONE ON THE ROW'S FIRST LINE; THE COST DROPS TO THE
SECOND.** They sat side by side with a dash between them, and five rows of
"₹1,000 – ₹1,500" read as five **ranges** rather than five prices — two numbers
competing where one is what the customer was charged and the other is the
workshop's own side. The dash went with it; a range was never what it meant.

Green because this is money **in** — the Profit page's own rule and the same
`--color-success`.

⚠ **THE BILL IS GREEN TOO, and it took a correction to get there.** It was left
dark on the reasoning that the Profit page keeps Gross Earnings uncoloured
because it is a structural waypoint — but that mapped the wrong figure. On
that page the HERO is green and the intermediate waypoint is not; here the hero
is the bill and the waypoints are the three **section subtotals**. A dark bill
over green rows had it exactly inverted, so a settled card printed the amount
actually taken in the one colour on the page that does not mean money. The
subtotals stay dark, because with green above and below them there would be
nothing for the eye to land on.

Green means money in **paid or not** — revenue is earned rather than received,
and whether it has arrived is what the state chip and `.dv-owed` are for.
→ `test_money_in_is_green_and_the_waypoint_between_is_not` The cost sits on the **second grid row of the money column**,
opposite the dates, so it costs no height at all on a row that already has a meta
line — which is nearly all of them. Measured: nine prices right-aligning on one
column you can run an eye down and add up.

**A PART'S DATE DROPS THE CARD'S OWN YEAR — a width fix with a measurement
behind it, not a formatting preference.** The full pair plus a shop name
("16/07/2026 – 17/07/2026 · Spare club") is 38 characters and **wrapped to two
lines on a 375px phone**, so rows in one list came out different heights and the
list read as broken. Dropping a year already stated twice in the card above takes
it to 30 and it fits; measured after, every meta line is 18px and every spare row
52px.

⚠ **The year is KEPT the moment it differs**, because then it is the whole point:
a part ordered in December for a car admitted in January is the one case where
the reader must not have to assume. Each half is compared **separately**, so a
pair straddling New Year prints one short and one long rather than hiding the
crossing. With no card year to compare against, `_short_date` prints in full
rather than guessing.
→ `test_a_mark_that_would_be_the_same_on_every_row_is_not_drawn`,
`test_the_price_is_green_and_the_waypoints_are_not`,
`test_a_part_date_drops_the_cards_own_year_and_keeps_a_different_one`

**THE TWO ACTIONS LIVE IN A ⋮ MENU, and the trigger is small while the items are
not.** They were filled Invoice and Edit buttons pinned to the head's top-right —
the two loudest objects on a screen whose only job is to be read, both of them
leaving it, and ~90px off the car's own name at 375px. The trigger is 32px (34px
under `@media (hover: none)`) because it sits beside a heading and must not
shout; the **menu items are 44px**, because those are what a thumb actually hits.
Both items are **plain links**: a ⋮ elsewhere in this app routinely holds a POST
form (Completed's Undo Completion), and copying that shape in here is the obvious
way this page would stop being read-only.

⚠ **THE WRAPPER NEEDS `display: flex` AND `line-height: 0`, or the ⋮ lands in
the wrong place.** Bootstrap's `.dropdown` is a plain inline box, so inside the
`<h1>` it inherits the heading's 36.8px line-height and the inline-block button
sits on THAT line box's baseline: measured as a 40px wrapper around a 32px
button, the button 8px below the top of its row — and since the strut then sets
the row's height, the corner the button is meant to sit in carried 8px of empty
space under it. Same cause and same fix as the table cell holding an inline-flex
child, recorded under Traps.

A settled card gets a lock glyph on the Edit item — the door is still open, since the form unlocks itself, so it
**annotates rather than disables**.

⚠ **`.dv-head` MUST NOT BE `overflow: hidden`, AND MUST STAY POSITIONED.** Two
halves of one change, and the second cost a real defect. The clip had to go the
moment the head grew a dropdown: a clipping ancestor is the one thing Popper
cannot escape, and it fails invisibly and only sometimes. But the car's colour
rail is an absolutely-positioned `::before` with `inset: 0 auto 0 0`, so it needs
the head as its **containing block** — `position: sticky` was providing that for
free. The card is no longer pinned (it carries the money now, and pinning ~240px
of a 667px phone spends a third of the screen on something already read), and the
first attempt at unpinning used `static`, which is not a positioned value: the
rail measured itself against the VIEWPORT and rendered **812px of car colour down
the left edge of the whole page**. `relative` unpins it and keeps the containing
block. Nothing in the Django suite executes CSS, so this failed silently until it
was measured in a browser.

**THE STATE BANNER IS GONE**, folded into the foot of the answer card.
"Completed" as a full-width amber bar said what a date under the word Completed
says better. What survives is the **lock**, which is not derivable from a date —
and **ON HOLD**, which was nowhere at all: a paused car drew exactly the same page
as a running one, on the one screen that claims to say where the car is.

**There are no labels anywhere else**, and the reasoning is load-bearing: *a
caption is what you need the FIRST time and what costs you every time after*, on
a page four people open twenty times a day. Under a part there is nothing but its
two dates and its two figures.

**A missing value leaves no trace** — no "Not recorded", no dash. The identity line's
facts are separate elements with the separators drawn in CSS
(`.dv-fact + .dv-fact::before`), so a missing value takes its own separator with it
and a stray one is not expressible. **Consequence for tests: line 2 is asserted as a
LIST of values in order, never as one joined string.**

**Everything a PART prints is joined in the view by `_describe_spare()`** — a
template doing it is a chain of `{% if %}`s that has to get every separator right, and
gets it wrong on the row with no shop.

**ONE COLUMN, and every row is a GRID.** The four sections shipped 2×2 and were
straightened out: a 2×2 makes you read in a Z, and the two columns are unrelated lists
of unrelated lengths, so the right-hand one starts wherever the left-hand one happened
to end. One column also buys the thing that fixed the crowding: with the full width, a
row can be **what a part IS on the left, what it COST right-aligned in its own column,
the facts about it quietly underneath**. They used to be one string, where the eye had
to find the ₹ to know where the dates stopped. Right-aligned and `tabular-nums`, the
figures form a line you can run down. **The cost is drawn quieter than the price** —
it is the workshop's own side, and what an owner scans a bill for is what the customer
was charged.

**The mechanic wears the dashboard car card's own `bi-person-gear`**, at its colour
and size — it is the one fact on that line that is a PERSON, and the board people
arrive from already marks it that way.

**The customer's name and number are ONE transparent box**, an outline with no fill,
because they are one thing and the rest of the line is about the car. It is not drawn
at all when there is nothing to put in it, and carries no dot separator of its own — a
box is already a separation.

**The four sections keep the drawer's own values**, copied to the character
(`test_the_row_styling_is_the_drawers_own` compares the two rules), and below 640px
they still shed their boxes. **An empty section is still drawn**, a deliberate
divergence from the drawer: a page whose sections come and go is one you cannot learn.

**Nothing on it posts** — `test_nothing_on_the_page_posts` scopes to `<main>`, because
base.html's logout modal is a real form.

**The money line never prints a figure twice**: with nothing received the balance IS
the bill, and paid in full with no discount the receipt IS the bill, so in each case
the repeat goes and the state chip carries it.

⚠ **`.dv-money` is the footer and `.dv-money-col` is on every part row**, so a test
splitting on the bare string `dv-money` finds a PRICE and asserts about the bill. Match
the exact class attribute.
→ `workshop/tests/test_jobcard_detail_view.py`

## Shop pages & shared list conventions

**A shop header gives up its actions before it gives up its name.** Below 768px the
actions take a row of their own, **aligned right** so the ⋮ still lands in the corner
under the thumb, and the name gets the full width with truncation lifted entirely.
Pinning the actions beside the title made the buttons and the shop NAME compete for one
line and the name lost — cutting off the one piece of text that says what you are
looking at.

**The two payment histories are one screen.** Spare Shops and Supplies Shops share
markup exactly; amounts print green on both. The tests assert the **parity** rather
than either implementation — the failure worth catching is them drifting apart again.

**The Items / Products count leads both shop headers**, in the order the question is
asked — how many things, what they cost, what has been paid, what is left. Both files
in one edit: these two pages are opened one after the other.

**Purchase History carries the same sticky row number the parts tables do**, with two
differences: the number is assigned in the **view** (`item.row_no`), because the
template regroups rows by date and `forloop.counter` would restart at every separator;
and the **date separator row gets its own sticky cell**, or the column has a hole at
every date and the numbers appear to float.

⚠ **A new column means the date separator's `colspan` grows with it**, or the date sits
under a short rule with a gap beside it.

**"Spare Parts" is ONE glyph app-wide: `bi-gear-wide-connected`.** Three symbols had
been meaning spare parts, including `bi-tools` on every Spare Shops page — which is the
**Job Performed** icon, so the section that buys parts wore the icon of the section that
fits them. `bi-tools` now means Jobs/Labour and nothing else.
→ `test_spare_parts_wears_the_same_glyph_everywhere_it_is_named` scans every template
and fails if a second glyph comes back.

**The unassigned-spares add form scrolls sideways at EVERY width, laptop included.** It
used to wrap above 768px and scroll below it — one row of boxes with two shapes,
depending on whether it was opened on the tablet it is filled in on or the laptop it is
checked on. Wrapping was also getting worse rather than better: `.main-content` caps at
800px, so "desktop" here is a 767px column. **Every field carries a fixed width rather
than a flex basis**, or a wide screen stretches the boxes and quietly brings the wrap
back. Its row actions are **two inline buttons rather than a ⋮ menu** — a Bootstrap
dropdown inside a horizontal scroller is clipped, and it is one tap instead of two.

**List/ledger views with a time filter share one calendar-aligned vocabulary**: Today /
This Week / This Month / This Year / Last Week / Last Month / Last Year / Custom. Reuse
this set rather than inventing a rolling `30d`/`365d` window.

**A custom range is PARSED before it reaches the ORM** — `date.fromisoformat()` in a
`try/except ValueError`, ignoring an unusable range rather than filtering by it. Handed
straight to a `__date__gte` lookup, `?start_date=abc` reaches `get_prep_value` and
raises. The pickers are `type="date"`, so this only fires on a crafted URL, but a 500 is
a 500.

**COMPLETED IS NEWEST-FIRST, AND THE TIEBREAKER IS `-id`.** `completed_date` is
a **DateField**, so every car handed over today carries the same value and the
order inside that day was whatever the database returned — which, on the
default `today` filter, is the whole page. The car somebody opened the list to
see could be anywhere in it, so it was found by scrolling.
`order_by('-completed_date', '-id')`.

⚠ **Not `-updated_at`.** It is `auto_now=True`, so an old card edited for an
unrelated reason would jump to the top of today — the exact defect `paid_date`
exists to keep off Paid Bills, one list over. `-id` never moves after creation.
Within a day it orders by newest card rather than by true completion time;
there is no completion timestamp to sort by, and adding one is a migration for
a precision nobody has asked for.
→ `TheCompletedListPutsTheNewestFirstTests`

**A CAR STILL ON THE FLOOR IS NOT A PENDING BILL.** `pending_payments_list`
filters `completed=True`. A card is `PENDING` from the moment it is created, so
every live card sat in the chase list: nothing about them is chaseable — no
figure is final and no bill was handed to anybody — and they buried the bills
that are. Nothing is stranded, which is what makes this safe against the
money-owed-is-always-reachable rule: a live card is on the dashboard board the
whole time it is on the floor, and it joins this list the moment it is marked
completed.

⚠ **CONSEQUENCE: `total_outstanding` is deliberately SMALLER than the Profit
page's "Customers owe us".** That figure counts every unsettled card, fleet and
still-on-the-floor included; this page excluded fleet already and now excludes
live cards too. The subtitle under the title — *"Handed over and not yet
settled"* — is what stops the total quietly meaning something new. Don't
"reconcile" the two by widening either: they answer different questions.
→ `PendingBillsListsOnlyHandedOverCarsTests`

**List views paginate at 45 items/page** (10 for inventory category grids) and use
`select_related`/`prefetch_related`.

**Use `timezone.localdate()`, never `date.today()`**, for any "today"/date-range logic
— the server can run in UTC while the business operates in IST (`TIME_ZONE =
'Asia/Kolkata'`), and `date.today()` silently returns the wrong calendar day near
midnight IST. The same rule with the same reason covers `timezone.localtime()` in
place of a bare `datetime.now()`, and it reaches past views: a **management command**
is the easy miss, because it is written and read on an IST laptop where the two agree.
`backup_db` stamped its filenames with `datetime.now()` for months, so on Railway a
backup taken at 02:00 on a Kerala morning was filed under the PREVIOUS day — noticed
only on the day somebody picks a file by eye out of fourteen and needs "yesterday" to
mean yesterday.

**Two things that look like the same defect and are not.** `timezone.now()` is
correct wherever a `DateTimeField` is being *set* — it is UTC-aware and Django
localises it on the way out. And an ORM `__date` lookup is already IST: Django
converts to `TIME_ZONE` in SQL, so `created_at__date` picks the right calendar day.
Where `created_at` is wrong it is wrong for a *business* reason — it records the
keystroke rather than the day the money moved — never a timezone one. **AWS SigV4
signing in `photos.py` must stay UTC**; that is protocol, not display.

**Never pass template variables through `|safe`**; use `json_script` to hand data to JS.

## Outcome sounds

**Five synthesised tones, riding on Django's message tags, wired nowhere else.**
`data-sound-tag` on the message banner. The app already tags every outcome, so one
attribute covers every action in the system and anything added later.

⚠ **Do not wire per-button sounds:** ~180 call sites is 180 chances to attach the wrong
tone, and each would fire at *click* time, announcing "done" before the server had done
anything.

**`info`/`debug` are deliberately silent** — a tone for every notice trains everyone to
stop hearing the two that matter.

**Web Audio oscillators, no audio files and no dependency.** Per-device toggle in the
drawer (`localStorage`, default ON).

**The printed invoice and estimate are standalone templates and had to be given the tag
and the script explicitly**, or the one page where money is actually settled would be
the one page that stayed silent.

**Browsers block audio on a freshly loaded page without user activation**, which Chrome
**exempts for an installed PWA** — so it is reliable on the owners' phones and the Floor
tablet, and the first outcome in a plain browser tab may be silent. That is a missing
nicety, never a missing fact: the banner is on screen either way.

**A fourth tone, `prompt`, rides the three ways this app asks a question** — a Bootstrap
modal (`show.bs.modal`, which bubbles, so one document listener catches every one), a
native `<dialog>` (no bubbling open event, so `showModal` is wrapped once on the
prototype), and plain **`window.confirm()`**, wrapped the same way.

⚠ **The third was missed for a day and it was close to half the sites.** The `confirm()`
sites are thirteen calls across eleven templates, most of them an
`onsubmit="return confirm(…)"` attribute — nothing about that markup looks like a
dialog needing wiring — against seventeen of the other two kinds (fourteen
`data-bs-toggle="modal"` triggers and three `showModal()` calls, all three of those on
the invoice).
→ `test_every_way_the_app_asks_a_question_is_hooked` scans the templates for all three
shapes and fails if sound.js does not hook one it finds, because a *missing* hook is
invisible to every other kind of test.

⚠ **One trap in the wrapper:** `window.confirm()` **freezes the main thread** until it is
answered, so `play()`'s usual `resume().then(tone)` path cannot settle and the beep would
arrive *after* the decision, where it reads as the outcome sound for it.
`play(kind, blocking)` therefore resumes for next time and stays quiet now. The native
return value is passed straight through.

**It is gated to questions.** A plain "add a payment" form modal is a *workspace* and
stays silent — a tone every time a modal opened is noise, and noise is how the tones that
matter stop being heard.

*Bonus worth knowing:* the prompt fires on a real click, so it is never blocked by the
autoplay policy and it warms the AudioContext, which makes the *outcome* tone after a
confirmed action audible even in a plain browser tab.

**A fifth tone, `shutter`, rides neither the message tags nor a question.** It fires
directly from `photos.js`'s capture handler — the same direct-call shape already used
there for the upload-failure `error` tone — and plays once per frame of a burst on
purpose, which none of the other four do. See Photos, "the shutter click is not an
outcome tone," for why that is safe here when the identical idea (a `success` chime
per shot) was rejected for the outcome tones.

## App icons & PWA

**Every app icon is GENERATED from one file** — `static/images/icons/app_icon_source.png`
→ `scratchpad/build_app_icons.py`. None is hand-edited; a new mark means replacing the
source and re-running the script. Two things it does that a resize would not:

- **It crops to the ink first.** The supplied file sits in a lot of empty canvas, and
  scaled as-is to 32px the mark would be a dozen pixels adrift in a white square.
- **It pads by purpose.** The 192 and 512 are declared `"purpose": "any maskable"`, so
  Android crops them to the launcher's shape and only the central 80% is guaranteed —
  those get the mark at **76%**. A favicon is never masked and is fighting for legibility
  at 16px, so it gets **92%**; apple-touch sits between at 84%.

⚠ **Do not show a maskable icon raw.** The PWA install banner used `icon-192.png` in a
42px box and rendered a small mark adrift in white; it uses `icon-180.png`, the un-inset
one.

The background is forced to pure white — a 253-grey square is visible as a faint box
against a white browser tab. **Re-run `collectstatic` after regenerating**: the filenames
do not change, but the content hashes do.
---

# Traps that cost hours

Each of these failed **silently** — no exception, no console error, a green test
suite. They are recorded so the next person does not have to rediscover them.

## Django

**Django overwrites an inherited `get_<field>_display`, silently.**
`Field.contribute_to_class` guards its generated accessor with
`"get_%s_display" % self.name not in cls.__dict__` — the class's **own** dict,
never its bases, expressly so a subclass can override inherited choices. So
`CarColourMixin.get_car_color_display` was replaced by Django's partialmethod on
both models and nothing raised: `car_color='Other'` started reading back the
literal word "Other", and an unset colour read `''` instead of "Unknown". Each
model therefore repeats `get_car_color_display = CarColourMixin.get_car_color_display`
in its own body — one line, implementation still shared. `get_car_color_hex` has no
such clash and inherits normally.
→ `test_the_estimate_and_the_job_card_agree_on_every_colour`

**`form.is_valid()` mutates the bound instance** in `_post_clean()`, so an "old
name" read after validation is already the new one. Any preview or comparison
against the stored value must run **before** `is_valid()`.

**A model's `unique=True` fires before the view runs**, rejecting the very rename
that merges a duplicate. `CarBrandForm.validate_unique` skips `name` on edit only.

**A form-level `__iexact` dedupe has to be create-only**, or it blocks every merge.

**`add_error` removes the field from `cleaned_data`**, so `_post_clean()` no longer
overwrites the stored value with the posted one — which is why a refused saved row
names itself in the error summary instead of falling back to "row 1". The reverse
of the `_post_clean` trap above, and useful.

**`BaseModelFormSet.clean()` caches `deleted_forms`.** It calls `validate_unique()`,
which reads `self.deleted_forms`, and that property caches its answer in
`_deleted_form_indexes` on first access. Marking rows DELETE after `super().clean()`
marks them too late.

**An absent field behaves in OPPOSITE ways on a ModelForm and a formset.** On a
ModelForm, omitting it leaves the stored value alone. In a formset, an absent field
**saves as blank and wipes the row.** This asymmetry decides several rules in this
codebase — it is why Floor's price inputs are rendered inside `d-none` rather than
dropped, and why the customer fields *can* simply not be rendered.

**`.update()` fires no signals.** Safe for rows that move no stock; not safe
otherwise. Views resolving a spare's shop with `.update()` must do the
model-`save()` bookkeeping themselves.

**`default=timezone.now` on a `DateField` is safe under a non-UTC `TIME_ZONE`** —
`DateField.to_python` converts the aware datetime before taking `.date()`, so it
lands on the correct IST calendar day.

**Django's `{# … #}` comment is single-line only.** See UI conventions.

**A TEMPLATE TAG TYPED INSIDE A `<script>` IS STILL A TEMPLATE TAG.** Django's
parser knows nothing about script elements, so `{% block content %}` written
into a JavaScript comment *to explain a bug* produced a second block of that
name and a `TemplateSyntaxError` on the whole page. Same family as the `{# … #}`
trap: a comment that stops being a comment. Describe a tag in prose ("the
content block"), never by quoting it.

## CSS & Bootstrap

**A running CSS TRANSITION outranks `!important` — it is the highest origin in the
cascade, above important-author.** `.form-control` transitions `background-color`
and `border-color`, so inspecting a locked card anywhere that is not painting
frames (a background tab, a headless snapshot, a screenshot tool) reads those two
properties as the LIVE colours while `color` and `cursor` — not transitioned — read
as the locked ones. It looks exactly like an `!important` rule losing to nothing at
all.
→ Set `element.style.transition = 'none'` and re-read. **The wrong fix is a more
specific duplicate rule** — that is a second copy of a palette, free to disagree.
This applies to any measurement of a transitioned property on this codebase's forms.

**A status colour must never animate**, for the same reason: the colour IS the
state, so while a transition is in flight the computed background is the OLD colour
whatever the rule says. The four spare-status classes carry `transition: none`,
scoped to the classes rather than the control so a select moving between two of them
matches on both sides and cannot animate in either direction.

**AN `!important` BOOTSTRAP UTILITY BEATS A NORMAL INLINE STYLE, so painting an
element from `el.style.*` in script can render nothing at all.** Inline styles
outrank stylesheet rules — but only *normal* ones; an important-author
declaration wins over an inline declaration that is not itself `!important`.
Bootstrap's utilities are all `!important`, so on an input carrying `bg-light`
and `border-0`, setting `el.style.borderColor` and `el.style.background`
changed `el.style.*` and painted **nothing**: computed `0px none` on the border
and the unchanged grey behind it. Note `border-0` zeroes the WIDTH, so a colour
alone could never have shown even without the specificity problem.

⚠ **The reason it survived a browser check is the check.** Reading back
`el.style.borderColor` returns the amber that was just assigned and says
nothing about what rendered — the same shape of mistake as measuring a
transitioned property mid-flight. **Assert on `getComputedStyle`, and disable
the transition first** (`.form-control` transitions both of these). The fix is a
real scoped class carrying `!important`, which is what the spare shop's
`.ssp-datebox.is-custom` was already doing.
→ `test_the_amber_state_can_actually_beat_bootstrap`

**TWO TEMPLATES CAN CLAIM THE SAME CLASS NAME, AND THE SECOND ONE INHERITS
WHATEVER THE FIRST DECLARED.** Page-scoped CSS in this codebase is only scoped
by *convention* — a `<style>` block in a child template is served on that page
and applies to every element on it, `base.html`'s included. The notification
row's headline was `.nf-head`; so was the notification **page's** header block,
declared in `notification_list.html` with `margin-bottom: 18px`. Every row on
the feed therefore carried 18px of margin nothing intended, and no row in the
panel did — measured as a 40.3px headline sitting inside a 58.3px line, on one
of the two surfaces only.

It fails in exactly the way that costs hours: no error, no console warning, the
page looks *nearly* right, and **nothing in the Django suite executes CSS**, so
every test stays green. The tell is a wrapper measuring taller than its tallest
child. The defence is that a shared partial's class names must be unique across
the whole page, checked by a test that greps for the retired name in both
directions (`test_the_headline_class_does_not_collide_with_the_pages_own_header`).

**A `<tr>` background is INVISIBLE on a Bootstrap table.** Bootstrap 5.3 gives every
cell `background-color: var(--bs-table-bg)` — an opaque cell sitting on top of its
own row — so a background declared on the `<tr>` is painted over and never appears,
**whatever its specificity**. This is paint order, not the cascade, which is why
`!important` buys nothing. Declare row states on the **cells**
(`#spare-list > tr.jc-row-invalid > td`).

**Bootstrap's cell rule (`.table > :not(caption) > * > *`) is one class and one
element, so a bare class on a `<td>` LOSES to it** — silently, on padding as well as
background. Write `.table > * > tr > .yours`.

**At equal specificity the winner is document order**, which makes the *position* of
a rule a decision: the refused-row block sits after the focus block so that "this is
wrong" outranks "you are here".

**Never `transition: all` on a control that flexes.** `all` transitions
**flex-grow itself**, so chips given `flex: 1` in a media query stayed pinned at
their content width with the correct rule matching, `justify-content` from the same
block applied, and `getComputedStyle().flexGrow` reporting `0` forever. It looks
exactly like a media query that is not being applied. **Transition the paint**
(background, border-color, color, box-shadow), never the layout.

**A rounded list container must NOT be `overflow: hidden` if it holds a dropdown.**
Popper cannot escape a clipping ancestor, and it fails invisibly and only sometimes:
with a long list the menu opens over the rows beneath and stays inside the box, so it
looks correct; with one row the box is barely taller than the row and both items are
cut off with nothing on screen to say why. Round the corners on
`.list > :first-child` / `:last-child` instead.

**A SHOWN OFFCANVAS OR MODAL RUNS A FOCUS TRAP, so an input in a hand-rolled
overlay outside it CANNOT BE TYPED INTO.** Bootstrap's `FocusTrap` is a
document-wide `focusin` listener: anything focused outside the panel is pulled
back to the panel's first focusable child, in the same tick. Clicking the box
focuses it and the caret is gone before a keystroke lands — no error, nothing in
the console, and the box looks perfectly normal. Caught on the Fleet Account
page, where the reason box in `.bd-confirm-overlay` sat outside the Payment
History offcanvas: every reversal reached the Owner's Deletion History with a
blank reason. Measured `focusin` order: `INPUT(reason)`, then
`BUTTON.btn-close`.

Three things about it:
- **A Bootstrap MODAL used as the confirmation is immune**, and that is why the
  two shop pages never had this: their `#confirmActionModal` runs its own trap,
  registered later, so it wins. The defect only bites a **plain div** overlay.
- **Offcanvas has no `focus` option to turn the trap off** — only Modal does
  (`data-bs-focus="false"`). So the fix is to **close the panel the confirmation
  was opened from**, which `confirmSubmit` on both shop pages already did for a
  parent *modal* and not for an offcanvas.
- **A DROPDOWN is not a trap.** The same page's Rename overlay opens from the
  header's `.dropdown` and has always been fine; only a shown offcanvas or modal
  does this.

⚠ **Verify with a REAL click, never `el.focus()`.** Programmatic focus can stick
where a click does not, so the check that matters is: click the box, then read
`document.activeElement`.

**Check which clipping shape you actually have before designing around it.** A
bespoke clip-proof menu was once built to avoid a problem that did not exist:
`.offcanvas-body` is `overflow-y: auto`, the usual setup for Popper being clipped —
but it is **full viewport height**, so at the bottom edge Popper simply flips the
menu upwards and it stays fully visible. The `.cb-list` trap is a different shape:
`overflow: hidden` on a box barely taller than one row, where there is nowhere to
flip to.

**`overflow-x: auto` computes `overflow-y` to `auto` too**, so a horizontal scroller
clips a dropdown as well. Use `position: fixed` for a popover inside one — and verify
first that nothing between the cell and the root creates a containing block
(`transform` / `filter` / `will-change` / `contain`).

**`px-5` and `flex-grow-1` on the same Bootstrap button is a wrap waiting to
happen.** `px-5` is 3rem of padding *each side* — over half a button's width on a
375px phone — and the padding was doing nothing anyway, because `flex-grow-1` is
already what makes that button the wide one. Add `text-nowrap`.

**An inline-flex child adds its line box's strut underneath**, so a cell holding one
needs `line-height: 0` to cost zero row height.

**A `stroke-width` attribute is a presentation attribute and loses to any stylesheet
rule** — leaving both is two numbers for one line.

**Hover on a touch screen never fires, and where it does fire it STICKS.** Put every
hover rule behind `@media (hover: hover)` and give a finger `:active` instead. Size
touch targets by `@media (hover: none)` rather than a width breakpoint — it is the
finger that decides, and the Floor tablet is wider than plenty of laptops.

**A target is only as big as its smaller side** — set `min-width` as well as
`min-height`.

## JavaScript & formsets

**Three traps in `script.js`'s formset-row cloning, all of which fail silently.** The
symptom in every case was a control that simply did nothing, with a clean console.

1. **Never track "already wired" in a `data-*` attribute.** It is serialized into the
   HTML, and the hidden `#empty-*-form` templates are themselves in the document — so
   the initial `initializeAutocompleteInContainer(document)` sweep marks the
   *template's* input as wired and every cloned row inherits the mark. Use a
   `WeakSet` keyed on the element, which a clone cannot inherit.
2. **Declare those `WeakSet`s at the very top of the `DOMContentLoaded` callback.**
   `const` is not hoisted the way `function` is, so declaring them next to the
   functions that use them left them in the temporal dead zone when the initial sweep
   ran. The `ReferenceError` fired inside a `forEach` callback and aborted the rest of
   the handler — taking unrelated features with it, and surfacing in no error log.
3. **`container.querySelectorAll()` searches DESCENDANTS only.** On the add-a-row path
   the container passed in *is* the new `<tr>`, so a selector matching the row itself
   finds nothing. See `inventoryRowsWithin()`.

**Prefer delegation on `document` over per-element wiring.** All three traps above
live in per-element wiring, and a delegated handler works on a row added after page
load with nothing re-initialised.

**`bootstrap` IS NOT DEFINED WHILE A PAGE'S OWN `<script>` IS PARSED.**
`base.html` renders the content block ABOVE its own script tags, so a
`new bootstrap.Modal(el)` at the top level of page JS throws a `ReferenceError`
— and, being inside the page's IIFE, **aborts every listener registered below
it**. It fails in the worst possible way: Bootstrap registers its OWN delegated
handlers when it finally loads, so the ⋮ menus still open and the page looks
completely normal while the control those listeners drove silently does nothing.
Build a modal **on first use**, inside the handler, by which time the bundle has
arrived. (Anything else reading `bootstrap`, `Chart` or another vendored global
at parse time has the same problem.)

**`new Event('change')` DOES NOT BUBBLE**, so a delegated listener never sees it. Pass
`bubbles: true`. A status changed through a confirmation dialog once fired nothing at
all while a status changed directly repainted correctly — two paths, one silent.

**Setting `.value` in script fires no event.** Anything that fills a box in script
must dispatch the event itself (`window.jcFormTouched()`, `carcolour:change`).

**A `<template>`'s contents are a detached fragment that `querySelectorAll` cannot
reach** — which is exactly why blank formset rows live in one, so a `__prefix__`
placeholder can never be picked up by a document-wide sweep.

**Removing a formset row must tick DELETE and hide it, never remove the node** —
Django reads a formset by contiguous index.

**A `ResizeObserver` whose callback sets a dimension must compare only the OTHER
axis**, or it calls itself forever.

**Re-adding a class an element already carries does nothing** — `void el.offsetWidth`
to restart an animation.

**An animation hung off `:active` is cut off halfway on a touch screen** — a tap
releases in about 80ms. Fire it from a class on `pointerdown`.

**Disabling a submit button inside its own submit handler cancels the submission** in
some browsers. Use `setTimeout(0)`.

**A bare `<button>` inside a form submits it.** Every non-submitting button needs
`type="button"`.

**`toISOString()` converts to UTC**, so a "today" built from it reports yesterday for
the whole of an IST morning. Build the ISO string from local parts.

**An `<a>` may NOT wrap a `<button>`.** An anchor cannot contain interactive content,
and browsers do not forgive it quietly: the parser closes the anchor and reopens it
around what follows, so one row rendered as **four** anchor elements, three of them
empty, and the CSS grid row split into four grid containers. Django renders the markup
verbatim so nothing server-side notices, and the page looks *almost* right. Use a
`.stretched-link` inside a `<div>` — the link's `::after` covers the row at z-index 1
and the ⋮ menu sits above it at z-index 2.
→ `test_no_list_row_puts_a_button_inside_a_link` parses the rendered page and asserts
the invariant, not the implementation.

**An HTML parser pops `<form>` when an ancestor `<div>` closes.** Controls created
while the form was open still submit — which is what makes it a trap rather than a
bug — but `form.querySelectorAll(...)` silently skips everything past that point.

## Static files

**`STATICFILES_STORAGE` is DEAD on Django 5.1+ and Django does not warn.** The setting
was removed in favour of `STORAGES`; leaving the old name in place raises nothing and
changes nothing. This project ran on plain `StaticFilesStorage` for months while
`base.py` said `CompressedManifestStaticFilesStorage` — no content-hashed filenames,
so no far-future caching and none of WhiteNoise's pre-compression.

**Symptom to recognise:** `collectstatic` reports files *copied* but none
*post-processed*. One-line check:

```bash
python manage.py shell -c "from django.contrib.staticfiles.storage import staticfiles_storage; print(staticfiles_storage.__class__)"
```

**A manifest storage is STRICT.** Once genuinely active, any `{% static %}` naming a
file that does not exist raises `ValueError: Missing staticfiles manifest entry` at
render time instead of emitting a dead link — including in the test suite. **So adding
a static file means running `collectstatic`, or every page 500s.**

**Assert on the stem, never the filename.** The rendered name is content-hashed
(`js/sound.951c822c33d6.js`), so a test asserting `js/sound.js` would only pass for as
long as static hashing stayed broken. Assert `js/sound.`.

**`?v=` IS THE ONLY CACHE-BUSTER IN DEVELOPMENT, AND IT IS MANUAL.** In production the
manifest content-hashes every filename, so a changed file is a changed URL. Under
`runserver` with DEBUG on, `{% static %}` returns the plain path and the `?v=N` strings
in the templates are the whole mechanism. **Bump `?v=` in the same edit as any static
file change**, and reach for a hard refresh before concluding a fix did not work — an
hour was once spent testing a JavaScript fix the browser was not running.

**`STATICFILES_DIRS` puts `static/` ahead of an app directory**, so a same-named file
inside an app is never served and `collectstatic` warns about the collision on every
run.

**EVERY third-party frontend asset is VENDORED into `static/vendor/`, and none of
it is hand-edited.** Bootstrap's CSS, its icon font and its JS bundle, Chart.js and
the Barlow families used to come from `cdn.jsdelivr.net` and
`fonts.googleapis.com` across 14 templates. They are fetched by
`scratchpad/vendor_assets.py` — the same rule `build_app_icons.py` follows: to
change a version, change it in the script and re-run, then `collectstatic`.

The reasoning is in `TITAN_MASTER_HANDOVER.md` §Carried into go-live and is not
repeated here. What belongs in this file are the three things that will bite
somebody:

⚠ **A `sourceMappingURL` comment fails `collectstatic` outright.** The minified
bundles end with a pointer to a `.map` file, and `ManifestStaticFilesStorage`
resolves those references like any other — so the run dies with
`MissingFileError: … bootstrap.bundle.min.js.map` unless the maps are vendored
too. The script strips the comment, which changes no executable byte. **Anything
new added under `static/vendor/` needs the same treatment.**

⚠ **`{% load static %}` must appear ABOVE the first `{% static %}` in the file.**
Django resolves tags at parse time, so a load tag further down is not merely
untidy — every page raises `TemplateSyntaxError: Invalid block tag 'static'`.
`base.html` had its load tags on line 18 while the stylesheet links moved to lines
12 and 15, and the whole app 500'd. They now sit at the very top of the file so a
`<link>` added higher in the `<head>` cannot repeat it. **A child template needs
its own `{% load static %}`** — it is not inherited through `{% extends %}`.

⚠ **The Railway Build Command is load-bearing and does NOT travel with the repo.**
`collectstatic` is not in the `Procfile`; it is set per project in the Railway
dashboard (`GO_LIVE_RUNBOOK.md` §1.2). Without it the vendored assets are never
collected and the manifest storage 500s every page.

**`.gitattributes` marks `static/vendor/**` as `-text`**, because `core.autocrlf`
is true on the development machine: without it a Windows working copy holds a
different file from the one `collectstatic` hashes on the server.

**The error pages load NOTHING — not even from our own origin.** `403.html`,
`404.html` and `500.html` each pulled 233 KB of Bootstrap to style one link; they
carry that button themselves and are ~1.4 KB. That matters most on `500.html`,
where depending on static serving means an error page that breaks for the very
reason it is being shown.

---

# Frontend architecture — settled, not a backlog item

**The frontend is server-rendered Django templates with page-scoped inline JavaScript,
and there is no build step.** Every outside review reaches the same suggestion, so the
reasoning is recorded here rather than re-argued.

Roughly 188 KB of inline JS across 36 templates, and ~551 KB of inline CSS across 60
of the 106 (most templates carry their own `<style>`). Seven shared JS files exist —
`script.js`, `estimate.js`, `notifications.js`, `sound.js`, `photos.js`,
`photos-core.js`, `spare_autofill.js` — and the rule for what goes in one is
**used on more than one page**; what stays inline is genuinely page-specific.

⚠ **`static/css/style.css` is the CSS side of that same rule, and it is easy to
miss because most of this app's CSS is inline.** `base.html` links it on every
page, so it is where a control drawn by more than one template belongs — the
"Record a Payment" card (`.rpay-*`) lives there for exactly that reason, after
three templates spent months keeping three copies of one form in step by hand
and drifting three different ways. **A component in one place has none of the
inline-JS objection**: no DOM entanglement, nothing to rewrite, and CSS cannot
fail silently the way a moved event handler can. If a new thing is drawn on more
than one page, put it here rather than pasting it a second time.

The usual arguments do not apply here:
- **There is no CSP**, so no hardening is unlocked today.
- The largest page — the job card form — carries ~52 KB of inline script and ~62 KB of
  inline CSS, read by four devices on one shop's LAN, so caching is a rounding error.
  It is re-sent on every navigation anyway, because `no-store` makes a signed-in page
  uncacheable; that is what `GZipMiddleware` is for, not a bundler.
- **There is no npm, no bundler and no linter, and none will be added.**

That last point is load-bearing: **nothing in the Django suite executes a line of
JavaScript**, so a JS refactor leaves it green whether or not it broke — and this
codebase has already been bitten by exactly that (see the three cloning traps above).
**Moving working code with no way to prove it still works is the bad trade, not the
inline JS.**

**There IS a JS test runner, and it cost nothing.** `node --test "workshop/tests/js/*.test.js"`
uses Node's built-in runner — still no npm, no `package.json`, no `node_modules`, no
bundler, no linter. It covers exactly one file, `photos-core.js`, because that file was
*written* to be coverable: pure functions, no DOM, no fetch, a `module.exports` guard at
the bottom.

⚠ **This does not reopen the extraction argument for the other ~3,700 lines.** Inline
page JS is entangled with the DOM and would have to be **rewritten, not moved**, to be
testable. What it does establish is the shape for anything NEW: **if a piece of logic can
fail silently and can be written DOM-free, put it in its own file and test it.**

`photos-core.js` is loaded as a **plain `<script>` before `photos.js`, never as an ES
module** — the manifest storage rewrites URLs in CSS but not in JS, so a relative
`import` would 404 in production and work perfectly in development. Its test file lives
**outside** the static tree, or `collectstatic` would ship it and give it a manifest
entry.

**No new runtime dependency is added without a defect it is the only fix for.**

**The near-copies drifted a second time, and it was a DELAY rather than a bug.**
`updateResults()` computed `const delay = (event && event.type === 'submit') ? 0 : 300`
— so only a search-form submit skipped the keystroke debounce, and a **filter tap, a
pager tap and the custom-date Apply each waited 300ms for nothing**. Measured 367ms
from tap to results on Completed, of which the fetch was ~35ms. It now reads
`(event && event.type === 'input') ? 300 : 0`: debounce TYPING, nothing else.

Worth recording because the *spread* was the surprise — only **three** files had it
(Completed, Paid Bills, Pending Payments). Cashbook and Estimates already called
their fetch directly from the filter handler, and Car Profiles and Job Cards have no
filter or pager at all, only a search box. **Check each copy before assuming a fix
applies to all seven.**

*Consequence, accepted knowingly:* the AJAX list-search pattern exists as **seven
near-copies** across the list pages. It has drifted once already — an
out-of-order-response guard was written in `estimate_list.html` and never reached the
other six, so they showed stale rows for a fast typist until it was copied across by
hand. Logged as `AUD-0086`/`AUD-0096` in `TECH_DEBT.md`. A shared `list_search.js` is the
textbook fix and was deliberately declined: **seven working copies beat one untested
abstraction** on a system this close to shipping. Revisit only if that pattern needs
changing again.

---

# Commands

All commands assume the venv is active (`venv\Scripts\activate` on Windows) and require
`DJANGO_ENV` set — the settings package raises `ImproperlyConfigured` if it is missing.
It is **not** read from `.env`; it must be a real shell/session env var.

```bash
# Windows (PowerShell)
$env:DJANGO_ENV = "development"
```

```bash
# Dev server
python manage.py runserver
```

```bash
# Full test suite — 59 files, 1,921 tests. Always SQLite (see below).
python manage.py test workshop inventory
```

```bash
# JavaScript tests — a SECOND command, not part of `manage.py test`.
node --test "workshop/tests/js/*.test.js"
```

```bash
# Re-fetch the vendored frontend assets (Bootstrap, its icon font, Chart.js,
# Barlow). Only needed when changing a version — the files are committed.
# ALWAYS followed by collectstatic, or the manifest still points at the old ones.
python scratchpad/vendor_assets.py && python manage.py collectstatic --noinput
```

```bash
# A single test file / class / method
python manage.py test workshop.tests.test_financial
python manage.py test workshop.tests.test_financial.SomeTestClass
python manage.py test workshop.tests.test_financial.SomeTestClass.test_something
```

```bash
# Migrations
python manage.py makemigrations
python manage.py migrate
```

## Management commands

| Command | What it does |
|---|---|
| `backup_db` | rotated backup of whichever DB is active, keeps last 14 in `/backups` |
| `sweep_photo_blobs` | DRY RUN — photo objects whose rows are gone (`--yes` to delete) |
| `purge_old_photos` | DRY RUN — photos past the 1-year window (`--yes`; always skips unpaid bills) |
| `setup_groups` | (legacy) creates the Owner/Office/Floor auth groups |
| `sync_owner_identity` | DRY RUN — owner group / mobile / admin-access: `.env` → DB (`--yes`) |
| `set_owner_email <user> <email>` | DRY RUN — preview (`--yes` to apply) |
| `load_master_data` | brands / models / spare parts — **prerequisite for seeding** |
| `seed_dummy_data` | demo data; `--start`, `--end`, `--cards-per-day` |
| `seed_meeting_data` | DRY RUN — wipes every financial record and rebuilds a **uniform** 100-day set (`--yes`). Keeps the inventory catalog, shops, staff roster, master lists and logins |
| `seed_salary_data` | salary months + advances only |
| `purge_business_data` | DRY RUN — prints what it would delete (`--yes`) |
| `copy_sqlite_to_postgres` | DRY RUN — prints the plan (`--yes` to replace Postgres) |

**`backup_db` follows whichever database is active** — `pg_dump` for PostgreSQL, a file
copy for SQLite.

⚠ **The extension tells you how to restore it**: a custom-format archive is `.dump`
(needs `pg_restore`), plain SQL is `.sql` (needs `psql`), a SQLite copy is `.sqlite3`.
Custom format is tried first and plain is the fallback, so both are possible from one
run; naming them alike would leave you guessing on the day you actually need one. A dump
is written to a `.part` file and only renamed once `pg_dump` reports success — a
truncated file under a real backup's name would occupy one of the 14 retention slots and,
once the folder filled, evict a good backup to keep itself. Requires the PostgreSQL
client tools on PATH.

**`purge_business_data` clears ALL business tables** — job cards, shops, fleet accounts,
inventory, cashbook, staff roster, deletion history. It deliberately does *not* try to
distinguish "dummy" rows from real ones, because nothing in the schema marks them and a
command claiming otherwise would be lying. It never touches login accounts, groups, or
the master lists. **It is the thing to run against Postgres before go-live.**

**`seed_meeting_data` is the opposite of `seed_dummy_data`, on purpose.** That one
randomises to look like a real workshop; this one makes **every card identical** —
same concerns, same job lines, same parts, same amounts — so any figure on any
screen can be checked by multiplying one card by the number of cards. One card is
`5 spares x 1,500 + inventory 6,500 + labour 8,000 = 22,000`, and 150 cards must
total ₹33,00,000 everywhere it is reported. It keeps what `purge_business_data`
would destroy (the inventory catalog, the shops, the staff roster), which is why
it does its own narrower purge rather than calling that command.

⚠ **Its opening restock bill is dated three days BEFORE the first job card, and
that is load-bearing.** `inventory/costing.py` replays receipts in date order, so
a draw dated on or before its first receipt has no cost basis, is stored NULL, and
the Profit page reports it as "no cost recorded".

**`seed_dummy_data`** writes everything through the ORM so signals fire, commits one day
at a time with monthly bookends (never one long transaction — a remote Postgres would
time out), and restocks monthly *to demand* rather than a fixed quantity, so warehouse
stock hovers around `average_stock` instead of compounding upward.

It also seeds Salary & Advance, with every month settled **except the last** — that is a
live workshop's normal mid-month state and it exercises `salary_expense`'s
loose-advances branch. Net pay imports the app's own `_compute_net` rather than restating
the arithmetic, so seeded figures cannot drift from what the settlement screen produces.
The Cashbook seeder deliberately has **no "Staff Salaries" line**: wages belong to Salary
& Advance, and a cashbook row named like wages is exactly what the Profit page flags as a
possible double count.

---

# Which database am I on?

`DJANGO_ENV=development` runs against **PostgreSQL** (the local instance in `.env` —
`localhost:5432`, `titan_db`), not SQLite. Development matches what ships, so Postgres-only behaviour — stricter GROUP BY,
real numeric types, case sensitivity, sequences — surfaces while it is cheap to fix.

| Situation | Database | How |
|---|---|---|
| Normal dev, runserver, one-off commands | **PostgreSQL** | default |
| Bulk dummy-data seeding | SQLite | `USE_SQLITE=true` |
| `manage.py test` | SQLite | **automatic, always** |
| `DJANGO_ENV=production` | PostgreSQL | + SSL/HSTS enforcement |

**Tests always use SQLite, whatever `USE_SQLITE` says.** The runner CREATEs and DROPs a
whole database, which is not something to point at a database holding anything you
want. SQLite's test database is also in-memory, which is most of why a 1,921-test run
is ~70 minutes rather than considerably worse. There is deliberately no flag to
remember and no way to run the suite against live data by accident
(`development.py` keys off `sys.argv[1] == 'test'`).

*(This used to justify itself with "~75 ms per round-trip", which was the latency of
the hosted Singapore database. That number is dead; the rule is not.)*

**Seed on SQLite, then copy up — now a choice rather than a necessity.**
`seed_dummy_data` writes every row through the ORM so signals fire, which is tens of
thousands of round-trips. That was unusable against the hosted Singapore database and
is the reason this two-step exists. Against **local** Postgres the round trip is
sub-millisecond, so seeding straight into it is viable. Set `USE_SQLITE=true`, seed,
unset it, then `copy_sqlite_to_postgres --yes` — or skip the dance and seed directly.
**Measure before assuming you still need the two-step**, and note the copy carries the
three access-and-recovery hazards below that seeding directly does not.

**`copy_sqlite_to_postgres` REPLACES the target tables.** It refuses to run if the two
databases are on different migration states, orders tables by a topological sort of their
FKs, inserts with `bulk_create` so signals *don't* re-fire and re-deduct stock, **resets
Postgres sequences** afterwards (explicit ids don't advance them — miss this and the next
insert collides), and re-counts every table before declaring success. It skips content
types, permissions, sessions and admin log.

> ⚠ **It also replaces `auth.User`, `auth.Group`, `auth.User_groups` and `UserProfile` —
> so it can silently break access and recovery. Always do these three checks around it.**
>
> 1. **Emails.** Reset codes go to `User.email`. Placeholder addresses in the seed file
>    would replace the owners' real ones, pointing password recovery at undeliverable
>    mailboxes. Copy the live emails into the SQLite users *before* the copy, or repair
>    with `set_owner_email` straight after.
> 2. **Run `sync_owner_identity --yes` afterwards, always.** The copy has left both
>    owners with `is_staff=True` (opening `/admin/`, which bypasses `DeletionLog`, the
>    Financial Lock and archive-don't-delete) **and stripped their `Owner` group
>    membership** — and since notification audience resolves by group, they would have
>    silently stopped receiving alerts while RBAC still let them in.
> 3. **Extra accounts get copied in.** Any login present only in the seed file is created
>    on the target, group memberships included. A stray Owner-group test account is a real
>    privilege grant.
>
> Expect these to be emptied, since nothing seeds them: `Notification`,
> `PushSubscription`, `AccountLockout`, `PasswordResetOTP`, `DeletionLog` and all three
> Salary tables. `PushSubscription` is the one with a human cost — **every device has to
> re-enable push by hand**, and wages read ₹0 on the Profit page until salary months are
> re-entered.

The `sqlite` alias is always present in `DATABASES` under development, which is how the
copy command reads the file while `default` points at Postgres.

**Page loads are fast now, and that removed a signal rather than a problem.** This
said a 47-query page cost ~3.5 s of pure latency because the database was in
Singapore. It is on `localhost`, so that latency is gone — but the query counts
that produced it have not changed, and production reaches Railway's Postgres over
a real network. **A page that feels instant here can still be slow there**, so keep
query counts low on the evidence (`AUD-0096` measures the job-card form), not on
how it feels locally.

## Environment variables

**Required** (see `settings/base.py`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `OWNER_1_USERNAME`/`OWNER_1_MOBILE` and the `OWNER_2_*` pair
(read only by `sync_owner_identity`; the authoritative copy lives in the database).
Production adds `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

**Photos** (optional): `PHOTO_S3_ACCESS_KEY_ID`, `PHOTO_S3_SECRET_ACCESS_KEY`,
`PHOTO_S3_BUCKET`, plus either `PHOTO_S3_ACCOUNT_ID` (Cloudflare R2, host derived) or
`PHOTO_S3_ENDPOINT` + `PHOTO_S3_REGION` + `PHOTO_S3_PATH_PREFIX` (any other
S3-compatible provider). Optionally `PHOTO_S3_PREFIX`.

They are **named `PHOTO_S3_*` rather than `R2_*` deliberately** — the moment they point
at Supabase, a setting called `R2_BUCKET` is describing something it is not.

⚠ **Use separate buckets for development and production** — they are free, and one shared
bucket means a purge run on dev can reach real photos. Each also needs a **CORS policy**.

**There are THREE photo outcomes, not two, and the third is what makes it
demonstrable.** `photos.storage_backend()` returns `s3` whenever credentials are
present, **`local` on a DEBUG server without them** (photographs go to
`MEDIA_ROOT/photos/` and are served back through two Django endpoints), and `off`
otherwise, where the section disappears entirely.

The local backend exists because **Cloudflare R2 requires a payment card even for its
free tier**, and the workshop's accounts do not exist until after the owners' meeting;
without it the whole feature would have been undemonstrable at exactly the meeting where
it is being shown. It costs almost nothing to support because the browser is handed a URL
and PUTs to it, so it does not care whether that URL points at a bucket or at this Django
process.

⚠ **It is gated on DEBUG, and that gate is load-bearing**: Railway's container filesystem
is wiped on every deploy, so a production server that lost its credentials must fall back
to `off`, which is honest, rather than to a disk that accepts photographs all week and
loses them on the next push.
→ `WhichBackendTests.test_production_with_no_bucket_is_OFF_not_local`

**Supabase Storage is the no-card fallback for production** if the card is still
unavailable at go-live: it speaks the same S3 protocol, so it is three settings and no
code change (`test_a_supabase_endpoint_needs_no_code_change`).

**Web Push** (optional): `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_ADMIN_EMAIL`.
Generated once — **regenerating them invalidates every existing subscription**, so treat
them as permanent. The public key ships to the browser and is not a secret. They must
also be set in the host's environment, or push is skipped there while the in-app feed
keeps working.

**Email** — two transports, one flow. Production sets
`EMAIL_BACKEND = 'workshop.email_backend.ResendEmailBackend'` and needs only
**`RESEND_API_KEY`** plus `DEFAULT_FROM_EMAIL`. Development uses SMTP and reads
`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`DEFAULT_FROM_EMAIL`, plus `EMAIL_REAL` — there `EMAIL_HOST_PASSWORD` is a Google **App
Password**, not the account password, and needs 2-Step Verification on that account.

Only the transport differs: there is exactly one `send_mail()` call site, so the flow,
the throttles and the tests are identical on both. Recipients are per-account
`User.email` values in the database, **never in `.env`** — change one with
`set_owner_email`, which is why it needs no deploy. Development uses the console backend
unless `EMAIL_REAL=true`; `manage.py test` uses locmem regardless.

---

# Architecture

## App boundaries

**`workshop/`** — job cards, billing, fleet accounts, spare shops, cashbook, estimates,
photos, auth, owner analytics, deletion history, master data.

`views/` is a package of **20 modules**: `about`, `audits`, `autocomplete`,
`billing`, `bulk_payer`, `car_profiles`, `completed`, `dashboard`, `deletion_history`,
`estimate`, `jobcard`, `master_lists`, `notifications`, `paid`, `pending`, `photos`,
`push`, `salary_advance`, `spare_shop`, `withdrawal`. **`views/__init__.py` re-exports everything**, so
`from . import views; views.some_function` and existing URL wiring keep working — when
adding a view, add it to both its module and the re-export list.

**Five flat view modules sit outside that package** and are imported directly in
`urls.py`: `analysis_views`, `auth_views`, `cashbook_views`, `cleanup_views`,
`management_views`.

**Nine modules hold no views at all** — this is the codebase's main structural idea, and
each exists so that one rule has exactly one implementation:

| Module | The one question it answers |
|---|---|
| `analysis_engine.py` | the money math behind Analysis (pure functions over a date window) |
| `invoice.py` | what does the customer see? — owns **both** documents |
| `settlement.py` | what is still unfilled before this bill should be settled? |
| `master_data.py` | the rename/merge rule, shared by Master Lists and Data Cleanup |
| `money.py` | is this typed rupee amount acceptable for its column? |
| `money_dates.py` | what day did this money move? — both Cashbook forms, all three payment screens, the Supplies Shop bill and the job card's admitted date |
| `spare_dates.py` | is this ordered/received pair the right way round? |
| `delete_window.py` | has this money row been in the books too long for Office to delete? |
| `photos.py` | where do the bytes go, and how is the URL signed? |

`decorators.py` defines the RBAC decorators. `middleware.py` holds
`SessionTrackingMiddleware`, `NoStoreMiddleware` and `NoIndexMiddleware`.

**`inventory/`** — stock items/categories and supplier shops (`views.py` for core
inventory, `views_suppliers.py` for the supplier-shop module). Stock levels stay in sync
with workshop activity **purely via Django signals** in `signals.py`; there is no direct
view-to-view coupling between the two apps for stock changes.

## Signals-driven stock sync

`inventory/signals.py` has three independent groups (**10 handlers**) on
`pre_save`/`post_save`/`post_delete`:

1. **Workshop consumption** (`JobCardSpareItem`, 3 handlers) — deducts stock for
   **`source='INVENTORY'` rows only**, resolved through the `item` FK. Quantity edits and
   product corrections are handled by a `pre_save` snapshot of
   `(source, item_id, quantity)`, netted per product so the common case is one query.
   **Nothing is clamped at zero.**
2. **JobCard soft-delete reversal** (2 handlers) — **dormant**. Job cards are
   hard-deleted and the delete guard forbids deleting a card that still holds spares, so
   `is_deleted` never flips. Kept for safety; don't rely on them for new logic.
3. **Supplier restocking** (5 handlers) — 3 on `SupplierRestockItem`, which increase
   stock using the same snapshot+delta pattern and are the **only** thing that moves
   `Item.avg_cost` (via `recompute_average_cost`, a full replay); plus a
   `SupplierRestockBill` **pre/post_save pair** that re-costs the bill's items when
   `bill_date` or `discount_amount` changes, since neither of those lives on a line.

⚠ **Count them before quoting the number.** This group grew from 3 handlers to 5 when
the bill-terms pair was added, and every doc went on saying "8 handlers" for months —
the grouping stayed right while the total went stale.

## Settings

Split into `formulad_workshop/settings/{base,development,production}.py`. `__init__.py`
picks one via `DJANGO_ENV` — **there is no fallback default**, so forgetting to set it
fails loudly rather than silently using the wrong DB. The PostgreSQL and SQLite
connection dicts are built by `postgres_db()` / `sqlite_db()` in `base.py` and shared by
both environments; they used to be duplicated per file, which is how a connection setting
gets fixed in one and left broken in the other.

## Financial & data-integrity rules

- **All monetary fields are `DecimalField(max_digits=10, decimal_places=2)`. Never
  `FloatField`.** Inventory **stock quantities** are also `DecimalField` (exact fractional
  units like 1.5 L of oil); display them with the `clean_qty` / `qty` template filter,
  which strips trailing zeros (1.00→"1", 1.50→"1.5").
- **`JobCard.total_bill_amount` is a denormalized physical column** updated via
  `update_totals()`. Don't recompute it ad hoc in views or templates.
- **Model properties check for pre-annotated aggregates before falling back to a
  `.count()` query.** When adding list views, annotate rather than relying on the
  property's DB fallback.
- **Auto-learned taxonomy must dedupe with `__iexact`, never plain `=`.**
- **Only one active job card per registration number at a time** — a hard block, no
  bypass, via `JobCard.get_active_conflict()`. Any code path that can put a job card into
  the active state (create, edit the registration, undo a completion) must call it first.
- **The completion field is `JobCard.completed`** (boolean) with `completed_date`, served
  at `/completed/`. Renamed from `delivered`/`discharged_date` — the whole stack uses
  `completed` now; don't reintroduce "delivered" naming.
- **Most FKs use `CASCADE`/`SET_NULL`.** There are exactly **two**
  `on_delete=PROTECT` in the codebase: inventory `Category → Item`, and
  `OwnerWithdrawal.owner`. The second exists because that row's whole job is to
  say WHICH owner took the money, so one cascaded free would be a rupee figure
  attributed to nobody. It can never fire — Control Hub refuses to delete an
  owner account — so it is a backstop, not a workflow.

## Naming that must not be "tidied"

| Code says | UI says | Why it stays |
|---|---|---|
| `BulkPayer` | Fleet Account | model, fields and URLs are referenced throughout |
| `BULK_PAID` | Fleet Paid | only the display label changed |
| `Mechanic` | Staff Registration | `JobCard.lead_mechanic` and years of history point at it by id |
| `is_trashed` / `is_active` | Archived | the flag name differs by model; internal only |

**The `Mechanic` model is the whole staff roster, not just mechanics.** `Mechanic.role`
(Mechanic / Assistant Mechanic / Office Staff / General Helper) turned a mechanics-only
table into the general roster at `/manage/?section=staff`. Only
`Mechanic.JOBCARD_ELIGIBLE_ROLES` (Mechanic, Assistant Mechanic) can be a job card's
`lead_mechanic`. Renaming the class would be a pure-cosmetic, high-blast-radius change.

---

# Testing conventions

Tests live in `workshop/tests/` (53 `test_*.py` plus `tests.py`) and `inventory/` (5
files) — **59 files, 1,921 tests**.

⚠ **Re-count rather than trusting that line; it has gone stale six times.** The counter:

```bash
python -c "import django,os,sys; os.environ.setdefault('DJANGO_SETTINGS_MODULE','formulad_workshop.settings'); sys.argv=['manage.py','test']; django.setup(); from django.test.runner import DiscoverRunner; print(DiscoverRunner(verbosity=0).build_suite(['workshop','inventory']).countTestCases())"
```

Grepping `def test_` **cannot see tests inherited from shared base classes**.

**Expect 20–80 minutes.** The spread is load-dependent rather than meaningful — a run at
40 minutes has not hung.

**Running two suites at once is safe.** SQLite's test database is in-memory by default
(no `TEST['NAME']` is set), so concurrent `manage.py test` processes cannot collide —
worth knowing when you only need to re-check one file.

**They always run against SQLite**, so the suite stays fast and never touches the hosted
Postgres.

**Test the invariant, not the implementation.** The strongest tests in this suite assert
a *property*: that two screens agree, that a total adds up from its own rows, that a
refused identifier cannot authenticate, that a rendered page for a real and an invented
username are byte-identical.

**Assert on what causes the behaviour, not on a string that happens to appear.** A whole-
page search finds stylesheet rules as well as rendered elements — scope to the element
under test. A blunt `assertNotIn('http://', html)` finds XML namespace declarations.

---

# Repo hygiene

**`errors.log` is a real source of findings, not just noise — read it before
clearing it.** It is gitignored, so nothing recovers it once cleared. Two defects no
review had caught were lifted straight out of it: a duplicate-name 500 in the
Supplies Shop form (40 occurrences), and Resend rejecting every outbound message that
day with HTTP 422.

**A stale Claude worktree can hold unmerged work, in TWO different ways.**
`.claude/worktrees/` is gitignored machine-local state, and both failures look
like nothing is wrong.

- **Uncommitted edits.** A worktree whose branch is already merged can still
  carry working-tree changes that were never applied to `main`. Run
  `git -C <worktree> status` before pruning one; the branch being merged says
  nothing about the working tree.
- **Committed but never merged** — the one that actually happened. A session
  ended reporting a clean tree and a finished commit, and it was both: the
  commit simply sat on `claude/<branch>` while `main` moved on without it. A
  clean `git status` in the main checkout says nothing either way. **Check
  `git worktree list` and `git log main..<branch>` at the start of any session
  that picks up earlier work.**

⚠ **A migration in such a commit is a second thing left behind.** Landing the
code does not apply it — `9e5eab7` added `0071` and the dev database was still on
`0070`, so every spare-shop page 500'd on a column that existed only in
`models.py`. `manage.py migrate` is part of landing the branch, not a follow-up.

---

# Doc ownership map

Each fact has exactly one home. **Update the owning doc; don't restate its content
elsewhere.**

| Doc | Owns |
|---|---|
| **`MASTER_BLUEPRINT.md`** | the numbers — model/field tables, URL routes, template inventory, admin registrations, settings/env vars, test inventory, file tree |
| **`OPERATIONAL_BLUEPRINT.md`** | the workflow narrative — lifecycle flows, who does what by role, billing/cascade walkthroughs, screen descriptions |
| **`TITAN_MASTER_HANDOVER.md`** | mission, current status, the **single authoritative roadmap**, the **deliberately out-of-scope list**, working conventions |
| **`README.md`** | the outward-facing summary — features, tech stack, install steps |
| **`CLAUDE.md`** (this file) | how to work here day to day, plus the **deliberate decisions** that must not be "fixed" |
| **`TECH_DEBT.md`** *(local, gitignored)* | known issues **not yet scheduled** |
| **`GO_LIVE_RUNBOOK.md`** | the **one-time** go-live procedure, rollback, and lockout recovery |
| **`RAILWAY_OPERATIONS.md`** | the **ongoing** platform reference — env vars, deploys, backups, cost, troubleshooting |
| **`master_data_export.md`** | the workshop's own brand/model/spare list, as a source record |
| **`SYSTEM_MAP.html`** / **`_DARK.html`** | the whole system on one page, as a drawing — every section as a card, every flow as a line |

**Both files are GENERATED — edit `scratchpad/build_system_map.py`, never the
HTML.** One set of coordinates emits a light and a dark theme, so they cannot
drift apart. Each is self-contained (inline SVG, no CDN, no script) and pinned to
A4 landscape, so "Save as PDF" gives an exact full-bleed sheet.

⚠ **Do not print these from a browser — use the committed PDFs.** The Print
dialog stamps a date, the page title, the file path and a page number onto the
sheet. **No CSS can stop that** (`@page{margin:0}` suppresses it in some browsers
and not Chrome); it is the dialog's "Headers and footers" checkbox, which means
everyone who ever prints it has to know to untick it. The build runs headless
Chrome with `--no-pdf-header-footer`, which never adds them, so
`SYSTEM_MAP.pdf` / `SYSTEM_MAP_DARK.pdf` are generated once and committed.
`python scratchpad/build_system_map.py` writes all four files; the PDF step is
skipped with a note if no Chrome or Edge is installed.

⚠ **There is a FIFTH output, and it is a Django template.** The dark sheet is
also written to `workshop/templates/workshop/includes/_system_map_svg.html`,
which the **About page** includes as its header. It is the same geometry from
the same run — a pasted `<svg>` would be a second set of coordinates free to
drift from the printed sheet, and the drift would be invisible, because both
would still look like a map. **Never hand-edit that partial**; it is
overwritten on the next build. Only the font differs: the standalone files
load Inter from a CDN, and the app is third-party-free, so the embed asks for
the vendored Barlow instead.

⚠ **Never write the card or connector counts down.** They were stamped along
the bottom edge of the sheet ("REV 4.0 · A4-L · 66 MODULES · 39 SIGNALS") as a
hard-coded literal, which was wrong the moment a card was added; deriving them
fixed that, and then the **stamp itself was removed** on the owner's call —
nobody reads a module count off a drawing, and at 5.5px it was a smudge rather
than a fact. `build_system_map.py` **prints both counts on every run**, which
is where they are actually useful. The module docstring carried the same stale
pair once too.

⚠ **ANYTHING DRAWN THAT READS AS A CONNECTION MUST GO INTO `links`.** The
expense trunk did not, for months — `trunk()` drew a path and appended
nothing — so the checker could not see it, and a tap ending **31px short of
the rail's own start** shipped as a coral line with a terminal node floating
in clear space under CONTROL HUB. Found by eye on the rendered sheet, which is
exactly what this file exists to make unnecessary. `trunk()` now appends, and
**check 6 asserts every tap actually lands on it** (verified by reintroducing
the bug and watching it fail). The checker is only ever as good as what it is
shown.

⚠ **The drafting rulers and the zone numbers are GONE, deliberately.** A–I
across the top, 1–6 down the side, and a numbered circle per zone: nothing on
this map is ever referenced by grid square, and the zone circles printed a
number with no legend anywhere saying what `04` meant. `zone()` is kept as a
no-op call so the zone declarations still read as the layout's structure. The
title block reads **SYSTEM MAP**, not the product name — the owner's own names
for the system stay off the drawing.

⚠ **AN ARROW IN A STATE STRIP IS A CLAIM, and two of them were false.**
`states()` takes `breaks` — the gaps that get no arrow. **ON HOLD** is a side
state a car drops into and comes back from, not a step between WORKING and
COMPLETED; and **PART PAID only ever happens to a fleet card**, because a
walk-in pays once at pickup and any shortfall becomes a discount. So the BILL
row is `PENDING → PAID` and, separately, `PART PAID → FLEET PAID`. There is
deliberately **no SETTLED on the CARD row**: settlement is the *bill's* state
and is already there as PAID / FLEET PAID. The two rows are two things, which
is why they are two rows.

**Run `scratchpad/check_system_map.py` after any change.** It re-runs the
generator and checks the six things that are invisible by eye at this density —
connectors cutting through unrelated cards, connectors missing their target,
anything off-canvas, overlaps, and **long same-colour lines running parallel and
close**. Each of those has caught a real defect:

- **Corridors.** A first version packed the zones tight and let the router find
  its own way — 12 of 32 lines cut through cards. Zones now have real gutters and
  every long connector is steered through one with `via=[...]`.
- **Adjacency.** A line between two cards with a third between them has nowhere to
  go, which is why the parts-and-stock zone is ordered by what connects to what
  rather than by category.
- **Parallel runs.** Not crossing a card is not enough. Three long red lines side
  by side are individually correct and collectively unreadable. The four expense
  streams are drawn as **one trunk with short taps**, which is also the truer
  picture — they add up to one number. The remaining shared-corridor lines are
  spaced by hand, ~12px minimum.

⚠ **It states counts** (14 events, 11 critical, 10 signal handlers, ₹3,500, 25%,
keeps 14). Those drift like every other count in these docs — check them when you
touch it. It read "10 critical" for a day after `LOGIN` was raised to CRITICAL,
and that is worse on the map than in prose: the **About page prints this drawing
directly above its own explanation of the same thing**, so a stale label there
contradicts the page it heads.

⚠ **The SIGN-IN card said `user - email - mobile`, which was a capability rather
than a fact.** `resolve_user_by_identifier` does try all three — but
`manage_create_user` collects only a username, a password and a role, so **no
staff login in this workshop carries an email or a number**, and an owner is
narrowed to their email address by `resolve_login_identifier` anyway. The label
is `username - owner email`. Read what the account-creation form actually
stores before describing how somebody signs in.

**Roadmap vs debt:** `TITAN_MASTER_HANDOVER.md` says what we plan to do;
`TECH_DEBT.md` says what we know is wrong. Re-verify an item before acting on it — it
goes stale like anything else.

**Product scope deliberately left out** — GST, customer-facing notifications, attendance,
multi-mechanic assignment, general file attachments — is recorded in
`TITAN_MASTER_HANDOVER.md` §VII. **Proposing one of those is proposing scope, not
reporting a defect.**

The two operational docs state no rules of their own, so a decision recorded here or in
the handover is never restated in either.
