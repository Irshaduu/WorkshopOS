# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

WorkshopOS ("Titan") is a Django 5.2 monolith for a single premium automotive workshop: job cards, inventory, spare/supplier shops, bulk payer billing, cashbook, and owner analytics. Two apps: `workshop` (core business logic) and `inventory` (stock + supplier shops). **PostgreSQL** is the database in both development and production as of 2026-07-27; SQLite survives only for bulk dummy-data seeding and the test suite — see "Which database am I on?" below. Development runs against **Neon** (Singapore); **production will be Railway's own PostgreSQL**, in the same project as the app, so the two talk over Railway's private network instead of crossing a region. The app is still pre-go-live: neither instance holds a real workshop's books, so don't describe either as "live production data". Deployment lives in `GO_LIVE_RUNBOOK.md` and `RAILWAY_OPERATIONS.md`.

Built for a low-volume, high-value workshop (premium/luxury car servicing, appointment-driven, not a high-throughput chain garage) with a small, flat staff structure — this is why RBAC only needs three tiers and why performance work should be judged against realistic load, not generic "web scale" assumptions.

### Deliberate decisions — do NOT "fix" these
Things that look like bugs, were audited as bugs, and are actually business rules. Each
was raised as a finding and then explicitly ruled *intended* by the owner. If you are
about to correct one of these, you are about to break the business:

- **A part-paid bill books the shortfall as a discount, and is marked `PAID`.**
  `update_bill_status` sets `payment_status='PAID'` as soon as `received_amount > 0` and
  puts `total_bill_amount - received_amount` into `discount_amount`. That is correct here:
  a normal customer has exactly one payment event — they pay at pickup, at whatever amount
  the owner verbally agrees — so "unpaid portion" *is* the discount. There is no
  pay-the-rest-later case for them. Genuine multi-payment relationships are Fleet Accounts
  (`BulkPayer`), which run through `bulk_payer_pay` and do use `'PARTIAL'` correctly.
  `audit_high_discounts` is the intended compensating control for anomalies.
  **`workshop/tests/test_jobcard_views.py:341` is the regression test for this rule — it
  asserts a ₹100 discount on ₹500-of-₹600. Do not delete it as "locking in a bug".**
- **Brand / model / spare / concern are free text, not FKs to the master lists.**
  `CarBrand`, `CarModel`, `SparePart` and `ConcernSolution` exist as reference tables, but
  `JobCard.brand_name`, `JobCardSpareItem.spare_part_name` etc. are `CharField`s filled by
  autocomplete. This is a deliberate trade for data-entry speed on the shop floor, not an
  oversight. The mitigation is normalisation on save (already done for
  `registration_number` and `brand_name`), not converting them to ForeignKeys.
  **One deliberate exception, added 2026-07-30: a warehouse draw
  (`JobCardSpareItem.source == 'INVENTORY'`) is FK-backed by `item`.** Inventory
  products are a closed set by construction — they exist only because someone created
  them through Supplier → Add Product — so there is no data-entry speed to protect by
  keeping them free text, and a great deal of correctness to gain. Spare-shop rows stay
  free text.

- **The Inventory picker SEARCHES categories and never OFFERS them.** Added
  2026-08-12, and it is the other half of the invoice rule above. Typing
  "Engine Oil" in the Job Card's Inventory box returns the products inside that
  category — Liqui Moly, Castrol — and every row it returns is a real `Item`
  with a real pk. Both halves are load-bearing. A person thinks in the generic
  term, because that is the word the customer uses *and the word the bill
  prints*; matching `Item.name` alone meant searching "Engine Oil" returned
  nothing at all, and the obvious next move is to create a **product** called
  "Engine Oil", which puts a generic name on the shelf as a fake SKU and makes
  the printed bill read identically whichever way it was recorded. What the job
  card must store is the branded SKU, because that is what moves stock and
  carries the cost. So the category can lead you to the product; it can never
  be the answer. `distinct()` is required — an OR across the category join
  offers a product matching on both its own name and its category's twice.
  Guarded by `test_searching_a_category_returns_the_products_inside_it` and
  `test_a_category_is_never_itself_an_option`.
  Two smaller rules landed with it. **Stock crosses the wire already formatted**
  — `clean_qty`, the same filter every other quantity in the app goes through,
  so one product cannot read "38" on one screen and "38.00" on another. And
  **the stock line under the box reserves its height whether or not it has
  text**: it was an empty `div`, so choosing a product wrote a line into it and
  the row — with everything below it — jumped, which on a tablet means the box
  you were aiming at has moved by the time your finger lands.
  **The Inventory table carries NO `align-middle`, unlike Spare Parts, and that
  is the fix for the second half of the same complaint.** The Item cell is
  taller than its neighbours because it holds that stock line, so centring
  every cell vertically lifted the Item box above the Qty and price boxes
  beside it by half the line's height — visibly out of line on a row of four
  identical controls. `.inventory-table > tbody > tr > td { vertical-align:
  top; }` starts all four at the same y (measured: 0px spread) and lets the
  stock line hang below, which is the only place it can go without moving
  something else.

- **A job-card spare's route is stored in `source`, never inferred. Do not reintroduce
  name matching.** Added 2026-07-30. A part reaches a car either from a spare shop
  (`source='SHOP'`, the ordering workflow and shop ledger apply) or off the warehouse
  shelf (`source='INVENTORY'`, `item` FK set, ordering fields meaningless). Before this
  there was no such column: the route was guessed from a NULL `shop` plus a
  case-insensitive match of `spare_part_name` against `Item.name` — and the guess was
  made *differently* in `inventory/signals.py` than in `analysis_engine.py`. A part
  bought from a shop whose name happened to equal a stock product was deducted from the
  warehouse by one rule while correctly billed as a shop purchase by the other, so the
  shelf count drifted down until a restock bill papered over it. Proven by
  `ShopPurchaseNeverMovesStockTests` in `inventory/test_signals.py`.
  **Every consumer now reads `source`** — the stock signals, `analysis_engine.py`,
  Stock History and the master-list rename. `_inventory_item_names()` and
  `_warehouse_names()` are deleted. The conversion was verified by computing both
  rules over 8,295 live rows: shop ₹15,039,196 vs warehouse ₹9,504,030 all-time,
  **identical to the rupee** under the old inference and the new column, and
  `DoubleCountRuleTests` keeps its original assertions untouched — only its fixtures
  now declare their route instead of implying it by name.

- **Warehouse stock is allowed to go NEGATIVE. The old `Greatest(…, ZERO)` clamp is gone
  and must not come back.** Added 2026-07-30, on the owner's reasoning: a job card
  records a part the mechanic has *already physically taken*, so refusing or truncating
  that record does not put the part back on the shelf — it only stops a mechanic
  mid-shift and makes the system disagree with reality. The clamp never prevented an
  overdraw, it destroyed the evidence of one: drawing 5 from a shelf of 2 stored 0
  instead of −3, so when the missing supplier bill arrived (+10) the count landed on 10
  instead of 7 and three units were invented, permanently and silently. A negative
  balance is self-healing (−3 + 10 = 7) and is the signal that a Supplies Shop bill is
  missing or a count is wrong. Guarded by `NegativeStockTests`. Consequence: **negative
  is not "Low Stock"** — low means buy more, negative means a bill is missing, and
  showing them alike would have someone reordering a part sitting on the shelf. The
  Low Stock page therefore reports negatives as a separate amber **"stock
  discrepancy"** banner ("a Supplies Shop bill has not been entered yet — don't
  reorder these"), and `out_of_stock` counts `== 0` rather than `<= 0` so the two
  counts are disjoint; one overdrawn product used to be reported as two problems.

- **Warehouse cost is a weighted average, not FIFO — and it is always a full replay.**
  Added 2026-07-30, `inventory/costing.py`. FIFO was the owner's first instinct and was
  costed out: on the reference case (2 L @ ₹1200 then 5 L @ ₹1000, draw 4 L) it differs
  by ₹171 on ₹4,400, and both routes total the same ₹7,400 over the stock's life — they
  disagree only about which month the cost lands in. The average won because stock may
  go negative (FIFO has no layer to draw from and would need retro-costed allocations)
  and because restock bills are editable (FIFO re-costs every consumption that drew from
  the changed layer). Per-batch cost is still recorded forever on `SupplierRestockItem`,
  so real FIFO can be reconstructed later — this choice forecloses nothing. There is
  deliberately **no incremental update path**: a moving average is path-dependent and
  cannot be un-averaged, so keeping both a fast and a correcting implementation would be
  two versions of one number free to disagree. Receipts move the average; draws do not.

- **`JobCardSpareItem.unit_price` is the workshop's COST, and its SHAPE differs
  by route: a shop line's LINE TOTAL, a warehouse draw's cost PER UNIT.**
  Changed 2026-08-17 on the owner's instruction, reversing "cost per unit on
  both routes". Putting a *customer* price in it on either route is still wrong
  and would make the margin report compute revenue − revenue = zero.
  **Why the shop side moved.** This workshop enters what it was billed, not a
  rate: Office copies the figure off the spare shop's own bill. The engine was
  multiplying it by the row's quantity, so 5,000 typed on a row of 2 became
  ₹10,000 owed — money nobody was billed. It was noticed because the Job Card
  grew a `×2` badge to *warn* about the difference between that box and the
  Customer Price beside it (which was always a line total), and the owner asked
  the right question: "2 different logic may make user confused?" It did. The
  fix was to remove the difference rather than label it, so **both boxes on a
  Spare Parts row now hold the line total** and there is no arithmetic at all
  between a typed figure and a ledger.
  **The warehouse route is NOT the same and must not be "made consistent".**
  There `unit_price` is a weighted average of what the shelf paid, written by
  `JobCardSpareItem.save()` and rewritten by the date-ordered replay in
  `inventory/costing.py`. It is per unit *by construction* — derived from the
  shelf, never typed — so a draw's cost is still `× quantity`.
  **`analysis_engine.SPARE_COST` is the one expression that knows which is
  which** (a `Case/When` on `source`, over `SHOP_LINE_COST` and
  `WAREHOUSE_LINE_COST`), and **nothing may re-derive it.** That mattered
  immediately: the expression had been hand-rolled in FIVE places — the engine,
  `SpareShop.update_totals()`, and three aggregates in `views/spare_shop.py`
  (the ledger's running balance, its grand total, and the amount written to
  `DeletionLog` when an unassigned spare is removed) — which is five chances to
  fix one and leave four, and they would have disagreed exactly where it hurts:
  a shop's own page and the Profit page quoting different debts for the same
  rows. All five now import it; `models.py` imports locally because
  `analysis_engine` imports `models`.
  **Consequences worth knowing.** Nothing was migrated, because nothing real
  exists yet — pre-go-live, both instances hold demo data (`purge_business_data`
  before go-live). On the seeded data the Spare Shops expense drops from
  ₹41.9L to ₹33.9L across 253 multi-quantity rows; `seed_dummy_data` now writes
  the line total so a fresh seed is coherent. **A shop row's quantity no longer
  moves any money at all** — it is a description of what was bought, and it
  still prints on the invoice. 30 tests asserted the old rule and were updated;
  `test_a_shop_line_costs_what_was_typed_not_that_times_quantity` (formerly
  `test_spare_shop_quantity_math`, which asserted the exact opposite) is the
  inverted guard.
  **Revised 2026-07-31 — an inventory row's cost is DERIVED, not frozen.** The rule
  was "snapshot at draw time and never recompute", to stop next month's price rise
  rewriting last month's margin. That defended against something which cannot
  happen: the replay in `inventory/costing.py` is **date-ordered**, so a draw is
  priced by the receipts preceding *its own date* and a later-dated bill cannot
  reach back (`test_a_later_dated_bill_never_disturbs_an_earlier_draw`). Meanwhile
  freezing broke the workshop's actual rhythm — a Supplies Shop delivers, keeps its
  own book, and the bill is only keyed when the collector comes at month end, so a
  month of draws recorded no cost at all and ₹36,000 of consumed oil read as free.
  `recompute_average_cost` now rewrites any draw whose stored cost disagrees with
  the replay. Only two things move a past draw, and both should: a bill **backdated
  to before it**, or an existing bill **corrected**. Nothing customer-facing moves —
  that is `total_price`, never touched here. A fill-only-if-NULL variant was tried
  first and rejected: with two suppliers keyed in one sitting, whichever bill went
  in first froze every draw before the second was known.

- **An ARCHIVED spare shop must stay attached to what was already bought from
  it.** Fixed 2026-07-31. The job-card resolution pass rebuilds each spare's
  `shop` FK from the posted pk and looked only at `is_trashed=False` shops, and
  the dropdown offered only those too. So once a shop was archived, opening any
  job card holding one of its spares rendered a select with nothing marked —
  the browser then posted a blank value, the FK was cleared, and that purchase
  silently disappeared from the shop's ledger. An unrelated edit (fixing a
  customer's name) was enough to erase ₹2,000 of debt. Both halves are fixed:
  `_resolvable_shops()` resolves active shops **plus any archived one these rows
  already point at**, and `_shop_options()` puts that archived shop back in the
  dropdown so it round-trips. Archiving still hides a shop from cards that never
  used it. Guarded by `ArchivedShopKeepsItsDebtTests`.

- **Every unassigned spare is created through `_build_unassigned_spare()`, which
  validates.** Added 2026-07-31. The old inline create accepted a **negative
  price** (making the shop appear to owe the workshop), a negative or zero
  quantity, and an oversized price that did not fail cleanly — it was written, and
  every later read of that shop's ledger then raised `InvalidOperation` while
  aggregating it, leaving the shop's page permanently un-openable. Bounds come
  from the columns (`unit_price` max_digits=10, `quantity` max_digits=8), the name
  is truncated to the column width rather than crashing, and an archived shop is
  refused. The rules live in one helper rather than a view precisely so a second
  "add" screen cannot inherit the holes by copy-paste — and there now is one:
  the Unassigned Hub's own Add a Purchase form (`unassigned_spare_add`) records a
  purchase without opening the shop's page, passing the shop as a field instead of
  a URL segment and going through the same helper. Its shop select is **required**,
  because a row with no job card *and* no shop is filtered out of the Hub, missing
  from every ledger, and unreachable by the only delete there is. Guarded by
  `AddUnassignedValidationTests` and `AddingFromTheHubTests`.

- **Master data dedupes on `__iexact`, and there is exactly ONE rename
  implementation — `workshop/master_data.py`.** Added 2026-08-02. Two things
  were wrong. (a) The models' `unique=True` is *case-sensitive*, so "Toyota"
  and "toyota", "Oil Filter" and "oil filter" were both insertable, and
  `ConcernSolution` had no uniqueness at all — the same concern could be added
  any number of times. Every duplicate then showed twice in autocomplete and
  staff picked whichever came first. The job-card auto-learn path had always
  deduped with `__iexact`; the four Master Lists *forms* were the manual entry
  points that did not, and they now carry the check (excluding the row being
  edited, so re-saving an unchanged name is never blocked). (b) A spare or
  concern could be renamed from **two** screens — Master Lists and Data Cleanup
  — and they were two implementations of one rule: Data Cleanup merged
  case-variant duplicates and rewrote the job-card lines carrying the old name,
  Master Lists' plain `form.save()` did neither, so the same edit meant
  different things depending on which page you opened and left history stranded
  on the old spelling. Both now call `rename_spare()` / `rename_concern()`.
  Three properties of a merge worth knowing: **the surviving entry's spelling
  wins** (so list and history can never disagree), it is scoped to
  `source=SHOP` because the rename uses `.update()` and firing no signals is
  only safe for rows that move no stock — relabelling a warehouse draw would
  desync it from the `Item` it is FK'd to — and it is **not cleanly undoable**,
  since renaming back relabels every row now carrying the surviving name.
  Guarded by `RenamingAMasterEntryMeansTheSameThingFromBothScreensTests` and
  `MergingAMasterEntryNeverMovesMoneyOrStockTests`.
  **A merge is CONFIRMED first; a plain rename is not.** Added 2026-08-10.
  That "not cleanly undoable" property above was the only warning anyone got —
  the merge happened on the same POST as an ordinary rename, and the sole sign
  was the success message afterwards, by which point the history had already
  moved. Mistype "ABS Sensor" as "ABS Module" and 13 job cards are relabelled
  with nothing asked. The gate fires **only on a collision**: a rename that
  matches nothing stays one POST, because confirming what cannot surprise
  anyone is how confirmations stop being read. `merge_preview()` in
  `master_data.py` returns the two names and both usage counts, or `None` when
  it is only a rename, and it reads the **same** `*_rename_target()` helpers
  `rename_*` uses to decide — two lookups of "does this collide" would be two
  answers free to disagree, and they would disagree exactly where it matters,
  as a merge nobody was warned about. A brand merge additionally discloses the
  models it will carry across and the ones it will **drop** as duplicates,
  which is a second permanent delete hidden inside the first;
  `brand_merge_model_split()` is likewise shared with the code that performs
  it. Both screens gate it, or the silent merge just moves to whichever door
  is open. One trap: in `brand_edit` the check must run **before**
  `form.is_valid()` — `_post_clean()` writes the posted name onto the bound
  instance, so a preview built after validation names the *survivor* as the
  row being deleted, i.e. tells the owner the opposite of what will happen.
  Guarded by `AMergeIsConfirmedBeforeItHappensTests`.
  *Considered and rejected in the same discussion:* **blocking delete on an
  in-use entry.** Usage effectively never returns to zero (the job-card delete
  guard forbids deleting a card that carries spares), so `used > 0` is a
  one-way door — every name ever typed would become permanently unremovable
  and the list could only grow, which is the opposite of what Data Cleanup is
  for. It also guards nothing: a master-list delete touches no job card, no
  bill and no report, is logged, and auto-learn restores the entry the moment
  someone types it again. Delete and merge are different intents at every
  usage count — merge relabels the history onto one wording, delete just drops
  the suggestion — and the confirmation above is what keeps them from being
  confused for each other.

- **Renaming a BRAND or MODEL reaches the job cards too, and the master list
  decides how its own entries are spelled.** Added 2026-08-02. Reports group by
  `JobCard.brand_name` / `model_name` — free text on the card, by the deliberate
  decision above — so a brand recorded as "Toyta" on one card was a permanent
  second brand in `_insight_vehicles`, and correcting the master list changed
  nothing. Spares and concerns had propagated since day one; brands and models
  never did. `rename_brand()` / `rename_model()` now close that, and a brand
  merge carries the dying brand's **models** across, dropping any whose name
  already exists under the survivor — `CarModel` is
  `unique_together('brand','name')` and moving it would violate that. A model
  rename is **scoped to its brand**: Toyota's "Corola" and another make's are
  different cars. Separately, `model_name` had no normalisation at all while
  `brand_name` and `registration_number` did, so 'corolla' and 'COROLLA' were
  two models everywhere they were counted. It is deliberately **not**
  title-cased the way `brand_name` is — that turns 'i20' into 'I20' and 'CR-V'
  into 'Cr-V'. `JobCard.clean()` collapses whitespace and then snaps to the
  master list's own spelling when that brand already has the model recorded;
  anything genuinely new stays exactly as typed. Three traps were hit building
  this and are worth not rediscovering: **`form.is_valid()` mutates the bound
  instance** in `_post_clean()`, so an "old name" read after validation is
  already the new one; **the model's `unique=True` fires before the view runs**,
  rejecting the very rename that merges a duplicate (`CarBrandForm.validate_unique`
  now skips `name` on edit only); and the `__iexact` form dedupe has to be
  **create-only**, or it blocks every merge. Guarded by
  `RenamingABrandOrModelReachesTheJobCardsTests` and
  `TheMasterListDecidesHowItsOwnEntriesAreSpelledTests`.

- **Deleting a master-list entry cannot touch history — and that is worth a
  test, not an assumption.** Added 2026-08-02. Brand / model / spare / concern
  names live on job cards as free text, never as a FK (the deliberate decision
  above), so removing one changes no bill, no ledger and no report, and
  auto-learn re-creates the name the next time someone types it.
  `MasterDataDeleteTouchesNoHistoryTests` pins that down so the day someone
  converts one of these to a ForeignKey it fails loudly instead of a delete
  quietly cascading a car's history away. What the delete *did* lack was any
  trace at all: one POST, no confirmation, no log. It now shows a confirmation
  carrying the usage count and writes `DeletionLog.ENTITY_MASTER_DATA`. When
  the entry is still in use the page steers towards **merging instead**, which
  is the right tool for two wordings of one part — merge relabels the old job
  cards onto the wording you keep; delete just drops the suggestion and leaves
  both spellings in the history.

- **A month cannot be SETTLED while someone handed an advance would get no
  settlement line.** Added 2026-08-02. `salary_payment_form` writes a line only
  for staff who are active *and* have `current_salary` set, and
  `salary_expense()` stops counting a month's advances as "loose" the moment
  the month is settled — so an advance belonging to anyone else was counted in
  **neither** place and settling the month dropped that cash off the Profit
  page permanently (measured: ₹3,000 for a staff member with no salary yet,
  ₹4,000 for one who had left). Neither state is exotic; the home page has a
  whole "needs a salary" list, and staff leave. `_unsettleable_staff()` now
  blocks the settlement and names them, the same block-and-name shape as the
  Fleet archive guard. It fires **only** on staff who actually received money
  that month, so a salary-less staff member with no advances never blocks
  anything.

- **Salary months have THREE states, and the rules follow the workshop's own
  rhythm.** Rewritten 2026-08-03 after the owner pushed back on a first attempt
  that was more complicated than the business. A month is settled in the first
  days of the *next* one, and the cash is handed over immediately, so:
  **open** (not yet settled) → **locked** (settled, still the most recent;
  correctable via "Edit this settlement" in the ⋮ menu) → **closed** (a newer
  month has since been settled; no edit, no delete, for anyone including
  owners). Both the lock and the closure are enforced in the view, not just by
  the template — `salary_payment_form` refuses a POST without
  `settlement_unlock`, and refuses a closed month outright;
  `salary_payment_delete` refuses a closed month on the GET as well, so its
  confirmation page never renders. The locked fields use **`readonly`, never
  `disabled`**: a disabled input is not submitted, and the settlement loop
  skips any staff member whose `leave_days` key is absent, so disabling would
  silently write no line for anybody — the same trap the job-card price fields
  document.
  **Closure is a STORED one-way flag (`SalaryPayment.superseded`), never a
  computed "is this the latest?" — and that distinction is the whole point.**
  The computed version was shipped first and looked tidy: deleting the newest
  settlement handed the frontier back to the month before it. It is a ratchet
  that turns both ways. Delete the newest, the previous becomes editable,
  delete that, and the entire history can be walked backwards one delete at a
  time — observed doing exactly that on live data, 13 settled months down to
  10, which is how it was caught. `superseded` is set on every earlier month
  when a month is settled and is never cleared, so removing a later settlement
  reopens nothing. Two tests asserted the reversal as a feature and passed 729
  times before that; both are now inverted, and
  `test_the_history_cannot_be_walked_backwards_by_deleting` is the guard.
  Closure is keyed to being superseded rather than to a date, deliberately: a
  rule like "July closes once August opens for settling" closes a month the
  instant it is settled whenever settlement runs late, punishing exactly the
  month that was hardest to get right. Guarded by `ASettledMonthIsLockedTests`
  and `OnlyTheMostRecentSettlementCanBeChangedTests`.

- **A SETTLED month is a closed set of people; an UNSETTLED month is the
  roster.** Added 2026-08-10. The entry below freezes the salary of everyone
  who *has* a line. This is the half it did not cover — people who have
  **none**. Both the settlement screen and its POST loop walked
  `Mechanic.objects.filter(is_active=True)` regardless of whether the month was
  settled, so a staff member hired *after* a month was settled appeared on it;
  with no line to read, `salary_used` fell through to `staff.current_salary`.
  A month captioned "Closed — paid and settled" therefore rendered that person
  at **today's** salary with a live "Pay now" figure that was never paid —
  observed as ₹5,55,000 on a month whose real payroll was five people. Worse,
  the POST loop's `update_or_create` would then write them a genuine line at
  that salary if the newest settlement was ever unlocked and re-saved, adding a
  wage the month never carried to `salary_expense`. Stored data was never
  wrong: `salary_expense` reads `SalaryPaymentLine` only, and no line existed.
  The **page** was wrong, which is worse than it sounds on a screen an owner
  reads to decide what to pay. The GET now builds its rows from
  `payment.lines` when a settlement exists, the POST skips any staff member
  with no existing line (`already_settled and existing_line is None`), and the
  template gates on **`row.salary_used`, never `row.staff.current_salary`** —
  which also fixes a settled card rendering blank after a salary was cleared.
  Adding somebody to a past month is deliberately not an edit: delete the
  settlement and settle again, the same remedy as repricing one.
  **It fixes the mirror defect for free, which is the tell that the rule is the
  right one:** rows came from *active* staff, so retiring someone also erased
  them from a month they were genuinely paid in — the line sat in the database
  with nothing on screen accounting for it. Reading a settled month from its
  own lines answers both directions with one rule. Guarded by
  `ASettledMonthIsAClosedSetOfPeopleTests`.

- **A month keeps the salary it was FIRST settled at, and there is no way to
  edit it.** Added 2026-08-03. Salaries are revised at the same month boundary
  the previous month is settled on, so whichever was done first used to decide
  the answer: enter a raise, then settle July, and July was paid *and reported*
  at August's salary. The first fix was an editable salary field on the
  settlement screen — which the owner correctly rejected as solving by
  interface what the order of work already solves. The rule is: **settle the
  finished month, then apply the raise.** `salary_used` is frozen at the first
  settlement and every later save reuses it, so re-saving a month to fix leave
  days can never reprice it. To settle a month at a different figure, delete
  the settlement and settle again — deliberate, Owner-only, logged. A crafted
  `salary_<pk>` POST field is ignored. Guarded by
  `AMonthKeepsTheSalaryItWasSettledAtTests`.

- **An advance cannot be recorded into a settled month — blocked, not
  detected.** Rewritten 2026-08-03. A `_stale_settled_months()` detector used to
  catch this afterwards and flag the month for re-settling, but it nagged from
  another screen days later and, by existing, invited people back into
  reopening a closed month. Refusing it puts the guidance at the moment of the
  mistake, and the message is **role-aware** because deleting a settlement is
  Owner-only: Office is told to *ask an owner*, an owner is told to delete it
  themselves. Both are offered the second route — record it in the current
  month with a note — which is how a genuinely late discovery is handled, and
  the only route once the month is closed. Guarded by
  `AnAdvanceCannotEnterASettledMonthTests`.

- **Overtime is one amount per person per month, added to the net.** Added
  2026-08-03. Only a few staff have any in a given month, so it is a single
  figure entered at settlement rather than an hours-and-rate calculation.
  Stored on `SalaryPaymentLine.overtime_amount` and folded into `net_amount`,
  which means the wage cost the Profit page reads (`net + advance`) includes it
  with no change to `salary_expense()` at all. Junk input falls back to zero
  rather than corrupting a settlement. Guarded by `OvertimeIsAddedToThePayTests`.

- **Retiring a staff member warns about their unsettled advances, at the moment
  it happens.** Added 2026-08-02. Retiring someone who still holds advances in
  an unsettled month is legitimate, but the settle-guard above then refuses that
  month until they are reactivated. Control Hub is where the click happens and
  Salary & Advance is where it bites, so without a word at the click the owner
  got a green tick and Office hit a wall days later in a different section with
  nothing connecting the two. `_unsettled_advance_total()` counts only months
  with no `SalaryPayment` — an advance already sitting on a payment line changes
  nothing when its owner retires, so warning about it would be noise. **The
  warning never blocks**; retiring is still one click.

- **Creating a login is all-or-nothing.** Added 2026-08-02. `create_user()` ran
  *before* `Group.objects.get(name=role)`, so a missing group row (a database
  where `setup_groups` never ran, or a group deleted by hand) raised
  `DoesNotExist` and 500'd the panel **having already created the account**.
  That login had no group at all: invisible in Control Hub, which lists strictly
  by group; able to sign in; then 403'd by every RBAC decorator — a ghost nobody
  could see in order to delete it. The group is now resolved first and the
  create runs inside `transaction.atomic()`.

- **Every rupee field is bounded by its own column, and the bound is READ from
  the schema.** Added 2026-08-02, `_parse_money()` in `views/salary_advance.py`.
  An amount too large for `max_digits` behaved differently on each database:
  SQLite stored it, silently violating the declared precision, while PostgreSQL
  — what actually ships — raises `numeric field overflow` and 500s the page.
  Neither is an acceptable answer to a fat-fingered `999999999999`. The limit is
  derived from `max_digits`/`decimal_places` so it cannot drift from the column.
  It also rejects `NaN` and `Infinity`, which **parse as valid Decimals** and
  would otherwise poison every `SUM` they touch. An advance dated in the future
  is refused too: cash cannot have been handed over on a day that has not come.

- **Brand and model deletes are disclosed and logged.** Added 2026-08-02.
  Deleting a brand CASCADEs every model under it — the largest permanent delete
  in the app — and the confirm page said only "this will also delete all car
  models", never how many or which, while nothing was written to
  `DeletionLog`. The page now lists them and the delete is logged with the model
  names in its snapshot. Job cards are unaffected either way (`brand_name` and
  `model_name` are free text on the card).

- **Salary inputs are bounded, and access changes are announced.** Added
  2026-08-02. `leave_days` was unvalidated: `-10` produced a net of ₹26,666.67
  on a ₹20,000 salary (a negative deduction pays *more* than the salary) and
  `400` produced −₹246,666.67 — now rejected outright rather than clamped,
  because a clamp saves a number nobody typed. The settlement month came
  straight off the URL, so `/salary-advance/payment/2099/12/` created a Dec 2099
  settlement that then counted as a settled month forever. A retired staff
  member could still be given a new advance, which the guard above would then
  block the whole month over. `salary_set_amount`'s `next` went straight to
  `redirect()` — an open redirect — and now goes through `auth_views._safe_next`
  like the login form's. In Control Hub, **deleting a login and changing a
  staff password now notify the other owner** (creating one always did; the two
  actions that actually revoke or hand over access were silent), and usernames
  dedupe with `__iexact` — Django's is case-sensitive, so "Office" and "office"
  were two logins, and sign-in matches exactly, so whoever typed the wrong case
  just got "invalid credentials".

- **A Fleet Account holding unsettled job cards cannot be ARCHIVED, and an
  archived one takes no new cards, no new payments and no reversals.** Added
  2026-08-02. Archiving used to be unguarded, and it hid the account from every
  screen at once: `bulk_payer_detail` 404s on an archived payer, the picker in
  `bulk_payer_list` drops it, `pending_payments_list` already excludes *any*
  card carrying a `bulk_payer` (they belong on the fleet page), and
  `update_bill_status` refuses a fleet card with "settle it from that account's
  page" — a page that no longer opened. So one click made real debt unreachable
  by every route, while the Archived list went on printing the balance beside a
  lone Reactivate button. A `PARTIAL` card was the worst case: it could not even
  be detached, because the received-money guard in `bulk_payer_remove_card`
  (correctly) blocks that. `bulk_payer_delete` now refuses while any
  PENDING/PARTIAL card is attached and names them; `move_jobcard_to_bulk`,
  `bulk_payer_pay` and `bulk_payment_history_delete` all require an active
  account. Blocking rather than opening a back door keeps one rule: **money owed
  is always reachable from exactly one screen.** Guarded by
  `ArchivingAFleetAccountCannotStrandItsDebtTests`.

- **A Fleet payment may only be reversed while its effects are still intact —
  newest first.** Added 2026-08-02. `bulk_payment_history_delete` restored job
  balances and advance credit through two `max(0, …)` clamps, which silently
  absorbed the difference whenever a *later* payment had already spent this
  one's leftover credit. Overpay ₹1,500 on a ₹1,000 bill (₹500 credit), let a
  following ₹300 payment spend that credit on a second car, then reverse the
  first: the clamp wrote 0 instead of −500 and the second car stayed `BULK_PAID`
  on ₹800 the fleet never handed over. The account's two balance figures then
  disagreed by exactly that ₹500. The view now pre-flights both clamp conditions
  under the same locks and refuses, naming which payment to reverse first —
  the same **block rather than guess at a reversal** choice `bulk_payer_remove_card`
  makes. The invariant it protects, worth asserting in any new fleet test:
  **`Σ(card.received_amount) + advance_balance == Σ(history.amount)`.** Verified
  to hold across all live data. Guarded by
  `ReversingAFleetPaymentOutOfOrderIsRefusedTests`.

- **An unlocked edit that moves a SETTLED card's bill must fix the payment
  state — and the two routes are fixed differently.** Added 2026-08-02,
  `_reconcile_settled_bill()` in `workshop/views/jobcard.py`. The Financial Lock
  exists because editing a settled card is a real need, but nothing followed the
  money afterwards. A `PAID` walk-in kept its old `discount_amount`, so the
  Profit page read revenue as `bill − discount` off the **new** total while
  `received_amount` never moved — adding a ₹500 part to a ₹1,000 card settled at
  ₹800 turned ₹800 of turnover into ₹1,300, breaking the identity CLAUDE.md
  relies on (`bill − discount == received` for a settled card). A walk-in has
  exactly one payment event, so the shortfall **is** the discount; recomputing
  it is that deliberate rule applied to the new total, and a large jump trips
  the existing HIGH_DISCOUNT alert and `audit_high_discounts`, which is
  precisely the compensating control for it. A `BULK_PAID` fleet card is the
  opposite case — a fleet genuinely does pay later, so the extra is owed, not
  discounted — but `bulk_payer_pay` only cascades over PENDING/PARTIAL cards, so
  the difference was uncollectable forever: the fleet page showed "₹0
  outstanding across 0 cards" while `get_pending_balance` said ₹500, and a
  further ₹500 payment parked itself as advance credit. It now drops back to
  PARTIAL. **A bill that shrank below what was received is left alone in both
  cases** — that is an overpayment, not a shortfall, and inventing a refund
  would be guessing. Guarded by `EditingASettledBillKeepsThePaymentHonestTests`.

- **Every typed rupee amount goes through `workshop/money.py`, and the bound is
  READ from the column.** Added 2026-08-02, after the identical hole was found
  independently in Salary & Advance and the Cashbook. Three failures, one rule:
  a figure too large for `max_digits` was **stored by SQLite** (silently
  violating the declared precision) and **rejected by PostgreSQL** — what
  actually ships — with `numeric field overflow`, i.e. a 500 from a fat finger;
  `Infinity` and `NaN` both parse as **valid Decimals**, and since `NaN`
  compares False against everything while `Infinity` is genuinely `> 0`, a bare
  `amount > 0` guard let one through in each direction, making every aggregate
  that touched them meaningless; and unparseable input each view handled its
  own way. `fit_text()` is the same story for strings — a 400-character note
  into `max_length=255` is another SQLite-accepts / Postgres-500s split, and is
  trimmed rather than crashed, matching `_build_unassigned_spare`.

- **A Cashbook category snaps to the spelling already in use.** Added
  2026-08-02. The Profit page breaks General Cashbook down with
  `values('category')` and the category is free text with no picker, so
  "Electricity", "electricity" and "ELECTRICITY" were three lines for one real
  cost — the total stayed right, the breakdown an owner reads to see *where*
  money went did not. There is no master list for these, so the entries already
  recorded **are** the list: first spelling wins, exactly as a job card snaps to
  the master list's spelling of a car model. The row being edited is excluded
  from the check, so deliberately re-casing the only entry of its kind still
  works. Wage-looking categories are still **flagged, never filtered** — that
  rule is unchanged.
  **Extended 2026-08-03: the name box now offers those spellings as a
  `<datalist>`.** The snap was silent — someone typed "electricity", the row
  saved as "Electricity", and nothing had said why. Showing the list while
  typing puts the rule where it applies rather than after it. It is a
  suggestion, not a constraint: a genuinely new category is still just typed.
  Skipped on the AJAX path, since the datalist sits outside the swapped
  regions and does not change with a filter. `CashbookEntry.category` gained
  an index in the same change — that `DISTINCT` and the Profit page's
  `values('category')` are both full-column reads.

- **The Cashbook is ONE stream, and every row behind the total is reachable.**
  Added 2026-08-02 as "a capped list says so", rewritten 2026-08-03 when the
  page was redesigned. Two things were wrong and they had the same root.
  (a) The page was an expenses list beside an income list — two totals, two
  add forms, two of every control — for a ledger whose income side is used a
  handful of times a month. It is now a single chronological stream with
  `All / Out / In` chips over it, one search box and one pager.
  (b) Each list was sliced at `LIST_CAP = 300` while the totals above them
  came from the full queryset, so a busy period printed a figure that could
  not be added up from what was on screen, and the rows past the cap were
  reachable only by narrowing the date range until they fitted. The notice
  explaining the gap was honest but was papering over it. `PAGE_SIZE = 45`
  (the app's list-view convention) replaced the cap: nothing is hidden, so
  nothing needs explaining. **The totals deliberately follow the date window
  and the search but NOT the type chip** — a chip is a way of reading the
  period, not a different period, and moving the headline when one is tapped
  would make the expenses appear to vanish from a period they are still part
  of. Totals and both chip counts come from **one** aggregate, so they can
  never disagree. Guarded by `TheLedgerIsOneSearchableStreamTests` and
  `ALongCashbookPeriodStaysReadableTests`.

- **The Cashbook's date box is small, first, and silent only while it is
  right.** Added 2026-08-03. Almost every entry is dated today, so the field
  that is nearly always correct should not be the widest control on the row —
  it is a 46px calendar glyph with the real `<input type="date">` invisible on
  top of it, which is what makes one tap open the OS picker on every platform.
  Two things stop that being a trap. The moment the date is *not* today the box
  turns amber and spells the day out, and the add confirmation repeats the
  whole entry — date included, marked "(today)" when it is — before a rupee is
  written. Desktop Chrome opens a date picker only from the calendar glyph,
  which the transparent overlay hides, so the click handler calls
  `showPicker()`; on mobile the tap has already opened it and the second call
  throws, which is caught.

- **The Cashbook is ~98% expenses, and the page is weighted for that.** Added
  2026-08-03 on the owner's clarification. Income is scrap, black oil and the
  like — a handful of entries a month against a stream of small general
  expenses. So **Money Out leads the headline and is the largest figure on the
  page**, the add form opens on Money Out, and the income card recedes to grey
  reading "nothing came in — normal" on the many periods with none. **Net is
  rendered only when there IS income**: with none it is Money Out with a minus
  sign, the same number twice, and a figure labelled "Net" beside an expense
  total invites being read as profit — which is the Profit page's job and a
  different calculation. When shown it is labelled "Net movement / in minus
  out — not profit". The engine still computes `cashbook_totals['net']`
  unconditionally; only the card is conditional.

- **`.cb-list` must NOT be `overflow: hidden`, however much the rounded corners
  want it.** Learned 2026-08-03. The row's Edit/Delete menu is an
  absolutely-positioned Bootstrap dropdown inside that box, and hiding the
  overflow clips it — invisibly, and only sometimes, which is what made it hard
  to spot: with a long list the menu opens over the rows beneath it and stays
  inside the box, so it looks correct, and with one row the box is barely
  taller than the row and both items were cut off with nothing on screen to
  say why. Popper cannot escape a clipping ancestor. The corners are rounded on
  `.cb-list > :first-child` / `:last-child` instead. Same trap waits for any
  future list that puts a dropdown inside a rounded, clipped container.

- **Never `transition: all` on a Cashbook control.** Learned 2026-08-03 and
  worth not rediscovering. `.cb-chip` carried `transition: all 0.15s` and the
  mobile rule gives those chips `flex: 1` — `all` transitions **flex-grow
  itself**, and the chips stayed pinned at their content width with the
  correct rule matching, `justify-content` from the same block applied, and
  `getComputedStyle().flexGrow` reporting `0` forever. It looks exactly like a
  media query that is not being applied. Transition the paint (background,
  border-color, color, box-shadow), never the layout.

- **Income mis-keyed as an expense can be corrected in place.** Added
  2026-08-02. It lands on the *wrong side* of the Profit equation — a
  double-sized error — and the only way back was deleting the row and re-adding
  it. `entry_type` is honoured on edit **only when a valid one is posted**, so a
  payload without it keeps what the entry already has rather than silently
  flipping it, and the control is rendered in the edit modal: a server-side fix
  with nothing posting to it would have been unreachable.

- **A Cashbook entry is dated by the day the money moved, and that date is
  editable.** Added 2026-08-02. `CashbookEntry.date` has always existed, been
  indexed, driven the page's Today/Last Week/Last Month filters and been what
  `analysis_engine` files the whole stream under — but **no form rendered a date
  input and neither view read one**, so every entry was stamped with the day it
  was typed and a crafted POST carrying a date was ignored. A month-end expense
  keyed the following week landed in the wrong month on the Profit page
  permanently, because the edit form could not move it either. Both views now
  take `date` through `_entry_date()`, which falls back to today on anything
  unparseable. *Checked while fixing, worth not re-deriving:* `default=timezone.now`
  on a `DateField` **is** safe here — `DateField.to_python` converts the aware
  datetime to `TIME_ZONE` before taking `.date()`, so it lands on the correct
  IST calendar day. Guarded by `CashbookEntriesAreDatedByTheDayTheMoneyMovedTests`.

- **On the Profit page, Salary sits beside the donut and the Cashbook runs
  full width beneath it — and its long tail COLLAPSES, it is never
  truncated.** Added 2026-08-06, on the owner's instruction, reversing how the
  two were first laid out. The reason the swap is stable rather than a matter
  of taste: **Salary & Advance is a fixed four-line calculation** that can
  never outgrow the narrow half of a split row, while **General Cashbook is an
  open-ended list of free-text categories** that grows with the period being
  read — All Time runs to dozens, and the tail is a long run of one-off ₹200
  lines that pushed everything below the card off the screen. The larger
  container goes to the thing that varies.
  Two rules on the collapse itself. **Every row is in the page, only hidden**
  — the heading carries `report.cashbook.total`, so the figure must always add
  up from what "Show all" reveals; a capped list that dropped its tail would
  print a total the rows beneath it could not account for (the same reasoning
  that replaced the Cashbook's own `LIST_CAP` with a pager). And **a
  wage-looking category is never collapsed**, whatever its size: the warning
  underneath names it and tells the owner to go and move it, so flagging a row
  while hiding it is the page arguing with itself.

- **Labour is ONE charge per job card, not a price per job line.** Added
  2026-08-04, on the owner's description of how the workshop actually sells:
  work is quoted whole — a customer is told "₹22,300 for the job" — and nobody
  costs it line by line. So `JobCard.labour_amount` holds the figure, Office
  types it once into the **Total Labour** box at the foot of the Jobs
  section, and `JobCardLabourItem` became a list of what was done with no money
  on it at all. `update_totals()` is now `spares + labour_amount`.
  `JobCardLabourItem.amount` is **dormant** in the same sense as
  `JobCard.is_deleted`: still on the table, never written, never read for money.
  Migration **0066** summed every existing card's line amounts into
  `labour_amount` in one correlated-subquery UPDATE, so not a single
  `total_bill_amount` changed value; the old column is kept rather than dropped
  so a historical card's original pricing stays inspectable.
  Four consequences worth not rediscovering:
  (a) **Saving a job line no longer recomputes the bill**, because the line
  carries no money — `JobCardLabourItem.save()`/`delete()` lost their
  `update_totals()` calls. That means **`jobcard_create` and `jobcard_edit` must
  call `jobcard.update_totals()` explicitly**, or a card whose only change was
  its labour figure keeps its old total forever. The seeder needs the same call
  before it reads `total_bill_amount` to decide what was paid.
  (b) **Deleting a job line must NOT move money.** It used to; that was the
  per-line column. `test_jobcard_properties` asserted the old behaviour and is
  now inverted — removing a typo from the job list cannot reduce a customer's
  bill.
  (c) **Dropping `amount` from the formset closed a hole rather than opening
  one.** It was rendered for Floor inside a `d-none` cell (an absent formset
  field saves as blank and wipes the row — the same reason the spare prices are
  rendered hidden), but `_price_locked_data` only ever rewrote the `spares` and
  `inventory` prefixes, so a Floor login could POST `labours-0-amount` and
  rewrite the labour charge. That is AUD-0081 for parts, unnoticed for labour. A
  field that does not exist cannot be posted. **`labour_amount` needed the
  opposite treatment**: it is a field on the *card*, so `_price_locked_data` now
  pins it too and its return value binds `JobCardForm` as well as the formsets.
  Unlike a formset field it can safely be omitted from the template for Floor —
  an absent field on a ModelForm leaves the stored value alone.
  (d) **The field is `blank=True` and empty means zero.** Plenty of cards are
  parts only; required would refuse to save one. `clean_labour_amount` turns
  empty into `Decimal('0')` (the column is NOT NULL, so cleaning to None would
  be an IntegrityError rather than a message) and refuses a negative outright
  rather than clamping. Guarded by `TheLabourChargeLivesOnTheCardTests` and
  `LabourPrintsAsOneSubtotalTests`.

- **Settling asks what is still unfilled — and it NEVER blocks, and it never
  fires on a clean card.** Added 2026-08-12, `workshop/settlement.py`. Settling
  is the last thing that happens to a job card and the only irreversible one: a
  walk-in has exactly one payment event, so the moment a figure is typed the
  card is PAID, the shortfall becomes a permanent discount, and the Financial
  Lock stands between the card and anyone correcting it. `settlement_gaps()`
  lists what nobody filled in — mechanic, mileage, unfixed concerns, work listed
  but not priced, parts with no customer price, and (shop parts only) not
  received, missing dates, no shop, no shop price. Four rules hold it up.
  (a) **It never blocks.** Two of the three buttons go forward. The workshop
  settles at the counter with the customer standing there, and a checklist that
  refused to let them pay would be worked around inside a week — by not opening
  this screen until afterwards, which loses the check entirely.
  (b) **It is not rendered at all when there is nothing to say**, and the settle
  button reads *its absence from the DOM* to decide. Same reasoning as the
  large-discount gate and the master-list merge confirmation: a dialog that
  appears on every settlement, most of them fine, is one people learn to dismiss
  without reading, and then it is not protecting the settlements that were not
  fine.
  (c) **A warehouse draw is never chased for a shop's fields.** The `source`
  rule again — a draw came off the shelf already fitted, so it has no shop, no
  order and no arrival, and its `status` column is meaningless. Reporting a
  problem that cannot exist and cannot be fixed is precisely how a checklist
  teaches people to click past it. The one check spanning both routes is the
  customer price, because that is the figure that bills whichever shelf the part
  came off.
  (d) **A card with no job lines is NOT nagged about labour.** ₹0 labour is the
  correct answer on a parts-only bill; the gap is reported only when work was
  *recorded* and left unpriced.
  **It is a CHECKLIST, not prose — no sentences, no tinted boxes.** Rewritten
  2026-08-12 on the owner's instruction after the first build explained each gap
  in a sentence: every sentence was true and the whole thing was four paragraphs
  deep, which on the one screen where somebody is standing at a counter with a
  customer is the same as saying nothing. **The uncompleted flag has no filled
  panel behind it** — a tinted box made it the loudest thing on a dialog that is
  mostly a list, and pushed the list below the fold. The body's `max-height` is
  `min(62vh, calc(100vh - 330px))`, sized so the worst card measured (six rows,
  thirteen chips, 483px) does not scroll on a 375×812 phone; it is a subtraction
  as well as a fraction because the head and the buttons do not shrink with the
  viewport. `test_a_chip_is_a_label_not_a_sentence` keeps prose out.
  *Superseded 2026-08-17 — see "One gap, one box" below for how a gap is
  drawn now, and for why a concern is named by its wording rather than by its
  status.*
  **There are THREE buttons and the DOM order serves both layouts at once, so
  it must not be shuffled**: left-to-right weakest→strongest on a laptop
  (Cancel · Open job card · the action), and on a phone a two-column grid with
  the action hoisted by `order` to a full-width row at the TOP under the thumb
  and the two ways out side by side beneath it. Verified at 375×812 (action
  297px wide on its own row; Cancel and Open job card level at 144px each) and
  at 1280px (one right-aligned row).
  **"Settle without completing" was REMOVED on 2026-08-17**, on the owner's
  question about what it was for — which was the right question. A walk-in has
  exactly one payment event and it happens at pickup, so by the time anybody is
  on this screen the car is going out; settling while leaving the card open
  says the workshop still holds a car it does not, and that card then sits on
  the home board and in every "in workshop" count until somebody notices. It
  traps nobody: completing a card is the one action here that is **not**
  one-way, and Undo Completion is in the ⋮ menu on the Completed list. Two ways
  out are still on the dialog, so this is not the checklist starting to block.
  Guarded by `test_there_is_no_way_to_settle_and_leave_the_car_on_the_board`.
  **An uncompleted card is kept apart from the list, with its own button.** It
  is not one more unfilled box — it is a contradiction (money taken for a car
  the board still shows as being worked on) and it is the only item here fixable
  from this screen. "Complete & settle" posts `complete_card=true`, which
  `update_bill_status` reads. That runs **before** the money moves and outside
  any condition on it, so a card that is genuinely finished stays marked
  finished even if the settlement then fails; and `JobCard.mark_completed()` is
  a **no-op on an already-completed card**, deliberately, because
  `completed_date` is what the Completed list filters and sorts on and a
  re-settlement weeks later must not restamp the day the car was handed over.
  That method is now the one implementation, shared with the Completed button.
  Guarded by `workshop/tests/test_settlement_preflight.py`.

- **The printed invoice is NOT a transcription of the job card, and the four
  differences are rules.** Added 2026-08-04, `workshop/invoice.py`. The bill is
  a customer-facing document rebuilt to match the workshop's own reference
  invoice, and each departure from the job card is deliberate.
  (a) **Both spare routes print in ONE "PART NAME" list.** `JobCardSpareItem`
  already holds a shop purchase and a warehouse draw in one table, told apart by
  `source`; the Job Card *edits* them as two sections because a draw has no shop
  and no ordering workflow, but a customer has no interest in which shelf a part
  came off. One list, insertion order, one subtotal.
  (b) **A warehouse draw is billed under its CATEGORY, never its product.**
  `Item.name` is the branded SKU the workshop buys ("Castrol Edge 5W-30");
  `Category.name` is what it is ("Engine Oil"). Naming the brand on a document
  the workshop hands out also publishes its supply chain. Shop spares keep their
  free-text name — those are typed per job and already read the way the customer
  would say them. **Consequence for go-live: the Category is what the customer
  reads, so the taxonomy has to be Category = generic part, Item = branded SKU.
  The demo seed data is the other way round (Category "Fluids", Item "Engine
  Oil") and would print "Fluids" on a bill** — that is the seed file being
  wrong, not the rule.
  (c) **Labour prints its descriptions and one SUBTOTAL, never per-line
  amounts.** Splitting a ₹2,500 job into five numbers invites a line-by-line
  negotiation about work that was quoted whole.
  (d) **A blank QTY is ONE for the money, and a single part prints NEITHER a
  quantity NOR a unit price.** Staff routinely leave the box empty for a single
  part, so blank has to resolve to 1 somewhere — before it did, the column
  divided by the missing quantity and printed ₹0.00 beside a real amount. But
  **the workshop writes a quantity down only when there is more than one of
  something**, and on a row of one **the unit price IS the amount**, so printing
  it is the same figure twice in adjacent columns. QTY and UNIT PRICE are the
  BREAKDOWN of the amount; with one unit there is nothing to break down, and the
  row prints as a name and a price, which is how the workshop says it. Changed
  2026-08-17 on the owner's instruction, reversing "prints as 1" — **the
  arithmetic is untouched, only the cells.** Blank, a typed 1 and a typed 1.00
  therefore produce byte-for-byte identical markup (asserted by comparing two
  rendered parts tables, which is the only form of that requirement worth
  testing), and it is now two empty cells rather than a 1 and a repeat of the
  amount.
  **The two cells travel together and are decided ONCE**, by an `itemised` flag
  in `build_invoice` — one row either reads "qty × unit = amount" or reads just
  the amount, and it can never say a quantity it does not price or price a
  quantity it does not say. `derive_unit_price` still holds the division and is
  still tested on its own: keeping the arithmetic there and the display decision
  here is what lets each be checked without the other.
  **Compared NUMERICALLY** — the column stores two decimals, so a string test
  would itemise every row somebody typed rather than left blank — and only
  against exactly one: **0.5 litres is not a single anything** and still
  itemises in full, on the row where the per-unit figure is the whole point.
  Zero and negative are folded in with blank rather than left to divide by
  nothing. Only shop spares can reach here without a quantity:
  `InventoryDrawForm` refuses a draw with no quantity, because that number moves
  warehouse stock. **The Live Report's spare lists follow the same rule**
  (`|gt:1`, so "× 1" never prints), as the home board's live details already did
  — one rule about how this workshop writes a quantity, wherever a quantity is
  written. Guarded by `OnePrintsAsNothingTests`, which also pins the two
  properties a customer could catch by hand: whenever both are printed they
  multiply back to the amount beside them, and a free part still prints ₹0.00
  while an unpriced one prints nothing.
  Two further things worth not rediscovering. **The UNIT PRICE column is always
  DERIVED** as `total_price ÷ quantity` and never read from a stored field:
  `JobCardSpareItem.unit_price` is the workshop's *cost* (see the entry above)
  and printing it would put the margin on every part into the customer's hand;
  deriving also gives the identical answer where `customer_rate` is set, so one
  rule covers both routes and `qty × unit` always reconciles to the amount beside
  it. And **a part with no price prints an empty cell while one given away prints
  ₹0.00** — `PartLine.priced` exists so a truthiness check cannot collapse the
  two. Guarded by `workshop/tests/test_invoice.py`.

- **The PAID box is a receipt stamp, not a line of the bill — and "settled" is
  the payment STATUS, never `received >= total`.** Added 2026-08-11. A settled
  bill prints a small green box under TOTAL carrying what was actually
  received; an unsettled one prints nothing there, not an empty box and not a
  zero. Three things are load-bearing. (a) **`settlement()` in
  `workshop/invoice.py` decides, not the template.** A template asking
  `received_amount > 0` or comparing it to the total would invent a second
  definition of settled, and it would be wrong on the commonest case: a
  part-paid walk-in is marked **PAID** with the shortfall booked as
  `discount_amount` (the deliberate rule at the top of this file), so the
  comparison prints nothing on exactly the bills most worth stamping. Settled
  is `payment_status in ('PAID', 'BULK_PAID')`; **PARTIAL is deliberately
  excluded** — for a walk-in it never occurs, and for a fleet card it means
  money is still owed, which is not something to stamp PAID on a customer's
  document. (b) **It prints the received amount and nothing else.** Not the
  discount — that is the workshop's own write-off, agreed verbally, and
  printing "DISCOUNT ₹3,000" invites a negotiation about a figure the customer
  was never quoted. Not a balance either: a walk-in has none by construction,
  and a fleet card's remainder is owed by the account, not by whoever holds
  this sheet. (c) **The label is "PAID" / "FLEET PAID", not
  `get_payment_status_display()`**, which reads "Fully Paid" — written for the
  office screens, and beside ₹37,000 on a ₹40,820 bill it puts two claims on
  one page. The box sits **outside** the table: those two totals rows are the
  bill's arithmetic, and a third row would also widen the totals block's
  `break-inside: avoid` on a long bill. Guarded by
  `ThePaidStampAppearsOnlyOnceSettledTests` — note its assertions run against
  `_sheet()`, because `.paid-box` is also a stylesheet rule and a whole-page
  search finds it on every render.

- **A notification's URL is permanent, so the fix for a bad one is to make that
  URL work — not to repoint the next alert.** Added 2026-08-11. `SALARY_ADVANCE`
  used to link to `/salary-advance/staff/<id>/`, the AJAX fragment the history
  modal fetches, which extends no base template; the link was repointed at the
  section on 2026-08-10, and that changed nothing for anyone, because a
  `Notification` stores its `url` in a column and keeps it forever. Every alert
  raised before the fix still arrives at that view, and an owner tapping a
  month-old one still got an unstyled wall of rows with no nav and no way back.
  The view now serves a **full page** on navigation and the bare fragment only
  when `X-Requested-With: XMLHttpRequest` is present. That direction is
  deliberate: **the fragment is the opt-in branch.** Lose the header and the
  modal shows a whole page inside itself, which is untidy; the other way round
  puts a naked fragment back in front of an owner, which is the defect being
  closed. Guarded by `TheStaffAdvancePageOpensAsAPageTests`. **General rule:
  before changing a notification's `url`, ask what happens to the ones already
  sent.**

- **That page answers THREE questions and then stops.** Added 2026-08-11, on
  the owner's description of what they actually do: the alert buzzes, they tap
  it, and they want *who is this* (Amlah), *how much just now* (₹5,000), *how
  much this month* (₹8,000). Four to six seconds, then done. The first build
  was a staff-role line plus a month-grouped history list plus a row-cap
  notice — every part correct, every part in the way. **The history already has
  a home** (the ⋮ modal on Salary & Advance, one tap through the link at the
  foot), so a second copy here bought nothing and cost the glance. Four things
  follow. The figures are **stacked at every width, never side by side**: the
  owners' phones straddle any sensible breakpoint (375 vs 414), so a split
  layout showed the same alert as two different pages, and side-by-side reads
  as a *comparison* when the questions are a sequence. The notification now
  carries **`?advance=<pk>`** so the exact advance is named; without it the
  newest stands in and the label changes from "Advance given" to **"Latest
  advance"**, because a months-old alert must not present today's advance as
  the one it announced. The month total follows **the advance's own month**,
  not today's — an alert opened on the 2nd about an advance given on the 31st
  would otherwise put two figures on screen describing different months. And
  the total is aggregated **in the database**, never summed from what is
  rendered, so it cannot drift from what the settlement screen deducts. With a
  single advance the two figures are the same number twice, so the subtitle
  says "only advance" rather than leaving the repeat looking like a fault.

- **The letterhead is the owner's own PNG, inlined as a data URI, from ONE
  include — and a TRACE was tried twice and rejected.** Added 2026-08-11,
  `workshop/includes/_brand_mark.html`. It replaced a three-part typographic
  approximation (Arial Black italic wordmark, a CSS rule, an Arial Black
  tagline) declared separately in both print templates. That was close — the
  logo *is* a heavy oblique grotesque, and it is **not Racing Sans One**, which
  has flared calligraphic terminals — but it was never the mark, and it printed
  "Diagnosis & Service" where the real one reads **Diagnosis&Service**.
  **Why not a vector.** Two auto-traced SVGs were offered. The first was
  rejected on measurement: 98% of its path data was anti-aliasing noise, the
  letterforms shredded into grey bands (`#d4d4d4` 40%, `#aaa` 26%, `#555` 11%).
  The second was genuinely clean — 19 paths, every fill near-black or near-red,
  no greys — and was shipped for half a day. It still lost, and the reason is
  the useful part: **a trace approximates letterforms by construction.** Its
  rendered ratio was **3.73:1 against the artwork's true 4.40:1**, a 15%
  vertical stretch the owner spotted immediately beside the real mark. Measuring
  colour purity caught the bad file; only measuring the *aspect* caught the
  plausible one. **Greys in a two-colour logo are the tell for the first
  failure; a ratio that disagrees with the source is the tell for the second.**
  Five things are load-bearing.
  (a) **A `data:` URI, never `<img src="/static/...">`.** The note this replaced
  had the right instinct: anything fetched can fail to arrive, and a bill that
  prints without its letterhead is worse than one that never had it. A static
  path would render identically in development and then 404 on a deploy that
  missed `collectstatic`. A data URI is part of the document — no request, no
  static dependency, nothing for a CSP to permit — which keeps the printed
  pages' promise of loading nothing from anywhere.
  (b) **A raster is safe HERE because it out-resolves the paper.** 1323px across
  56mm is **600 DPI**, twice what a 300 DPI print consumes;
  `test_the_artwork_is_dense_enough_to_print` fails below 500. The old "a raster
  prints soft" warning was about rasters that do not clear this bar.
  (c) **The supplied file needed three fixes, all invisible on screen and all
  obvious on paper**: its canvas carried whitespace padding (so 56mm would have
  shrunk the *ink* below the real bill's size), its background was 253-grey
  rather than white (a faint grey box on the sheet), and at 2168px it inlined
  ~331KB. Cropped to ink, lifted to pure white, resampled to 600 DPI, and
  encoded as a **16-colour palette** — 130KB truecolour became 38.6KB, since the
  mark is three flat colours plus an anti-aliased skirt. Regenerate with
  `scratchpad/build_logo_png.py`; never hand-edit the base64.
  (d) **Sized by WIDTH (56mm), height `auto`.** Not a guess: the owner's running
  bill was measured by splitting its top-left ink into bands, giving a lockup of
  **55.6mm × 13.3mm** (the red rule is the widest element). 56mm renders
  12.74mm tall — within 0.4mm and 0.6mm on the two axes. Height stays `auto` so
  the ratio can only come from the file.
  (e) **One include, both documents**, same reasoning as
  `_car_color_picker.html`: the estimate and the invoice reach one customer days
  apart. `BothDocumentsCarryTheSameLetterheadTests` asserts they embed the
  byte-identical image and size it with an identical `.brand-logo` rule.

- **The invoice page loads NOTHING from a third party — and that is asserted on
  FETCHES, not on the string "http".** Rewritten 2026-08-11. The rule is
  unchanged and still right; the test enforcing it was blunt
  (`assertNotIn('http://', html)`) and broke the moment the logo went in,
  because every SVG element declares `xmlns="http://www.w3.org/2000/svg"` — an
  XML namespace **identifier**, a name shaped like a URL that no browser ever
  resolves. A blunt failure like that pushes whoever hits it towards deleting
  the namespace (breaking the SVG) or deleting the test (losing the rule);
  neither is the answer. It now checks what actually causes a request: no
  `cdn.`, no `<link`, no `@import`, no `url(http`, every `src`/`href`
  same-origin, and every absolute URL one of the two namespace declarations.
  Note `src=` is legitimately present — the page loads its own
  `js/sound.js` off `/static/`, which is this server; the *printed sheet*
  carries no reference at all, which is asserted separately.

- **The invoice toolbar breaks into TWO CHOSEN rows on a phone.** Added
  2026-08-17 on the owner's report. Five controls will not sit on one line at
  375px, so they wrapped — and wrapping a flex row that has a `flex: 1 1 auto`
  spacer in the middle of it gives you whatever happens to fit: Back and the
  status chip stranded on row one with Edit Job shoved to the far right of it,
  then two buttons of different widths beneath. Nothing lined up with anything
  and the widest button was the one that mattered least.
  The break is now chosen. **`.bar-spacer` becomes `flex: 0 0 100%; height: 0`
  below 640px** — a full-width line break, which is what it already is on a
  laptop, just made explicit — so row 1 is *where you came from and what state
  this bill is in* and row 2 is *the three things you can do*, in equal columns
  (measured 114px each at 375px).
  Three things are load-bearing. **`flex: 1 1 0` with `min-width: max-content`**
  is what makes them equal when they fit and wrap INTACT when they do not: no
  label is ever truncated, which on a row of verbs is the difference between a
  button and a guess. The fleet card is the case that needs it — "Settle on
  Fleet Account" takes most of row 2 and Print drops to a row of its own
  (verified; no overflow at 375px). **The rule is scoped `.bar .btn`, not
  `.btn`** — the same class is the dialogs' button, and the settle dialog's
  footer has its own phone layout that this would fight. And **"Print / Save
  PDF" sheds its second half** into a `.btn-print-long` span: the icon is a
  printer and the sheet is on screen behind it, so "Print" is not ambiguous, and
  the full label alone is wider than a third of a phone. Consequence for tests:
  the full wording is no longer one contiguous string in the markup, so
  `test_the_controls_are_all_marked_no_print` checks that button by its ACTION
  (`window.print()`) — "Print" on its own also matches `@media print` in the
  stylesheet and would prove nothing.
  **`estimate_print.html` carries the identical block**, and that is the point
  rather than a copy-paste slip: the estimate is handed over first and the bill
  follows it for the same car, so the two screens are opened days apart by the
  same person and a toolbar that rearranges itself between them reads as two
  different products. The two templates already share `workshop/invoice.py`, the
  letterhead include and the row-padding rules. Verified at 375px — invoice:
  Home + chip, then three 114px columns; estimate: All Estimates + chip, then
  two 175px columns; neither overflows.

- **The invoice page's controls live outside the paper.** Added 2026-08-04. It used to pull Bootstrap CSS, Bootstrap
  JS and an icon font from a CDN — which bought one modal and cost control over
  what lands on paper: a framework reset shipping upstream could move a column on
  a customer's bill, and a workshop printing on a dropped connection got an
  unstyled page. Everything is now inline in the template, the modal is a native
  `<dialog>`, and the icons are inline SVG. Separately, the screen controls
  (toolbar, buttons, messages, dialog) are **outside the `.sheet` element
  entirely**, not merely `display:none` in print — `NothingInteractiveLivesOnThePaperTests`
  asserts the sheet contains no `<button>`, `<a>`, `<form>`, `<input>`, `<script>`
  or `<dialog>`, because a CSS-only rule is one stylesheet edit from printing.
  The template is standalone (does not extend `base.html`), so it **must** render
  the `messages` block itself — that is not the double-render `base.html`
  forbids. It previously rendered none, so "Billing updated" from
  `update_bill_status` was never shown here and surfaced later on an unrelated
  screen.

- **The invoice is one A4 sheet on screen as well as on paper — narrow screens
  SCALE it, they do not reflow it.** Added 2026-08-04. Owners open invoices on a
  phone, and a bill that rearranged itself to fit would stop being a preview of
  what prints. `fitSheet()` applies a `transform: scale()` and sets the wrapper's
  height to match; `@media print` clears the transform outright. Two traps: the
  wrapper's height is set by the same function that watches it resize, so the
  `ResizeObserver` must compare **width only** or it calls itself forever; and
  both `window.resize` and the observer are attached deliberately, since some
  browsers report a rotation through only one of them. Pagination is pure CSS —
  `thead { display: table-header-group }` repeats the column headings on every
  page, `tr { break-inside: avoid }` stops a row splitting across the fold, and
  SUBTOTAL/TOTAL sit in their own `<tbody class="totals">` rather than a
  `<tfoot>`, **which would have repeated them at the foot of every page.**

- **An ESTIMATE is connected to NOTHING, and that isolation is the feature.**
  Added 2026-08-05. `Estimate` / `EstimateJobLine` / `EstimatePartLine` are read
  by five views and one printing function, and by nothing else — no job card, no
  spare shop, no warehouse stock, no ledger, no line in `analysis_engine.py`.
  Money on an estimate is a *proposal*: a quote that moved stock or entered the
  Profit page would be the workshop counting work it has not done and parts it
  has not fitted. Three consequences worth not rediscovering.
  (a) **The part name is free text and matches nothing on purpose** — quoting
  "Castrol Edge 5W-30" must not deduct the shelf, and
  `test_quoting_a_stock_product_moves_no_warehouse_stock` says so.
  (b) **`EstimatePartLine.customer_rate` / `.amount` are named the OPPOSITE way
  round from `JobCardSpareItem`** deliberately. There, `unit_price` is the
  workshop's COST and `total_price` is what the customer pays. An estimate has
  no cost side at all — every figure on it is a quoted price — so the per-unit
  field reuses the one `JobCardSpareItem` name that already means exactly that.
  Nothing here may ever be read as a cost.
  (c) **Deleting one writes NO `DeletionLog` row.** The only place the section
  departs from the app's deletion model, and it is a decision. `DeletionLog.record()`
  is also the origin of `RECORD_DELETED`, which is **CRITICAL** and pushes to
  both owners' phones; an estimate is a draft expected to be rewritten and
  discarded, and buzzing two phones over housekeeping is precisely how a critical
  alert stops being read. Logging-without-notifying was rejected because it means
  weakening the choke point that keeps the other ten entity types correct.
  Guarded by `AnEstimateIsConnectedToNothingTests`.

- **`workshop/invoice.py` owns BOTH customer documents — do not fork it, and
  do not "unify" the two places they deliberately differ.** Added 2026-08-05,
  amended the same day. `build_invoice()` and `build_estimate()` share
  `effective_quantity`, `derive_unit_price`, `PartLine`, `JobLine` and the
  `MIN_JOB_ROWS`/`MIN_PART_ROWS` padding. The estimate is handed over first and
  the invoice follows it for the same car, so where they agree they must agree
  exactly: an unpriced part prints an empty cell while a free one prints ₹0.00
  (`PartLine.priced` exists so a truthiness check cannot collapse them), labour
  prints descriptions plus one SUBTOTAL, and both pad to the same row counts.
  `EstimateJobLine` has **no money column at all** — `JobCardLabourItem.amount`
  was kept only because it had history to preserve. Numbers are `EST-26-001`,
  never `JB-`.
  **TWO columns diverge, on the owner's instruction, and both follow from one
  fact: a bill records work that happened, an estimate describes work that has
  not.** So a blank box on a bill is a fact too obvious to type, while a blank
  box on an estimate is something nobody has decided — and filling either in
  with a computed number puts a figure on the page that no one chose.
  (a) **QTY.** Both leave a BLANK box blank, and they differ on a **typed 1**:
  the invoice hides it, because on a bill one is the figure this workshop never
  writes down (see the rule above, changed 2026-08-17); the estimate prints it,
  because somebody chose to put it in front of the customer. Both still count a
  blank as 1 in the arithmetic. `PartLine` therefore carries both `quantity`
  (the money) and `display_quantity` (the cell), and **neither document sets
  them equal any more** — the invoice blanks the cell at exactly one, the
  estimate passes through whatever was typed.
  (b) **UNIT PRICE.** The invoice DERIVES it as `amount ÷ quantity` on any row
  that ITEMISES — a billed part has a real quantity, so the division is always
  safe — and prints nothing on a row of one, where the answer would be the
  amount over again in the column beside it (see the rule above; the two cells
  are decided together). The estimate prints it **only when `customer_rate` was
  actually entered**, whatever the quantity: deriving would present the
  workshop's own arithmetic as a quoted rate, and on a row with no quantity it
  would divide by a 1 nobody agreed to. So a quote CAN carry a rate on a
  single-unit row and a bill cannot — right on both, because one is a figure
  somebody chose to quote and the other would be a repeat of the total. It
  still reconciles when shown, because `amount = customer_rate × quantity` is
  enforced on save.
  Nothing can carry an estimate's figures onto a job card — the card is typed
  fresh — so the two documents can never contradict each other on one car.
  `TheEstimatePrintsWhatSomebodyTypedTests` pins the divergence;
  `TheEstimatePrintsLikeTheBillTests` pins what is still shared.

- **Django overwrites an inherited `get_<field>_display`, silently.** Learned
  2026-08-05 while sharing the car-colour helpers between `JobCard` and
  `Estimate`. `Field.contribute_to_class` guards its generated accessor with
  `"get_%s_display" % self.name not in cls.__dict__` — the class's **own**
  dict, never its bases, expressly so a subclass can override inherited
  choices. So `CarColourMixin.get_car_color_display` was replaced by Django's
  partialmethod on both models and nothing raised: `car_color='Other'` started
  reading back the literal word "Other" instead of the picked colour, and an
  unset colour read `''` instead of "Unknown". Each model therefore repeats
  `get_car_color_display = CarColourMixin.get_car_color_display` in its own
  body — one line, with the implementation still shared. `get_car_color_hex`
  has no such clash and inherits normally. Guarded by
  `test_the_estimate_and_the_job_card_agree_on_every_colour`.

- **One car-colour palette, one picker.** Added 2026-08-05. `CAR_COLOR_CHOICES`
  and `CAR_COLOR_HEX` live at module level in `models.py`, and
  `workshop/includes/_car_color_picker.html` is the single swatch control, used
  by the Job Card and the Estimate. Both were previously inline in
  `jobcard_form.html`; a second copy for Estimates would have been ~100 lines of
  markup, CSS and JS plus fifteen hex values free to drift, and a Grey job card
  printing a different grey from a Grey estimate is invisible until the two are
  side by side. The estimate's colour is **not printed on the quotation** — it
  is the stripe down each history row, the same identity cue the dashboard's
  live cards use, and the customer already knows what colour their car is.

- **On an Estimate there is no delete button — clearing the name IS the
  delete.** Added 2026-08-05, on the owner's instruction. A ✕ beside every row
  is a one-tap way to lose work on a tablet, and a quote is typed in a hurry.
  `BlankRowIsNoRowFormSet` therefore marks a row DELETE when it is blank, **and
  additionally whenever a STORED row has lost its name — even if its figures are
  still there.** That last part is the whole gesture: refusing a priced row
  would make the only delete there is fail on exactly the rows people want to
  remove. A **new** row carrying figures with no name is still refused, because
  there it is a slip rather than an erasure and dropping it would throw away a
  price someone just typed. Guarded by
  `test_clearing_the_name_deletes_a_PRICED_stored_line` and
  `test_a_priced_NEW_row_with_no_name_is_still_refused`.

- **On an Estimate, a blank row is not a row — and the fix has to run BEFORE
  `super().clean()`.** Added 2026-08-05, `BlankRowIsNoRowFormSet` in
  `workshop/forms.py`. Everything on a quote is optional, so a line someone
  typed into and then cleared must not become "This field is required" — that is
  the form arguing with the person filling it in. The line forms drop
  `required` and the formset marks any all-blank row `DELETE`, which Django's
  own delete path then skips (new row) or removes (stored row). **The ordering
  is load-bearing and cost an hour to find:** `BaseModelFormSet.clean()` calls
  `validate_unique()`, which reads `self.deleted_forms` — and that property
  **caches** its answer in `_deleted_form_indexes` on first access. Marking the
  rows after `super().clean()` marks them too late; the cache is already built
  from the unmarked forms and `deleted_forms` stays empty forever. The failure
  is worse than a no-op: `_post_clean` excludes a blank value on a
  not-required field from model validation, so the emptied row raises no error
  either — it is simply **saved**, writing `description=''` and printing an
  unnamed line on a customer's document. Guarded by
  `test_clearing_an_existing_line_removes_it_instead_of_erroring`.

- **A money box must not fight the person typing into it.** Added 2026-08-05,
  `_tidy_money_initial` in `workshop/forms.py`. A field arriving with `0` turns
  the first keystroke into `08500`; one arriving with `8500.00` puts two zeros
  and a point between the caret and the next digit, so entering a figure means
  deleting characters first. Both were true of Total Labour, the box Office
  touches on nearly every estimate. Display only — `clean_labour_amount` still
  turns empty into `Decimal('0')` and the column still holds two decimals — and
  **real paise are kept** (`1250.50`), because dropping those changes the number
  rather than tidying it. Bound forms are deliberately untouched:
  `BoundField.value()` reads submitted data, not `initial`, so a rejected POST
  still shows exactly what was typed instead of a reformatted guess at it.

- **Estimates offer TWO date filters, not the eight the day-to-day lists
  carry.** Added 2026-08-05. Paid Bills / Completed / Cashbook sort a stream of
  daily activity, where Today and Last Month each answer a real question. A
  workshop writes a handful of quotes a month and looks them up months later, so
  six of those eight would return an empty page most of the time — which reads
  as a broken screen, not an empty period. This Year (default) or All Time, as
  two pills rather than a dropdown, because two options should be one tap. An
  unrecognised `?filter=` falls back to This Year rather than silently widening
  to everything.

- **The Manage pill's highlight is a LIST in Python, not a chain of `{% if %}`
  in `base.html`.** Fixed 2026-08-05. It used to be ten `p|slice` comparisons
  inline on the button, and it had quietly fallen two sections behind: **Salary
  & Advance and Estimates were both in the drawer and missing from it**, so
  Manage read as inactive on pages reachable only through it. A missing entry in
  a ten-clause boolean is invisible. It is now `DRAWER_SECTION_PREFIXES` in
  `templatetags/custom_filters.py` with an `is_drawer_section` filter, and
  `test_every_drawer_destination_lights_the_manage_button` scrapes the drawer's
  own links and asserts every one is covered — so the next section added fails
  loudly instead of shipping unhighlighted.

- **A list row may NOT be an `<a>` wrapped around a `<button>`.** Learned
  2026-08-05 on the Estimates history and worth not rediscovering. An `<a>`
  cannot contain interactive content, and browsers do not forgive it quietly:
  the parser closes the anchor and reopens it around what follows. One estimate
  rendered as **four** anchor elements, three of them empty, and the CSS grid row
  split into four grid containers. Django renders the markup verbatim so nothing
  server-side notices, and the page looks *almost* right. The fix is a
  `.stretched-link` inside a `<div>` — the link's `::after` covers the row at
  z-index 1 and the ⋮ menu sits above it at z-index 2, so it keeps its own clicks
  with no click-swallowing JavaScript. `test_no_list_row_puts_a_button_inside_a_link`
  parses the rendered page and asserts the invariant, not the implementation.
  (Two related traps already documented elsewhere apply here too: `.est-card`
  must stay `overflow: visible` or the dropdown is clipped invisibly, and never
  `transition: all` on a filter chip.)

- **The Estimate's part-price suggestion is a PLACEHOLDER, and never anything
  more.** Added 2026-08-05, `spare_price_hint` in `views/autocomplete.py`. When a
  part name is entered, the Unit Price box's *placeholder* becomes the average
  customer price over the last 5 times that name was billed. It is never written
  into the field and never posted, so the worst case when the endpoint is slow,
  wrong or down is grey text nobody uses — **a price on a document handed to a
  customer must be something a person decided.** Three rules: it is the
  **customer price**, derived with the printed document's own
  `derive_unit_price` rule (`total_price ÷ effective_quantity`) and never
  `JobCardSpareItem.unit_price`, which is the workshop's *cost* and would quote
  every part at cost; it reads **job cards only, never past estimates**, or one
  optimistic quote would drift the suggestion upward forever with nothing real
  underneath it; and a part with no history returns `found: false` rather than
  zero, because "never sold" and "it is free" are different answers. It is
  `@office_required`, not `@staff_required` like its neighbours in the same
  module — Floor is shown no prices anywhere else in the app. The `__iexact`
  filter runs on an unindexed column deliberately: the table is single-digit
  thousands of rows and a plain btree index cannot serve a case-insensitive
  match anyway; if it ever shows up in a slow-query log the fix is a functional
  index on `UPPER(spare_part_name)`, not a change of rule.

- **The Estimates header keeps its action beside the title, at every width —
  the row must never become a column.** Added 2026-08-05. The mobile rules
  originally switched `.est-header-top` to `flex-direction: column`, which gave
  a phone a full-width "New Estimate" button on a line of its own and pushed the
  first card below the fold. The title shrinks instead (`.est-title-word` is a
  separate element precisely so it, and not the count pill or the button, is
  what truncates), and the description sits **outside** that flex row rather
  than inside its left column. Verified at 320 / 360 / 414px. Same reasoning
  puts the search box and both filter chips on one row on a phone: the chips go
  small rather than the search wrapping. Guarded by
  `test_the_header_puts_the_action_beside_the_title_not_under_it`.

- **An Estimate list row survives every combination of blank fields.** Added
  2026-08-05. Most of a quote is optional, so the row cannot assume a make, a
  model, a registration or a customer. Two rules do that work: the **headline is
  whatever identifies the car best** — brand + model, else the registration,
  else the estimate number — so there is always exactly one big line and never
  an empty space where one should be; and **nothing blank is announced**, so a
  missing customer prints nothing rather than "No customer name" (a row with
  three grey apologies in it looks broken). The registration shares the headline
  line rather than sitting under it, which is what keeps every row two lines
  tall: on its own line, rows ran 67px or 91px depending on whether someone had
  typed one, and down a list of 45 that raggedness is the first thing the eye
  catches and it carries no meaning. **On a phone the row keeps that shape
  rather than folding** — dropping the amount onto a third line made every card
  half again as tall (102px against 60px), and a list is read by scanning down
  it, so fewer cards per screen is the cost that matters, not a few pixels of
  width. The name truncates instead; the plate and the amount never do. A quote with no figures yet prints **"Not
  priced"**, never `₹0.00` — the same `priced` distinction the printed sheet
  makes.
  **The row's TYPE was raised on 2026-08-17, and its shape was not.** Every
  fact on it had been set a step smaller than the same fact anywhere else in
  the app and it added up: the car's name at 1.12rem against the dashboard
  card's 1.15, the "QUOTED" caption at 0.58rem — which on a 375px phone
  rendered at **8.3px**, smaller than anything else the app asks anyone to
  read — and the whole row 59.7px tall where a dashboard card is 172px. Now
  70.9px on a phone, with the headline at 1.16rem there and 1.28rem on a
  laptop. The two-line shape above is untouched, because that reasoning still
  holds. The phone's *controls* went up with it (search 0.76→0.84rem, chips
  0.72→0.78rem): they had been shrunk to fit search + both chips on one row,
  and the row was fitting with room to spare — re-measured at a 288px
  container (a 320px phone) the search still gets 133px and nothing wraps.

- **The Estimate form uses a native `<datalist>` for part names, not the Job
  Card's fetch autocomplete.** Added 2026-08-05. The master spare list is ~200
  entries (a few KB), and a datalist needs no wiring — so a row added *after*
  page load gets the same suggestions with nothing to re-initialise. That is the
  whole point: `script.js`'s three documented cloning traps all live in
  per-element wiring, and there was no reason to let a new section reintroduce
  one on the Job Card. For the same reason `estimate.js` is its own file and is
  **pure event delegation** on the two list containers, and its blank rows live
  in `<template>` elements rather than hidden `<div>`s — a template's contents
  are a detached fragment that `querySelectorAll` cannot reach, so the
  `__prefix__` placeholder can never be picked up by a document-wide sweep.
  Removing a row **ticks DELETE and hides it, never removes the node**: Django
  reads a formset by contiguous index, so pulling a row out of the DOM renumbers
  everything after it.

- **An unassigned spare can be deleted, and only from the Unassigned Hub.**
  Added 2026-07-31. There was previously no way to delete one at all — no route,
  no button, and `/admin/` unreachable by design — so a mistyped ledger entry
  inflated what the workshop owed that shop permanently. `spare_shop_delete_unassigned`
  is scoped to `job_card__isnull=True`: a spare already fitted to a car is removed
  from that car's own Spare Parts section instead, so every row has exactly one
  screen that owns deleting it. Permanent and written to `DeletionLog` under the
  new `ENTITY_UNASSIGNED_SPARE`, like every other financial delete. The Hub also
  stopped querying 200 job cards for a picker its template never rendered.

- **A spare's shop can change, so BOTH ledgers must be refreshed.** Fixed 2026-07-31
  (AUD-0080). `JobCardSpareItem.save()` only ever called `self.shop.update_totals()`
  — the new shop — so moving a spare from A to B left A's cached
  `total_purchased_amount` still counting a row it no longer owned: one ₹1,000
  purchase showed as ₹1,000 owed to A *and* ₹1,000 owed to B, permanently, and
  clearing the dropdown stranded the debt entirely. `save()` now snapshots the
  previous `shop_id` and refreshes both. The two job-card views need the same guard
  separately, because they resolve the shop with `.update()` (which skips `save()`)
  — they add the pre-edit `spare.shop_id` to `shops_to_update`, which is a set of
  **ids**, not objects. Guarded by `MovingASpareBetweenShopsTests`.

- **Floor may not set prices, and that is enforced on the SERVER.** Fixed 2026-07-31
  (AUD-0081). The template hides prices from Floor but still renders the inputs
  inside a `d-none` cell — it has to, or a mechanic saving the card would blank what
  Office entered. That left the rule as UI-only: a Floor login POSTing
  `total_price=1` turned a ₹5,000 bill into ₹1. `_price_locked_data()` in
  `workshop/views/jobcard.py` rewrites every posted `unit_price` / `total_price` /
  `customer_rate` with the value already stored (blank for a new row) before the
  formsets are bound, so a crafted POST is inert. Do not "simplify" it by deleting
  the keys instead — an absent field saves as empty and wipes the price, which is
  the exact failure the rendered-but-hidden inputs exist to prevent. `JobCardForm`
  itself carries no money fields, so the parts formsets are the whole surface.

- **A Supplies Shop bill's DISCOUNT is part of what the stock cost, and its DATE
  changes the average.** Added 2026-07-30 after an audit found four cost-attribution
  defects, all fixed together. (a) The discount is apportioned pro-rata across the
  bill's lines by value — `SupplierRestockItem.effective_unit_price`, which costing
  uses; `per_unit_price` stays gross for display. Without it, `avg_cost` came from
  gross prices while the Profit page expensed the discounted amount, so one purchase
  carried two costs. (b) A discount above its bill total is **dropped and reported**,
  never applied: it made the bill negative, so the supplier appeared to owe the
  workshop and the Supplies Shops expense went negative, *raising* profit from a
  mistyped zero. `get_effective_amount` is floored at zero as a second line of
  defence. (c) `update_totals()` re-costs the bill's items itself when a discount
  exists — the total is the apportionment denominator, is written with `.update()`
  (no signal), and is only known *after* the lines save, so a line's own post_save
  would divide by a stale or zero total. (d) A `SupplierRestockBill` pre/post_save
  pair re-costs when `bill_date` or `discount_amount` changes, since neither lives on
  a line: backdating a bill across an existing draw left the average stale by ₹818.18.
  Guarded by `inventory/test_supplier_costing.py`.

- **A warehouse draw with no cost basis stores NULL, never 0.** Added 2026-07-30.
  `Item.avg_cost == 0` means the cost is *unknown* — opening stock counted onto the
  shelf before any supplier bill exists, or a product whose only restock bill was
  deleted — not that the part was free. Storing 0 reported those parts as pure profit.
  `JobCardSpareItem.save()` therefore leaves `unit_price` NULL, and
  `analysis_engine.uncosted_draw_count()` counts such draws so the Profit page can say
  so out loud instead of quietly understating cost. Expect this on go-live day until
  the first restock bill for each product is entered.

- **The Job Card edits the two routes as two sections over ONE formset model.**
  Added 2026-07-30. `JobCardSpareFormSet` and `JobCardInventoryFormSet` are both
  inline formsets on `JobCardSpareItem`, with prefixes `spares` and `inventory`;
  each scopes itself to its own `source` in `get_queryset()` and stamps `source` in
  `save_new()` (`SourceScopedSpareFormSet`). `source` is deliberately **not** an
  editable field — moving a row between routes would have to move warehouse stock
  and a shop-ledger balance at the same time. Two consequences worth knowing:
  every job-card POST must now carry the `inventory-*` management form (the
  template always renders it, so a payload without it is malformed, not a
  regression — that is what broke six existing tests), and the shop-resolution
  pass in `jobcard_create`/`jobcard_edit` filters to `SOURCE_SHOP`, because it
  reads `shop_name` as a posted pk and a draw has none.
  **The Inventory product is picked, never typed** — the visible search box has no
  `name` attribute and posts nothing; the hidden `item` field carries the choice.
  Guarded by `workshop/tests/test_jobcard_inventory_section.py`.

- **Three traps in `script.js`'s formset-row cloning, all of which fail silently.**
  Learned the hard way on 2026-07-30 while wiring the Inventory picker; the symptom
  in every case was a control that simply did nothing, with a clean console.
  1. **Never track "already wired" in a `data-*` attribute.** It is serialized into
     the HTML, and the hidden `#empty-*-form` templates are themselves in the
     document — so the initial `initializeAutocompleteInContainer(document)` sweep
     marks the *template's* input as wired and every cloned row inherits the mark.
     Use a `WeakSet` keyed on the element, which a clone cannot inherit.
  2. **Declare those `WeakSet`s at the very top of the `DOMContentLoaded`
     callback.** `const` is not hoisted the way `function` is, so declaring them
     next to the functions that use them left them in the temporal dead zone when
     the initial sweep ran. The `ReferenceError` fired inside a `forEach` callback
     and aborted the rest of the handler — taking unrelated features with it, and
     surfacing in no error log.
  3. **`container.querySelectorAll()` searches DESCENDANTS only.** On the add-a-row
     path the container passed to `initializeAutocompleteInContainer` *is* the new
     `<tr>`, so a selector matching the row itself finds nothing. See
     `inventoryRowsWithin()`.
  Also note the Financial Lock needs no per-section work: it disables via a generic
  `form.querySelectorAll('input:not([type="hidden"]), select, textarea, button…')`,
  so a new section is covered automatically. Hidden FK fields stay enabled on
  purpose — disabling them would drop them from the POST.

- **`JobCardSpareItem.customer_rate` is INPUT ONLY.** It backs the optional "Unit Price"
  box on an inventory row (customer price per unit) and is never back-filled from
  `total_price ÷ quantity`, so a null honestly means "nobody entered a rate" and the two
  figures can never quietly disagree. When it *is* set, `total_price = customer_rate ×
  quantity` is enforced on save, so editing 7 L down to 4 L recomputes the bill instead
  of leaving a stale one. Staff usually skip the box and type the total, so it must never
  be required. "Customer Price" is the UI label for `total_price`, not a third field.
- **The `Mechanic` model is the whole staff roster, not just mechanics — the name is kept
  for continuity, don't rename it.** Added 2026-07-26: `Mechanic.role` (Mechanic / Assistant
  Mechanic / Office Staff / General Helper, default `Mechanic`) turned this from a
  mechanics-only table into the general "Staff Registration" roster shown in the UI at
  `/manage/?section=staff`. The model/table/FK name stays `Mechanic` — same pattern as
  `BulkPayer`/"Fleet Account" above — because `JobCard.lead_mechanic` and years of job-card
  history point at it by id; renaming the class would be a pure-cosmetic, high-blast-radius
  change for no behavioural gain. Only `Mechanic.JOBCARD_ELIGIBLE_ROLES` (Mechanic, Assistant
  Mechanic) can ever be assigned as a Job Card's `lead_mechanic` — Office Staff / General
  Helper never appear in that dropdown. Changing someone's `role` (e.g. Mechanic →
  Office Staff) is an in-place field update on the same row, never a delete-and-recreate —
  that's what keeps `lead_mechanic` on old job cards intact, and it's also what makes this
  roster reusable as-is for the "Salary Advance" section planned next (see
  `TITAN_MASTER_HANDOVER.md` roadmap) and Attendance after that: one staff identity across
  role changes, not a fragmented one. There is deliberately no delete for staff, only
  deactivate (`is_active`) — matches the archived-not-deleted pattern for
  Spare Shops/Fleet Accounts/Supplier Shops above.

- **Owner accounts are `is_superuser=True` but `is_staff=False` — that pairing is
  deliberate, not an inconsistency to tidy up.** `is_superuser` is what every RBAC
  decorator and the `has_group` template filter check, so owners keep full authority
  everywhere inside the app. `is_staff` gates **only** the Django admin site, and
  `/admin/` bypasses the protections the app is built around: a delete there writes no
  `DeletionLog`, the Financial Lock doesn't apply, and archive-don't-delete isn't
  honoured. Clearing `is_staff` closes that door while removing nothing an owner can
  actually do. `sync_owner_identity` re-asserts this on every run. If you genuinely need
  admin, `createsuperuser` a separate account and delete it after — it won't be an
  `OWNER_n` entry, so the command leaves it alone. Consequence worth knowing: with no
  `is_staff` accounts, `/admin/` is unenterable by anyone, which is intended.
- **Never use `is_staff` as a workshop role check.** It means "can log into Django
  admin", nothing more. It was previously used to gate the Invoice link in
  `dashboard_home.html` and `car_profile_detail.html`, which hid billing from the Office
  role whose job it is — while `invoice_view` itself is `@office_required`. Template
  gates must mirror their view's decorator: `{% if request.user|has_group:"Office" or
  request.user|has_group:"Owner" %}`. Guarded by `InvoiceLinkVisibilityTests`.

- **Password reset is a hand-built 6-digit emailed code, not Django's built-in
  `PasswordResetView` link — and that extra ~150 lines is the point, not an oversight.**
  Django's link flow is less code and better tested, and it was the original plan. It was
  rejected for one reason: **on iOS an installed PWA has its own cookie jar, separate from
  the browser.** A link tapped in the mail app opens in Safari/Chrome and completes the
  reset *there*, so the owner returns to the installed app still signed out and has to type
  the new password anyway. Android is better but not guaranteed either — whether a WebAPK
  captures the link depends on digital asset links and how it was installed. A 6-digit code
  is plain text with no such dependency: the reset finishes in the same session that
  requested it, on every OS, installed or not. The owners read this on iPhones, so the code
  wins. See `PasswordResetOTP` and `workshop/tests/test_password_reset.py`. If you are about
  to "simplify" this into `PasswordResetView`, you are about to break the flow on the exact
  device it was built for.
- **The reset code is in the email *subject* line on purpose.** iOS and Android both show
  the subject in the notification banner, so the owner reads the code without opening the
  mail app. The trade — briefly visible on a lock screen — is deliberate and worth it.
- **The reset throttle is now *told to the visitor*, and that is not a regression of the
  non-disclosure rule.** Changed 2026-07-29 on the owner's instruction after a silent
  throttle was read as a broken app: an owner who re-requested inside the cooldown was
  shown "a code has been sent", received nothing, and kept pressing. There are two limits
  and they are treated differently on purpose. `PasswordResetOTP.throttle_reason` is keyed
  to the **account** and stays silent — reporting it would answer "does this account exist
  and can it reset?", which is the entire reason step 1 has one generic reply. The new
  `_own_request_throttle` is keyed to the **browser session** and is reported in full,
  because it describes what this visitor just did and is identical for a real account and
  an invented one. It runs on the same two numbers (60s / 3 per hour) so in the ordinary
  one-owner-one-phone case the message shown *is* the rule that gets applied; it is not a
  security control and clearing cookies resets it. Guarded by
  `test_the_visible_throttle_is_not_an_existence_oracle` — if that fails, the message has
  started leaking account existence and must go back to being generic.
  `test_throttled_request_sends_nothing_and_says_so` replaced an older test that asserted
  the opposite; don't restore it.
- **A reset code that failed to send is deleted, not retired.** `throttle_reason` counts
  rows by `created_at` regardless of `used_at`, so a retired-but-present row still spent
  the hourly budget: three failed sends exhausted it and flipped the honest "could not
  send" error into the generic "code sent" reply, so the app reported two contradictory
  things about one outage. An undelivered code is worth nothing — `issue()` has already
  retired whatever preceded it.
- **Step 2 echoes the submitted code back into the field; it must keep doing so.** Every
  rejection there except a spent code is about the *password*, and dropping the six digits
  with it sent the owner back to the mail app on a phone for a mistake they had already
  fixed. The code is single-use, expiring, and already in their inbox, so echoing it
  reveals nothing. The two password fields are deliberately not echoed.

- **A refused job card says so, names what, and keeps what was typed — and the
  list is built in PYTHON.** Added 2026-08-12, `_collect_problems` in
  `views/jobcard.py`. The error summary at the top of `jobcard_form.html`
  enumerated four formsets by hand and the Inventory section was the fifth, so a
  warehouse draw saved with a blank Qty was refused with **no banner, no
  message and no sound** — the only sign was one line of 0.72rem red text
  several screens down inside a horizontally scrolling table. From the front
  that is indistinguishable from the Save button doing nothing. What it *did*
  print was "Check Spares section for errors" (which withholds the only part
  anyone needs) plus a leftover debugging loop that rendered Django's raw error
  dict onto the page. Three rules now: the list is assembled in the view, so a
  new section cannot be forgotten in markup; each row is named by **what it
  holds** (`InventoryDrawForm.row_label()` → the product), because "Inventory
  item 7" means counting rows on a card with eleven draws; and a `messages.error`
  is raised, which is what makes the banner appear and what plays the error tone
  (sound.js reads the message tag).
  **The visible product box is re-rendered from the POSTED choice, never from
  `instance.spare_part_name`.** That box is not a form field — it posts nothing,
  and the hidden `item` pk is the row's whole identity — so on a rejected save a
  NEW row came back with the pk intact and the box beside it empty, i.e. looking
  untouched, and got filled in a second time. `InventoryDrawForm.search_value`
  resolves it from `cleaned_data` (no query on the normal path).
  **There is now ONE `_form_context()` for every render of the form**, and
  building it closed a live data-loss path: the duplicate-registration refusal
  passed **no `spare_shops`**, so every spare row's shop `<select>` re-rendered
  holding only "-- Shop --". Correct the registration, press save, and each
  select posts blank, the resolution pass clears the FK, and the purchase
  disappears off that shop's ledger. Same failure the archived-shop rule exists
  to prevent, reached through a different door, and needing nothing unusual —
  only a customer bringing a car back before the last card on it was closed.
  Guarded by `ARefusedSaveSaysWhatIsWrongTests`.

- **Car Profiles: the header stacks, the totals come from the DATABASE, and the
  list is paginated.** Added 2026-08-12. Three real defects sat under the
  redesign. The list template read **`search_query`**, a name this view has
  never passed — so the search box came back empty after every search *and* the
  pagination links carried the same dead name, meaning page 2 of a search
  silently returned page 2 of every car in the workshop. The detail view loaded
  a car's **entire** history with no pager and then asked the template for
  `bill.concerns.count` and `.first`, two queries per row. And the summary
  figures are now a single aggregate over the whole history rather than a total
  of the rows on screen — with a pager, anything summed from the page would
  quietly start describing "this page" while labelled "this car", the same
  reasoning that replaced the Cashbook's `LIST_CAP`. "Billed to date" is
  `total_bill_amount − discount_amount`, **the Profit page's own definition of
  revenue**, because a second definition of "what this customer has paid us" is
  the one an owner would end up quoting at the counter.
  On the header: **one row from 768px up, two rows below it** — the owner's
  instruction, and mobile-first so the narrow case cannot be broken by
  forgetting a media query. On a phone the title and a search box with a
  five-word placeholder compete for ~360px and both lose (the count is pushed
  against the edge, the placeholder truncates); above 768px there is room for
  both and giving the search its own line there would push the first card down
  for nothing. A visit row is a `<div>` with a `.stretched-link`, never an
  `<a>` wrapped round other controls — see the Estimates entry for what
  browsers do to that markup.
  **The search box is deliberately the SAME control as Completed's** — the
  values in `.cp-search-*` are copied from `.del-search-*`, not approximated,
  and were verified equal on all ten computed properties in a browser. Those
  two pages are opened one after the other all day and a search box that
  changes shape between them reads as two different products. If Completed is
  restyled, restyle this with it; `TheSearchLooksLikeCompletedsTests` fails
  either way round.
  Three things the page deliberately does NOT carry, all removed 2026-08-12 on
  the owner's instruction. **The colour is worn, not written** — "Red" printed
  beside a red bar is the same fact twice, in a row where every other chip says
  something the rail cannot. **The first concern no longer previews in each
  visit row**: it was the only free-text line in the list, so it made every row
  a different height, and a history is scanned for *when* and *how much* — the
  concerns are one tap away on the card. Dropping it removed the only reason
  the view prefetched that relation. And **a visit row has no Invoice button**:
  the job card it opens carries its own, so it was a second door to the same
  place, costing a column of width on a phone, forcing the row to reflow below
  520px, and needing its own z-index to stay clickable above the row-wide link.
  The row is one target with one meaning now, and it stays two lines tall at
  every width (92px on a phone, down from 186px).
  **The car wears its own colour on both screens — the SAME wash `.lr-car`
  uses on the Live Report, at the identical `14` (8%) alpha.** Added 2026-08-12
  on the owner's suggestion. A rail down the edge plus
  `background-image: linear-gradient(var(--shade), var(--shade))` over the card
  colour. Copying the alpha rather than picking a new one is the point: a car
  you can see has to look the same on every screen that shows it, and a second
  stronger wash invented here would make two pages disagree about one Red car.
  **Both exceptions come with it, and both are load-bearing**: a WHITE car's
  rail is outlined (`inset` box-shadow) or it vanishes against the card, and a
  car with **no colour recorded gets a hatched rail and NO wash at all** — a
  slate tint would say "this car is grey", which is a different fact from
  "nobody wrote it down". One extra rule the Live Report does not need: the
  hero's stat tiles sit *on* the wash, so they carry their own
  `rgba(255,255,255,.72)` ground or they take the tint twice and read as a
  different colour from the card around them.
  **The headline figure is "Total billed"**, not "Billed to date" (unreadable
  at a glance) and deliberately not "Total spent" — that is the customer's side
  of the same number and it is wrong on exactly the cars that matter, since an
  unpaid bill has been billed and not spent. When there is an unpaid part the
  "Still owed" tile appears beside it, so the pair reads without ambiguity.

- **"GROSS PROFIT" on a car profile is GROSS, and the word is the whole
  safety of it.** Added 2026-08-12 on the owner's request for a per-car profit
  figure. It is `revenue − parts cost` — the labour charge (which carries no
  direct cost of its own) plus the margin on both part routes — and it is
  **before wages, rent, power and every other overhead**, because this workshop
  attributes none of those to a car: labour is quoted whole with no hours
  recorded, so there is nothing to apportion by. Measured over the current
  data it reads **45.0% where the business actually makes 31.8%**, and that gap
  *widens* as payroll grows (the seed carries only ₹14L of wages across 13
  settled months). So "Profit" was refused as a label and "Gross profit" chosen
  — the standard accounting term, understood by any owner, with *gross* doing
  the warning. `analysis_engine.build_profit_report` remains the one true
  profit figure in this app. Guarded by `test_it_is_never_called_plain_profit`.
  Four rules hold it up.
  (a) **BOTH part routes are costed, and that is NOT the double-count rule
  being broken.** That rule governs the workshop-wide Profit page, where a
  warehouse draw must never be charged again because a Supplies Shop restock
  bill already paid for it. The question here is a different one — what did
  *this car* cost us — and a part off the shelf cost what the shelf paid for
  it. Nothing is being added to a total that already contains the restock
  bills.
  (b) **`SPARE_COST` is imported from `analysis_engine`, never restated.** It
  is the app's one definition of what a spare cost, shared with the Profit page
  and `SpareShop.update_totals()`; a second copy would be a second answer on
  the screen an owner reads to judge a customer. Since 2026-08-17 it is also
  **route-aware** — a shop line costs what was typed, a warehouse draw costs its
  per-unit average × quantity — which matters most here, because this is the one
  query that spans both routes on purpose. A caller picking one rule for the
  whole queryset would misprice half of every car.
  (c) **It says so when its cost side is incomplete.** `SPARE_COST` counts a
  missing `unit_price` as ₹0, so an uncosted part reads as *free* and pushes
  the figure UP — the one way it can be wrong without looking wrong, and
  CLAUDE.md already warns to expect exactly that at go-live on opening stock.
  The count of such parts is aggregated alongside the cost and printed as a
  quiet caveat under the tiles; a fully-costed car says nothing, because a
  caveat on every car is a caveat nobody reads.
  (d) **Owner only, and not computed at all for anyone else** — `None` from the
  view, so the two aggregates never run and the template gates on the value
  rather than on a second role check free to fall out of step. This is the only
  place in the app where a per-car *cost* appears, and Office is shown the
  workshop's cost side nowhere else.
  **It is deliberately not highlighted**: the same grey tile as its
  neighbours, and on each visit row a small muted line under the bill. The bill
  stays the loudest number in the row. On a phone the word "gross" is dropped
  from that line (the tile above still says it) — the line is `nowrap`, so
  every character widens the right-hand column and takes it from the bill
  number and badges, measured at +24px of row height because those badges then
  wrap.
  **Tile widths are PROPORTIONAL, and the two fixed ones are fixed for a
  reason**: a visit count and a date cannot vary in width, so they get 92px and
  132px and stop taking a money-sized box for a small fact; the money tiles
  flex because their width *is* a function of the data. Sizing every tile to
  its contents is the tempting version and the wrong one — a car billed ₹500
  and one billed ₹1,25,000 would lay the row out differently, so the boxes move
  between cars. Verified at 3, 4 and 5 tiles (Office, no balance, owner) and on
  a 375px phone, where they drop to two per row.

- **The frontend is server-rendered Django templates with page-scoped inline
  JavaScript, and there is no build step. This is the settled architecture, not
  a backlog item.** Added 2026-08-10, because every outside review reaches the
  same suggestion. Measured: ~2,660 lines of inline JS across 34 templates.
  Three shared files already exist — `script.js`, `estimate.js`,
  `notifications.js` — and the rule for what goes in one is **used on more than
  one page**; what stays inline is genuinely page-specific. The usual arguments
  for extracting the rest do not apply here: **there is no CSP** (so no
  hardening is unlocked today), the largest page carries ~12 KB of script read
  by four devices on one shop's LAN (so caching is a rounding error), and there
  is **no npm, no bundler, no linter and no JS test runner** — none of which
  will be added. That last point is the load-bearing one: **nothing in the 956
  Django tests executes a line of JavaScript**, so a JS refactor leaves the
  suite green whether or not it broke, and this codebase has already been bitten
  by exactly that (see the three `script.js` cloning traps above — "the symptom
  in every case was a control that simply did nothing, with a clean console").
  Moving working code with no way to prove it still works is the bad trade, not
  the inline JS. Two rules follow: the printed invoice and estimate load
  **nothing** from a third party and keep their JS inline on purpose (see the
  invoice entry above), and **no new runtime dependency is added without a
  defect it is the only fix for.**
  *Consequence, accepted knowingly:* the AJAX list-search pattern exists as
  **seven near-copies** across the list pages. It has drifted once already —
  `estimate_list.html` gained an out-of-order-response guard the other six never
  received, so they showed stale rows for a fast typist until 2026-08-10, when
  the guard was copied to all of them by hand. Logged as AUD-0086 in
  `TECH_DEBT.md`. A shared `list_search.js` is the textbook fix and was
  deliberately declined: seven working copies beat one untested abstraction on a
  system this close to shipping. Revisit only if that pattern needs changing
  again.

- **Password reset stays an EMAILED CODE. TOTP was considered and rejected.**
  Added 2026-08-10, after Railway's SMTP block sent three separate AI reviews to
  the same suggestion. TOTP is a *second factor*; a reset is a *recovery
  channel*, and the two fail in opposite directions — TOTP proves you hold the
  device, which is worthless exactly when the device is what was lost. That
  matters more here than in most apps because **owners cannot reset each
  other**: `manage_reset_password` refuses any account in the Owner group, so
  email is the only self-service route an owner has. Making TOTP the reset
  would leave a lost phone with no in-app recovery at all. It would also delete
  a working, tested subsystem (`PasswordResetOTP`, `test_password_reset.py`,
  the two-step form, both throttles, the subject-line delivery built for iOS
  PWAs) and add a dependency, to solve a transport problem. The fallback the
  suggestion was really reaching for is a shell password reset, documented in
  `GO_LIVE_RUNBOOK.md` §5.3 — nothing to carry, nothing to lose, nothing to
  expire.
- **Mail leaves over Resend's HTTPS API in production, not SMTP — and only the
  transport changed.** Added 2026-08-10, `workshop/email_backend.py`. Railway
  blocks outbound SMTP on every plan below Pro (ports 25/465/587/2525), and
  Render's free tier does the same — the reset mail timed out at the 10s
  `EMAIL_TIMEOUT`. Since Django routes every `send_mail()` through
  `EMAIL_BACKEND` and this app has exactly **one** call site
  (`auth_views.py:189`), swapping that setting moves the mail onto HTTPS with
  no change to the flow, the throttles or the tests. Written against stdlib
  `urllib.request` rather than `requests` or the `resend` SDK: `requests` was
  removed when Twilio went, and re-adding a dependency to send single-digit
  emails per year is a poor trade. The SMTP block in `base.py` stays, because
  development and any host that permits SMTP still use it. **Verify the sending
  domain on a SUBDOMAIN** (`mail.formuladservice.in`) — SPF/DKIM at the root
  can disturb mail for the business domain itself, which carries the public
  WordPress site.
- **`STATICFILES_STORAGE` is DEAD on Django 5.1+ and Django does not warn.**
  Learned 2026-08-10, and it had been broken in production for months. The
  setting was removed in favour of `STORAGES`; leaving the old name in place
  raises nothing and changes nothing, so this project ran on the plain
  `StaticFilesStorage` while `base.py` said `CompressedManifestStaticFilesStorage`
  — no content-hashed filenames, therefore no far-future caching, and none of
  WhiteNoise's gzip/brotli pre-compression. **The `?v=4` query strings on the
  `<script>` tags in `base.html` are the workaround someone reached for when
  cache-busting silently stopped working; they are what the setting is supposed
  to make unnecessary.** Symptom to recognise: `collectstatic` reports files
  *copied* but none *post-processed*. One-line check —
  `manage.py shell -c "from django.contrib.staticfiles.storage import staticfiles_storage; print(staticfiles_storage.__class__)"`.
  Note that a manifest storage is **strict**: once it is genuinely active, any
  `{% static %}` naming a file that does not exist raises at render time
  instead of emitting a dead link, so re-run the render smoke tests after
  touching it.
- **The app tells search engines to stay out, in two ways, and they cover
  different crawlers.** Added 2026-08-10. `robots.txt` (a `TemplateView` in
  `urls.py`, no view function needed) carries `Disallow: /`, and
  `NoIndexMiddleware` sets `X-Robots-Tag: noindex, nofollow` on every response.
  This is **not** redundancy: a crawler that obeys `Disallow` never fetches the
  page and so never sees the header, so `Disallow` stops well-behaved bots
  while the header is what de-indexes a URL that got in anyway. The middleware
  is deliberately not a `<meta>` tag — the printed invoice, the printed
  estimate and the four signed-out auth pages are all standalone templates that
  do not extend `base.html`, and a fifth would be added one day with nothing
  failing. **Neither is a security control**; every page worth protecting is
  behind a login.

- **A signed-in page is `no-store`, so Back cannot un-log-out.** Added
  2026-08-10, `NoStoreMiddleware`. Logging out flushes the session, so the
  next *request* is bounced to the sign-in page — but Back never makes a
  request. It restores the page from the browser's back/forward cache, fully
  rendered: the dashboard, a customer's bill, the Profit page, on a laptop
  now in somebody else's hands. Nothing server-side can undo that after the
  page has been sent, so the only lever is telling the browser at the time
  not to keep it. Scoped to authenticated responses (it reads `request.user`,
  so it must stay after `AuthenticationMiddleware`); static assets never
  reach it because WhiteNoise returns them earlier in the chain. The cost is
  accepted knowingly: Back re-fetches instead of restoring instantly.

- **The service worker is registered on EVERY page load, and `sw.js` has a
  `fetch` handler that caches nothing.** Added 2026-08-10. Registration used
  to live only inside `enablePush()` in `notifications.js`, which runs when an
  *owner* taps "turn alerts on" in the bell panel — so on an ordinary page
  load there was no worker at all, and Office and Floor had no bell and
  therefore no route to ever register one. Two things followed, and both
  looked like hosting problems: Chrome fires `beforeinstallprompt` only for a
  page with a registered worker **that has a fetch handler**, so the "Install
  Formula D" banner could appear on iOS only (a separate branch in
  `base.html`); and moving host made it look newly broken when what actually
  reset it was the new **origin** — registration, install state and push
  subscriptions are all per-origin, so every device has to re-enable push
  after a move. Chrome dropped the worker requirement for menu-installing in
  108/112 but kept it for the automatic prompt. The registration lives in
  `script.js`, not inline, because it runs on more than one page — this
  codebase's rule for what earns a place in a shared file — and `register()`
  is idempotent, so `notifications.js` calling it again changes nothing.
  **The fetch handler caches nothing and must not start.** The no-caching
  rule at the top of `sw.js` stands; all it does is pass requests through and
  answer a *navigation* that fails with a plain inline "no connection" page,
  so bad workshop wifi reads as an explanation rather than a broken app.
  Guarded by `ServiceWorkerRouteTests` and
  `TheAppRegistersItsWorkerOnEveryPageTests`, which assert both the handler's
  presence and that no cache API is referenced.

- **Abusing the password-reset form now tells BOTH owners.** Added
  2026-08-10. `PASSWORD_RESET` fired only on a *successful* reset, so the
  system announced every routine sign-in and stayed silent for the two
  signals that mean somebody is working through an owner's account — and only
  owner accounts can reach that flow (`can_reset_password`).
  `RESET_CODE_LIMIT` (the account's hourly code budget exhausted) and
  `RESET_CODE_ATTEMPTS_SPENT` (all five verify attempts burned on one code)
  are both CRITICAL. They are the only events raised with **no actor**, so
  they reach both owners including the one targeted: there is no signed-in
  person to exclude, the account holder is who can act, and the other owner
  is the corroboration. Three things are load-bearing. (a) Only the HOURLY
  limit fires — the 60-second cooldown is a double-tapped button.
  `PasswordResetOTP.throttle_kind()` is the single lookup behind both the
  message and the alert, because two implementations of "is this throttled,
  and why?" would disagree exactly where it matters. (b) `recently_raised()`
  de-dupes to one per account per hour: the form needs no login, so without
  it anyone knowing an owner's username could buzz both phones until the
  alert stopped being read — that, not the reset, is the attack. (c) **The
  visitor's response must not change by a single byte.** Step 1 has one
  generic reply precisely so it cannot answer "does this account exist", and
  a notification raised behind it must not become a new way to ask;
  `test_raising_an_alert_changes_nothing_the_visitor_can_see` compares the
  rendered pages for a real and an invented username. Note the ordering does
  most of the work: `_own_request_throttle` is checked first on the same two
  numbers, so an owner fumbling in one browser is stopped by their own
  session log and never reaches here. Getting this far means the requests
  arrived with no session history behind them.

- **A large discount is a flat ₹3,500, not 30% — and it is CONFIRMED before
  it happens.** Changed 2026-08-10 on the owner's instruction.
  `JobCard.HIGH_DISCOUNT_AMOUNT` replaces `HIGH_DISCOUNT_RATIO`, read by
  `audit_high_discounts`, the `HIGH_DISCOUNT` alert and the settle dialog, so
  no two can disagree about where the line is. A proportion answered the
  wrong question: what an owner wants telling about is *money*, and 30% means
  something different on every bill — ₹1,500 off a ₹5,000 service tripped the
  old alert and is a rounding-down at pickup, while ₹7,000 off a ₹60,000
  rebuild did not and is a quarter of a month's margin. Accepted consequence:
  a small bill can now be discounted to almost nothing silently, because the
  amount at stake is genuinely small; the audit page still lists every one.
  Separately, **the settle screen now says what the shortfall becomes.** A
  part-paid walk-in books its shortfall as a discount and is marked PAID —
  the business rule above — and nothing on screen had ever said so, so Office
  typed the figure agreed at the counter and the difference became a
  permanent write-off named nowhere. The running shortfall shows on *every*
  settlement; the confirmation fires **only past the threshold**, because
  confirming what cannot surprise anyone is how confirmations stop being
  read. It does not block — the owner may well have agreed the figure.
  Guarded by `ALargeDiscountIsConfirmedBeforeItHappensTests` and
  `TheDiscountAuditListsByAmountTests`.

- **Outcome sounds ride on Django's message tags, and are wired nowhere
  else.** Added 2026-08-10. Three synthesised tones — success, error, warning
  — played from `data-sound-tag` on the message banner. The app already tags
  every outcome (78 `messages.success`, 94 `messages.error`, 6 warnings), so
  one attribute covers every action in the system and anything added later is
  covered by default. **Do not wire per-button sounds:** ~180 call sites is
  180 chances to attach the wrong tone, and each would fire at *click* time,
  announcing "done" before the server had done anything. `info`/`debug` are
  deliberately silent — a tone for every notice trains everyone to stop
  hearing the two that matter, the same reasoning that keeps the CRITICAL
  push list short. Tones are Web Audio oscillators, **no audio files and no
  dependency**, which keeps the no-new-runtime-dependency rule. Per-device
  toggle in the drawer (`localStorage`, default ON), same shape as the push
  toggle. Two things worth not rediscovering: the printed invoice and
  estimate are standalone templates and had to be given the tag and the
  script explicitly, or the one page where money is actually settled would be
  the one page that stayed silent; and browsers block audio on a freshly
  loaded page without user activation, which Chrome **exempts for an
  installed PWA** — so it is reliable on the owners' phones and the Floor
  tablet, and the first outcome in a plain browser tab may be silent. That is
  a missing nicety, never a missing fact: the banner is on screen either way.
  **Extended 2026-08-10 with a fourth tone, `prompt`, on the ways this app
  asks a question** — a Bootstrap modal (`show.bs.modal`, which bubbles, so one
  document listener catches every one), a native `<dialog>` (no bubbling
  open event, so `showModal` is wrapped once on the prototype), and — added
  2026-08-11 — plain **`window.confirm()`**, wrapped the same way. Those hooks
  cover every confirmation in the app including any added later, same reasoning
  as the message tags. **The third was missed for a day and it was half the
  app.** The original note here said "the two ways", and the `confirm()` sites
  are `onsubmit="return confirm(…)"` attributes across sixteen templates —
  nothing about that markup looks like a dialog needing wiring — against
  nineteen of the other two kinds, so roughly half of every confirmation asked
  its question in silence. `test_every_way_the_app_asks_a_question_is_hooked`
  now scans the templates for all three shapes and fails if sound.js does not
  hook one it finds, because a *missing* hook is invisible to every other kind
  of test. One trap in the wrapper: `window.confirm()` **freezes the main
  thread** until it is answered, so `play()`'s usual `resume().then(tone)` path
  cannot settle and the beep would arrive *after* the decision, where it reads
  as the outcome sound for it. `play(kind, blocking)` therefore resumes for
  next time and stays quiet now — announcing the wrong thing is worse than
  announcing nothing. The native return value is passed straight through; these
  are `return confirm(…)` on a form's onsubmit, so anything else would silently
  submit or silently refuse to.
  It is **gated to questions**: `confirmActionModal`, the
  logout confirm, and anything carrying `data-sound-prompt`. A plain "add a
  payment" form modal is a *workspace* and stays silent — a tone every time a
  modal opened is noise, and noise is how the tones that matter stop being
  heard. Bonus worth knowing: the prompt fires on a real click, so it is never
  blocked by the autoplay policy and it warms the AudioContext, which makes the
  *outcome* tone after a confirmed action audible even in a plain browser tab.
  **Three views were silent and now report themselves** — `mark_completed`,
  `undo_completed` and `toggle_hold` wrote no message at all, so on a tablet
  the card vanished off the board with nothing distinguishing that from a
  mis-tap. Fixing it at the view is what earns them a sound, rather than wiring
  a button; that is the rule working as intended.

- **The two payment histories are one screen, and the Bootstrap dropdown in
  them is SAFE — measured.** Added 2026-08-10. Spare Shops and Supplies Shops
  both keep a payment history in an offcanvas and had drifted into looking like
  different products: a bare trash icon on one and a ⋮ menu on the other,
  different typography, amounts in different colours. They now share markup
  exactly, amounts print green on both, and the tests assert the **parity**
  rather than either implementation — the failure worth catching is them
  drifting apart again. Two things were found while doing it. The delete gate
  on the spare-shop side said `Owner` while `spare_shop_payment_reverse` is
  `@office_required` and its own docstring says "Owner + Office", so it hid the
  action from the role whose job it is — both gates now mirror their decorators
  (the `InvoiceLinkVisibilityTests` rule). And the "Bulk Pay" badge printed on
  every supplier payment unconditionally, so it distinguished nothing.
  **The clipping worry was wrong and it is worth recording why**, because the
  first attempt built a bespoke clip-proof inline menu to avoid a problem that
  does not exist here. `.offcanvas-body` is `overflow-y: auto`, which is the
  usual setup for Popper being clipped — but the body is **full viewport
  height**, so at the bottom edge Popper simply flips the menu upwards and it
  stays fully visible (verified with a scrolled 19-row list, last row hard
  against the edge: menu bottom 74px *inside* the container). The `.cb-list`
  trap is a different shape — `overflow: hidden` on a box barely taller than
  one row, where there is nowhere to flip to. Check which one you have before
  designing around it.

- **A shop header gives up its actions before it gives up its name.** Settled
  2026-08-10 after two attempts, and the second is the rule. The first pinned
  the actions beside the title at every width, reasoning that a control belongs
  next to what it acts on. On a phone that made the buttons and the shop NAME
  compete for one line and the name lost — "Kochi Auto Spares" rendered as
  "Kochi Auto Spa…", cutting off the one piece of text that says what you are
  looking at. Below 768px the actions now take a row of their own, **aligned
  right** so the ⋮ still lands in the corner under the thumb, and the name gets
  the full width with truncation lifted entirely. Above 768px there is room for
  both and nothing gives. Note this is the *opposite* call from the Estimates
  header, and the difference is real: there the action is a short fixed "New
  Estimate" button against a title the page controls, here it is a variable
  count badge against a name the customer chose.

- **Adding a static file means running `collectstatic`, or every page 500s.**
  Learned 2026-08-10 while adding `sound.js`. Now that `STORAGES` genuinely
  points at `CompressedManifestStaticFilesStorage` (see the note above about
  `STATICFILES_STORAGE` being dead on Django 5.1+), the manifest is **strict**:
  `{% static 'js/sound.js' %}` raises `ValueError: Missing staticfiles
  manifest entry` at render time for any file not in `staticfiles.json`. It
  fails in the test suite too, which is how it was caught. Consequence for
  tests: assert on `js/sound.` and never `js/sound.js`, because the rendered
  name is content-hashed (`js/sound.951c822c33d6.js`) — asserting the plain
  filename would only pass for as long as static hashing stayed broken. The
  `?v=` query strings in `base.html` are now belt-and-braces rather than the
  mechanism; leave them, but the hash is what actually busts the cache.

- **Every app icon is GENERATED from one file, and the padding differs by
  purpose.** Added 2026-08-11, `scratchpad/build_app_icons.py`. The owner
  supplies one piece of artwork —
  `static/images/icons/app_icon_source.png` — and the five PNGs plus
  `favicon.ico` beside it are derived from it; none of them is hand-edited, and
  a new mark means replacing the source and re-running the script. Two things
  it does that a resize would not, both load-bearing.
  (a) **It crops to the ink first.** The supplied file sits in a lot of empty
  canvas, and scaled as-is to 32px the mark would be a dozen pixels adrift in a
  white square — which is the failure the icon it replaced already had (a
  *photograph* of the wordmark on a concrete wall, unreadable below 128px, and
  off-centre).
  (b) **It pads by purpose.** The 192 and 512 are declared
  `"purpose": "any maskable"` in `manifest.json`, so Android crops them to the
  launcher's shape and only the central 80% is guaranteed — those get the mark
  at **76%** of the canvas. A favicon is never masked and is fighting for
  legibility at 16px, so it gets **92%**; apple-touch sits between at 84%.
  Consequence worth knowing: **do not show a maskable icon raw.** The PWA
  install banner used `icon-192.png` in a 42px box and rendered a small mark
  adrift in white; it uses `icon-180.png`, the un-inset one, for that reason.
  The background is forced to pure white — the supplied file is near-white but
  not white, the same thing the printed letterhead's artwork needed, and a
  253-grey square is visible as a faint box against a white browser tab.
  Re-run `collectstatic` after regenerating, per the entry above: the filenames
  do not change, but the content hashes do, so without it every page links the
  old artwork.

- **"BILLED BUT NOT FILLED" leads the Live Report, and it reads the SAME
  function the settle dialog does.** Added 2026-08-16 on the owner's
  instruction. Every other box on that page is about work in progress, where an
  empty box is a task nobody has got to yet. These cards have been billed: the
  money moved, the card went PAID, the shortfall became a permanent discount,
  and the Financial Lock now stands between the card and anyone correcting it.
  An empty box on one of those is a hole in the books, so it goes first.
  **`workshop/settlement.py` is the one implementation and is now read at two
  moments** — "you are about to skip this" by the settle dialog, "you skipped
  this" by this container. A second copy would drift, and it would drift
  exactly where it matters: a card the dialog waved through turning up on the
  chase list, or the reverse. `unfilled(jobcard)` returns the grouped
  structure both surfaces draw — the card's own chips (Mileage / Mechanic /
  Job Amount), then a row per unfixed concern, per unpriced draw, per shop
  spare. `settlement_gaps()` and its flat `Gap(key, label, tags)` are gone.
  Six rules.
  (a) **BILLED is `PAID`, `BULK_PAID` and `PARTIAL`.** PARTIAL never happens to
  a walk-in (the shortfall becomes a discount and the card goes straight to
  PAID), so every one here is a Fleet card that has been invoiced and is still
  being collected — which has been billed. It wears amber rather than the
  settled green, because money is still owed.
  (b) **The narrowing is in the DATABASE and the detail in Python, and they are
  kept in step deliberately.** `_billed_but_unfilled()` is an index lookup in
  front of `settlement.unfilled`, never a second opinion — every clause mirrors
  a check in it, `Trim` included, and `Coalesce` runs first because
  `TRIM(NULL)` is NULL and a card that never had a mileage would otherwise
  match nothing. The view still drops any card whose computed gaps come back
  empty, so a drift can only ever show FEWER cards — never an empty red box,
  which is how an owner learns to stop reading a warning.
  (c) **The two spare DATES are ONE gap.** A part is finished when it has been
  ordered *and* received, so half-filled is still incomplete; which of the two
  is missing is answered by opening the date panel on the card. The old flat
  list's separate "Not received" chip went with it — status without dates and
  dates without status are the same fact, and `spare_autofill.js` derives one
  from the other anyway.
  (d) **A concern is named by its WORDING**, which reverses the old dialog's
  rule. There, quoting a TextField cost three lines of a dialog read in two
  seconds. Here somebody is deciding which car to walk over to, and the wording
  is the whole point. Both surfaces clamp it in CSS, so the stored text is
  never what gets shortened. It carries no STATUS — see "One gap, one box".
  (e) **`count` is in GAPS, not rows** — a spare missing four things is four
  problems, and that number is what says whether this is a typo or a card
  nobody filled in.
  (f) **Paginated, not windowed by date.** It is a queue to be worked down; the
  heading carries the true total so the size of it is visible, and nothing is
  hidden behind a filter that would have to be widened to find the oldest and
  worst cards. Sections cap at `UNFILLED_ROW_CAP` (8) with the remainder named.
  Guarded by `workshop/tests/test_billed_but_not_filled.py`.

- **ONE GAP, ONE BOX — and it holds the thing AND what is wrong with it.**
  Added 2026-08-17 on the owner's instruction, and it governs **both** surfaces
  above: the "Billed but not filled" container and the settle dialog.
  A gap used to be drawn as the name of the thing on one line with small red
  chips on the line beneath it, under a section heading carrying a count. One
  part missing one figure cost three lines and five elements, and a car with a
  concern and two parts was a fourteen-line block. Everything on it was true and
  none of it could be taken in at a glance, which the owner reported exactly:
  "lot of rows and texts to confuse user". A row now reads
  **"Castrol Edge 5W-30 — no customer price"**, in one bordered box.
  Six things hold it up.
  (a) **The phrases live in `settlement.MISSING` and are DERIVED from the chip
  labels**, not written out beside them. The labels are still a gap's identity —
  `count` counts them and the tests name them — so a second hand-written list
  would be one vocabulary twice, free to drift into a screen that chases "Shop
  Price" here and "no supplier price" there. `PartGap.missing`,
  `ConcernGap.missing` and `Unfilled.card_missing` are the only things either
  template prints.
  (b) **A concern says "not fixed", and its STATUS is gone from the module.**
  Dropping it is more correct rather than merely shorter: PENDING vs WORKING is
  a real distinction while the car is on the floor, and the moment it has been
  billed and driven away "Working" is a claim about the present that is not true
  — nobody is working on a car that left last Tuesday. What is true, and the
  only thing anyone can act on, is that it was never marked fixed.
  (c) **The SECTION HEADINGS went with the chips.** Each row carries the icon of
  the job-card section it belongs to instead — the same glyphs
  `jobcard_form.html` heads Customer Concerns / Inventory Items / Spare Parts
  with — so the icon says where to go and costs a line of type rather than a
  line of the page. The heading's count went too, and the capping rule survives
  it intact: a capped section still prints its exact remainder, so the visible
  rows plus "+N more" are the true total.
  (d) **On the Live Report each car is its own CARD.** "Need separation between
  cars" was the other half of the instruction — a hairline between rows ran a
  list of four together as one wall of red.
  (e) **The tint INVERTS between the two surfaces' chips and these boxes, and
  that is deliberate.** A chip was white-on-red because it sat directly on the
  section's red wash, where a red chip is a smudge. These boxes sit on a white
  card, so a faint red ground is what separates them and the red is spent on the
  words that say what is missing.
  (f) **The phrase WRAPS, it never truncates.** Measured at 375px: an ordinary
  row is one line (31px), a part missing two things wraps to two, and the worst
  case — a part missing all four — is three. The phrase being readable is the
  whole point of the row.
  The two templates stay separate MARKUP (the invoice loads nothing from
  anywhere and carries its stylesheet inline, so an include would still declare
  the classes twice) but never separate RULES;
  `test_the_dialog_prints_the_phrases_the_module_names` is what says so.

- **The settle dialog is AMBER for a question and RED for a warning.** Added
  2026-08-16 with the above. An uncompleted card on its own is a contradiction
  worth pausing on — the data is fine, and the button beside it fixes the one
  thing wrong — so it keeps the amber frame it always had. The moment anything
  is actually *unfilled* the frame turns red, because settling is what closes
  the door on correcting it. `readiness['is_critical']` decides in Python, so
  the frame and the body cannot come to disagree about which of the two this
  is; red outranks amber when both apply. Neither state blocks — two of the
  three buttons still go forward. **Note for any test of this:** `.pf-critical`
  is also a rule in the invoice's own inline stylesheet, present on every
  render, so assert on the rendered `<dialog>` tag — the same trap
  `ThePaidStampAppearsOnlyOnceSettledTests` records for `.paid-box`.

- **The Live Report is Office and Owner only, whole page — "Live Jobs" is
  gone.** Rewritten 2026-08-16 on the owner's instruction; the entry below is
  what remains true of the 2026-08-12 build. The detailed per-card list under
  the board was removed because the home page's own car cards, and the live
  details that open inside them, do that job better and are where Floor already
  works. With it went the only part of the page Floor could reach, so
  `live_report` is now `@office_required` rather than `@staff_required` with
  the board gated internally — everything left is supplier names, ordering
  state and money-side gaps, none of which Floor is shown anywhere else in the
  app. The nav pill was always gated `is_owner or is_office`, so the template
  gate and the decorator now agree, which is the `InvoiceLinkVisibilityTests`
  rule. The `q`/`status` filters went with the list; nothing on the page is
  narrowed by anything, and `test_financial_report_exhaustive_filters` is
  inverted to say so rather than deleted. `SECTION_ROW_CAP` and
  `_card_sections()` are deleted; `_capped()` stays, shared with the home
  board.

- **The Live Report's operations board is Office and Owner only.** Added
  2026-08-12, on the owner's instruction; the role gate has since moved to the
  whole page (see above), and everything below still governs the board itself.
  `/jobcards/live-report/` is the screen an owner opens on a phone to see the
  workshop without ringing anybody: who is holding which car, which parts are
  travelling, which parts nobody has ordered. Five rules hold it up.
  (a) **Only a SHOP part is ever chased.** A warehouse draw
  (`source='INVENTORY'`) came off the shelf already fitted, so its `status`
  column means nothing — the same deliberate rule the Live Jobs card already
  followed by badging draws "Stock". Listing one as waiting would send
  somebody after a part that is already on the car, and the live data has
  exactly that row: an INVENTORY spare sitting at `PENDING`. Rows on a
  completed or deleted card are out too (a stale PENDING on a delivered car is
  work nobody is going to do), as are spares with no job card — every row here
  opens a job card, and an unassigned spare has none to open.
  (b) **The board ignores every query parameter**, and since 2026-08-16 so does
  the rest of the page — `q`/`status` were read only by the Live Jobs list and
  went with it. The reasoning is unchanged and is why they were never widened
  to the board: it answers "what is the state of the workshop right now", and a
  half-filtered answer to that is worse than no answer.
  (c) **The "Not assigned" group's position is decided in Python, never by
  `order_by('lead_mechanic__name')`.** PostgreSQL sorts NULL last on an
  ascending sort and SQLite sorts it first, so a database ordering would put
  that group at a different end of the page in the tests than in production.
  (d) **A mechanic holding no car is not listed** — the owner's call: every
  name on the board has work under it, which is what keeps it short.
  (e) **A capped section names its remainder, and the cap lives in the VIEW.**
  `SECTION_ROW_CAP` is gone with the Live Jobs list, but the rule outlived it —
  `HOME_SECTION_ROW_CAP` (25) and `UNFILLED_ROW_CAP` (8) both follow it, through
  the one shared `_capped()`. **Never `|slice:":10"` in the template:** a cap in
  the markup and a remainder computed from a constant are two versions of one
  rule, free to disagree, and they would disagree as a "+3 more" beside eleven
  visible rows. Parts are what forced it: a rebuild in the live data carries 91,
  which rendered one card 3,314px tall and pushed every other car off the phone.
  Capping is safe on these lists and would not be on a money list: no total sits
  above the rows for the hidden ones to fall out of, the exact number left is
  printed rather than implied, the section heading still reports the true total
  so the two add back up, and every hidden row is on the job card the row
  already opens.
  **The page's shape was then set by the owner over 2026-08-12, and every
  choice below is theirs, not a default.** The Live Jobs card itself was
  removed on 2026-08-16 (see above); its four-section rule is kept here because
  **the home page's live-details drawer follows it exactly**, and that is now
  where it lives.
  *The card is FOUR sections* — Customer Concerns, Job Performed,
  Inventory Items, Spare Parts — in the order the work happens. The last two
  used to be one "Parts" list, and splitting them is what makes the badges
  mean something: only a bought-in part has an ordering state anyone can act
  on, and every warehouse draw used to carry an identical "STOCK" badge that
  distinguished nothing from nothing. So two sections carry a badge and two
  carry a bullet, which is the honest split rather than an inconsistency — a
  job performed is in the list *because* it was done, and a draw came off the
  shelf already fitted. **The printed invoice still merges both routes into one
  PART NAME list and that rule is untouched**: a customer has no interest in
  which shelf a part came off, an owner reading the floor does.
  An empty section is omitted entirely rather than printing "none" — four
  headings with two apologies under them, on every card, is noise multiplied by
  the length of the list; a card with nothing in any of the four says so once.
  *Mechanics are PANELS in a grid* (`.lr-crews` / `.lr-crew`), each name
  heading a column of that person's cars, **four across on a laptop, three on a
  tablet, two on a phone**, with `align-items: stretch` so panels on one row
  end level. A bare column with a rule beside it was tried first and read as
  clutter: a rule is only as tall as its column, so three mechanics holding
  three, two and one car drew three vertical lines of three different lengths
  and nothing lined up with anything. **A filled panel has no length to
  disagree about.** The column count is fixed per breakpoint rather than
  `auto-fill`, because the owner asked for a specific rhythm — four names to a
  row, the fifth wrapping underneath.
  *The car's name is the big word*, with the registration and the age sharing
  one very small line under it.
  *There is ONE age wording on the page* — `New`, `1d`, `213d`, from
  `_age_label()`. There were briefly two (a long "213 days" for the roomier
  Live Jobs card) and the owner collapsed them: the same fact worded two ways
  on one screen invites being read as two different facts. Day zero is **New**,
  not "Today", because the line answers how long the car has been here rather
  than what today's date is. The clock glyph in front of it is gone — an icon
  that explains nothing is one more thing to step over on a page built for
  scanning.
  *In the Live Jobs lists the STATUS leads the row and the wording follows.*
  What is scanned down those lists is state, not prose: a column of badges all
  starting at the same x reads in one sweep, where badges ragged-right against
  sentences of different lengths have to be hunted line by line.
  **`.status-badge`'s `min-width` is what holds that column straight** —
  without it "FIXED" and "RECEIVED" start their text at two different places
  and the whole point is lost.
  *The two spare boxes are SQUARE and their rows carry no card of their own*:
  the tint of the box is the row background, ruled off with a hairline, so each
  box reads as one block of colour. A white card floating on amber made every
  row a separate object, when the meaning is "these all belong to the same
  problem". Note the consequence, deliberate and the owner's to change:
  **"On the floor" keeps its rounded corners and white cards while the two
  below it are square** — the instruction named those two boxes.
  *"Not assigned" is RED* in both halves of the page — the board column and the
  Live Jobs chip — because it is the one label here asking for a decision, and
  a colour that meant urgent above and neutral below would mean nothing.
  *The badges are ONE traffic light* — red not started, amber under way, green
  done. `.status-working` and `.status-ordered` therefore **share a single
  declaration**, as do `.status-fixed` and `.status-received`: each pair means
  the same thing about a different kind of row, and two hand-written ambers
  would drift apart. ORDERED was blue until the owner changed it on
  2026-08-12; the amber it now uses is the "On the way" box's own, so a part
  waiting on a supplier reads the same colour wherever it is named on this
  page.

- **On the Job Card, an EMPTY box wears a hairline and a CHANGED box wears an
  amber edge — two marks, two different facts, and neither may move the
  page.** Added 2026-08-13 on the owner's instruction. The scope was argued
  and the owner chose the wide one knowingly: **every** empty box is marked
  except the ones carrying **`jc-optional`**. The exemption is declared on the
  **widget in forms.py**, not as a list of names in the template's script — one
  mechanism, sitting where somebody adding a field will see it. Exempt today:
  **Customer Name, Contact Number, the Internal note** (this workshop fills
  them on a minority of cards, so a mark would be permanent, and a mark that is
  always on is a mark nobody reads) and a **SHOP spare's Qty** (added
  2026-08-13 on the owner's instruction — nothing refuses a save without it,
  and the live data is full of rows that never had one).
  **The two spare DATES are NOT exempt, and are marked as a PAIR.** They were
  briefly exempt alongside the quantity and the owner reversed it the same day:
  a spare is finished when it has been ordered **and** received, so the chip
  stays marked until both are filled — half-filled is still incomplete, not
  half-done. The mark sits on the **chip**, because that is what is on screen;
  the two inputs inside the panel are swept like any other box, which is what
  says *which* of the two is missing once it is open. One control cannot carry
  two facts, so it does not try to. Guarded by
  `ADatePairIsOnlyDoneWhenBothAreInTests`.
  **The INVENTORY quantity is deliberately NOT exempt while the spare one is**,
  and that asymmetry is the rule working: the same word carries two different
  obligations — a warehouse draw is refused without a quantity, because that is
  the number leaving the shelf — so the mark follows the obligation, not the
  label. Guarded by
  `test_an_inventory_quantity_is_still_marked_when_a_spare_one_is_not`.
  Four rules hold it up.
  (a) **It is border COLOUR only** (`#eda9a9`, replacing Bootstrap's own 1px
  `#dee2e6`), so an unfilled box is a slightly warmer outline rather than an
  error. That restraint is the entire reason it can be applied this widely:
  measured, an ordinary edit carries **68 marks on a card with 12 spares**, and
  at any louder weight that is a page-long alarm. A border *width*, a padding
  or a margin here would reflow the parts tables as you type — the trap
  `.inventory-stock-hint` already exists to avoid.
  (b) **It is NOT the error state.** `.jc-row-invalid` paints a row's
  background and is what a refused save looks like, so the two cannot be
  confused.
  (c) **A settled card wears none** — the Financial Lock disables every box on
  a PAID record and an empty box on a closed one is nothing anybody will fill.
  Done as `.jc-empty:disabled` in CSS, deliberately, because the lock is
  applied on a `setTimeout(…, 100)` and script reading that state would race
  it.
  (d) **The amber `.jc-changed` edge is `box-shadow: inset`**, painted inside
  the box the browser already laid out. Three marks hang off one class on the
  body (`jc-dirty`) so they cannot disagree: that edge, the **sticky header
  turning amber**, and a note on the Save button — plus a `beforeunload`
  prompt. **The header tint is the signal that carries, not the pill**, and
  that ordering was forced by measurement: on a 375px phone the title had 150px
  for 240px of "Editing: Audi A4 KL 10 AA 1919" and was *already* truncating,
  and adding a 79px pill to that flex row cut it to 63px — "Editing:" and
  nothing. That is the Spare Shop header rule again (a header gives up its
  actions before it gives up its name), so the **wording is held back until
  576px** and a background colour, which occupies no width at all, does the job
  below it. It needs `!important` because the header sets its background inline
  alongside the car-colour rail. **`dirty` is cleared only on a submit that was not prevented** —
  the Financial Lock and the Inventory guard both cancel, and clearing the
  warning on a submit that never left would drop it on the one card still
  needing it. Two places fill boxes in script and therefore fire no event:
  `importSpare()` and the colour picker, both of which call
  `window.jcFormTouched()`. Guarded by `workshop/tests/test_jobcard_form_ux.py`.

- **The Job Card's blank-row DELETE flags are RECOMPUTED on every submit, not
  latched.** Fixed 2026-08-13, found while adding the guard below. The four
  passes that mark an empty concern / spare / draw / job for deletion only ever
  set `checked = true`, and a submit can be cancelled *after* they have run —
  the Financial Lock's own handler does exactly that. So a row left blank on a
  refused attempt stayed marked, and typing into that row and saving dropped
  what had just been typed. They now assign `checked = !value.trim()`, which is
  the only version that survives a cancelled submit; nothing else in the form
  ever ticks a DELETE box (they are all rendered inside `d-none`), so
  recomputing from the row itself is safe.

- **A warehouse draw with no quantity is refused in the browser by a SCRIPT
  guard, never by `required`.** Added 2026-08-13. `InventoryDrawForm.clean`
  already refuses it on the server and that stays the real rule; this only
  saves the round trip. The `required` attribute cannot express "only once a
  product has been picked" and breaks badly twice here: it blocks the **submit
  event**, and the handler that marks blank rows for deletion lives in that
  event — so a card carrying one untouched blank row would refuse to submit
  with nothing on screen — and a `required` control the browser cannot focus
  (`#empty-inventory-form` is in the document, inside `d-none`) makes Chrome
  abandon the submit **silently**. The guard runs on `document` in the
  **capture** phase and calls `stopPropagation()`, so a refused submit never
  reaches the DELETE-marking handlers at all. It names the product, not the row
  number, exactly as `_collect_problems` does server-side.

- **The Job Card shows the car's colour as a RAIL, not a wash — and the
  Internal note never leaves the workshop.** Added 2026-08-13. A full-page tint
  in the car's colour was built first, at the same 8% alpha `.cp-card` and
  `.lr-car` use, and the owner had it removed: on a form this long it sat behind
  every section for several screens, which is a lot of colour to carry for a
  fact the header rail and the colour dot beside the registration already state.
  What remains is `.jc-head::before`, one strip at the top, driven by
  `--jc-accent` on `#jcColour`. Both documented exceptions travel with it: a
  WHITE car's rail is outlined, and a car with **no colour recorded gets a
  hatched rail** rather than a slate one, because "nobody wrote it down" is a
  different fact from "this car is grey". The shared picker **dispatches
  `carcolour:change`** rather than letting each page reach into it — setting
  `.value` in script fires no event, and the Estimate uses the identical control
  and wants none of this. `JobCard.notes` mirrors `Estimate.notes` field for
  field; it is unprintable by construction (`invoice.py` and the invoice
  template both read named fields) and
  `test_the_internal_note_never_reaches_the_customer` keeps it so against the
  day somebody adds a generic field loop. It is **not** price-locked, so Floor
  may write one — that is the point of the box.

- **Three pieces of Job Card text were removed, and one of them moved rather
  than went.** 2026-08-13. The "Job Performed" column heading sat under a card
  titled "Jobs (Labour)" over a single column whose boxes are placeholdered
  "Job Performed" — a heading earns its place by telling one column from
  another. "Ordered from a spare shop" restated a section that has a Shop
  column, a Status and two dates. **The Inventory subtitle is the one to be
  careful about**: the fact it carried is real and recorded above — the picker
  searches *categories* as well as products — and it now lives only in that
  box's own placeholder, "Search by product or type (e.g. Engine Oil)", three
  inches lower and at the moment somebody is about to type. **That placeholder
  is load-bearing now.** Shorten it and the explanation has to come back
  somewhere; `test_the_inventory_box_still_says_it_searches_by_type` fails if
  it goes.

- **BOTH price boxes on a Spare Parts row hold the LINE TOTAL, and on a row of
  more than one they both say "total".** Settled 2026-08-17 over three passes,
  and the end of it is the simplest thing on the row — but the route there is
  worth keeping, because two of the three passes were wrong.
  The boxes used to hold different KINDS of number: `unit_price` was the price
  of ONE (multiplied by Qty for the shop's ledger and the Profit page) while
  `total_price` was the whole row (summed exactly as typed). Pass one added a
  `×2` badge to the Shop Price to warn about the multiplication. Pass two, on
  the owner asking whether the badge belonged on both boxes, marked each one
  differently — `×2` here, `total` there — because a `×2` on the customer box
  would have been an expensive lie: anyone believing it would type the per-unit
  price and halve the bill. Then the owner asked the question that ended it:
  **"2 different logic may make user confused? what is the stable flow?"** It
  did, and the stable flow is one logic — so the shop side stopped multiplying
  (see the `unit_price` entry above) and both boxes now mean the same thing.
  **What is left is one mark, one word, one condition**, and it earns its place
  by doing a different job from the badge it replaced: on a row of 2 it is the
  reminder to type ₹7,560 for the pair rather than ₹3,780 for one — the mistake
  that is still possible, and the only one left. Six things are load-bearing.
  (a) **Both are `<span>`s with no name.** They post nothing, compute nothing
  and store nothing; they are driven off the Qty box already in the row. Anyone
  auditing the money can ignore them entirely.
  (b) **INSIDE the boxes, absolutely positioned on the left**, so the mark reads
  at the left edge with the typed value at the right. Both are `text-end`, so
  the left half is space the value never occupies and neither mark costs any
  height. Anything that appears *below* a control moves every row under it — the
  trap `.inventory-stock-hint` already reserves space to avoid, and this table is
  worked on the Floor tablet. Measured: row 60.7px and page height 1972px,
  **identical with the marks showing and hidden**, across 2, 12, 0.5, 1, 1.00,
  blank, junk and 0. `padding-left: 52px` is applied only while a mark is there,
  and it is what stops a big figure sliding underneath — verified that a
  10-digit value makes the input SCROLL (scrollWidth 151 > clientWidth 138)
  rather than paint over its own padding.
  (c) **Both appear together, under one condition: a quantity that is not ONE.**
  One condition and one appearance, so a row can never mark one figure and not
  the other. On a row of one, "total" is true of every box on the page and says
  nothing, so both stay clean — the common case.
  (d) **Scoped by field NAME (`spares-…-quantity`), so the Inventory section is
  untouched** — a draw's Unit Price genuinely IS per unit there, and its cost
  comes off the warehouse average rather than a typed box. Verified: an
  inventory row carries no mark element at all.
  (e) **Pure delegation, no per-element wiring**, so a row added by "+ Add
  Spare" works with nothing re-initialised — all three of `script.js`'s
  documented cloning traps live in per-element wiring, and this section has
  none. `refreshRowTotals()` rides the same three sweeps the date chips do
  (`change`, `jcFormTouched`, DOMContentLoaded) plus the per-keystroke `input`
  path, because typing the Qty is exactly when the marks are needed and they
  must not wait for a blur. On a locked card they take the muted palette with
  the boxes they sit in.
  (f) **Floor is shown neither**, because Floor is shown no prices at all — its
  price cells are rendered inside `d-none` purely so they keep posting what
  Office entered. Guarded by `BothPriceBoxesAreLineTotalsTests`, which pins the
  pairing, the order, that neither can post, and that the *tbody* (never the
  whole page, where the stylesheet declares the same class names) is what gets
  searched.

- **The two spare DATES share one cell, and the column order follows the order
  the row is filled.** 2026-08-13, on the owner's instruction: Part Name · ⋮ ·
  Qty · Status · Shop · Dates · Shop Price · Customer Price, money last. The
  dates were two full-width columns costing a **measured 357px** of a table
  that already scrolls sideways on the Floor tablet, and they are blank on most
  rows because `spare_autofill.js` fills them from the Status dropdown — they
  are derived far more often than typed. They were first stacked in one cell
  (154px, but +24px of row height); on the owner's instruction they became **one
  CHIP reading `22/07 – 29/07`** that opens a small panel holding both. Final
  measurements: the cell is **148px** and the row is back to **55px**, the
  height the two-column version had — so the chip bought back the width *and*
  the height the stack had cost. A missing half prints an ellipsis
  (`22/07 – …`) so the chip always says which date you have; neither prints
  "Add dates". Showing only the date matching the current status was considered
  and dropped: correcting an ordered date on an already-received row would then
  need the box revealing first.
  **The panel is `position: fixed`, and that is two decisions in one.**
  (a) It is out of flow, so opening it cannot move a row — verified: table,
  row and page heights are byte-identical open and shut, which was the owner's
  requirement. (b) It is the only position that **escapes the clip**: the panel
  sits in a `<td>` inside `.table-responsive`, which is `overflow-x: auto`, and
  an absolutely-positioned panel in there is cut off invisibly and only
  sometimes — the `.cb-list` trap this file already records an afternoon for.
  Proven rather than assumed, twice: nothing between that cell and the root
  creates a containing block (no `transform`/`filter`/`will-change`/`contain`),
  checked *before* building; and on a 375px phone the panel hangs 8px past the
  scroller's right edge while `elementFromPoint` still returns its inputs.
  Three smaller things travel with it. The **inputs are unchanged form fields**
  with their names, inside the form — a hidden input still submits its value,
  so only where the boxes are *shown* changed and nothing about the data did.
  Everything is **delegated off `document`**, so a row added by "+ Add Spare"
  works with nothing re-initialised — all three of the cloning traps recorded
  above live in per-element wiring, and this section has none. And every button
  in it is **`type="button"`**: a bare `<button>` inside a form submits it, so
  one wrong and looking at a date saves the card.
  **Column order is safe to change and the reason
  is worth knowing** — every script touching these rows resolves fields by a
  row-scoped `querySelector` on the field NAME (`spare_autofill.js`,
  `importSpare`), never by cell position. What is *not* safe is dropping a cell:
  an absent formset field saves as blank, which is how the archived-shop bug
  erased a purchase from a ledger. **`#empty-spare-form` must be reordered in
  the same edit** — it is cloned by script.js and would otherwise lay an added
  row one column adrift of its header; nothing in the browser would say so, so
  `test_the_added_row_template_matches_the_live_rows` says it here.

- **Every Job Card section announces itself the same way, and the primary
  action says which act it is.** Added 2026-08-13 on the owner's instruction.
  Six sections now share one heading shape — a tinted glyph tile, the name, the
  action on the right (`.jc-sec-head`) — where there had been six hand-rolled
  flex rows whose only common element was a blue `<h6>`. **The Customer block
  had no heading at all**, so scrolling the form you counted "Vehicle Details …
  (something) … Customer Concerns", and the unnamed block was the one people
  were least sure they had filled in.
  **The band is ONE colour and it is a SOFT NEUTRAL: `#f8fafc`, with a 34px
  `#f1f5f9` icon badge at `#475569` inside a `#e2e8f0` border, and the name in
  `#0f172a` at 1.08rem/700.** ⚠ *Corrected 2026-08-18. This entry described a
  filled `#2a70da` blue band with white-on-blue contents, sampled from the nav
  gradient — that was true when it was written and has not been true for some
  time: the form carries the neutral above, `test_the_band_colour_comes_from_the_nav_bar`
  no longer exists, and `#2a70da` survives in this file and in two comments
  inside `jobcard_form.html` only as a description of something that is gone.
  It was caught by building the read-only page against the note instead of
  against the form. **Read these values off `.jc-sec-head` itself, never off
  this paragraph.***
  What is still true is why there is ONE colour at all. A six-step **ramp** was
  built first, on the owner's idea (each section a step further left, so the
  colour said how far down the form you were), and the owner looked at it and
  chose one flat treatment: **the sections are not a scale of anything** — a
  car's concerns are not "more" than its vehicle details — so six shades
  invited being read as a ranking, and the darkest drew the eye hardest at the
  bottom of the form where the least urgent sections live. Also still true:
  **a control on the band must not be tuned to one band colour**, which is what
  `btn-outline-primary` was when it disappeared into the Add buttons, and
  **the symbol keeps a tile** so it stays an object rather than dissolving into
  the band.
  **The read-only twin copies all of it** — `.dv-sec-head` in
  `jobcard_detail.html` is the same six values, and
  `test_the_section_band_is_the_forms_own_colour` compares the two rules so
  neither can move alone. Note the trap that test records: `.jc-sec-head` is
  re-used further down the form's stylesheet by the locked-record palette, so a
  selector match on `endswith` finds the wrong rule and reads as the band having
  changed colour when it has not.
  **The field labels are 600**, on the owner's instruction, at the colour and
  size they already had — weight is the only axis touched. They sit above boxes
  whose placeholders are deliberately quiet (see the top of `jobcard_form.html`),
  and at 400 the label and the hint read as the same kind of text. Not 700:
  the band above them is already 700 and they would compete with it.
  **Below 576px the Add button gives up its WORD, not the section its NAME.**
  Measured on a 375px phone: "Customer Concerns" needed 169px and had 134,
  because "+ Add Concern" was taking 124. Icon-only it is 44×44 (`min-width`
  as well as `min-height` — a target is only as big as its smaller side) and
  every one carries an `aria-label`, this codebase's rule for anything that can
  become icon-only. Same call the Spare Shop header records: a header gives up
  its actions before it gives up its name.
  **The submit button is AMBER on an edit and GREEN on a create, and neither
  carries a shadow.** Settled 2026-08-13 over three passes, and the reasoning
  is worth keeping because the first two looked right. `btn-primary` blue put
  the one control that matters most into a page that is now mostly blue, so it
  stopped being the loudest thing on it. A deeper navy (`--nav-blue-1`) was
  tried next and the owner rejected it: it solved the problem by being a
  *darker blue* and still read as one more blue thing. **Amber is the only
  colour on this page already about your changes** — the header goes amber, the
  pill is amber, every box you touched wears an amber edge — so the button that
  commits them wearing it is the page agreeing with itself. Amber forces DARK
  text and that is not optional: white on `#f59e0b` measures 2.2:1, `#1e293b`
  on it measures 6.81:1. Both colours come from **one `--jc-action`** read by
  the big button and the sticky one, so the two can never drift apart. Shadows
  were removed on the owner's instruction — the border light says "unsaved"
  now, and a drop shadow under it was a second, duller copy of the same
  message. The **"You have unsaved changes" line went with them**: it was a
  third copy of that fact and the only one that changed the button's *height*
  when it appeared.

  **The feedback is built for a FINGER, not a pointer**, on the owner's
  correction: these sections are worked on the Floor tablet, where hover is
  wrong twice over — it never fires on a touch screen, and where it does fire
  it **sticks**, so the last button tapped sits there looking half-pressed
  until something else is tapped. Every hover rule is therefore behind
  **`@media (hover: hover)`** and reaches a mouse only. What a finger gets is
  **`:active`** (fires on touch, releases with the finger) as a real squash —
  `scale(.94)` and a filled-in background, not the token 0.97 and 1px shadow a
  pointer would need, because it has to read at arm's length. The browser's own
  grey tap flash is replaced via `-webkit-tap-highlight-color`, or it fights
  the `:active` paint and lands a beat later.
  **An added row announces itself**, which is the other half of the same
  problem: the "+ Add" button is at the top of its section and the new row
  lands at the bottom of a list that may already be below the fold, so the only
  evidence of a tap was a scrollbar changing length. The row flashes
  (`jc-rowin`, a `background-color` keyframe — paint, so nothing moves) and is
  brought into view with `block: 'nearest'`, which scrolls nothing when it is
  already visible. `void row.offsetWidth` is needed to restart the animation on
  a second press; re-adding a class an element already carries does nothing.
  **A light SWEEPS across a pressed button**, on the owner's request for a
  sliding effect — a `translateX` on a pseudo-element, composited, costing no
  layout. It is **fired by a class on `pointerdown`, never by `:active`**: a tap
  releases in about 80ms and takes `:active` with it, so an animation hung off
  `:active` is cut off halfway on the exact device this is for.
  **While there is unsaved work, a light TRAVELS THE BUTTON'S BORDER** — the
  owner's idea, replacing a pulsing glow, "looks like the button has life". It
  is better than a pulse for a stateable reason: a pulse changes the button's
  apparent SIZE, so the eye keeps being pulled back to something growing and
  shrinking; a light running the edge is movement with no change of weight. It
  is **WHITE**, not amber — the edit button is amber now, so an amber light on
  it would be invisible, and white reads on the green create button too, so one
  gradient serves both. The `--jc-orbit` ANGLE turns, not the element:
  rotating the element would turn the ring with it and skew a wide rectangle.
  It is confined to the border by a `padding` + two-mask pair; without that the
  gradient washes across the button's face. **Built as progressive
  enhancement** — the ring needs `mask-composite` (Safari 15.4+) and a
  registered `@property` (Safari 16.4+), so a still white 2px INSET outline is
  declared unconditionally and the `@supports` block clears it where the ring
  can actually be drawn. An old browser loses the animation and still says
  "unsaved"; it never shows a broken ring and never shows nothing.
  **This is the ONLY looping animation on the page** (besides `jc-spin`, which
  sits on an element that is `display: none` until a save is in flight). An
  idle shimmer is noise on a screen staff work all day and costs battery on the
  tablet; this one is temporary, the person can end it, and it stops the moment
  the card is saved.
  Everything animates `transform`, `box-shadow` or `background-color` — all
  composited or paint-only, so a control reacting to a press can never nudge
  the form under the finger aiming at it. `prefers-reduced-motion` drops the
  sweep and the glow entirely and **keeps the colour**, because the colour is
  the feedback and both motions have a still equivalent already on screen.
  **One press makes one job card**: the button goes to "Saving…" and then
  disables, and `disabled` is set in a **`setTimeout(0)`, never inline** —
  disabling a submit button from inside its own submit handler cancels the
  submission in some browsers. The button carries no `name`, so dropping it
  from the payload costs nothing. Add buttons and the date chip are **38px, and
  44px under `@media (hover: none)`** — keyed on input method rather than a
  width breakpoint, because it is the finger that decides how big a target must
  be and the Floor tablet is wider than plenty of laptops.

- **The sticky save button is INSIDE the form, and is absent until there is
  something to save.** Added 2026-08-13 on the owner's request for a round
  button in the bottom-right so nobody has to scroll to the foot of a form
  several screens long, with the big button kept. Three things hold it up.
  (a) **Inside the `<form>`, which is an integrity matter and not a layout
  one.** The Financial Lock disables controls with `form.querySelectorAll(…)`,
  so a floating button outside the form would be the one control the lock never
  reached — a settled, locked job card, saveable from a button in the corner.
  Inside, it is disabled with everything else for free (verified: the lock's own
  selector matches it).
  (b) **It is not there unless the card is dirty.** The owner's condition was
  "no interruption to the total job card view", and the answer to that is not a
  smaller button — it is a button that is absent. It appears the moment
  something is typed and leaves when the card is saved, so it is never in the
  way of anybody with nothing to save and can never be pressed pointlessly.
  (c) **It clears the phone's bottom nav.** On ≤640px the bar renders at the
  BOTTOM, so a plain `bottom: 24px` would put this on top of it; the offset is
  `calc(var(--nav-h) + env(safe-area-inset-bottom) + 16px)`, both variables, so
  it follows the bar if that ever changes. Measured on a 375×812 phone: 15px
  clear. Stacking is **1020 — under the nav (1030) and under the date panel
  (1035)**: it must never cover navigation, and never cover a popover somebody
  opened deliberately. Both doors are disabled together on submit, or a second
  tap posts the card twice — two job cards for one car on the create page.
  Guarded by `TheStickySaveTests`.

- **Customer Details is FOLDED SHUT, because this workshop mostly does not
  record one.** Added 2026-08-13 on the owner's explanation of how the business
  runs: Owner 1 deals with customers personally and keeps those relationships
  himself, so **the workshop identifies a car by its registration, not by whose
  it is.** Most job cards carry no name and no number, and three permanently
  empty boxes between Vehicle Details and Customer Concerns are three boxes
  everybody scrolls past on every card. Nothing was removed and nothing was made
  harder — the same three fields, one tap away — and it is the same judgement
  that already exempts all three from the empty-box hairline: a box nobody is
  expected to fill should not be nagging, and should not be taking the screen
  either.
  It is a **native `<details>`**, not a JavaScript panel: nothing to wire, so
  nothing to get wrong, and keyboard plus screen-reader behaviour for free. The
  load-bearing fact is that **a closed `<details>` still SUBMITS the inputs
  inside it** — `display: none` has never stopped a form control posting — so
  folding changed what is on screen and nothing about what is stored;
  `test_a_closed_section_still_saves_what_is_typed_into_it` is the guard, and if
  it fails every customer name in the workshop is being wiped on save.
  **It opens itself whenever there is anything to see**: a card that HAS a name,
  a number or a note renders open, and so does one whose refused save put an
  error on one of those fields — otherwise the message hides behind a summary
  nobody thought to click, and the page says "not saved" while showing nothing
  wrong. Collapsed-by-default is only right while the section is genuinely
  empty.

- **WHO THE CUSTOMER IS is Office and Owner only; the INTERNAL NOTE is open to
  everybody.** Added 2026-08-16 on the owner's instruction, and it is the entry
  above taken one step further: the workshop identifies a car by its
  registration because Owner 1 deals with customers personally and keeps those
  relationships himself, so a mechanic never needs to know whose car it is. The
  job-card form was the only screen that would have told them — the invoice,
  Car Profiles, Job Cards, Completed, Paid Bills and the Fleet pages are all
  `@office_required` already, and **`jobcard_detail` was the one leak, since it
  is `@staff_required` and printed both fields with no gate**; it now carries
  the same gate the form does.
  The note stays open because it is about the CAR, not the customer ("noise
  only when cold", "do not wash") and the mechanic is usually the one who finds
  out. So the section holds different things for the two audiences and is
  **named differently for each** — "Customer & Notes" for Office and Owner,
  "Workshop Note" for Floor. A heading reading "Customer Details" over a box
  that says nothing about the customer is the page misdescribing itself.
  Three things are load-bearing.
  (a) **The two fields are simply NOT RENDERED for Floor**, which is safe here
  and would not be in a formset: an absent field on a ModelForm leaves the
  stored value alone, whereas an absent formset field saves as blank and wipes
  the row. That is the same asymmetry the hidden price inputs exist for.
  (b) **A crafted POST is answered separately, and it has to be.** Hiding a box
  is presentation; `_floor_locked_data` pinning the stored value is the
  control. Both directions matter — a payload can invent a customer *or* erase
  one, and only pinning (rather than dropping the key) stops the second.
  (c) **`_price_locked_data` was renamed `_floor_locked_data`.** The rule it
  enforces was never about money: *a field Floor cannot see on any screen must
  be a field Floor cannot post from any screen.* A helper called "price locked"
  that also pins a phone number is precisely the drift this file exists to
  prevent. `OFFICE_ONLY_CARD_FIELDS` names the two. Guarded by
  `WhoTheCustomerIsIsOfficeOnlyTests`.

- **The internal note is a TEXTAREA that grows, and its label no longer says
  "never printed".** Added 2026-08-16. It was a single-line `TextInput`, so a
  two-sentence note — which is what the workshop actually writes — could only
  be read by scrolling sideways through it. `rows=1` rather than a taller
  default, because most cards carry no note and three empty rows on the longest
  form in the app is three rows everybody scrolls past.
  **Built as progressive enhancement, the same shape as the save button's
  travelling light**: the CSS declares a draggable one-row textarea, and
  `autoGrow()` sets `overflow: hidden`, drops `resize` and sizes the box only
  once it runs. A page whose script never arrived is never left with a box that
  clips its own text. One trap: a textarea inside a CLOSED `<details>` is
  `display: none`, so `scrollHeight` reads 0 and sizing it there collapses the
  box the moment the fold is opened — hence the `offsetParent` guard and the
  `toggle` listener.
  **"— never printed on the bill" came off the label**, on the owner's
  instruction: "Internal" already says it, and that clause was the longest
  label on the form. The GUARANTEE is untouched and was never the label's job —
  `invoice.py` and the invoice template both read named fields, so a column
  nobody references cannot print, and
  `test_the_internal_note_never_reaches_the_customer` is what enforces it.
  *The Estimate's identical note box is deliberately NOT changed in the same
  edit* — it is a short quotation line, not a running record, and the two forms
  are only required to agree about the label and the placeholder rule.

- **"Job Performed" is suggested from the parts already on THIS card.** Added
  2026-08-16, answering the owner's question about auto-filling it. Nearly
  every job line in this workshop is a part on the same card plus a verb —
  "Engine Oil replaced", "Wheel Bearing replaced", "Brake Disc refurbished" —
  so the source is the card's own two parts sections, not a master list. The
  mechanic fitted these exact things, the whole line arrives in one pick, and
  there is no second taxonomy to keep in step with anything. Four rules.
  (a) **A native `<datalist>`, for the reason the Estimate's part names already
  use one**: nothing to wire, so a job row added *after* page load gets the same
  list with nothing re-initialised — and none of `script.js`'s three documented
  cloning traps can be reintroduced. It suggests and never fills: a job with no
  part behind it ("Road test") is typed exactly as before, and a browser that
  ignores datalists loses nothing.
  (b) **A warehouse draw is offered by its CATEGORY, never its branded SKU** —
  "Engine Oil", not "Castrol Edge 5W-30" — through `invoice.item_display_name`,
  which `part_display_name` also calls. That split was made for this: both
  strings end up on ONE document, so a job line naming the brand beside a part
  line naming the category is the invoice contradicting itself, and it publishes
  the supply chain into the bargain. A shop spare keeps its free text.
  (c) **The list is rebuilt on FOCUS of a Job Performed box**, delegated on
  `document`. That is the one moment it is about to be used and therefore the
  one moment it has to be current — and it needs no event from the inventory
  picker or the spare autocomplete, which would be coupling to maintain in
  three places. `data-category` on an inventory row is written by the server for
  saved rows and refreshed by the picker for one chosen just now; it starts
  EMPTY on `#empty-inventory-form`, so a cloned row can never inherit the
  previous row's category.
  (d) **The verbs exist in exactly one place**, ordered by the owner's own
  measurement — replaced ~70%, then removed-and-installed, refurbished,
  inspected, repaired at 7-8% each. The order is load-bearing: a datalist keeps
  document order for whatever survives filtering, so opening it cold shows one
  "replaced" line per part before any variant of anything. Guarded by
  `workshop/tests/test_job_line_suggestions.py`, which asserts everything the
  server owes the script — nothing in this suite executes JavaScript.

- **The Inventory row's "38 in stock" line is shown while PICKING and not
  afterwards.** Added 2026-08-16 on the owner's instruction, inverting
  `test_a_saved_draw_shows_its_stock_without_being_re_picked`. The count answers
  one question — is there enough on the shelf to take — asked at the moment of
  choosing and never again. On a card reopened weeks later it is a number about
  TODAY's shelf beside a part fitted long ago, printed once per row, which on a
  card with eleven draws is eleven lines of noise. `stock_display` returns ''
  for a row with a pk; the picker still writes the line the instant a product is
  chosen, on a new row or when an existing row's product is changed.
  **The empty div still reserves its height** — that rule is untouched and is
  the whole reason the div stays: a line that appears when it is written to is a
  row that jumps under the finger aiming at it.
  **The PICKER'S OWN SUGGESTIONS stopped printing it too, on 2026-08-17** ("it's
  everywhere, it's interrupting"). A dropdown row now carries the product and its
  category and nothing else. That is the same rule finished rather than a second
  one: the count belongs in exactly ONE place — under the box, the instant a
  product is chosen, gone once the card is saved — and printing it on every row
  of a list somebody is still reading NAMES in made a number that matters once
  into the loudest thing on screen. `script.js` still parses `item.stock`,
  because the hint line under the box is written from the same response.

- **A part cannot arrive before it was ordered, and the rule lives in
  `workshop/spare_dates.py`.** Added 2026-08-16, on the owner's report
  ("Ordered date 2026, Received date 2025 editing allowing"). Two dates on one
  row, and exactly one mistake the pair can express that neither date can
  express alone. It was already refused on the Unassigned Spares hub and was
  **not** checked on the job card — which is where most spares are actually
  entered. `pair_problem(ordered, received)` is now the one implementation, and
  `_clean_spare_dates` calls it after parsing rather than restating it; two
  answers to "is this pair the right way round" would disagree exactly where it
  matters, on a supplier's ledger. Three things worth knowing: **half a pair is
  never wrong** (ordered-and-not-yet-arrived is the normal mid-workflow state,
  and an empty pair is chased by "Billed but not filled" instead); a **future**
  date is refused too, because it is far more often a mistyped year than a plan
  and this workshop has no forward-ordering workflow; and the error is attached
  to `received_date` rather than raised as a non-field error, so the hairline
  lands on the box being corrected. `ShopSpareRowForm` also gained the
  `row_label()` contract, so the error summary names the PART rather than "row
  7". A row marked DELETE is not argued with. Guarded by
  `workshop/tests/test_spare_dates.py`.
  **Said WHILE it is typed, and said SHORT — both added 2026-08-17 on the
  owner's report** ("this screen totally confusing, lot of texts"). What they
  were looking at was the cost of only checking on the server: a mistyped year
  came back as a re-rendered form several screens long, with a red banner, a red
  summary box under it, and the reason spelled out after a field label —
  "Received date: Received date cannot be before the ordered date — this part
  would have arrived before it was ordered". Four pieces of furniture for one
  digit, none of them beside the box holding it. Two fixes, and the first is
  what means nobody sees the second. (a) `refreshChips()` runs the same rule in
  the browser the moment the two boxes disagree: the date chip turns red and one
  short line appears inside the panel. It is **not** `required` and it does not
  cancel the submit — a browser constraint on these boxes breaks the form in the
  two ways the Inventory quantity guard already records. (b) The server wording
  is now "Arrived before it was ordered — fix the date." / "Ordered date is in
  the future." A message that repeats the box it is attached to and then argues
  its case is one nobody finishes. **Keep the two implementations word for word
  identical**, in the same order — the browser copy exists only to save the
  round trip, and the moment it says something different from the refusal it
  causes it is worse than not being there. Its date arithmetic is string
  comparison on the ISO values and `todayISO()` is built from LOCAL parts, never
  `toISOString()`, which converts to UTC and so reports yesterday for the whole
  of an IST morning.
  **The panel's Done button greys out while the pair is wrong** (2026-08-17, the
  owner asked whether that was possible "without complications" — it is, and it
  is one line). Be clear what it is: **not a lock.** The panel still closes on
  Escape and on a tap outside, and it has to — a popover whose only exit is
  conditional on its contents is a trap, and on a phone the way out of a trap is
  reloading the page and losing the card. What it is, is the primary control
  declining to agree with you, at the moment and in the place the mistake was
  made; the chip stays red after the panel closes and the save is still refused
  server-side, so none of the enforcement lives here. One hazard to keep in
  mind: that button is `type="button"`, so a disabled state cannot swallow a
  submit. If it ever becomes a submit, this has to go.

- **`jobcard_form.html` closes its `<form>` before its wrappers, and that
  ordering is load-bearing.** Fixed 2026-08-13 (was AUD-0093). Two `</div>`s
  used to sit above the submit block: the HTML parser pops `<form>` when an
  ancestor `<div>` closes, so the Save button ended up a **sibling** of the
  form rather than inside it. It still submitted — the parser's form-element
  pointer associates a control created while a form is open — and *that* is
  what made it a trap rather than a bug: nothing looked wrong, while
  `form.querySelectorAll(...)` silently skipped everything past that point. It
  cost nothing until the empty-box sweep was added, which would have missed any
  control placed there. Guarded by `TheFormIsWellFormedTests`.

- **UNASSIGNED SPARES is open to FLOOR, add-only — and the price is stripped on
  the SERVER, not hidden in the template.** Added 2026-08-16 on the owner's
  instruction. The mechanic is who takes delivery of a part, so letting them
  record it is the only way the shop ledger is not a day behind; but Floor is
  shown cost nowhere else in this app. So `unassigned_spares_hub` is
  `@staff_required` and resolves `can_manage` / `can_see_prices` once, while
  `unassigned_spare_edit` and `spare_shop_delete_unassigned` stay
  `@office_required` — Floor adds and never changes what is already there.
  The half that matters is in `unassigned_spare_add`: for a non-Office user it
  passes **`PRICE_NOT_SUPPLIED`** instead of reading `unit_price` at all, so a
  crafted POST carrying a price writes nothing. Hiding the box is presentation;
  this is the control. Exactly the shape of AUD-0081, and
  `test_a_crafted_price_from_floor_is_ignored` is the guard.
  **An unpriced row stores NULL, never 0** — the same distinction the warehouse
  draw rule already makes: zero says the shop gave the part away and would
  settle the ledger at a figure nobody agreed, NULL says nobody has priced it
  yet. `SpareShop.update_totals()` coalesces NULL to 0, so an unpriced row adds
  nothing to the balance until Office fills the figure in from the shop's bill.
  Blank in Office's own price box means the same thing, on both the add and the
  edit path, so there is one rule rather than one per door.
  **An ARCHIVED shop's rows stay listed, stay editable, and keep their shop.**
  Archiving hides a shop from the pickers; it must never hide what is owed to
  it, or that debt is reachable from no screen at all. The group carries an
  "Archived" badge, takes no new purchase, and `unassigned_spare_edit` resolves
  an active shop **or the row's own shop whatever its state** — the same rule as
  `_resolvable_shops()` on the job card, and for the same reason: correcting a
  typo in a part name must not be the thing that walks that purchase onto
  another shop's ledger. The edit modal re-adds that archived shop as an option
  client-side so the select round-trips.
  **Two dates, checked as a pair.** `_clean_spare_dates()` refuses what nobody
  can have meant — unparseable (never silently stamped with today: both boxes
  are `type=date`, so anything else is a crafted POST and writing a date nobody
  chose onto a supplier's ledger is worse than refusing), a date in the future
  (these rows are created RECEIVED), and received-before-ordered. `blank_is_today`
  separates the two doors: on ADD an empty box means "the usual" because it
  arrives pre-filled with today, on EDIT it means somebody cleared it and that
  has to stick.
  **Layout: one horizontal scroller for the add form, and two inline buttons
  rather than a ⋮ menu.** Stacked, the form is seven full-width rows on a phone
  and the Save button lands below the fold; scrolling sideways is also the
  gesture staff already use on the Job Card's Spare Parts table, which is the
  screen this one sits beside in their day. And a Bootstrap dropdown inside a
  horizontal scroller is **clipped** — `overflow-x: auto` computes `overflow-y`
  to `auto` too and Popper cannot escape a clipping ancestor, the `.cb-list`
  trap again — so the row actions are two buttons, which is also one tap instead
  of two on the tablet. Delete still opens a confirmation. Guarded by
  `workshop/tests/test_unassigned_spares.py`.

- **PAID BILLS is Office-visible with a 7-day window; the HIGH DISCOUNT AUDIT is
  not.** Changed 2026-08-16. Office settles bills, so it needs to look one up
  and check what was taken for it — a few days' worth, not the year's. The
  window is enforced in `paid_bills_list`, **not** by hiding the filter
  dropdown: `?filter=all` is one URL edit away, so the template only decides
  whether to render a control the view already refuses to honour. The bills
  inside the window are shown in full, per-card amounts included; what is
  withheld from Office is the **grand total**, which is a business figure rather
  than a settlement one — say that plainly rather than calling it revenue
  concealment, because seven days of bills can be added up by hand.
  `audit_high_discounts` stays **`@owner_required`** (AUD-0041). It reads as what
  the workshop settled for against what it billed — the compensating control for
  the part-paid-books-a-discount rule — and it was briefly widened to Office by
  an outside change that deleted the line saying why. Its entry in the Paid
  Bills ⋮ menu is gated to match, because a door Office can see but not open is
  worse than no door. Guarded by `workshop/tests/test_paid_bills_rbac.py`.

- **FLOOR may put a card on hold and mark it completed. It may not UNDO a
  completion.** Added 2026-08-16 on the owner's instruction. Both buttons had
  been rendered for Floor all along while `toggle_hold` and `mark_completed`
  were `@office_required`, so pressing either gave a mechanic a 403 on the one
  screen they use all day — the template gate and the decorator disagreeing,
  which is the `InvoiceLinkVisibilityTests` rule in the other direction. Both
  are now `@staff_required`: neither moves money, and a hold is reversed by the
  same button. `undo_completed` is deliberately **not** widened — it can put a
  second active card on the floor for one registration and has to answer that
  rule when it does. Guarded by `workshop/tests/test_floor_board.py`.

- **The home board caps each drawer section at 25 rows, and `_capped()` is ONE
  function taking the cap as an argument.** Added 2026-08-16. A cap is needed
  because a rebuild in the live data carries **91 spares** and there are 45
  cards to a page; 25 rather than the Live Report's 10 on the owner's
  instruction, because this page is for taking in the whole floor at a glance.
  Safe here and not on a money list: no total sits above these rows, the exact
  remainder is printed rather than implied, the heading still reports the true
  count so the two add back up, and every hidden row is on the job card the card
  already opens.
  **The two boards were briefly two `_capped()` functions of the same name in
  one module, and the later silently shadowed the earlier** — so the home board
  capped at the Live Report's 10 while every comment said 25, and nothing on
  screen would have shown it, because the remainder line stayed arithmetically
  correct. One function, an explicit cap per call site, and
  `test_the_two_boards_do_not_share_one_cap_by_accident` pins it. As ever the
  cap lives in the view and never as `|slice` in the template.

- **THE CASHBOOK HEADLINE IS TWO FIGURES. There is no Net card.** Changed
  2026-08-16 on the owner's instruction, replacing the earlier "Net is rendered
  only when there IS income" rule. The workshop does not work out a cashbook
  net — cash in is very rare, cash out is constant — and the netting off belongs
  to the owner's Analysis section. Removing the card moves no money and hides
  no data: the Profit page does **not** read this screen,
  `analysis_engine.cashbook_income()` and `cashbook_expense()` aggregate the
  entries themselves, and `cashbook_totals['net']` is still computed in the view.
  What this page owes the business is that both sides are captured accurately,
  which is what `BothSidesAreCollectedEvenThoughOnlyTwoAreShownTests` asserts.
  The third card's CSS is deleted rather than left behind — dead rules for a
  card nobody renders is how the next person concludes it belongs there.
  `filter_label` now comes from the view: the window's name was written out
  twice in `_stats.html`, once per figure, which is two copies of one fact free
  to drift on the very headline whose job is to say which period the figures
  describe.

- **The vehicle and customer boxes carry NO placeholder.** Changed 2026-08-16 on
  the owner's instruction, and it reverses the older "Meter 00001" note. Every
  one of those boxes sits under a label that already names it, so the hint
  restated the label in quieter type — a second line of text per box, on the
  longest form in the app, for no fact. Both the Job Card and the Estimate strip
  them, and they strip them in `__init__` from one list rather than widget by
  widget, so the two forms cannot drift. The placeholders that survive earn it
  by saying something a label cannot: the Inventory picker's "or type" (which is
  load-bearing — see the entry above) and the money boxes' currency. Guarded by
  `test_the_vehicle_and_customer_boxes_carry_no_placeholder`.
  **The ones that survive are drawn QUIETLY, on both forms.** The Job Card has
  said so since the placeholder block at the top of `jobcard_form.html`
  (#b6bfcc, 0.86em, fading further on focus, colour and size only so no box
  changes height); the Estimate form was given the identical block on
  2026-08-17 on the owner's instruction, and needed it more — a quote is mostly
  empty boxes by design, one "Job to be performed" / "Part Name" / "Qty" /
  "Amount (₹)" per row, so at the browser default a blank estimate read as a
  filled-in one. **One exception, and it is told apart by the ATTRIBUTE:** the
  unit-price box carries two placeholders — the plain label, which is now as
  quiet as its neighbours, and `avg: 1064`, which `estimate.js` writes when the
  part has sales history and which is real information somebody is meant to
  notice. `.estimate-rate[placeholder^="avg"]::placeholder` keeps that one
  italic and darker; the selector works because `el.placeholder = …` reflects
  onto the attribute, so the rule follows the script with nothing to keep in
  step (the alternative was a class toggled on both branches of a function that
  already has two).

- **A LOCKED job card has to LOOK locked.** Fixed 2026-08-16. The form grew a
  soft-surface palette that painted every control `#f1f5f9` — and that was also
  what a `:disabled` control was painted, so on a settled card the Financial
  Lock disabled every field and none of them looked any different. The banner
  said LOCKED while the form under it looked ready to type into: the one screen
  where an edit is dangerous was the one screen giving no sign of it. Locked is
  now its own palette (cooler fill, visible border, muted text, `not-allowed`),
  deliberately further from the live state than the live state is from hover,
  and `[readonly]` gets the same treatment because the settlement screen uses it
  and it means the same thing to whoever is looking.
  Two rules travel with it. The extra treatment is keyed on the form's own
  **`data-locked`**, which `toggleRecordLock()` maintains — read in CSS rather
  than in script because the lock is applied on a `setTimeout(…, 100)` and
  script would race it, the same reason `.jc-empty:disabled` is a CSS rule. And
  the state is restated on every section heading as the **word** "LOCKED", not
  an icon-font glyph: a codepoint would depend on a stylesheet fetched from a
  CDN, and this is not the screen to take that bet on. Note the trap that cost
  two test failures — those rules re-use `.jc-sec-head` / `.jc-sec-icon`, so
  they are declared at the FOOT of the stylesheet; a copy earlier in the file
  is what several tests find first when they split on a selector. Guarded by
  `ALockedRecordLooksLockedTests`.
  **A running CSS TRANSITION outranks `!important` — it is the highest origin
  in the cascade, above important-author.** `.form-control` transitions
  `background-color` and `border-color`, so inspecting a locked card anywhere
  that is not painting frames (a background tab, a headless snapshot, a
  screenshot tool) reads those two properties as the LIVE colours while `color`
  and `cursor` — not transitioned — read as the locked ones. It looks exactly
  like an `!important` rule losing to nothing at all, and cost an hour on
  2026-08-16. `element.style.transition = 'none'` and re-read; the locked values
  appear. The wrong fix is a more specific duplicate rule — one was written and
  removed again, because a second copy of a palette is two things free to
  disagree. This applies to any measurement of a transitioned property on this
  codebase's forms, not just the lock.

- **The dashboard car card is worked with a THUMB, and its polish pass follows
  from that.** Added 2026-08-16, on the owner's request to polish without
  changing content or structure. Four changes, each a reason rather than a
  preference.
  (a) **The car's colour is stated twice, not three times.** It was the 10px
  stripe, an 8% wash across the card and a 20% coloured halo behind it. The
  halo was the weakest of the three and the only one that read as a rendering
  artifact — a red glow around a white card looks like something failing to
  paint, and down a list of 45 it turns the gaps between cards into colour. It
  now appears only under a POINTER, where it reads as the card lifting.
  (b) **Hover is behind `@media (hover: hover)`.** This board is worked on the
  Floor tablet all day, where hover never fires on touch and, where it does,
  STICKS — the last card tapped sat raised and shadowed until something else
  was tapped, which reads as a card still loading. Same rule the job card's own
  buttons follow.
  (c) **`:active` does something again.** It was an empty rule with the comment
  "Feedback removed to prevent blinking", so tapping a card gave nothing at all
  on the one device where it is always tapped. The blinking came from moving
  the card; a press that changes only paint plus a 0.5% settle cannot blink.
  (d) **The hold dot no longer BLINKS.** A 2s infinite loop per held card, on
  the screen the workshop looks at most — the same reasoning the job card
  records: an idle animation is noise on a board staff work all day and costs
  battery on the tablet. Nothing is lost, because "on hold" was already said
  three times over (the pill's word, its red ground, the dot's colour); a soft
  ring gives it presence at rest instead. That was the page's only looping
  animation, so `prefers-reduced-motion` now only has the press and lift to
  turn off, and both have a still equivalent already on screen.
  Two smaller ones: the **⋮ grew from a ~20×24px target to 34px, and 44px under
  `@media (hover: none)`** — keyed on input method rather than screen width,
  because it is the finger that decides, and it sits beside the card's own
  click area so a near miss opened the job card instead of the menu; and the
  **reg badge's border was softened** because `#cbd5e1` around a near-white
  fill is a darker line than the card's own border, so the plate outranked the
  card it sits on.

- **The card says the progress ONCE loudly, and the car's name is never cut
  off.** Added 2026-08-17, the owner's follow-up to the polish pass above
  ("premium look… within seconds, no confusion, no stress"), and it settles the
  `.car-name` question that entry deliberately left open.
  (a) **The ratio is a caption on the ring, not a headline.** "1/1" was
  0.95rem/800 in near-black — the second heaviest thing on the card after the
  car's name — with **DONE** under it in uppercase, and the ring beside it
  saying the same thing again as a percentage. Three tellings, one of them
  shouted. The ratio stays, because "2 of 5" is the fact a percentage rounds
  away, but at 0.74rem/700 in `#94a3b8` and with `tabular-nums` so it cannot
  change width as it counts up. The word DONE is **gone**: it labelled a number
  that already reads as a proportion, and it was one of only two uppercase runs
  on the card.
  (b) **The ring's track lightened and its stroke thinned to 3px.** At `#e2e8f0`
  the *unfinished* part of the ring was itself a mark, so the ring read as two
  arcs competing rather than one arc of colour on a hairline.
  **The ring is TWO colours and carries a tinted DISC.** Added 2026-08-17, the
  owner's follow-up ("round indicator need more good design"). It ran red under
  30%, amber to 60%, blue to 99% and green at 100% — and the board that produced
  is what they were looking at: three cars in, two showing a red 0% and one an
  amber 33%, so a perfectly normal morning read as three warnings. The colour
  was encoding **progress** while being decorated like **urgency**, and progress
  is not urgent: a car admitted two hours ago has done nothing yet and that is
  correct. It was also wrong in both directions at once — the 16-day card sat
  amber while a two-hour card sat red, because the ring knows nothing about age
  (that is the pill beside it). So **green means finished, one blue means under
  way**, and how far along it is is the ARC, which is what an arc is for. Two
  colours can be told apart at a glance on a moving tablet; four cannot.
  The **disc** (`r=16`, inside the 3px stroke's inner edge at 16.5, drawn first
  so the track paints over it) fixes the other half: at 0% there is no arc at
  all, so the indicator was a hollow grey circle with a red number in it, which
  reads as something that failed to load rather than as a car nobody has started.
  A body at every value makes it a badge that fills instead of a ring that is
  missing.
  (c) **Hover does LESS**, on the owner's instruction — the lift and the
  coloured halo are gone, leaving the border coming forward. Hover is the
  minority case on a board worked with a thumb; (b) in the entry above is why it
  is behind `@media (hover: hover)` at all.
  (d) **`.car-name` WRAPS to two lines instead of truncating**, and this
  reverses the "known and left alone" note the polish pass left here. "Land
  Rover Range Rover Sport" arriving as "Land Rover Range Ro…" is the card
  failing at the one job it has. Clamped at two, so a pathological name still
  cannot push the card down the screen.
  **The dashboard wraps NATURALLY and Completed RESERVES the second line, and
  the difference is the layout, not taste.** The dashboard is a single-column
  list, where a taller card has nothing beside it to look short against.
  Completed is a three-across `row g-3` of self-sized cards, so one wrapped name
  would draw a row of three different heights — the raggedness this codebase
  avoids — hence `min-height: 2.5em` on `.del-vehicle-name`. Measured: every
  Completed card 136px whatever its name, and the longest name unclipped at both
  1280px and 375px.
  *The `no-concerns-badge` also stopped being italic* — it was the only italic
  on the board, which made "no tasks yet", the most ordinary state a fresh card
  can be in, look like an apology.

- **The state is a DOT at the end of the car's name, and the phone's live
  details have no boxes.** Added 2026-08-17 on the owner's instruction, and the
  two halves pay for each other — the first frees width on the card's top line,
  the second frees a screenful under it.
  (a) **No ACTIVE / HOLD pill, just the dot, inside `.car-name`.** The word was
  true of nearly every card on a board of cars currently in the workshop, so it
  distinguished nothing; the only card it mattered on is the held one, and the
  colour already says that. **The dot is unchanged in size and colour** (the
  owner's "same current dot"), hold ring included — what changed is where it
  sits. It is part of the name's own text run, so on a name that wraps it lands
  at the end of the SECOND line, which is where the eye already is; it is
  preceded by **`&nbsp;`** so it binds to the last word and can never be left
  alone on a line the two-line clamp then hides. `role="img"` + `aria-label`
  carries the state that the word used to.
  (b) **The name is BIGGER on a phone than on a laptop** — 1.24rem against 1.15
  — which looks backwards and is not. It used to *shrink* to 1.05rem there
  because it was sharing 332px with the pill and the ⋮ and losing to both.
  Removing the pill gave the line ~77px back (measured), and this is the screen
  read at arm's length while walking, where the name is the only thing you look
  for from that distance. `.car-name` therefore has no rule in the ≤480px block
  at all any more; its phone size lives in the 640px one.
  (c) **The live-details drawer sheds its boxes below 640px, and ONLY below
  640px.** Measured before: four sections, ten rows, a drawer **599.7px** tall
  on a 375px phone — a whole screen for one car — of which **151.6px was section
  heading bars**, with each section additionally inside a white card with its
  own border, radius and padding, on a panel that already has a border. The
  owner read it back as confusing, which is the accurate word: the chrome was as
  loud as the content. **Nothing is removed and nothing is reworded** — sections,
  counts, status icons and the "+N more" tails all stay. What goes is the
  furniture: no card per section, no filled title bar, no rule under every row,
  one hairline *between* sections. Same card, same ten rows, now **449.6px**
  (−25%), with the heading bars down from 37.9px to 24.6px and the row text at
  the size it always was — 0.92rem, unchanged, because the rows are the content.
  Above 640px it is untouched, deliberately — the owner said the desktop reads
  correctly, and a wide drawer has room for boxes that help the eye find a
  section across a long line. One trap worth keeping: **`line-height: 1` on the
  title** is what actually shrank it, because that row is as tall as its tallest
  child and the glyph is the tallest — at the inherited 1.5 a 10px label was
  occupying 20px.
  (d) **The bar says "View" / "Hide".** It spans the whole card, sits directly
  under the car it belongs to and carries a chevron that turns; "View Live
  Details" was three words explaining a control that explains itself, on every
  card in a list of forty-five. The sentence moved to `aria-label`, which the JS
  keeps in step with the word.
  (e) **The ring's track is THINNER than its arc** (2.25 against 3.5), not just
  lighter. Both were 3px, so it was two arcs of equal weight told apart by
  colour alone — and at 0% that reads as a complete grey ring rather than an
  empty one. Declared in CSS, never as a `stroke-width` attribute: an attribute
  is a presentation attribute and loses to any stylesheet rule, so leaving both
  would be two numbers for one line. The disc at r=16 still clears the fatter
  stroke (inner edge 16.25). **The two numbers stay** — "1/2" beside the ring
  and "50%" inside it — on the owner's decision when offered the merge; the ring
  was polished, not rebuilt.

- **`px-5` and `flex-grow-1` on the same Bootstrap button is a wrap waiting to
  happen.** Fixed 2026-08-17 on the owner's report, `add_shop.html` and
  `edit_shop.html`. `px-5` is 3rem of padding *each side* — 96px of a ~187px
  button on a 375px phone — so "Create Shop" had about 90px left for its words
  and broke across two lines, inside a pill, which made the control half again
  as taller than the Cancel beside it. The padding was doing nothing anyway:
  `flex-grow-1` is already what makes that button the wide one, so the two were
  fighting over the same job. Dropping `px-5` and adding `text-nowrap` to both
  buttons fixes it (measured: 111px + 153px filling the 271px form width, both
  47px tall, one line each).
  **Both files, one edit.** They are the same row with different verbs, one
  click apart in the same section, and letting one keep the old shape is how two
  screens start looking like two different products. If a third form copies this
  row, copy the fixed one.

- **A `<tr>` background is INVISIBLE on a Bootstrap table, and that is how the
  refused-row red went missing on both parts tables.** Found 2026-08-17 while
  building the row marks below. Bootstrap 5.3 gives every cell
  `background-color: var(--bs-table-bg)`, which resolves to `#fff` here — an
  opaque cell sitting on top of its own row — so a background declared on the
  `<tr>` is painted over and never appears, **whatever its specificity**. This
  is paint order, not the cascade, which is why `.jc-row-invalid`'s
  `!important` bought nothing. The consequence had been shipped for months:
  `.jc-row-invalid` is the red that says "this row was refused", and on
  `#spare-list` and `#inventory-list` it did nothing at all — the one place
  CLAUDE.md says marking the row matters most, "because the failing box there
  is one of eight in a line". It went unnoticed because the class works
  perfectly everywhere *else* it is used: a concern row and a job line are
  `<div>`s, with no cell painting over them. Both row states are now declared
  on the **cells** (`#spare-list > tr.jc-row-invalid > td`), and the same trap
  waits for any future row state on any table in this app.
  Two smaller things came out of the same fix. **Bootstrap's cell rule
  (`.table > :not(caption) > * > *`) is one class and one element, so a bare
  class on a `<td>` LOSES to it** — silently, on padding as well as
  background; the number column below kept Bootstrap's 8px side padding and
  was left 18px for its digits, enough for "12" and not for three figures.
  Write `.table > * > tr > .yours` and it wins. And **at equal specificity the
  winner is document order**, which is what makes the *position* of the
  refused-row block a rule rather than a formatting choice: it sits after the
  focus block so that "this is wrong" outranks "you are here" on a row being
  corrected. Guarded by `TheRowYouAreInIsNamedAndLitTests`.

- **On the Job Card parts tables the row you are in is NAMED by a sticky
  number and LIT by a focus tint — two marks, two different questions.** Added
  2026-08-17 on the owner's report that "when lot of data came, these sections
  may confuse users". The measurement that reframed it: **`.main-content` is
  capped at 800px, so the form is the same width on every device.** The spares
  table is 1200px and hides **432px on a 1280px laptop exactly as on an 820px
  tablet**, and 857px on a 375px phone. Scrolled right to the two price boxes,
  the Part Name column is **106px past the left edge** — not truncated, gone.
  So this was never a tablet problem with a desktop escape hatch; it is one
  problem, and one fix covers all three devices.
  (a) **The LIGHT is what actually prevents the mistake**, and it is the half
  the owner did not ask for. The failure is rarely "wrong table" — it is
  off-by-one, catching the row above or below, because rows are 55px tall and
  every box in the grid looks like every other. `:focus-within` lights the
  whole row across every column the moment a box is touched. It costs no width,
  no height and **no JavaScript**, which is also why an added row has it for
  free: none of the three `script.js` cloning traps can be reintroduced by a
  rule that wires nothing. It is **blue** because amber on this form already
  means "you changed this" and red means "this was refused", and a third
  meaning on either would cost the other two theirs. A locked card needs no
  exception — the Financial Lock disables every control, and a disabled input
  cannot take focus.
  (b) **The NUMBER is the handle for the horizontal scroll** — 34px, pinned
  left, so row 7 is still row 7 with its name off screen. Measured: **34px of
  table width, 0px of row height, 0px of page height**, because a sticky cell
  is laid out in its row like any other. 9.1% of a 375px phone.
  **A truncated NAME was the obvious alternative and is wrong on this
  workshop's data** — which is the part worth not re-deriving. One real job
  card carries "Front Lower Control Arm LH" and "Front Lower Control Arm RH" in
  adjacent rows, and "Front Brake Pad Set (Brembo)" directly above "Rear Brake
  Pad Set (Brembo)". Any column narrow enough to afford (~100px) prints "Front
  Lower…" on both — worse than printing nothing, because it looks like an
  answer. A number cannot collide with another number. The trade the owner
  accepted knowingly: a number is arbitrary, so using it means looking left,
  remembering "Wheel Bearing is 7", and scrolling back. The light is what makes
  that rare rather than routine.
  Three things are load-bearing. The number is a **bare cell — no input, no
  name, no stored value**, so anyone auditing the money can skip it; it is
  written by `forloop.counter` and **re-derived from the DOM** by
  `renumberRows()`, never incremented from a counter, because its whole job is
  to agree with what is on screen. **Both clone templates carry the cell** —
  `#empty-spare-form` and `#empty-inventory-form` are cloned by script.js, and
  one missing cell lays every added row a column adrift of its header with
  nothing in the browser to say so. And a hidden row **keeps its number rather
  than closing the gap**: rows are never removed (Django reads a formset by
  contiguous index), so a position is stable, and renumbering under somebody
  mid-edit would be the mark undermining itself.

- **Completed, Pending Bills and Car Profiles are ONE shape, and the two
  breakpoints are 560 and 800.** Added 2026-08-17 on the owner's question about
  tablets. `row-cards` in `base.html` owns Completed, Pending Bills, Paid Bills,
  Job Cards and the High Discount Audit (the last three added 2026-08-18);
  `.cp-grid` on Car Profiles keeps its own declaration because it is CSS grid
  rather than Bootstrap columns, but **the numbers must never differ**. Both are
  measured.
  **800px is where `.main-content` reaches its `max-width` and stops growing**,
  so from there up nothing about a card changes — 245px on an 820px tablet and
  245px on a 1920px laptop, i.e. a tablet now gets the layout a laptop already
  had. Bootstrap's `lg` (992px) had been holding these lists at two-up for
  192px after the container had already stopped changing; it was the nearest
  tier, not the right number. **It must not start lower**: on Completed the
  plate and the payment badge stop fitting on one line at about a 236px card —
  measured at 772px, ten of forty-five cards wrapped to 164px while the rest
  stayed 138px, which is the exact raggedness `.del-vehicle-name`'s
  `min-height` exists to prevent.
  **560px** was already Car Profiles' own two-up point and the other two waited
  for `md` (768), so an **iPad Mini (744px) showed Car Profiles two across and
  Completed ONE across at 712px a card** — same screen, same minute, two
  answers. Two-up at 560 gives 256px, clear of the 236px floor, and takes
  Completed's own list from 7254px of page to 3900px. Measured across all three
  at 375 / 559 / 560 / 744 / 799 / 800 / 820 / 1280: no name clipped, no badge
  row wrapped, every card in a row the same height, no horizontal overflow.
  Two consequences. **The cards carry a bare `col-12` and no responsive
  `col-*`** — leaving `col-md-6 col-lg-4` on them would be two rules describing
  one grid, agreeing today and free to disagree the first time either is
  touched, with the winner decided by specificity. And **Car Profiles' four-up
  rule at 1400px is gone**: `.cp-page`'s own `max-width: 1400px` is dead inside
  an 800px `.main-content`, so a 1400px screen still had a 768px grid and four
  columns would have made cards *narrower on the biggest screen* than three
  columns are on a tablet. Widening the container is the only thing that would
  earn a fourth, and that is a decision about the whole app. Guarded by
  `workshop/tests/test_card_list_grid.py`.

- **All SIX card lists are on the same two breakpoints, and the audit card had
  to STACK to join them.** Added 2026-08-18, extending the entry above to Paid
  Bills, the High Discount Audit and Job Cards. The first two and Job Cards were
  a one-line change (`row-cards`, `col-12`). The audit card was not, and the
  reason is worth keeping: it was a **two-column card** — the car facing its
  figures — which is fine at 352px and falls apart at 245px. Measured at three
  across, the left block collapsed from 208.9px to 76.9px, which **squeezed the
  number plate itself** from 96.5px down to 76.9px (the plate is the one thing
  on an audit row nobody should have to guess at), and heights went **ragged by
  85px** as the longer names wrapped. Stacked — car above figures — every card
  is 267px at one, two and three across and the plate is back to its natural
  width. `min-height: 2.5em` on the name is copied from `.del-vehicle-name` on
  Completed rather than re-derived; `margin-top: auto` on the figures pins them
  to the foot so three Discount lines land at the same y. Its own
  `margin-bottom` went with the change — the row gutter spaces these now, and a
  card carrying both spaced them twice.

- **"Ordered For" is a NOTE on an unassigned spare, not a link to a car.** Added
  2026-08-18 on the owner's report. `original_vehicle_info` already existed,
  already printed on the Unassigned hub and both shop ledgers, and could only
  ever write **itself**: the one place that set it was the "move this spare out
  of a job card" path, so a purchase recorded straight onto a shop's ledger had
  no way to say which car it was for — which is the common case there, because
  the part is ordered *before* there is a job card to hang it on. **No
  migration was needed**; the column has existed since 0039. It is free text
  with no picker and no FK on purpose: at the moment somebody types it the car
  often has no job card to point at, and half the point is being able to write
  "Audi A4 — the white one". It moves no money and joins no table. Three rules:
  it is **trimmed to 255 rather than refused** (oversized is stored by SQLite
  and rejected by PostgreSQL, so trimming is the only answer that behaves the
  same on both — the rule the part name already follows), **clearing it stores
  NULL** so the column has one way of saying nothing, and **Floor may write it**
  — the mechanic takes delivery and is usually who knows, and it is not cost, so
  `PRICE_NOT_SUPPLIED` still strips the price in the same request. Guarded by
  `OrderedForSaysWhichCarThePartIsForTests`.
  **The add form now scrolls sideways at EVERY width, laptop included** (the
  owner's instruction). It used to wrap above 768px and scroll below it — one
  row of boxes with two shapes, depending on whether it was opened on the
  tablet it is filled in on or the laptop it is checked on. Wrapping was also
  getting worse rather than better: `.main-content` caps at 800px, so "desktop"
  here is a 767px column, and the row is eight controls now. Every field
  carries a fixed width rather than a flex basis, or a wide screen stretches
  the boxes and quietly brings the wrap back. The suggestion box was already
  `position: fixed`, so the scroller cannot clip it.

- **The read-only job card is DATA WITH NO LABELS, and it is Office and Owner
  only.** Added 2026-08-18, rewritten the same day. Two questions were settled
  here and the second reversed a decision from the first attempt.
  **(a) Keep it or delete it.** The owner asked whether `/jobcards/<pk>/` should
  go, with everything pointed at the edit form instead, "if financial lock, job
  card is locked right? so safely scroll and view". The lock is real and it is
  **not the common case**: it covers `PAID` and `BULK_PAID` only, so the cards
  people open most — the ones still on the floor — open fully editable.
  Redirecting a read to an editable form makes every glance one stray tap from a
  change, on a tablet, on the longest form in the app, behind a `beforeunload`
  prompt. Nine places link or redirect to it. So it stays.
  **(b) What it looks like.** The first rebuild kept the FORM's shape — a
  section per fact, a label over every value — and the owner read it back as
  "still useless because it's confusing", then drew the replacement:

      🟩 Audi A4, KL 10 AA 1000, 01/01/2026 – 25/01/2026
      10021 km, Amlah, customer name, contact
      note

      Customer Concerns | Job Performed
      Inventory Items   | Spare Parts

  **There are no labels anywhere**, and the owner's reasoning is the
  load-bearing part: *"few times repeatedly see, humans will understand and
  adapt easily."* A caption is what you need the FIRST time and what costs you
  every time after, on a page four people open twenty times a day. Under a part
  there is nothing but its two dates and its two figures. **A missing value
  leaves no trace** — no "Not recorded", no dash — which is also why the two
  identity line's facts are separate elements with the separators drawn in CSS
  (`.dv-fact + .dv-fact::before`): a missing value takes its own separator with
  it, and a stray one is not expressible. **The mechanic wears the dashboard car
  card's own `bi-person-gear`**, at its colour and size — it is the one fact on
  that line that is a PERSON, and the board people arrive from already marks it
  that way. **The customer's name and number are ONE transparent box**, an
  outline with no fill, because they are one thing and the rest of the line is
  about the car: the box groups rather than emphasises, and it is not drawn at
  all when there is nothing to put in it. It carries no dot separator of its
  own — a box is already a separation. Everything a PART prints is
  joined in the view by `_describe_spare()` for the same reason — a template
  doing it is a chain of `{% if %}`s that has to get every separator right, and
  gets it wrong on the row with no shop. Consequence for tests: line 2 is
  asserted as a LIST of values in order, never as one joined string, because the
  dots are not in the markup.
  **(c) Office and Owner ONLY, which is new.** It was `@staff_required` and
  gated the customer and the money inside the template; the owner asked "no
  chance to get Floor, right?" and the honest answer was that there was — by
  URL, and by the "View" button in the Vehicles-in-Workshop sidebar on the
  new-job-card screen, which is a Floor page (now gated to match, the
  `InvoiceLinkVisibilityTests` rule in the other direction). The reason to close
  it is the LAYOUT rather than the secrecy: line 2 runs mileage, mechanic,
  customer and phone number together with no captions, and every part sets the
  workshop's COST beside the customer's price. Removing two of four values from
  an unlabelled line does not produce a safe page, it produces a confusing one.
  **Floor loses nothing** — the dashboard car card's live-details drawer is
  these same four lists, on the board they work from all day.
  **(d) ONE COLUMN, and every row is a GRID.** The four sections shipped 2×2 and
  the owner had them straightened out the same day. A 2×2 makes you read in a Z,
  and the two columns are unrelated lists of unrelated lengths, so the
  right-hand one starts wherever the left-hand one happened to end. One column
  also buys the thing that fixed the crowding: with the full width, a row can be
  a grid — **what a part IS on the left, what it COST right-aligned in its own
  column, the facts about it quietly underneath**. They used to be one string,
  "10/07/2026 – 10/07/2026 · ₹5,727 – ₹7,967", where the eye had to find the ₹
  to know where the dates stopped. Right-aligned and `tabular-nums`, the figures
  form a line you can run down. The **cost is drawn quieter than the price** —
  it is the workshop's own side, and what an owner scans a bill for is what the
  customer was charged.
  Four smaller rules. **The four sections keep the drawer's own values**, copied
  to the character (`test_the_row_styling_is_the_drawers_own` compares the two
  rules) — the polish pass deliberately did NOT touch them, because "these 4
  exactly as Dashboard Card cards" was an instruction; what it touched was the
  header, the row data and the column count. Below 640px they still shed their
  boxes for one hairline, as the drawer does. **An empty section is still
  drawn**, a deliberate divergence from the drawer: the owner drew a fixed set,
  and a page whose sections come and go is one you cannot learn. **Nothing on it
  posts**, which is the whole argument for its existence —
  `test_nothing_on_the_page_posts` scopes to `<main>`, because base.html's
  logout modal is a real form. And **the money line never prints a figure
  twice**: with nothing received the balance IS the bill, and paid in full with
  no discount the receipt IS the bill, so in each case the repeat goes and the
  state chip carries it — the call the Cashbook already makes when it drops its
  Net card on a period with no income.
  *One trap this cost twice, worth not rediscovering:* `.dv-money` is the footer
  and `.dv-money-col` is on every part row, so a test splitting on the bare
  string `dv-money` finds a PRICE and asserts about the bill. Match the exact
  class attribute. Guarded by `workshop/tests/test_jobcard_detail_view.py`.

- **Purchase History carries the same sticky row number the Job Card's Spare
  Parts table does.** Added 2026-08-18. Same table, same problem: it is
  `text-nowrap` and wider than the page, so by the time you have scrolled to
  Status the Vehicle and Part columns are gone and the row being read is
  unnamed. Two things differ from the job card. The number is assigned in the
  **view** (`item.row_no`), because the template regroups these rows by date and
  `forloop.counter` would restart at every separator — two rows on one screen
  sharing a number is worse than no number. And the **date separator row gets
  its own sticky cell**, or the column has a hole at every date and the numbers
  appear to float. Numbered 1..45 within the page, matching what the job card
  numbers: what is on screen is what you are following.

- **The Items / Products count leads both shop headers.** Added 2026-08-18 on
  the owner's instruction. `/spare-shops/<pk>/` and `/inventory/shops/<pk>/` are
  the same four stat boxes about two kinds of shop, and the count sat last on
  both. First, it reads in the order the question is asked — how many things,
  what they cost, what has been paid, what is left — and the three money figures
  keep their order, so Balance Owed is still at the end where a total belongs.
  **Both files in one edit**: these two pages are opened one after the other,
  and a count that leads on one and trails on the other is two layouts for one
  idea.

- **"Spare Parts" is ONE glyph app-wide: `bi-gear-wide-connected`.** Added
  2026-08-18 on the owner's instruction, and it removed a glyph rather than
  swapping one. Three symbols had been meaning spare parts: this gear on the
  dashboard drawer, `bi-nut-fill` on the job-card form and the Live Report, and
  **`bi-tools` on every Spare Shops page — which is the JOB PERFORMED icon**, so
  the section that buys parts wore the icon of the section that fits them. The

- **"Spare Parts" is ONE glyph app-wide: `bi-gear-wide-connected`.** Added
  2026-08-18 on the owner's instruction, and it removed a glyph rather than
  swapping one. Three symbols had been meaning spare parts: this gear on the
  dashboard drawer, `bi-nut-fill` on the job-card form and the Live Report, and
  **`bi-tools` on every Spare Shops page — which is the JOB PERFORMED icon**, so
  the section that buys parts wore the icon of the section that fits them. The
  owner spotted exactly that. `bi-tools` now means Jobs/Labour and nothing else,
  in precisely three places (dashboard drawer, estimate form, job card form).
  `test_spare_parts_wears_the_same_glyph_everywhere_it_is_named` scans every
  template and fails if a second glyph comes back.

Known-but-unscheduled problems live in `TECH_DEBT.md` (local, not in git).
**Deploying is `GO_LIVE_RUNBOOK.md`** — the ordered steps, the environment
variables, the rollback, and what to do when both owners are locked out.
**Product scope that was deliberately left out** — GST, customer-facing
notifications, attendance, multi-mechanic assignment, car photos — is recorded
in `TITAN_MASTER_HANDOVER.md` §VII, not here and not in `TECH_DEBT.md`.
Proposing one of those is proposing scope, not reporting a defect.

### Devices the UI must work on
Every screen is used on **three** form factors, and each role uses a different one:
**laptop → Office**, **tablet → Floor**, **mobile → Owners**. Owner-only screens
(Analysis, Deletion History) are read mostly on a phone; Floor screens are tapped on a
tablet, so interactive controls need ~44px touch targets. Design responsively — a
desktop-only table or a layout that overflows horizontally on a phone is a defect, not
a cosmetic issue. `base.html` defines the light-mode CSS variables (`--color-*`) and
renders Django messages **once** for all pages — never re-render `{% if messages %}` in
a child template (it double-prints and loses the error/success styling).

**Django's `{# … #}` comment is single-line only.** Spread one across two lines and it
stops being a comment — the text renders on the page. Ten of these shipped on
2026-07-29 and put paragraphs of developer commentary inside the nav bar and the login
forms, with every functional test still green, because tests assert on specific strings
and status codes and nothing was reading what the page actually said. Use
`{% comment %} … {% endcomment %}` for anything spanning lines.
`workshop/tests/test_template_comments.py` statically scans every template for this.

### Navigation — one bar, one drawer (rebuilt 2026-07-25, moved to the bottom on phones 2026-08-06)
There is exactly **one** nav: a fixed bar in `base.html`, plus a Bootstrap off-canvas
drawer (`#appDrawer`) behind the Manage/Menu button. There used to be a second,
divergent mobile bottom nav; it was deleted because the two menus listed different
things. **Don't add a second nav** — a new destination goes in the drawer, in the
section it belongs to.
- Top bar is deliberately minimal, and it carries a DIFFERENT set per role
  (reordered 2026-08-16):
  - **Owner / Office** — Admin · Completed · **Live** · Alerts · Manage. The
    bell is Owner-only. That third tab is `live_report` and was called "Report"
    until 2026-08-17, which was the wrong word twice over: the page is the state
    of the workshop *right now* and carries no money at all, while the drawer's
    "Analysis & Reports" is the profit page and genuinely is a report — two
    entries a thumb's width apart, both saying "report", meaning opposite
    things. The URL, the view name and the page's own "Live Report" heading are
    unchanged; only the tab's word and its `aria-label` moved. There is no `+ New` here on purpose: Floor creates most job
    cards, and Owner/Office reach the form from the `+ New` button in the home
    page's own header. That is the owner's call, and it means **the only
    `{% url 'jobcard_create' %}` in `base.html` is the Floor tab** — if that
    button ever leaves the dashboard header, Owner and Office lose every
    navigation route to a new card.
  - **Floor** — Floor · New · Inventory · Menu.
  Floor's drawer also carries **Unassigned Spares**, which is its only door into
  the Spare Shops section (add-only, no prices — see the deliberate decision
  above). `/spare-shops/` is already in `DRAWER_SECTION_PREFIXES`, so that link
  lights the Manage button with no change there.
- **On phones (≤640px) that same bar renders at the BOTTOM.** It is the one element,
  repositioned in a media query — not a second nav. The top edge is the hardest place
  on a phone for a thumb and every destination on the bar is tapped constantly. Five
  things move with it and each is wired to `--nav-h`, so they cannot drift apart:
  `.main-content`'s offset (top margin → `body`'s `padding-bottom`), the notification
  panel (opens **upward** from the bar), the PWA install banner (sits on top of the
  bar, not under it — its z-index is below the bar's), the `--sticky-top` variable
  that sticky page headers rest against (`0` on a phone, `--nav-h` elsewhere), and the
  safe-area inset for the iPhone home indicator.
- `--nav-h` is the single source of truth for the bar height, and `--sticky-top` for
  where a `position: sticky` page header comes to rest. Change the variables, not the
  individual margins — a hard-coded `top: 60px` on two job-card headers is exactly how
  they ended up with an empty strip above them when the bar moved.
- The bar must carry Bootstrap's `fixed-top` class **even on the phone layout, where it
  paints at the bottom**. It is load-bearing, not cosmetic: Bootstrap's scrollbar helper
  only pads elements matching `.fixed-top` when the drawer locks body scroll, and
  without it the bar jumps sideways by the scrollbar width on open. Swapping in
  `.fixed-bottom` is **not** the fix — Bootstrap's `bottom: 0` would combine with our
  own `top: 0` and stretch the bar down the whole viewport. For the same reason `body`
  uses `overflow-y: scroll` **without** `scrollbar-gutter: stable` — the two together
  double-count the scrollbar.
- Phone tabs are **equal-width columns**, and separation comes from the container's
  `gap` — never from padding on the tabs. `flex-basis: 0` sizes the *content* box and
  padding is then added on top of the equal share, so one padded tab beside the bell's
  unpadded wrapper came out 4px wider than its neighbours. `max-width: 96px` stops a
  landscape phone rendering 150px slabs. The bell is the one icon-only pill on a wide
  screen and gains a label ("Alerts") on the tab bar only — an unlabelled tab among
  labelled ones sits its glyph ~7px lower than the rest, which reads as a misalignment.
- Every pill that can become icon-only carries an `aria-label`. Keep that pairing when
  adding a pill.
- Drawer items are role-filtered in the template to match each view's decorator. If you
  change a view's RBAC decorator, update its drawer entry in the same edit.
- **Logout is confirmed, and there is exactly one logout control in the whole app.** The
  drawer button is a `data-bs-toggle="modal"` trigger; the POST form lives in
  `#logoutConfirmModal`, which sits **outside** the off-canvas — a modal nested inside one
  inherits its stacking context and opens behind the backdrop. Verified layering: modal
  1055 > modal-backdrop 1050 > offcanvas 1045 > offcanvas-backdrop 1040. A second logout
  control anywhere would reinstate the one-tap sign-out this prevents, which is why
  `LogoutConfirmationTests` asserts the page contains exactly one `action="/logout/"`.
- **A panel that covers the screen has no way out, and both of them did.**
  Changed 2026-08-11 on the owner's report. The drawer was
  `min(88vw, 360px)` and the notification sheet was capped at
  `100vh - nav - 24px` (89%) — so on a phone each one was effectively a
  full-screen takeover. Both already close on a backdrop tap; neither left
  anywhere to put a thumb, which is the whole reason the backdrop exists.
  The drawer is now `clamp(240px, 70vw, 340px)` and the sheet
  `75vh - (everything below it)`, i.e. **exactly 25vh of live backdrop above**
  — measured at 25.0% on a 393×852 phone, and a synthetic tap in that strip
  verified as reaching `#notifBackdrop` and closing the panel.
  Two rules worth keeping. **The exposed strip is expressed as a subtraction
  from 75vh, never as a bare `66vh`**, so it stays a quarter of the screen as
  `--nav-h` or the safe-area inset change — the same reason the bar's height is
  a variable and not a number repeated in five places. And **the drawer's width
  and its type size are one decision, not two**: the owner asked for both a
  narrower drawer *and* bigger controls, which pull against each other. The
  arithmetic that reconciles them: the longest label ("Analysis & Reports")
  renders 138px at 1.02rem, the row spends 108px on padding / 38px icon tile /
  gaps / chevron, so **246px is the width at which the last label stops fitting
  on one line**. 70vw clears it from 360px up (252px, 5px spare); the 240px
  floor is what stops a 320px screen wrapping four labels. Grow the type or
  shrink the width past that and rows start wrapping — ugly, not broken, since
  nothing here truncates. Row height went 50→56px and the icon tile 36→38px in
  the same change; the horizontal padding came *down* 10→9px, which is where
  the pixels came from.

## Commands

All commands assume the venv is active (`venv\Scripts\activate` on Windows) and require `DJANGO_ENV` set — the settings package (`formulad_workshop/settings/__init__.py`) raises `ImproperlyConfigured` if it's missing. It is **not** read from `.env` (python-decouple isn't involved for this one var); it must be a real shell/session env var.

```bash
# Windows (PowerShell)
$env:DJANGO_ENV = "development"

# Run dev server
python manage.py runserver

# Run full test suite (40 test files, 1056 tests, ~23-69 min; always uses SQLite, see below)
python manage.py test workshop inventory

# Run a single test file / class / method
python manage.py test workshop.tests.test_financial
python manage.py test workshop.tests.test_financial.SomeTestClass
python manage.py test workshop.tests.test_financial.SomeTestClass.test_something
python manage.py test inventory.tests_suppliers

# Migrations
python manage.py makemigrations
python manage.py migrate

# One-off management commands
python manage.py backup_db       # rotated backup of whichever DB is active, keeps last 14 in /backups
python manage.py setup_groups    # (legacy) creates Owner/Office/Floor auth groups
python manage.py sync_owner_identity        # DRY RUN — owner group/mobile/admin-access: .env -> DB
python manage.py sync_owner_identity --yes  # apply
python manage.py set_owner_email Sahad a@b.com        # DRY RUN — preview the change
python manage.py set_owner_email Sahad a@b.com --yes  # apply
python manage.py load_master_data  # brands/models/spare parts — prerequisite for seeding

# Demo/dev data. seed_dummy_data needs load_master_data to have run first.
# Seed into SQLite (fast), then push the finished result up to Postgres.
python manage.py seed_dummy_data                                    # default 5-year range
python manage.py seed_dummy_data --start 2026-01-01 --end 2026-07-25 --cards-per-day 3
python manage.py seed_salary_data           # salary months + advances only
python manage.py purge_business_data        # DRY RUN — prints what it would delete
python manage.py purge_business_data --yes  # actually delete

# SQLite -> PostgreSQL (see "Which database am I on?" below)
python manage.py copy_sqlite_to_postgres        # DRY RUN — prints the plan
python manage.py copy_sqlite_to_postgres --yes  # replace Postgres with the SQLite contents
```

`backup_db` follows whichever database is active: `pg_dump` for PostgreSQL,
a file copy for SQLite. **The extension tells you how to restore it** — a
custom-format archive is `.dump` (needs `pg_restore`), plain SQL is `.sql`
(needs `psql`), a SQLite copy is `.sqlite3`. Custom format is tried first and
plain is the fallback, so both are possible from one run; naming them alike
would leave you guessing on the day you actually need one. A dump is written
to a `.part` file and only renamed once `pg_dump` reports success — a
truncated file left under a real backup's name would occupy one of the 14
retention slots and, once the folder filled, evict a good backup to keep
itself. Requires the PostgreSQL client tools on PATH; it says so plainly if
they are missing rather than failing obscurely.

`purge_business_data` clears **all** business tables (job cards, shops, fleet
accounts, inventory, cashbook, staff roster, deletion history) — it deliberately
does *not* try to distinguish "dummy" rows from real ones, because nothing in the
schema marks them, and a command claiming otherwise would be lying. It never
touches login accounts, groups, or the master lists. It is the intended reversal
for `seed_dummy_data`, and the thing to run against Postgres before go-live.

`seed_dummy_data` writes everything through the ORM so signals fire (stock sync,
`update_totals`), commits one day at a time with monthly bookends (never one long
transaction — a remote Postgres would time out), and restocks monthly *to demand*
rather than a fixed quantity, so warehouse stock hovers around `average_stock`
instead of compounding upward over a multi-year range.

It also seeds **Salary & Advance** (added 2026-07-31): staff carry a
`current_salary`, advances are handed out through each month, and every month is
settled — except the **last**, left open on purpose because that is a live
workshop's normal mid-month state and it exercises `salary_expense`'s
loose-advances branch. Net pay imports the app's own `_compute_net` rather than
restating salary − leave − advance, so seeded figures can never drift from what
the settlement screen would produce. The Cashbook seeder deliberately has **no**
"Staff Salaries" line: wages belong to Salary & Advance, and a cashbook row named
like wages is exactly what the Profit page flags as a possible double count, so
seeding one left the demo permanently warned.

### Which database am I on? (changed 2026-07-27 — dev is PostgreSQL now)
`DJANGO_ENV=development` runs against **PostgreSQL** (the Neon instance in
`.env`), not SQLite. Development matches what ships, so Postgres-only behaviour
— stricter GROUP BY, real numeric types, case sensitivity, sequences — surfaces
while it's cheap to fix rather than on go-live day.

| Situation | Database | How |
|---|---|---|
| Normal dev, runserver, one-off commands | **PostgreSQL** | default |
| Bulk dummy-data seeding | SQLite | `USE_SQLITE=true` |
| `manage.py test` | SQLite | **automatic**, always |
| `DJANGO_ENV=production` | PostgreSQL | + SSL/HSTS enforcement |

- **Tests always use SQLite, whatever `USE_SQLITE` says.** The test runner
  CREATEs and DROPs a whole database — not something to point at hosted
  Postgres — and 1,056 tests at ~75 ms per round-trip would take hours. There is
  deliberately no flag to remember and no way to run the suite against live data
  by accident (`development.py` keys off `sys.argv[1] == 'test'`).
- **Seed on SQLite, then copy up.** `seed_dummy_data` writes every row through
  the ORM so signals fire; over a network that's tens of thousands of
  round-trips. Set `USE_SQLITE=true`, seed, unset it, then
  `copy_sqlite_to_postgres --yes`.
- `copy_sqlite_to_postgres` **replaces** the target tables. It refuses to run if
  the two databases are on different migration states, orders tables by a
  topological sort of their FKs (parents first), inserts with `bulk_create` so
  signals *don't* re-fire and re-deduct stock, **resets Postgres sequences**
  afterwards (explicit ids don't advance them — miss this and the next insert
  collides), and re-counts every table before declaring success. It skips
  content types, permissions, sessions and admin log — `migrate` owns those on
  the target and their ids need not match.
- **`copy_sqlite_to_postgres` also replaces `auth.User`, `auth.Group`,
  `auth.User_groups` and `UserProfile` — so it can silently break access and
  recovery. Always do these three checks around it.** Learned the hard way on
  2026-07-30:
  1. **Emails.** Reset codes go to `User.email`. The seed file carried
     placeholder `@formulad.in` addresses that would have replaced the owners'
     real ones, pointing password recovery at undeliverable mailboxes. Copy the
     live emails into the SQLite users *before* the copy, or repair with
     `set_owner_email` straight after.
  2. **Run `sync_owner_identity --yes` afterwards, always.** The copy left both
     owners with `is_staff=True` (opening `/admin/`, which bypasses
     `DeletionLog`, the Financial Lock and archive-don't-delete) **and stripped
     their `Owner` group membership** — and since notification audience resolves
     by group, not `is_superuser`, they would have silently stopped receiving
     alerts while RBAC still let them in. This command re-asserts both.
  3. **Extra accounts get copied in.** Any login present only in the seed file
     is created on the target, group memberships included. Check the account
     lists match first; a stray Owner-group test account is a real privilege
     grant.
  Also expect these to be emptied, since nothing seeds them: `Notification`,
  `PushSubscription`, `AccountLockout`, `PasswordResetOTP`, `DeletionLog` and
  all three Salary tables. `PushSubscription` is the one with a human cost —
  every device has to re-enable push by hand, and wages read ₹0 on the Profit
  page until salary months are re-entered.
- The `sqlite` alias is always present in `DATABASES` under development, which
  is how the copy command reads the file while `default` points at Postgres.
- Expect page loads to feel slow from a dev machine: the database is in
  Singapore, so a 47-query page costs ~3.5 s of pure latency. That is distance,
  not the code — colocating app and database removes it. It is still a reason to
  keep query counts low.

Required `.env` keys (see `settings/base.py`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `OWNER_1_USERNAME`/`OWNER_1_MOBILE` and the `OWNER_2_*` pair (read only by `sync_owner_identity`; the authoritative copy lives in the database). Production adds `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

**Web Push** (optional): `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_ADMIN_EMAIL`. Generated once — **regenerating them invalidates every existing subscription**, so treat them as permanent. The public key ships to the browser and is not a secret; the private key is. They must also be set in the host's environment (Render) or push is skipped there while the in-app feed keeps working.

**Email** (password-reset codes) — **two transports, one flow.** Production sets `EMAIL_BACKEND = 'workshop.email_backend.ResendEmailBackend'` and needs only **`RESEND_API_KEY`** plus `DEFAULT_FROM_EMAIL`; Railway blocks outbound SMTP below the Pro plan, so mail leaves over Resend's HTTPS API instead. Development still uses SMTP and reads `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, plus `EMAIL_REAL` — there `EMAIL_HOST_PASSWORD` is a Google **App Password**, not the account password, and needs 2-Step Verification on that account. Only the transport differs: there is exactly one `send_mail()` call site (`auth_views.py`), so the flow, the throttles and the tests are identical on both. Recipients are per-account `User.email` values in the database, never in `.env`; change one with `set_owner_email`, which is why it needs no deploy. Development uses the console backend unless `EMAIL_REAL=true`, so ordinary work never sends real mail; `manage.py test` uses Django's locmem backend regardless.

## Architecture

### App boundaries
- **`workshop/`** — job cards, billing, bulk payers, spare shops, cashbook, auth, owner analytics, deletion history, master data (brands/models/spares/concerns).
  - `views/` is a package (17 modules: `audits`, `autocomplete`, `billing`, `bulk_payer`, `car_profiles`, `completed`, `dashboard`, `deletion_history`, `estimate`, `jobcard`, `master_lists`, `notifications`, `paid`, `pending`, `push`, `salary_advance`, `spare_shop`). `views/__init__.py` re-exports everything so `from . import views; views.some_function` and existing URL wiring keep working — when adding a view, add it to both its module and the `__init__.py` re-export list.
  - `analysis_views.py`, `analysis_engine.py`, `auth_views.py`, `cashbook_views.py`, `cleanup_views.py`, `management_views.py`, `settlement.py` are standalone top-level modules (not part of the `views/` package), imported directly in `urls.py` (or, for the pure ones, by the views that need them). **`settlement.py` holds no views either** — it is the one answer to "what is still unfilled before this bill should be settled?", read by the settle dialog on the invoice; see "Deliberate decisions" for the four rules it enforces. `analysis_engine.py` holds no views at all — it is the pure money math behind the Analysis section, and `master_data.py` likewise holds no views: it is the one implementation of the master-list rename/merge rule, shared by `views/master_lists.py` and `cleanup_views.py` (see "Deliberate decisions" for why that sharing is load-bearing). **`invoice.py` is the third of these** — no views, no HTTP: it is the one answer to "what does the customer see?", so a second printing surface (a PDF export, a reprint from Paid Bills) cannot grow its own slightly-different version. It owns **both** customer documents — `build_invoice()` and `build_estimate()`, sharing every rule between them (see "Deliberate decisions"). `views/billing.py` and `views/estimate.py` resolve the record and render; neither contains any arithmetic.
  - `decorators.py` defines the RBAC decorators (`owner_required`, `office_required`, `staff_required`) built on three Django auth Groups: **Owner**, **Office**, **Floor**. Superusers pass every check. Use these decorators on any new view instead of rolling custom permission checks.
  - `middleware.py` (`SessionTrackingMiddleware`) updates `UserSession` (device/IP/last-activity) on every authenticated request, throttled to a 5-minute cooldown per session.
- **`inventory/`** — stock items/categories and supplier shops (`views.py` for core inventory, `views_suppliers.py` for the supplier-shop module). Stock levels are kept in sync with workshop activity purely via Django signals in `signals.py` — there is no direct view-to-view coupling between the two apps for stock changes.
  - **Inventory workflow (automation-first):** stock moves *only* via signals — restock bills add (+), job-card spare usage removes (−); there is **no manual stock-number editing anywhere** (the old `update_stock` was removed; Low Stock is read-only). **Item creation happens only through Supplier → Add Product** (`add_shop_catalog_item`), which now **requires an Average Stock** threshold. A product is one shared `Item` (unique per `category`+`name`) linked to shops via `ShopCatalogItem`; the *same product across shops is that one Item*. Name/threshold are edited from the shop catalog (`edit_catalog_item`). A catalog entry can be **deactivated** (`ShopCatalogItem.is_active`) — it stays listed (greyed) but drops out of restock bills. That exclusion is enforced **server-side** in `shop_restock_bill`/`edit_restock_bill` via `_active_catalog_items()`, not just in the picker template — any view that writes `SupplierRestockItem` rows must re-validate ids against the shop's active catalog, because those rows move real stock. `remove_shop_catalog_item` **deactivates instead of deleting** when the shop has restock-bill history (a hard delete would alter historical bill totals) **or the product still holds stock** (stock is signal-only, so deleting would silently destroy a countable quantity). Only a zero-stock, no-history orphan Item is deleted — and, like every permanent delete, it writes `DeletionLog.record(ENTITY_INVENTORY_ITEM, …)` first, inside the same atomic block.
  - **`average_stock` means "how many we normally keep in stock"**, not an alert threshold — Low Stock fires at **below 25%** of it (`inventory_low_stock`). Don't relabel the field as a threshold in the UI; the two numbers are different by design.
  - **Inventory RBAC:** Floor sees only the main list, **Low Stock** (read-only), and **Stock History**; everything else (Manage/Category, Add Product, restock, catalog, payments) is `@office_required`. "Manage Database" is a **read-only Category browser** (add/list/edit/delete Category; drill in to view products + the shop(s) that stock them — no product actions there).
  - **Category rules:** names dedupe on `__iexact` in both `add_category` and `edit_category`. Duplicates aren't cosmetic — `add_shop_catalog_item` resolves a category with `get_or_create(name__iexact=…)`, which raises `MultipleObjectsReturned` as soon as two spellings coexist. `Category.name` has no DB-level `unique=True` yet (adding it needs a dedupe migration first), so the view guards are the only protection. **Delete is allowed only while the category holds no products** (`Item.category` is `PROTECT`); the three-dot menu hides Delete for non-empty ones and the view re-checks.
  - **Stock History** (`consumption_history` + `inventory_history_mechanic`) is a **live query over `JobCardSpareItem`** (item · qty · mechanic · car · reg, grouped by `admitted_date`, This/Last-Week filter, per-mechanic totals drill-down). It does **not** use the legacy `ConsumptionRecord` model (now dormant), and adds no signals. Both views filter `job_card__is_deleted=False` (dormant flag, still carried for pre-existing rows) and flag entries whose `spare_part_name` matches no `Item` as **"not from stock"** — the deduction signal matches on `Item.name__iexact`, so an unmatched name deducts nothing and must not be displayed as a warehouse draw. The mechanic drill-down groups on `Lower('spare_part_name')` for the same reason. Rows are capped at `HISTORY_ROW_CAP` rather than paginated, so the day-grouped layout is never split.

### Settings
Split into `formulad_workshop/settings/{base,development,production}.py`. `__init__.py` picks one via `DJANGO_ENV` — there is no fallback default, so forgetting to set it fails loudly rather than silently using the wrong DB. The PostgreSQL and SQLite connection dicts are built by `postgres_db()` / `sqlite_db()` in `base.py` and shared by both environments; they used to be duplicated per file, which is how a connection setting gets fixed in one and left broken in the other.

### Notifications — one catalogue, one entry point
The whole event list lives in **`workshop/notifications.py`**. Add an event to `EVENTS`, then call `notify()` from the single place it happens — **never** `Notification.objects.create()` in a view. With **17 call sites spread across 8 modules** (recounted 2026-08-10; `EVENTS` itself now holds **14** events, 10 CRITICAL and 4 INFO, after `STAFF_LOGIN` was added 2026-08-12), that file is the only way to answer "what does this thing notify about?" without grepping.
- **An OFFICE or FLOOR sign-in pushes; an OWNER sign-in does not.** Added 2026-08-12 on the owner's instruction. `LOGIN` (INFO) and `STAFF_LOGIN` (CRITICAL) are the same fact at two tiers, and the split lives in `EVENTS` rather than in a severity argument at the call site so that this file states the rule. The reasoning: `notify()` already excludes the actor, so making `LOGIN` critical would only ever buzz one owner about the other owner's ordinary working day — which is exactly how a critical list stops being read. A staff account is different: it is used on shared shop-floor devices and it is the one the owners cannot see being used. Volume is what makes it safe — `SESSION_COOKIE_AGE` is 40 days, so a signed-in tablet stays signed in and this fires on a genuinely new session, not every shift. The body carries the role (`amal (Office)`) because the alert arrives as one line on a lock screen and a bare username does not say whether that account can see money. Guarded by `workshop/tests/test_staff_login_alert.py`.
- **Fanned out per recipient**, so the unread count is one indexed query. **No FK to the subject** — most events announce a *deletion*, and a FK would cascade the notification away with the thing it was about; `object_type`/`object_id` plus a frozen label in `body` is the same discipline as `DeletionLog.snapshot`.
- **`DeletionLog.record()` is the deletion hook.** Every permanent delete already funnels through it, so one call covers all eleven entity types and any added later. Don't scatter equivalent `notify()` calls into individual delete views.
- **Owners only, and the actor never hears about their own action.** Floor gets nothing — a notification a mechanic can't act on trains everyone to ignore the bell. The bell in `base.html` is Owner-gated to match; widen the gate and the audience together or you get a bell that can never fill.
- Audience is resolved by **group membership**, not `is_superuser` — see the Owner-group note under Security below for why that distinction is load-bearing.
- `notify()` swallows its own errors so a malformed body can't fail a payment. That promise stops at database errors inside an atomic block: the surrounding transaction is already doomed and shouldn't be rescued.
- Severity is a tier, not decoration: **`CRITICAL` events send a Web Push, `INFO` events only land in the feed.** Keep the critical list short — a phone that buzzes for routine activity stops being read for the things that matter.
- **A notification's `url` must land somewhere that can act on its subject — check the destination actually *contains* it.** `ACCOUNT_LOCKED` pointed every lockout at Control Hub → Accounts, which lists Office and Floor only, and `manage_unlock_account` refuses owner accounts by design. So a locked *owner* opened a page that did not contain the account, did not mention a lockout, and offered nothing to press. It is now routed by role (owner → Security, staff → Accounts) with the remedy stated in the body. When adding an event, ask what the reader will do next and whether that page can do it; an empty `url` falling back to the feed is better than a confident link to the wrong place.
  **This rule was then broken a second time, in the same shape.** Found 2026-08-10, by the owner deliberately locking an account to see what the alert did. Archiving a Supplies Shop raised `ACCOUNT_ARCHIVED` pointing at `supplier_shop_list` — which filters `is_active=True`, making it the one page guaranteed *not* to contain the shop the notification is about. The spare-shop and fleet versions of the very same event already pointed at their archived lists; only this one did not. It now points at `deactivated_supplier_shop_list`, and the test follows the URL and asserts the shop's name is on the page it reaches, because comparing against a `reverse()` proves nothing about whether the destination shows the thing.
  **A stale instruction is a different failure from a wrong link, and needs a different fix.** The same investigation cleared `ACCOUNT_LOCKED` of being wrong: `manage_dashboard` sets `lock_minutes` per account and the template gates the unlock button on it, all correct. But a lockout lasts `AccountLockout.LOCKOUT_MINUTES` (15) and a notification is permanent, so an owner reading it an hour later followed "Unlock it from Control Hub → Accounts", found an ordinary account list, and reasonably concluded the alert was lying. The button is right to disappear — a permanent unlock button invites being pressed as a fix for something unrelated. The **body** was wrong to describe a permanent remedy, and now states the window first. The general rule: **if the remedy an event describes expires, the body has to say so**, because the reader may arrive at any time.
- **A password reset raises `PASSWORD_RESET` (CRITICAL) to the *other* owner.** Every routine sign-in was announced while the one event meaning an account changed hands was silent — and since a reset also terminates every session, the real owner was signed out everywhere with no message, which reads as the app misbehaving. `actor=user` excludes whoever performed it: a genuine owner needs no telling, and an intruder should not receive the warning about themselves. The victim's own signal is the reset email, which now says to raise it with the other owner.
- **Read rows are swept after `RETENTION_DAYS` (14); unread are kept forever.** This table is a feed, not an archive — the permanent record lives in `DeletionLog`, the audit pages and the ledgers.
- **The bell opens a floating panel, fetched lazily** from `/notifications/panel/`. The bell is on every owner page, so baking ten rows plus their actors into every response would cost a join on pages that have nothing to do with notifications; only the unread *count* rides in the context processor. The panel caps at `PANEL_SIZE`, and the badge caps at `99+` — past that the exact number changes nothing an owner would do.
- **Row markup lives in one partial** (`notifications/_row.html`), shared by the panel and the full feed, so "read" cannot come to look like two different things. Read state is carried by four signals — accent rail, background, title weight, trailing icon — not a dot alone, which is easy to miss on a phone.
- **Push on/silent is the small bell in the panel header**, not a card. `notifications.js` owns both the panel and that toggle.
- Anything owner-gated that lives *after* `{% endwith %}` in `base.html` must use `request.user|has_group:"Owner"`, not `is_owner` — that variable's scope ends there, and a stale `{% if is_owner %}` silently evaluated false, which is how the panel's JavaScript went missing once.

### Web Push — a delivery layer, never a source of truth
`workshop/push.py` sends; `workshop/views/push.py` is the HTTP surface; `PushSubscription` is one row per **device**, not per user.
- **`sw.js` is served from the origin root by a Django view, not from `/static/`.** This is load-bearing, not a preference: a service worker can only control pages at or below its own path, so WhiteNoise serving it at `/static/sw.js` would silently limit its scope to `/static/` and it would never receive a push for the app. The view also sends `Service-Worker-Allowed: /` and `Cache-Control: no-store` (a cached worker means a fix ships and nobody gets it).
- **Nothing waits on the network.** `queue_push()` hands off to a background thread via `transaction.on_commit` — so a rolled-back action never announces itself, and saving a payment doesn't pay for two ~200 ms HTTPS calls. The thread opens and closes its own DB connection; it doesn't inherit the request's.
- **Push failing must never affect the feed.** Missing VAPID keys, a dead push service, zero subscribers — all no-ops. `notify()` guards the push call separately from the row write so a push problem can't even change its *return value*.
- **404/410 from the push service means that endpoint is permanently gone** — the row is deleted, not retried. Other errors are counted and dropped after `MAX_FAILURES`.
- **iOS only delivers push to an app added to the Home Screen.** In a plain Safari tab `PushManager` is simply absent. `static/js/push.js` detects this and says so explicitly; without that the button just looks broken on the exact device the owners use.
- Push is **optional in every environment**. A deploy with no VAPID keys is valid and degrades quietly.

### Signed-out pages — one shell, one door
The login page and both password-recovery steps extend `workshop/auth/base_auth.html`. A page overrides only its accent colour and its copy; the layout, the input styling and the submit guard are shared. **Light theme, no imagery** — the wordmark and a 3px accent hairline carry the brand, in red (`#dc2626`), the brand mark's own colour.

**There used to be two login faces and they were merged on 2026-08-12** — a blue "Staff Sign In" at `/login/` and a red "Admin Sign In" at `/admin-login/`, one view behind both. The split **gated nothing**, because either face accepted any role; what it did was publish the org chart to anyone who typed the address — "Admin Sign In" at a fixed URL announces that privileged accounts exist and where their door is, and the staff face named the lower tiers outright in its placeholder ("Office/Floor username"). The page now says `Sign In` / `Identifier` / `Enter your identifier` and nothing else.

Three consequences worth not rediscovering. **`Forgot?` moved onto the one door**, where it belongs: it used to render only on the owner face while the nav bar links to `/login/`, so an owner arriving the ordinary way had no recovery route on screen at all — and it discloses nothing, because step 1 of the reset already answers identically whether or not an account exists. **All three RBAC decorators now use `login_url='/login/'`** — Owner and Office pages bounced anonymous visitors to `/admin-login/`, which is how probing an owner URL revealed the second door. And **`/admin-login/` survives as a `RedirectView` with `query_string=True`**, never deleted: the owners have it bookmarked, the name is still reversed, and dropping the query string would strand an old bookmark's `?next=`.

**Obscurity is not a control and must not be treated as one.** The controls are the password, the two lockouts, HTTPS and the RBAC decorators. This only stops the front door drawing a map. Note what it deliberately does *not* hide: the lockout message still confirms an account exists after five tries, which is the documented trade in `login_view`.
- The views pass `AUTH_PAGE` (`hide_chrome=True`), which suppresses the nav bar **and** the PWA install banner. A signed-out page owns the whole viewport; a bar offering "Floor" and "Login" above a login form is noise, and prompting someone to install the app before they have proved they can get into it is premature.
- **Every auth form must keep `js-auth-form` / `js-auth-submit`.** The guard in `base_auth.html` blocks a second submit while one is in flight — the staff form previously had none, so the button could be pressed repeatedly, each press another POST and each wrong one spending part of the account's five-attempt lockout budget. The `dataset.submitting` flag does the work, not `disabled`: a button disabled inside its own submit handler still lets a queued Enter keypress through in some browsers.

### Security model ("Steel Gate")
- **Two lockouts, different units.** `AccountLockout` is the primary: **5 failures locks that one account** for 15 minutes. `FailedAttempt` is a backstop counting by direct `REMOTE_ADDR` (X-Forwarded-For is intentionally ignored to prevent spoofed-IP bypass), at **`IP_FAILURE_LIMIT = 20`**. The IP threshold was raised from 5 on 2026-07-28 because the unit was wrong for this business: the laptop, the tablet and both owners' phones leave through one connection, so five fumbled attempts on the Floor tablet locked the owners out of their own devices. Don't lower it back — per-account lockout is what actually stops a guessing attack, and the IP gate now only catches a spray across many accounts. Tests touching either must clear `FailedAttempt.objects.all()` in `setUp` to avoid cross-test contamination.
- **Login is one view behind one door**, as of 2026-08-12 — see "Signed-out pages" above for why the two faces were merged and what must not be re-added. `auth_views.login_view` takes no `face` kwarg any more. **Any role signs in at `/login/`**; the old fake "Invalid credentials" shown to owners on the staff face is long gone (it protected nothing, since the owner door was a button away, and guaranteed a baffling support call). Both URL *names* stay load-bearing — `admin_login` is still reversed and is now a redirect. Sign in with **username, email, or mobile** — `resolve_user_by_identifier` tries each in that order and **fails closed** if more than one account matches.
- **An OWNER account is nameable only by its email address at the sign-in form.** Added 2026-08-12, `resolve_login_identifier`. The mobile branch above accepts the last ten digits of a number, so the workshop's own published phone — website, business cards, Google Maps — was a valid owner identifier, and a first-name username is barely better. Being nameable costs twice at *this* form and nowhere else: it is where guessing happens, and it is where five wrong tries lock the account, so anyone who could name an owner could lock that owner out on demand, repeatedly, for free. Three things are load-bearing.
  (a) **The refusal must also be enforced at the `authenticate()` call**, which is why `login_view` passes `username=account.username if account else ''` and **never the raw input**. Django's `ModelBackend` looks accounts up *by username*, so the old fallback would have handed the refused text straight to the backend and signed the owner in on it — the narrowing would have been decorative. `test_a_refused_identifier_cannot_authenticate_by_the_back_door` asserts this **with the correct password**; with a wrong one it passes whether or not the hole is open. Timing is unchanged: `''` matches nothing and ModelBackend still hashes a dummy password on a miss.
  (b) **The reset flow is deliberately NOT narrowed.** It answers identically whether or not an account exists, carries its own two throttles, and delivers only to the address already on file — so a username there hands an attacker nothing, while refusing it would strand an owner who remembers their username but not which address is on the account. Recovery paths should be generous about identifying you; authentication paths should not.
  (c) **An owner with no email is exempt**, or the rule would be a permanent lockout with no way back — no email login *and* no `can_reset_password`. Only an owner can clear an owner's email (`/admin/` is unreachable by design), so it is not a lever an attacker can pull. Consequence for tests: several older fixtures create owners with no email and therefore still sign in by username entirely legitimately — `test_auth.test_sign_in_by_mobile_reads_the_database` says so in its docstring rather than leaving it to look like an oversight.
  Guarded by `OwnersSignInByEmailOnlyTests`. **The trade, stated plainly:** an owner who types their username gets "Invalid credentials", which is indistinguishable from a wrong password — the message cannot say more without confirming the account exists. Both owners use password managers that fill the address, but this is the one thing to tell them out loud.
- **A password reset clears `AccountLockout`, and must keep doing so.** Fixed 2026-08-12. Owners cannot be unlocked from Control Hub (`manage_unlock_account` refuses them by design), so the emailed code is a locked-out owner's only self-service route back — and it dead-ended: the lock is keyed to the account, not the password, so the owner read "Password changed. Please sign in with your new password", did exactly that, and was answered "This account is locked after too many failed attempts." That reads as the reset having failed, and the obvious next move makes it worse — another code, against a budget of three an hour, until `RESET_CODE_LIMIT` alarms **both** owners over somebody correctly recovering their own account. The IP backstop (`FailedAttempt`) is deliberately **not** cleared: its message names the network rather than the account so it never contradicts the reset, it clears itself on the same timer, and wiping it would erase the record of a spray against every other account behind that connection. Guarded by `test_a_locked_out_owner_can_sign_in_straight_after_resetting` and `test_the_reset_does_not_wipe_the_network_wide_failure_count`.
- **The whole Control Hub (`/manage/`) is Owner-only** — accounts, staff roster, and security alike. It was `@office_required` while the drawer only ever offered it to owners, so Office could not see it but could reach it by URL and create logins or reset passwords. One rule, no exceptions. Owner accounts are never managed *from* this panel: reset, delete and unlock each refuse them, because owner credentials are changed at `/change-password/` or recovered by emailed code.
- **`manage_unlock_account` lets an owner lift a lockout immediately.** Five wrong attempts lock a staff account for 15 minutes, which is right against guessing and wrong when a mechanic fat-fingers their password mid-shift. The unlock button only renders while an account is actually locked.
- **RBAC decorators return 403, not a login redirect, for signed-in users.** Anonymous visitors still get the sign-in page (with `?next=`, validated by `_safe_next` against open redirects). A signed-in user who simply lacks the role gets `PermissionDenied` → `templates/403.html`. Previously both cases redirected to a login form, so an Office user opening an Owner page saw a sign-in screen *while already signed in* — indistinguishable from being logged out. If you add a test asserting 302 for an authenticated wrong-role user, it's asserting the old bug.
- Every successful login raises a `LOGIN` notification to the other owners (username, device, IP). **Twilio and Telegram are gone** — removed 2026-07-29 once the in-app feed had replaced them. There is no outbound SMS or chat integration left anywhere in this codebase, and no third-party messaging dependency; don't reintroduce one. The app makes exactly **two** kinds of outbound network call, both optional and neither on the request path: the password-reset email (SMTP in development, Resend's HTTPS API in production) and Web Push to the browser vendors' push services.
- `UserSession` + `management_views.manage_terminate_session` give owners a kill switch over any active Django session from the dashboard.

### Financial/data integrity rules (enforced across the codebase, follow them in new code)
- All monetary fields are `DecimalField(max_digits=10, decimal_places=2)`. Never use `FloatField` for money. Inventory **stock quantities** are also `DecimalField` now (exact fractional units like 1.5 L of oil); display them with the `clean_qty` / `qty` template filter in `workshop/templatetags/custom_filters.py`, which strips trailing zeros (1.00→"1", 1.50→"1.5").
- `JobCard.total_bill_amount` is a denormalized physical column updated via `update_totals()` on every spare/labour save — don't recompute it ad hoc in views/templates.
- Model properties like `get_completion_percentage` check for pre-annotated aggregates on the instance before falling back to a `.count()` query; when adding list views, annotate rather than relying on the property's DB fallback.
- **Deletion model — two verbs, enforced everywhere (see `DeletionLog` and the plan in git history):**
  - *Accounts that other records point to* — Spare Shops, Fleet Accounts (`BulkPayer`), Supplier Shops, Mechanics — are **deactivated (archived)**, never hard-deleted (that would CASCADE-destroy their financial ledgers). They keep a boolean flag (`is_trashed` on SpareShop/BulkPayer, `is_active` on SupplierShop/Mechanic — the name differs by model, internal only), drop out of active lists/dropdowns, and reactivate safely from a per-module **Archived** list.
  - *Transactions & records* — Job Cards, Fleet/Shop/Supplier payments, Restock bills, Cashbook entries — are **permanently deleted**, but every delete first writes a snapshot via `DeletionLog.record(...)` to the Owner-only, read-only **Deletion History** (`/deletion-history/`). There is deliberately **no restore** (reviving stale financial data corrupts running balances).
  - **Job-card delete guard:** a job card carrying spares, labour, or a received payment **cannot** be deleted — its spares must be removed/moved to Unassigned and labour cleared first (`jobcard_delete` in `views/jobcard.py`). A deletable card holds no spares, so no stock is affected.
  - Financial-transaction deletes **reverse their effect** (restore job-card balances / warehouse stock) inside the same atomic block, then log + hard-delete.
  - `is_deleted` (JobCard) is retained as a **dormant** column (still filtered on for compatibility with dashboards/analysis) but is no longer written — job cards are hard-deleted, not soft-deleted.
  - Most FKs use `CASCADE`/`SET_NULL`; the **only** `on_delete=PROTECT` in the codebase is inventory `Category → Item`. (An earlier version of this file claimed financial FKs use `PROTECT` — that was inaccurate.)
- Auto-learned taxonomy (Brands, Models, Spares, Concerns) must dedupe with `__iexact`, never plain `=`, to avoid case-variant duplicates.
- Only one active (`completed=False, is_deleted=False`) job card is allowed per registration number at a time — a hard block, no bypass, enforced via `JobCard.get_active_conflict()`. Any code path that can put a job card into the active state (create, edit the registration number, undo a completion) must call it first. Previously `jobcard_create` had a 3-attempt "confirm and save anyway" bypass that let duplicates through, and `undo_completed`/`jobcard_edit` had no check at all — fixed 2026-07-23. If you add a new way to reactivate or create a job card, route it through the same check.
- The job-card completion status is the field `JobCard.completed` (boolean) with `completed_date`, surfaced in the UI as "Completed" and served at `/completed/` (`completed_list`/`mark_completed`/`undo_completed`, in `views/completed.py`). This was renamed from `delivered`/`discharged_date` on 2026-07-24 — the whole stack (field, DB column, URLs, module, templates) uses `completed` now; don't reintroduce "delivered" naming.
- Cascade payments (Bulk Payer and Spare Shop) follow the same pattern: `select_for_update()` inside `transaction.atomic()`, oldest-first ordering, distribute until exhausted, status transitions PENDING → PARTIAL → PAID. Only `BulkPaymentHistory` stores a JSON snapshot for reversal; Spare Shop payment history does not. `BulkPayer` also carries an `advance_balance` (credit carried forward when a lump payment exceeds what's owed) — `bulk_payer_pay()` pools new payment + existing advance before distributing, so `total_balance` can legitimately go negative. Note: the UI labels this feature **"Fleet Account"**; the model, fields, and URLs all still say `BulkPayer` — don't rename them to match the UI copy, and don't be confused when they don't match.
- **`JobCard.paid_date`** (added 2026-07-26) is set only inside `update_bill_status`/`bulk_payer_pay` when `payment_status` becomes `PAID`/`BULK_PAID`, and cleared when a payment is undone (`update_bill_status`'s zero-received branch, `bulk_payment_history_delete`'s reversal). Paid Bills filters and sorts on this field, not `updated_at` — `updated_at` is `auto_now=True` and changes on *any* save, so filtering by it made an old paid bill resurface under "Today" the moment someone edited it for an unrelated reason. Use `paid_date` for anything that means "when was this actually settled."
- **Financial Lock** (the "FINANCIAL LOCK ACTIVE" banner + auto-disabled fields in `jobcard_form.html`) covers `PAID` and `BULK_PAID` alike — a Fleet-settled job card gets the same protection as a directly-paid one. It's enforced both client-side (JS disables fields, requires a confirm() to unlock) and server-side (`jobcard_edit` rejects the POST unless the hidden `financial_unlock` field is `"true"`, which the unlock button sets) — don't remove either half, the client-side lock alone is trivially bypassed by a raw POST.
- **A job card can't be removed from a Fleet Account once it has `received_amount > 0`** (`bulk_payer_remove_card`). It's blocked, not auto-reversed: that money may be part of a lump payment shared with other cards in the same cascade, so there's no clean single amount to claw back. Reverse the specific `BulkPaymentHistory` entry first if the assignment was a mistake. (This gap was unguarded before 2026-07-26 — a `PARTIAL` job removed from its Fleet Account was left sitting at `PARTIAL` with no `bulk_payer`, a state normal customers should never be able to reach.)
- The `BULK_PAID` payment-status choice displays as **"Fleet Paid"** in the UI (changed from "Bulk Paid" for the Fleet Account rebrand) — the constant name `BULK_PAID` is unchanged, only `get_payment_status_display()`'s label.
- List views paginate at 45 items/page (10 for inventory category grids) and use `select_related`/`prefetch_related` — match this when adding new list views.
- Never pass template variables through `|safe`; use `json_script` to hand data to JS (owner analytics dashboard is the reference implementation).
- Use `timezone.localdate()`, never `date.today()`, for any "today"/date-range logic — the server can run in UTC while the business operates in IST (`TIME_ZONE = 'Asia/Kolkata'`), and `date.today()` silently returns the wrong calendar day near midnight IST. This is already the standard across `cashbook_views.py`, `completed.py`, `paid.py`, `spare_shop.py`, `views_suppliers.py`, and `analysis_views.py`.
- List/ledger views with a time filter (Paid Bills, Completed, Spare Shop, Supplier Shop, Cashbook) use one shared calendar-aligned filter vocabulary: Today / This Week / This Month / This Year / Last Week / Last Month / Last Year / Custom range. Reuse this set for new filtered views instead of inventing a different one (e.g. a rolling `30d`/`365d` window).
- **A custom range is PARSED before it reaches the ORM.** `date.fromisoformat()`
  in a `try/except ValueError`, ignoring an unusable range rather than
  filtering by it. Handed straight to a `__date__gte` lookup, a string like
  `?start_date=abc` reaches `get_prep_value` and raises — a 500 from a
  hand-edited URL. `cashbook_views._apply_date_filter` always did this; Paid
  Bills, Completed, Spare Shop and the discount audit did not, and were brought
  into line on 2026-08-16. The pickers are `type="date"`, so this only ever
  fires on a crafted URL, but a 500 is a 500.

### Owner Analysis & Reports — rebuilt from scratch 2026-07-27
The old zone/tab placeholder system is **gone** (views, `analysis_zone`, and all
eleven `zones/`+`tabs/` templates deleted). Two pages now:

- **`/analysis/` — Profit.** The protected page: `Total Turnover − Total Expenses = Profit`
  for one date window, with the equation shown literally on screen. Owners read it to decide
  **profit distribution**, so keep it plain — no drill-downs, no cleverness. Filters are
  This Month / Last Month / This Year / Last Year / All Time / Custom (deliberately *not* the
  Today/This Week vocabulary the day-to-day list views use — profit isn't a daily number).
- **`/analysis/insights/` — Deep Analysis.** Everything else (mechanics, spares, vehicles,
  fleet, shops, operations), one AJAX-loaded section at a time via
  `/analysis/insights/<section>/`.

**All money math lives in `workshop/analysis_engine.py`, never in the views or templates** —
pure functions taking a date window, so the arithmetic is testable without a request. Views
resolve the window, call the engine, and render.

**The double-count rule — the thing most likely to get "fixed" into a bug.** A spare reaches a
car by one of two routes and is paid for exactly once:
- `JobCardSpareItem.shop` set → bought from a spare shop for that job → charged as the
  **Spare Shops** expense — `unit_price` as typed, the shop's LINE TOTAL, never
  multiplied by the quantity (changed 2026-08-17; see `SHOP_LINE_COST`).
- `shop` NULL **and** the part name matches an inventory `Item` → taken off warehouse stock →
  **already paid for** by a Supplies Shop restock bill, so it must **never** be charged again.

Against live data these partition the rows exactly (₹1.49Cr vs ₹97.9L); adding the second one
would overstate expenses by ~₹9.8M. `DoubleCountRuleTests` in `workshop/tests/test_analysis.py`
is the regression guard — if it fails, the workshop is being charged twice for one part. Don't
"fix" it by summing all spare cost. Anything with no shop *and* no stock match is surfaced as
its own "Other Spare Purchases" line rather than silently dropped.

**Wages come from Salary & Advance, never the Cashbook** (owner's rule: the Cashbook is for
general expenses). Wage cost for a settled month is `net_amount + advance_used` (an advance is
cash already out; the settlement pays the remainder), plus loose advances in months not yet
settled. Cashbook rows *named* like wages are **flagged, not filtered** — free-text categories
mean a keyword filter would hide real money — so the Profit page shows a "wages may be counted
twice" warning and lets the owner move the entry.

**`financial_position()` is deliberately NOT filtered by archive flags on the fleet side.**
The Profit page labels `fleet_due` "Of that, fleet accounts", directly under `receivable` —
it claims to be a *slice* of the figure above it, so the two must be drawn from the same
population. `fleet_due` used to filter `is_trashed=False` while `receivable` did not, so an
archived account with an unpaid card made the page contradict itself ("Customers owe us
₹1,000 / of that, fleet accounts ₹0"). The archive guard above means new data can't reach
that state, but accounts archived before it existed still can, and a balance must not
depend on whether someone tidied a list. **`payable_spare` and `payable_supplier` still
carry the equivalent filter and are still wrong** — logged as AUD-0082 in `TECH_DEBT.md`,
and worse than the fleet version was, because a vanishing *payable* raises reported profit.

Other invariants worth keeping: revenue is `total_bill_amount − discount_amount` (a discount is
money never earned, not an expense — for a settled card this equals `received_amount` exactly);
every stream is dated by its own natural date so a period never mixes bases; and
`monthly_series()` must always total to `build_profit_report()` (asserted in `ConsistencyTests`)
so the chart can never contradict the headline.

### Signals-driven stock sync
`inventory/signals.py` has three independent signal groups (8 `@receiver` handlers total) on `pre_save`/`post_save`/`post_delete`:
1. Workshop consumption (`JobCardSpareItem`, 3 handlers) — deducts stock for **`source='INVENTORY'` rows only**, resolved through the `item` FK. Rewritten 2026-07-30: it used to match `spare_part_name` against `Item.name`, which silently deducted the warehouse for shop-bought parts that shared a name (see the `source` entry under "Deliberate decisions"). Quantity edits and product corrections are handled by a `pre_save` snapshot of `(source, item_id, quantity)`, netted per product so the common case is one query. **Nothing is clamped at zero** — negative stock is intended, see the same section.
2. JobCard soft-delete reversal (`JobCard`, 2 handlers) — historically returned spare stock to the warehouse when a job card was soft-deleted (and re-deducted on restore), via a `pre_save` `_old_is_deleted` snapshot that only acts when the flag flips. **Now dormant:** job cards are hard-deleted (never soft-deleted), and the delete guard forbids deleting a card that still holds spares — so `is_deleted` never flips and these handlers no longer fire. Kept for safety; don't rely on them for new stock logic.
3. Supplier restocking (`SupplierRestockItem`, 3 handlers) — increases stock using the same snapshot+delta pattern, and is the **only** thing that moves `Item.avg_cost` (via `recompute_average_cost`, a full replay — see `inventory/costing.py`).
Keep any new stock-affecting model change signal-driven rather than mutating `Item.current_stock` directly in views.

## Testing conventions
Tests live in `workshop/tests/` (46 files) and `inventory/` (`tests.py`, `tests_suppliers.py`, `test_signals.py`, `test_costing.py`, `test_supplier_costing.py`) — 51 files, **1,414 tests** (counted 2026-08-18 from a full green run, after the read-only job card was laid out to the owner's own design and `test_jobcard_detail_view.py` grew with it; the figures here had gone stale five times before, so re-count rather than trusting this line — `DiscoverRunner(verbosity=0).build_suite(['workshop','inventory']).countTestCases()` is the counter, since grepping `def test_` cannot see tests inherited from shared base classes). Expect the full suite to take **20-79 minutes** — timed at 53 minutes on 2026-08-04, 31 on 2026-08-05, then 23, 33, 35, 41 and 42, and 63 and 69 on 2026-08-12, 79 and 63 on 2026-08-16, 71 on 2026-08-17, and 46 and **30 on 2026-08-18**, which is the clearest evidence that the spread is load-dependent rather than meaningful; the 71 had two other test processes competing for the same cores, and a run at 40 minutes has not hung. **Running two suites at once is safe** — SQLite's test database is in-memory by default (no `TEST['NAME']` is set), so concurrent `manage.py test` processes cannot collide on it, which is worth knowing when you only need to re-check one file. They always run against SQLite (see "Which database am I on?"), so the suite stays fast and never touches the hosted Postgres. When a test fails, the project convention (stated in `TITAN_MASTER_HANDOVER.md`) is "fix the code, not the tests" — treat failing tests, especially security/financial ones, as a signal the implementation regressed, not the test being wrong.

## Repo hygiene notes
- `API_DOCUMENTATION.md` and `TECH_INFO.md` were **deleted on 2026-08-10**, along with
  `TITAN_BLUEPRINT.html` (a v7 render, two doc versions behind), `migrate_to_postgres.py`
  (superseded by the `copy_sqlite_to_postgres` management command) and `_phase3_audit.py`
  (a throwaway exploration script). All five were unreferenced. The two docs were worse
  than merely stale: they described Twilio/Telegram notifications, `is_deleted` soft-delete
  with a Trash screen, and SMS 2FA as the *current* system, and `TECH_INFO.md` opened by
  instructing future AI agents to "copy these exact patterns. Do NOT hallucinate
  alternatives." Every one of those patterns had been removed from the codebase. They are
  in git history if a historical question ever needs them. Don't recreate them — what was
  accurate is owned by `MASTER_BLUEPRINT.md` and this file.
- `AUDIT_LOG.md` and `Aditing files/` were **removed on 2026-07-25**. Every finding was re-verified against the code; the ones still open were consolidated into `TECH_DEBT.md` (local, not in git), the deliberate ones into "Deliberate decisions" above, and the rest were confirmed fixed. Don't recreate them — that split was what caused the drift.
- The SMS/Telegram notification system was **deleted on 2026-07-29**, along with `verify_twilio.py`, `verify_alerts.py`, the `twilio` and `requests` dependencies, and its `.env` keys. Owner alerts are the in-app feed now (see "Notifications" above). If you find a doc still describing a dual-channel broadcast, that doc is stale.
- **`QA_VERIFICATION_REPORT.txt` was deleted on 2026-08-10**, after all 17 of its items
  were checked against the code: 13 were already fixed or already recorded as a
  deliberate decision, and the four still open became `AUD-0089`–`AUD-0092` in
  `TECH_DEBT.md`. It was superseded by the owner's own list
  (`TITAN_MASTER_HANDOVER.md` §VI.16), which is now marked up with a verified status
  per item. Two backlogs describing one set of problems is how the original drift
  started — don't recreate it.
- **`errors.log` is a real source of findings, not just noise — read it before
  clearing it.** Truncated on 2026-08-10, but only after two defects were lifted out
  of it that no review had caught: `AUD-0089` (adding a Supplies Shop under a
  duplicate name 500s, 40 occurrences, because the `except IntegrityError` runs on an
  already-broken transaction) and `AUD-0090` (Resend rejecting every outbound message
  that day with HTTP 422). It is gitignored, so nothing recovers it once cleared.
- **A stale Claude worktree can hold unmerged work.** `.claude/worktrees/` is
  gitignored machine-local state, but on 2026-08-10 the one there
  (`quizzical-curie-d3bace`, branch already merged) still carried **uncommitted**
  edits that were never applied to `main` — the fix removing the duplicated
  `{% if messages %}` blocks from `jobcard_form.html` and `data_cleanup.html`. Check
  `git -C <worktree> status` before pruning one; the branch being merged says nothing
  about the working tree.

## Doc ownership map (avoid re-introducing drift)
As of 2026-07-23 the root docs were restructured so each fact has exactly one home; update the owning doc, don't restate its content elsewhere:
- **`MASTER_BLUEPRINT.md`** — the numbers: model/field tables, URL route tables, template inventory, admin registrations, settings/env vars, test file inventory, file tree. If a model/view/route/template changes, update here.
- **`OPERATIONAL_BLUEPRINT.md`** — the workflow narrative: lifecycle flows, "who does what" by role, billing/cascade-algorithm walkthroughs, dashboard screen descriptions. Links to `MASTER_BLUEPRINT.md` for exact field/route names instead of repeating them.
- **`TITAN_MASTER_HANDOVER.md`** — mission statement, current status, the **single authoritative roadmap** ("Coming Soon"), the **deliberately out-of-scope list** (§VII — features left unbuilt on purpose), and the AI/developer working conventions ("Titan Creed"). Other docs link here instead of keeping their own roadmap list.
- **`README.md`** — the outward-facing summary for this deployment: feature highlights, tech stack, install steps. Summarizes and links to the three docs above rather than duplicating their tables.
- **`CLAUDE.md`** (this file) — how to work in the codebase day to day, plus the **deliberate decisions** that must not be "fixed".
- **`TECH_DEBT.md`** (local, gitignored) — known issues that are *not yet scheduled*. Distinct from the roadmap: `TITAN_MASTER_HANDOVER.md` says what we plan to do, `TECH_DEBT.md` says what we know is wrong. Re-verify an item before acting on it; it goes stale like anything else.
- **`GO_LIVE_RUNBOOK.md`** — the **one-time** go-live procedure, as an ordered checklist: the DNS records, the ordered day-of steps, and the rollback and lockout recovery paths.
- **`RAILWAY_OPERATIONS.md`** — the **ongoing** platform reference: creating the project, the full environment-variable table, how a deploy runs, shipping updates after go-live, database backups and restore, cost control, the maintenance schedule, and a troubleshooting table. Both are operational only and state no rules of their own, so a decision recorded here or in the handover is never restated in either.

When a change touches more than trivia (new model/field, new route, new workflow, roadmap item completed), update the owning doc in the same session — that's what let these go four commits stale last time.
