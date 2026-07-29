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

## Commands

All commands assume the venv is active (`venv\Scripts\activate` on Windows) and require `DJANGO_ENV` set — the settings package (`formulad_workshop/settings/__init__.py`) raises `ImproperlyConfigured` if it's missing. It is **not** read from `.env` (python-decouple isn't involved for this one var); it must be a real shell/session env var.

```bash
# Windows (PowerShell)
$env:DJANGO_ENV = "development"

# Run dev server
python manage.py runserver

# Run full test suite (27 test files; always uses SQLite, see below)
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
  Postgres — and 457 tests at ~75 ms per round-trip would take hours. There is
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
  - `analysis_views.py`, `analysis_engine.py`, `auth_views.py`, `cashbook_views.py`, `cleanup_views.py`, `management_views.py` are standalone top-level modules (not part of the `views/` package), imported directly in `urls.py`. `analysis_engine.py` holds no views at all — it is the pure money math behind the Analysis section.
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

### Web Push — a delivery layer, never a source of truth
`workshop/push.py` sends; `workshop/views/push.py` is the HTTP surface; `PushSubscription` is one row per **device**, not per user.
- **`sw.js` is served from the origin root by a Django view, not from `/static/`.** This is load-bearing, not a preference: a service worker can only control pages at or below its own path, so WhiteNoise serving it at `/static/sw.js` would silently limit its scope to `/static/` and it would never receive a push for the app. The view also sends `Service-Worker-Allowed: /` and `Cache-Control: no-store` (a cached worker means a fix ships and nobody gets it).
- **Nothing waits on the network.** `queue_push()` hands off to a background thread via `transaction.on_commit` — so a rolled-back action never announces itself, and saving a payment doesn't pay for two ~200 ms HTTPS calls. The thread opens and closes its own DB connection; it doesn't inherit the request's.
- **Push failing must never affect the feed.** Missing VAPID keys, a dead push service, zero subscribers — all no-ops. `notify()` guards the push call separately from the row write so a push problem can't even change its *return value*.
- **404/410 from the push service means that endpoint is permanently gone** — the row is deleted, not retried. Other errors are counted and dropped after `MAX_FAILURES`.
- **iOS only delivers push to an app added to the Home Screen.** In a plain Safari tab `PushManager` is simply absent. `static/js/push.js` detects this and says so explicitly; without that the button just looks broken on the exact device the owners use.
- Push is **optional in every environment**. A deploy with no VAPID keys is valid and degrades quietly.

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

Other invariants worth keeping: revenue is `total_bill_amount − discount_amount` (a discount is
money never earned, not an expense — for a settled card this equals `received_amount` exactly);
every stream is dated by its own natural date so a period never mixes bases; and
`monthly_series()` must always total to `build_profit_report()` (asserted in `ConsistencyTests`)
so the chart can never contradict the headline.

### Signals-driven stock sync
`inventory/signals.py` has three independent signal groups (8 `@receiver` handlers total) on `pre_save`/`post_save`/`post_delete`:
1. Workshop consumption (`JobCardSpareItem`, 3 handlers) — deducts stock (handles rename/quantity-change/deletion via delta calculated from a `pre_save` snapshot).
2. JobCard soft-delete reversal (`JobCard`, 2 handlers) — historically returned spare stock to the warehouse when a job card was soft-deleted (and re-deducted on restore), via a `pre_save` `_old_is_deleted` snapshot that only acts when the flag flips. **Now dormant:** job cards are hard-deleted (never soft-deleted), and the delete guard forbids deleting a card that still holds spares — so `is_deleted` never flips and these handlers no longer fire. Kept for safety; don't rely on them for new stock logic.
3. Supplier restocking (`SupplierRestockItem`, 3 handlers) — increases stock using the same snapshot+delta pattern.
Keep any new stock-affecting model change signal-driven rather than mutating `Item.current_stock` directly in views.

## Testing conventions
Tests live in `workshop/tests/` (24 files) and `inventory/` (`tests.py`, `tests_suppliers.py`, `test_signals.py`) — 27 files, 457 tests. They always run against SQLite (see "Which database am I on?"), so the suite stays fast and never touches the hosted Postgres. When a test fails, the project convention (stated in `TITAN_MASTER_HANDOVER.md`) is "fix the code, not the tests" — treat failing tests, especially security/financial ones, as a signal the implementation regressed, not the test being wrong.

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
