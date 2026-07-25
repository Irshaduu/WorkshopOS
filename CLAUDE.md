# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

WorkshopOS ("Titan") is a Django 5.2 monolith for a single premium automotive workshop: job cards, inventory, spare/supplier shops, bulk payer billing, cashbook, and owner analytics. Two apps: `workshop` (core business logic) and `inventory` (stock + supplier shops). SQLite in dev; `settings/production.py` is fully wired for PostgreSQL but the live deployment has **not migrated yet** — that's a planned, not-yet-done step (see `TITAN_MASTER_HANDOVER.md` roadmap). Don't describe Postgres as "in production" until that migration actually happens.

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
  **`workshop/tests/test_jobcard_views.py:268` is the regression test for this rule — it
  asserts a ₹100 discount on ₹500-of-₹600. Do not delete it as "locking in a bug".**
- **Brand / model / spare / concern are free text, not FKs to the master lists.**
  `CarBrand`, `CarModel`, `SparePart` and `ConcernSolution` exist as reference tables, but
  `JobCard.brand_name`, `JobCardSpareItem.spare_part_name` etc. are `CharField`s filled by
  autocomplete. This is a deliberate trade for data-entry speed on the shop floor, not an
  oversight. The mitigation is normalisation on save (already done for
  `registration_number` and `brand_name`), not converting them to ForeignKeys.

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

## Commands

All commands assume the venv is active (`venv\Scripts\activate` on Windows) and require `DJANGO_ENV` set — the settings package (`formulad_workshop/settings/__init__.py`) raises `ImproperlyConfigured` if it's missing. It is **not** read from `.env` (python-decouple isn't involved for this one var); it must be a real shell/session env var.

```bash
# Windows (PowerShell)
$env:DJANGO_ENV = "development"

# Run dev server
python manage.py runserver

# Run full test suite (19 test files across both apps)
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
```

`DJANGO_ENV=production` switches to PostgreSQL + SSL/HSTS enforcement (`settings/production.py`) — only use this if you actually have Postgres configured; otherwise always `development`.

Required `.env` keys (see `settings/base.py`, `auth_views.py`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `OWNER_*` (mobile numbers/chat IDs for the two owners), `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_FROM_NUMBER`, `TELEGRAM_BOT_TOKEN`. Production adds `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

## Architecture

### App boundaries
- **`workshop/`** — job cards, billing, bulk payers, spare shops, cashbook, auth, owner analytics, deletion history, master data (brands/models/spares/concerns).
  - `views/` is a package (13 modules: `dashboard`, `jobcard`, `completed`, `deletion_history`, `billing`, `bulk_payer`, `spare_shop`, `pending`, `paid`, `car_profiles`, `master_lists`, `autocomplete`, `audits`). `views/__init__.py` re-exports everything so `from . import views; views.some_function` and existing URL wiring keep working — when adding a view, add it to both its module and the `__init__.py` re-export list.
  - `analysis_views.py`, `auth_views.py`, `cashbook_views.py`, `cleanup_views.py`, `management_views.py` are standalone top-level modules (not part of the `views/` package), imported directly in `urls.py`.
  - `decorators.py` defines the RBAC decorators (`owner_required`, `office_required`, `staff_required`) built on three Django auth Groups: **Owner**, **Office**, **Floor**. Superusers pass every check. Use these decorators on any new view instead of rolling custom permission checks.
  - `middleware.py` (`SessionTrackingMiddleware`) updates `UserSession` (device/IP/last-activity) on every authenticated request, throttled to a 5-minute cooldown per session.
- **`inventory/`** — stock items/categories and supplier shops (`views.py` for core inventory, `views_suppliers.py` for the supplier-shop module). Stock levels are kept in sync with workshop activity purely via Django signals in `signals.py` — there is no direct view-to-view coupling between the two apps for stock changes.
  - **Inventory workflow (automation-first):** stock moves *only* via signals — restock bills add (+), job-card spare usage removes (−); there is **no manual stock-number editing anywhere** (the old `update_stock` was removed; Low Stock is read-only). **Item creation happens only through Supplier → Add Product** (`add_shop_catalog_item`), which now **requires an Average Stock** threshold. A product is one shared `Item` (unique per `category`+`name`) linked to shops via `ShopCatalogItem`; the *same product across shops is that one Item*. Name/threshold are edited from the shop catalog (`edit_catalog_item`). A catalog entry can be **deactivated** (`ShopCatalogItem.is_active`) — it stays listed (greyed) but drops out of restock bills. That exclusion is enforced **server-side** in `shop_restock_bill`/`edit_restock_bill` via `_active_catalog_items()`, not just in the picker template — any view that writes `SupplierRestockItem` rows must re-validate ids against the shop's active catalog, because those rows move real stock. `remove_shop_catalog_item` **deactivates instead of deleting** when the shop has restock-bill history (a hard delete would alter historical bill totals) **or the product still holds stock** (stock is signal-only, so deleting would silently destroy a countable quantity). Only a zero-stock, no-history orphan Item is deleted — and, like every permanent delete, it writes `DeletionLog.record(ENTITY_INVENTORY_ITEM, …)` first, inside the same atomic block.
  - **`average_stock` means "how many we normally keep in stock"**, not an alert threshold — Low Stock fires at **below 25%** of it (`inventory_low_stock`). Don't relabel the field as a threshold in the UI; the two numbers are different by design.
  - **Inventory RBAC:** Floor sees only the main list, **Low Stock** (read-only), and **Stock History**; everything else (Manage/Category, Add Product, restock, catalog, payments) is `@office_required`. "Manage Database" is a **read-only Category browser** (add/list/edit/delete Category; drill in to view products + the shop(s) that stock them — no product actions there).
  - **Category rules:** names dedupe on `__iexact` in both `add_category` and `edit_category`. Duplicates aren't cosmetic — `add_shop_catalog_item` resolves a category with `get_or_create(name__iexact=…)`, which raises `MultipleObjectsReturned` as soon as two spellings coexist. `Category.name` has no DB-level `unique=True` yet (adding it needs a dedupe migration first), so the view guards are the only protection. **Delete is allowed only while the category holds no products** (`Item.category` is `PROTECT`); the three-dot menu hides Delete for non-empty ones and the view re-checks.
  - **Stock History** (`consumption_history` + `inventory_history_mechanic`) is a **live query over `JobCardSpareItem`** (item · qty · mechanic · car · reg, grouped by `admitted_date`, This/Last-Week filter, per-mechanic totals drill-down). It does **not** use the legacy `ConsumptionRecord` model (now dormant), and adds no signals. Both views filter `job_card__is_deleted=False` (dormant flag, still carried for pre-existing rows) and flag entries whose `spare_part_name` matches no `Item` as **"not from stock"** — the deduction signal matches on `Item.name__iexact`, so an unmatched name deducts nothing and must not be displayed as a warehouse draw. The mechanic drill-down groups on `Lower('spare_part_name')` for the same reason. Rows are capped at `HISTORY_ROW_CAP` rather than paginated, so the day-grouped layout is never split.

### Settings
Split into `formulad_workshop/settings/{base,development,production}.py`. `__init__.py` picks one via `DJANGO_ENV` — there is no fallback default, so forgetting to set it fails loudly rather than silently using the wrong DB.

### Security model ("Steel Gate")
- `FailedAttempt` tracks login failures **by direct `REMOTE_ADDR` only** (X-Forwarded-For is intentionally ignored for lockout purposes to prevent spoofed-IP bypass) — 5 failures triggers a 15-minute IP lockout. Tests touching this must clear `FailedAttempt.objects.all()` in `setUp` to avoid cross-test contamination.
- Every successful login fires a dual-channel alert (Telegram Bot API + Twilio SMS) to both owners with username, device fingerprint, and IP. This notification system is flagged in the codebase as a legacy component slated for replacement — don't extend it further; ask before investing in it.
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
- List views paginate at 45 items/page (10 for inventory category grids) and use `select_related`/`prefetch_related` — match this when adding new list views.
- Never pass template variables through `|safe`; use `json_script` to hand data to JS (owner analytics dashboard is the reference implementation).
- Use `timezone.localdate()`, never `date.today()`, for any "today"/date-range logic — the server can run in UTC while the business operates in IST (`TIME_ZONE = 'Asia/Kolkata'`), and `date.today()` silently returns the wrong calendar day near midnight IST. This is already the standard across `cashbook_views.py`, `completed.py`, `paid.py`, `spare_shop.py`, `views_suppliers.py`, and `analysis_views.py`.
- List/ledger views with a time filter (Paid Bills, Completed, Spare Shop, Supplier Shop, Cashbook) use one shared calendar-aligned filter vocabulary: Today / This Week / This Month / This Year / Last Week / Last Month / Last Year / Custom range. Reuse this set for new filtered views instead of inventing a different one (e.g. a rolling `30d`/`365d` window).

### Owner Analysis & Reports dashboard — mid-rebuild, don't "fix" it
`analysis_dashboard` renders fine, but `analysis_zone` (the AJAX endpoint each zone card calls) currently renders `workshop/templates/workshop/analysis/zones/zone_*.html` — all seven of which are intentional 8-line placeholder stubs, not a bug. The fuller replacement templates already exist at `workshop/templates/workshop/analysis/tabs/{financials,inventory,operations}.html` but aren't wired to any view yet — they're mid-transplant, not dead code to delete. This whole section is a planned ground-up rebuild (see roadmap in `TITAN_MASTER_HANDOVER.md`); don't restore the old zone content or wire up the tabs templates unless the user specifically asks for that work.

### Signals-driven stock sync
`inventory/signals.py` has three independent signal groups (8 `@receiver` handlers total) on `pre_save`/`post_save`/`post_delete`:
1. Workshop consumption (`JobCardSpareItem`, 3 handlers) — deducts stock (handles rename/quantity-change/deletion via delta calculated from a `pre_save` snapshot).
2. JobCard soft-delete reversal (`JobCard`, 2 handlers) — historically returned spare stock to the warehouse when a job card was soft-deleted (and re-deducted on restore), via a `pre_save` `_old_is_deleted` snapshot that only acts when the flag flips. **Now dormant:** job cards are hard-deleted (never soft-deleted), and the delete guard forbids deleting a card that still holds spares — so `is_deleted` never flips and these handlers no longer fire. Kept for safety; don't rely on them for new stock logic.
3. Supplier restocking (`SupplierRestockItem`, 3 handlers) — increases stock using the same snapshot+delta pattern.
Keep any new stock-affecting model change signal-driven rather than mutating `Item.current_stock` directly in views.

## Testing conventions
Tests live in `workshop/tests/` (16 files) and `inventory/` (`tests.py`, `tests_suppliers.py`, `test_signals.py`). When a test fails, the project convention (stated in `TITAN_MASTER_HANDOVER.md`) is "fix the code, not the tests" — treat failing tests, especially security/financial ones, as a signal the implementation regressed, not the test being wrong.

## Repo hygiene notes
- `API_DOCUMENTATION.md` is a long-form design doc kept at repo root — check it for historical rationale before assuming something is undocumented.
- `AUDIT_LOG.md` and `Aditing files/` were **removed on 2026-07-25**. Every finding was re-verified against the code; the ones still open were consolidated into `TECH_DEBT.md` (local, not in git), the deliberate ones into "Deliberate decisions" above, and the rest were confirmed fixed. Don't recreate them — that split was what caused the drift.
- The SMS/Telegram notification system is explicitly called out in the docs as legacy and due for replacement — don't treat it as the long-term design.

## Doc ownership map (avoid re-introducing drift)
As of 2026-07-23 the root docs were restructured so each fact has exactly one home; update the owning doc, don't restate its content elsewhere:
- **`MASTER_BLUEPRINT.md`** — the numbers: model/field tables, URL route tables, template inventory, admin registrations, settings/env vars, test file inventory, file tree. If a model/view/route/template changes, update here.
- **`OPERATIONAL_BLUEPRINT.md`** — the workflow narrative: lifecycle flows, "who does what" by role, billing/cascade-algorithm walkthroughs, dashboard screen descriptions. Links to `MASTER_BLUEPRINT.md` for exact field/route names instead of repeating them.
- **`TITAN_MASTER_HANDOVER.md`** — mission statement, current status, the **single authoritative roadmap** ("Coming Soon"), and the AI/developer working conventions ("Titan Creed"). Other docs link here instead of keeping their own roadmap list.
- **`README.md`** — the outward-facing summary for this deployment: feature highlights, tech stack, install steps. Summarizes and links to the three docs above rather than duplicating their tables.
- **`CLAUDE.md`** (this file) — how to work in the codebase day to day, plus the **deliberate decisions** that must not be "fixed".
- **`TECH_DEBT.md`** (local, gitignored) — known issues that are *not yet scheduled*. Distinct from the roadmap: `TITAN_MASTER_HANDOVER.md` says what we plan to do, `TECH_DEBT.md` says what we know is wrong. Re-verify an item before acting on it; it goes stale like anything else.

When a change touches more than trivia (new model/field, new route, new workflow, roadmap item completed), update the owning doc in the same session — that's what let these go four commits stale last time.
