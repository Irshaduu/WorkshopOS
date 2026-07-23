# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

WorkshopOS ("Titan") is a Django 5.2 monolith for a single premium automotive workshop: job cards, inventory, spare/supplier shops, bulk payer billing, cashbook, and owner analytics. Two apps: `workshop` (core business logic) and `inventory` (stock + supplier shops). SQLite in dev; `settings/production.py` is fully wired for PostgreSQL but the live deployment has **not migrated yet** — that's a planned, not-yet-done step (see `TITAN_MASTER_HANDOVER.md` roadmap). Don't describe Postgres as "in production" until that migration actually happens.

Built for a low-volume, high-value workshop (premium/luxury car servicing, appointment-driven, not a high-throughput chain garage) with a small, flat staff structure — this is why RBAC only needs three tiers and why performance work should be judged against realistic load, not generic "web scale" assumptions.

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
- **`workshop/`** — job cards, billing, bulk payers, spare shops, cashbook, auth, owner analytics, trash/soft-delete, master data (brands/models/spares/concerns).
  - `views/` is a package (13 modules: `dashboard`, `jobcard`, `delivered`, `trash`, `billing`, `bulk_payer`, `spare_shop`, `pending`, `paid`, `car_profiles`, `master_lists`, `autocomplete`, `audits`). `views/__init__.py` re-exports everything so `from . import views; views.some_function` and existing URL wiring keep working — when adding a view, add it to both its module and the `__init__.py` re-export list.
  - `analysis_views.py`, `auth_views.py`, `cashbook_views.py`, `cleanup_views.py`, `management_views.py` are standalone top-level modules (not part of the `views/` package), imported directly in `urls.py`.
  - `decorators.py` defines the RBAC decorators (`owner_required`, `office_required`, `staff_required`) built on three Django auth Groups: **Owner**, **Office**, **Floor**. Superusers pass every check. Use these decorators on any new view instead of rolling custom permission checks.
  - `middleware.py` (`SessionTrackingMiddleware`) updates `UserSession` (device/IP/last-activity) on every authenticated request, throttled to a 5-minute cooldown per session.
- **`inventory/`** — stock items/categories and supplier shops (`views.py` for core inventory, `views_suppliers.py` for the supplier-shop module). Stock levels are kept in sync with workshop activity purely via Django signals in `signals.py` — there is no direct view-to-view coupling between the two apps for stock changes.

### Settings
Split into `formulad_workshop/settings/{base,development,production}.py`. `__init__.py` picks one via `DJANGO_ENV` — there is no fallback default, so forgetting to set it fails loudly rather than silently using the wrong DB.

### Security model ("Steel Gate")
- `FailedAttempt` tracks login failures **by direct `REMOTE_ADDR` only** (X-Forwarded-For is intentionally ignored for lockout purposes to prevent spoofed-IP bypass) — 5 failures triggers a 15-minute IP lockout. Tests touching this must clear `FailedAttempt.objects.all()` in `setUp` to avoid cross-test contamination.
- Every successful login fires a dual-channel alert (Telegram Bot API + Twilio SMS) to both owners with username, device fingerprint, and IP. This notification system is flagged in the codebase as a legacy component slated for replacement — don't extend it further; ask before investing in it.
- `UserSession` + `management_views.manage_terminate_session` give owners a kill switch over any active Django session from the dashboard.

### Financial/data integrity rules (enforced across the codebase, follow them in new code)
- All monetary fields are `DecimalField(max_digits=10, decimal_places=2)`. Never use `FloatField` for money.
- `JobCard.total_bill_amount` is a denormalized physical column updated via `update_totals()` on every spare/labour save — don't recompute it ad hoc in views/templates.
- Model properties like `get_completion_percentage` check for pre-annotated aggregates on the instance before falling back to a `.count()` query; when adding list views, annotate rather than relying on the property's DB fallback.
- Foreign keys to historical/financial records use `on_delete=models.PROTECT`; views must catch `ProtectedError` and show a friendly message instead of a 500.
- Auto-learned taxonomy (Brands, Models, Spares, Concerns) must dedupe with `__iexact`, never plain `=`, to avoid case-variant duplicates.
- Cascade payments (Bulk Payer and Spare Shop) follow the same pattern: `select_for_update()` inside `transaction.atomic()`, oldest-first ordering, distribute until exhausted, status transitions PENDING → PARTIAL → PAID. Only `BulkPaymentHistory` stores a JSON snapshot for reversal; Spare Shop payment history does not. `BulkPayer` also carries an `advance_balance` (credit carried forward when a lump payment exceeds what's owed) — `bulk_payer_pay()` pools new payment + existing advance before distributing, so `total_balance` can legitimately go negative. Note: the UI labels this feature **"Fleet Account"**; the model, fields, and URLs all still say `BulkPayer` — don't rename them to match the UI copy, and don't be confused when they don't match.
- List views paginate at 45 items/page (10 for inventory category grids) and use `select_related`/`prefetch_related` — match this when adding new list views.
- Never pass template variables through `|safe`; use `json_script` to hand data to JS (owner analytics dashboard is the reference implementation).
- Use `timezone.localdate()`, never `date.today()`, for any "today"/date-range logic — the server can run in UTC while the business operates in IST (`TIME_ZONE = 'Asia/Kolkata'`), and `date.today()` silently returns the wrong calendar day near midnight IST. This is already the standard across `cashbook_views.py`, `delivered.py`, `paid.py`, `spare_shop.py`, `views_suppliers.py`, and `analysis_views.py`.
- List/ledger views with a time filter (Paid Bills, Delivered, Spare Shop, Supplier Shop, Cashbook) use one shared calendar-aligned filter vocabulary: Today / This Week / This Month / This Year / Last Week / Last Month / Last Year / Custom range. Reuse this set for new filtered views instead of inventing a different one (e.g. a rolling `30d`/`365d` window).

### Owner Analysis & Reports dashboard — mid-rebuild, don't "fix" it
`analysis_dashboard` renders fine, but `analysis_zone` (the AJAX endpoint each zone card calls) currently renders `workshop/templates/workshop/analysis/zones/zone_*.html` — all seven of which are intentional 8-line placeholder stubs, not a bug. The fuller replacement templates already exist at `workshop/templates/workshop/analysis/tabs/{financials,inventory,operations}.html` but aren't wired to any view yet — they're mid-transplant, not dead code to delete. This whole section is a planned ground-up rebuild (see roadmap in `TITAN_MASTER_HANDOVER.md`); don't restore the old zone content or wire up the tabs templates unless the user specifically asks for that work.

### Signals-driven stock sync
`inventory/signals.py` has three independent signal groups (8 `@receiver` handlers total) on `pre_save`/`post_save`/`post_delete`:
1. Workshop consumption (`JobCardSpareItem`, 3 handlers) — deducts stock (handles rename/quantity-change/deletion via delta calculated from a `pre_save` snapshot).
2. JobCard soft-delete reversal (`JobCard`, 2 handlers) — when a job card is soft-deleted its spares' stock is returned to the warehouse; restoring it deducts again. Uses a `pre_save` `_old_is_deleted` snapshot and only acts when the flag actually flips.
3. Supplier restocking (`SupplierRestockItem`, 3 handlers) — increases stock using the same snapshot+delta pattern.
Keep any new stock-affecting model change signal-driven rather than mutating `Item.current_stock` directly in views.

## Testing conventions
Tests live in `workshop/tests/` (16 files) and `inventory/` (`tests.py`, `tests_suppliers.py`, `test_signals.py`). When a test fails, the project convention (stated in `TITAN_MASTER_HANDOVER.md`) is "fix the code, not the tests" — treat failing tests, especially security/financial ones, as a signal the implementation regressed, not the test being wrong.

## Repo hygiene notes
- `AUDIT_LOG.md` and `API_DOCUMENTATION.md` are long-form audit/design docs kept at repo root — check them for historical rationale before assuming something is undocumented.
- `Aditing files/` contains one-off audit reports, not application code.
- The SMS/Telegram notification system is explicitly called out in the docs as legacy and due for replacement — don't treat it as the long-term design.

## Doc ownership map (avoid re-introducing drift)
As of 2026-07-23 the root docs were restructured so each fact has exactly one home; update the owning doc, don't restate its content elsewhere:
- **`MASTER_BLUEPRINT.md`** — the numbers: model/field tables, URL route tables, template inventory, admin registrations, settings/env vars, test file inventory, file tree. If a model/view/route/template changes, update here.
- **`OPERATIONAL_BLUEPRINT.md`** — the workflow narrative: lifecycle flows, "who does what" by role, billing/cascade-algorithm walkthroughs, dashboard screen descriptions. Links to `MASTER_BLUEPRINT.md` for exact field/route names instead of repeating them.
- **`TITAN_MASTER_HANDOVER.md`** — mission statement, current status, the **single authoritative roadmap** ("Coming Soon"), and the AI/developer working conventions ("Titan Creed"). Other docs link here instead of keeping their own roadmap list.
- **`README.md`** — the outward-facing summary for this deployment: feature highlights, tech stack, install steps. Summarizes and links to the three docs above rather than duplicating their tables.
- **`CLAUDE.md`** (this file) — how to work in the codebase day to day.

When a change touches more than trivia (new model/field, new route, new workflow, roadmap item completed), update the owning doc in the same session — that's what let these go four commits stale last time.
