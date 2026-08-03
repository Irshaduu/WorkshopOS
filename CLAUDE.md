# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

WorkshopOS ("Titan") is a Django 5.2 monolith for a single premium automotive workshop: job cards, inventory, spare/supplier shops, bulk payer billing, cashbook, and owner analytics. Two apps: `workshop` (core business logic) and `inventory` (stock + supplier shops). **PostgreSQL** (Neon, Singapore) is the database in both development and production as of 2026-07-27; SQLite survives only for bulk dummy-data seeding and the test suite — see "Which database am I on?" below. The app is still pre-go-live: the Postgres instance holds demo data, not a real workshop's books, so don't describe it as "live production data".

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

- **`JobCardSpareItem.unit_price` means COST PER UNIT on both routes** — typed by Office
  for a shop purchase, snapshotted from `Item.avg_cost` at draw time for a warehouse
  one. `SPARE_COST` in `analysis_engine.py` and `SpareShop.update_totals()` both
  multiply it by quantity, so its meaning must stay uniform across routes; putting a
  customer price in it for inventory rows would make the margin report compute
  revenue − revenue = zero.
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

- **A capped Cashbook list says so.** Added 2026-08-02. The lists are sliced at
  `LIST_CAP` for performance while the totals above them are computed from the
  full queryset, so any period holding more rows than the cap showed a total
  that plainly did not add up from what was on screen, with nothing to explain
  the gap. Both the full page and the AJAX partial now state the real count.

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

Known-but-unscheduled problems live in `TECH_DEBT.md` (local, not in git).

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

### Navigation — one bar, one drawer (rebuilt 2026-07-25)
There is exactly **one** nav: a fixed top bar in `base.html` that renders identically on
all three form factors, plus a Bootstrap off-canvas drawer (`#appDrawer`) behind the
Manage/Menu button. There used to be a second, divergent mobile bottom nav; it was
deleted because the two menus listed different things. **Don't add a second nav** — a new
destination goes in the drawer, in the section it belongs to.
- Top bar is deliberately minimal: Floor · New · Completed · Notifications · Manage
  (Floor role gets Inventory instead of Completed/Notifications; the bell is Owner/Office
  only and is an intentional `href="#"` placeholder until the feature exists).
- `--nav-h` is the single source of truth for the bar height; `.main-content` offsets
  itself by it. Change the variable, not the individual margins.
- The bar must carry Bootstrap's `fixed-top` class. It is load-bearing, not cosmetic:
  Bootstrap's scrollbar helper only pads elements matching `.fixed-top` when the drawer
  locks body scroll, and without it the bar jumps sideways by the scrollbar width on
  open. For the same reason `body` uses `overflow-y: scroll` **without**
  `scrollbar-gutter: stable` — the two together double-count the scrollbar.
- Labels shed worst-first on narrow phones (Manage below 420px, then
  `.nav-btn--label-optional` below 350px), so every pill that can become icon-only
  carries an `aria-label`. Keep that pairing when adding a pill.
- Drawer items are role-filtered in the template to match each view's decorator. If you
  change a view's RBAC decorator, update its drawer entry in the same edit.
- **Logout is confirmed, and there is exactly one logout control in the whole app.** The
  drawer button is a `data-bs-toggle="modal"` trigger; the POST form lives in
  `#logoutConfirmModal`, which sits **outside** the off-canvas — a modal nested inside one
  inherits its stacking context and opens behind the backdrop. Verified layering: modal
  1055 > modal-backdrop 1050 > offcanvas 1045 > offcanvas-backdrop 1040. A second logout
  control anywhere would reinstate the one-tap sign-out this prevents, which is why
  `LogoutConfirmationTests` asserts the page contains exactly one `action="/logout/"`.

## Commands

All commands assume the venv is active (`venv\Scripts\activate` on Windows) and require `DJANGO_ENV` set — the settings package (`formulad_workshop/settings/__init__.py`) raises `ImproperlyConfigured` if it's missing. It is **not** read from `.env` (python-decouple isn't involved for this one var); it must be a real shell/session env var.

```bash
# Windows (PowerShell)
$env:DJANGO_ENV = "development"

# Run dev server
python manage.py runserver

# Run full test suite (32 test files, 555 tests, ~15-30 min; always uses SQLite, see below)
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
python manage.py backup_db       # rotated SQLite backup, keeps last 7 in /backups
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
python manage.py purge_business_data        # DRY RUN — prints what it would delete
python manage.py purge_business_data --yes  # actually delete

# SQLite -> PostgreSQL (see "Which database am I on?" below)
python manage.py copy_sqlite_to_postgres        # DRY RUN — prints the plan
python manage.py copy_sqlite_to_postgres --yes  # replace Postgres with the SQLite contents
```

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
  Postgres — and 555 tests at ~75 ms per round-trip would take hours. There is
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

**Email** (password-reset codes): `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, plus `EMAIL_REAL` (development only). One workshop-owned sending mailbox — `EMAIL_HOST_PASSWORD` is a Google **App Password**, not the account password, and needs 2-Step Verification enabled on that account. Recipients are per-account `User.email` values in the database, never in `.env`; change one with `set_owner_email`, which is why it needs no deploy. Development uses the console backend unless `EMAIL_REAL=true`, so ordinary work never sends real mail; `manage.py test` uses Django's locmem backend regardless.

## Architecture

### App boundaries
- **`workshop/`** — job cards, billing, bulk payers, spare shops, cashbook, auth, owner analytics, deletion history, master data (brands/models/spares/concerns).
  - `views/` is a package (14 modules: `dashboard`, `jobcard`, `completed`, `deletion_history`, `billing`, `bulk_payer`, `spare_shop`, `pending`, `paid`, `car_profiles`, `master_lists`, `autocomplete`, `audits`, `salary_advance`). `views/__init__.py` re-exports everything so `from . import views; views.some_function` and existing URL wiring keep working — when adding a view, add it to both its module and the `__init__.py` re-export list.
  - `analysis_views.py`, `analysis_engine.py`, `auth_views.py`, `cashbook_views.py`, `cleanup_views.py`, `management_views.py` are standalone top-level modules (not part of the `views/` package), imported directly in `urls.py`. `analysis_engine.py` holds no views at all — it is the pure money math behind the Analysis section, and `master_data.py` likewise holds no views: it is the one implementation of the master-list rename/merge rule, shared by `views/master_lists.py` and `cleanup_views.py` (see "Deliberate decisions" for why that sharing is load-bearing).
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
The whole event list lives in **`workshop/notifications.py`**. Add an event to `EVENTS`, then call `notify()` from the single place it happens — **never** `Notification.objects.create()` in a view. With a dozen call sites across fourteen view modules, that file is the only way to answer "what does this thing notify about?" without grepping.
- **Fanned out per recipient**, so the unread count is one indexed query. **No FK to the subject** — most events announce a *deletion*, and a FK would cascade the notification away with the thing it was about; `object_type`/`object_id` plus a frozen label in `body` is the same discipline as `DeletionLog.snapshot`.
- **`DeletionLog.record()` is the deletion hook.** Every permanent delete already funnels through it, so one call covers all nine entity types and any added later. Don't scatter equivalent `notify()` calls into individual delete views.
- **Owners only, and the actor never hears about their own action.** Floor gets nothing — a notification a mechanic can't act on trains everyone to ignore the bell. The bell in `base.html` is Owner-gated to match; widen the gate and the audience together or you get a bell that can never fill.
- Audience is resolved by **group membership**, not `is_superuser` — see the Owner-group note under Security below for why that distinction is load-bearing.
- `notify()` swallows its own errors so a malformed body can't fail a payment. That promise stops at database errors inside an atomic block: the surrounding transaction is already doomed and shouldn't be rescued.
- Severity is a tier, not decoration: **`CRITICAL` events send a Web Push, `INFO` events only land in the feed.** Keep the critical list short — a phone that buzzes for routine activity stops being read for the things that matter.
- **A notification's `url` must land somewhere that can act on its subject — check the destination actually *contains* it.** `ACCOUNT_LOCKED` pointed every lockout at Control Hub → Accounts, which lists Office and Floor only, and `manage_unlock_account` refuses owner accounts by design. So a locked *owner* opened a page that did not contain the account, did not mention a lockout, and offered nothing to press. It is now routed by role (owner → Security, staff → Accounts) with the remedy stated in the body. When adding an event, ask what the reader will do next and whether that page can do it; an empty `url` falling back to the feed is better than a confident link to the wrong place.
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

### Signed-out pages — one shell, two faces
Both login faces and both password-recovery steps extend `workshop/auth/base_auth.html`. A face overrides only its accent colour and its copy; the layout, the input styling and the submit guard are shared. **Light theme, no imagery** — the wordmark and a 3px accent hairline carry the brand; blue (`#2563eb`) is the Staff face, red (`#dc2626`) the Admin face. That mirrors `login_view` being one engine behind two faces, and it is why the two doors cannot drift apart visually the way the two *views* once did.
- The views pass `AUTH_PAGE` (`hide_chrome=True`), which suppresses the nav bar **and** the PWA install banner. A signed-out page owns the whole viewport; a bar offering "Floor" and "Login" above a login form is noise, and prompting someone to install the app before they have proved they can get into it is premature.
- **Every auth form must keep `js-auth-form` / `js-auth-submit`.** The guard in `base_auth.html` blocks a second submit while one is in flight — the staff form previously had none, so the button could be pressed repeatedly, each press another POST and each wrong one spending part of the account's five-attempt lockout budget. The `dataset.submitting` flag does the work, not `disabled`: a button disabled inside its own submit handler still lets a queued Enter keypress through in some browsers.

### Security model ("Steel Gate")
- **Two lockouts, different units.** `AccountLockout` is the primary: **5 failures locks that one account** for 15 minutes. `FailedAttempt` is a backstop counting by direct `REMOTE_ADDR` (X-Forwarded-For is intentionally ignored to prevent spoofed-IP bypass), at **`IP_FAILURE_LIMIT = 20`**. The IP threshold was raised from 5 on 2026-07-28 because the unit was wrong for this business: the laptop, the tablet and both owners' phones leave through one connection, so five fumbled attempts on the Floor tablet locked the owners out of their own devices. Don't lower it back — per-account lockout is what actually stops a guessing attack, and the IP gate now only catches a spray across many accounts. Tests touching either must clear `FailedAttempt.objects.all()` in `setUp` to avoid cross-test contamination.
- **Login is one view behind two faces.** `/login/` (staff) and `/admin-login/` (owner) both route to `auth_views.login_view` with a `face` kwarg; only the heading, the accent colour, and the Forgot Password link differ. **Either face accepts any role** — there is no per-face gate, and the old fake "Invalid credentials" shown to owners on the staff face is gone (it protected nothing, since the owner door is a button away, and guaranteed a baffling support call). Both URL names are load-bearing: the decorators' `login_url` values and every `reverse()` point at them. Sign in with **username, email, or mobile** — `resolve_user_by_identifier` tries each in that order and **fails closed** if more than one account matches.
- **The whole Control Hub (`/manage/`) is Owner-only** — accounts, staff roster, and security alike. It was `@office_required` while the drawer only ever offered it to owners, so Office could not see it but could reach it by URL and create logins or reset passwords. One rule, no exceptions. Owner accounts are never managed *from* this panel: reset, delete and unlock each refuse them, because owner credentials are changed at `/change-password/` or recovered by emailed code.
- **`manage_unlock_account` lets an owner lift a lockout immediately.** Five wrong attempts lock a staff account for 15 minutes, which is right against guessing and wrong when a mechanic fat-fingers their password mid-shift. The unlock button only renders while an account is actually locked.
- **RBAC decorators return 403, not a login redirect, for signed-in users.** Anonymous visitors still get the sign-in page (with `?next=`, validated by `_safe_next` against open redirects). A signed-in user who simply lacks the role gets `PermissionDenied` → `templates/403.html`. Previously both cases redirected to a login form, so an Office user opening an Owner page saw a sign-in screen *while already signed in* — indistinguishable from being logged out. If you add a test asserting 302 for an authenticated wrong-role user, it's asserting the old bug.
- Every successful login raises a `LOGIN` notification to the other owners (username, device, IP). **Twilio and Telegram are gone** — removed 2026-07-29 once the in-app feed had replaced them. There is no outbound SMS or chat integration left anywhere in this codebase, and no third-party messaging dependency; don't reintroduce one. The app now makes exactly **one** kind of outbound network call: SMTP, for password-reset codes.
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
  **Spare Shops** expense (`unit_price × quantity`).
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
Tests live in `workshop/tests/` (27 files) and `inventory/` (`tests.py`, `tests_suppliers.py`, `test_signals.py`, `test_costing.py`, `test_supplier_costing.py`) — 34 files, 730 tests. Expect the full suite to take **15-25 minutes**; budget for that rather than assuming it has hung. They always run against SQLite (see "Which database am I on?"), so the suite stays fast and never touches the hosted Postgres. When a test fails, the project convention (stated in `TITAN_MASTER_HANDOVER.md`) is "fix the code, not the tests" — treat failing tests, especially security/financial ones, as a signal the implementation regressed, not the test being wrong.

## Repo hygiene notes
- `API_DOCUMENTATION.md` is a long-form design doc kept at repo root — check it for historical rationale before assuming something is undocumented.
- `AUDIT_LOG.md` and `Aditing files/` were **removed on 2026-07-25**. Every finding was re-verified against the code; the ones still open were consolidated into `TECH_DEBT.md` (local, not in git), the deliberate ones into "Deliberate decisions" above, and the rest were confirmed fixed. Don't recreate them — that split was what caused the drift.
- The SMS/Telegram notification system was **deleted on 2026-07-29**, along with `verify_twilio.py`, `verify_alerts.py`, the `twilio` and `requests` dependencies, and its `.env` keys. Owner alerts are the in-app feed now (see "Notifications" above). If you find a doc still describing a dual-channel broadcast, that doc is stale.

## Doc ownership map (avoid re-introducing drift)
As of 2026-07-23 the root docs were restructured so each fact has exactly one home; update the owning doc, don't restate its content elsewhere:
- **`MASTER_BLUEPRINT.md`** — the numbers: model/field tables, URL route tables, template inventory, admin registrations, settings/env vars, test file inventory, file tree. If a model/view/route/template changes, update here.
- **`OPERATIONAL_BLUEPRINT.md`** — the workflow narrative: lifecycle flows, "who does what" by role, billing/cascade-algorithm walkthroughs, dashboard screen descriptions. Links to `MASTER_BLUEPRINT.md` for exact field/route names instead of repeating them.
- **`TITAN_MASTER_HANDOVER.md`** — mission statement, current status, the **single authoritative roadmap** ("Coming Soon"), and the AI/developer working conventions ("Titan Creed"). Other docs link here instead of keeping their own roadmap list.
- **`README.md`** — the outward-facing summary for this deployment: feature highlights, tech stack, install steps. Summarizes and links to the three docs above rather than duplicating their tables.
- **`CLAUDE.md`** (this file) — how to work in the codebase day to day, plus the **deliberate decisions** that must not be "fixed".
- **`TECH_DEBT.md`** (local, gitignored) — known issues that are *not yet scheduled*. Distinct from the roadmap: `TITAN_MASTER_HANDOVER.md` says what we plan to do, `TECH_DEBT.md` says what we know is wrong. Re-verify an item before acting on it; it goes stale like anything else.

When a change touches more than trivia (new model/field, new route, new workflow, roadmap item completed), update the owning doc in the same session — that's what let these go four commits stale last time.
