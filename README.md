# WorkshopOS (Titan) — Workshop Management System

A Django workshop-management system for a single premium automotive workshop. Job
cards, inventory, spare and supplier shops, fleet billing, estimates, invoicing,
cashbook, photos and owner analytics in one platform.

> **Start here:** [`SYSTEM_MAP.html`](SYSTEM_MAP.html) — the whole system on one
> page, as a drawing. Open it in a browser; every section is a card and every flow
> is a line.
>
> **Docs:** technical reference (models, routes, templates) →
> [`MASTER_BLUEPRINT.md`](MASTER_BLUEPRINT.md) · workflow walkthrough →
> [`OPERATIONAL_BLUEPRINT.md`](OPERATIONAL_BLUEPRINT.md) · status & roadmap →
> [`TITAN_MASTER_HANDOVER.md`](TITAN_MASTER_HANDOVER.md) · coding conventions and
> deliberate decisions → [`CLAUDE.md`](CLAUDE.md)

---

## Features

### Access control
- **Three tiers** — **Owner**, **Office**, **Floor (Mechanic)** — as Django auth
  groups, enforced by decorators on every view. Template gates mirror their view's
  decorator.
- **Role-specific UI** — the nav bar, the drawer and per-page controls are filtered
  by role. Floor is shown no prices anywhere in the app.
- **Layered security** — per-account and per-IP lockout, session monitoring with
  remote revoke, no-store on authenticated pages, and an owner-only in-app alert feed.

### Job cards
- **Digital job cards** — customer, vehicle, concerns, work performed, and parts from
  either of two routes (a spare shop, or the warehouse shelf).
- **Real-time status** — progress rings and colour-coded state on the dashboard and
  the Live Report.
- **Auto-learning master data** — new concerns and spare names are captured for
  future suggestions, deduped case-insensitively.
- **Duplicate prevention** — only one active job card per registration number at a
  time, enforced on create, edit and completion-undo alike. No bypass.
- **Financial Lock** — a settled card's fields are disabled and its POST is refused
  without an explicit unlock, on the server as well as in the browser.
- **Safety-hardened master data** — renames propagate to historical job cards, and a
  merge is confirmed before it happens with both usage counts disclosed.

### Finance
- **Spare Shops** — parts suppliers, outstanding balances, and lump-sum payments with
  oldest-first cascade distribution.
- **Unassigned Spares Hub** — record a purchase against a shop before there is a job
  card to hang it on. Open to Floor as well, **add-only and with no price**: the
  mechanic who takes delivery records the part, and the office prices it when the
  shop's bill arrives.
- **Fleet Accounts** — repeat/fleet customers with cascading payments, automatic
  advance-credit carry-forward on overpayment, and reversal guarded to newest-first.
- **Pending & Paid Bills** — Owners get every time range plus the grand total
  collected; Office gets the last 7 days and no grand total, which is what settling a
  bill actually needs.
- **General Ledger (Cashbook)** — one chronological stream with `All / Out / In`
  chips, calendar-aligned date filters and a searchable pager. Office and Owner only.
- **Financial audits** — High Discount audit (flat ₹3,500 threshold) and Deletion
  History, both Owner-only.
- **Owner Analysis** — a protected **Profit** page showing
  `Turnover − Expenses = Profit` for one date window, plus a separate **Deep
  Analysis** page covering mechanics, spares, vehicles, fleet accounts, shops and
  operations.

### Inventory
- **Signal-driven stock** — restock bills add, job-card draws remove. There is no
  manual stock editing anywhere; Low Stock is read-only.
- **Weighted-average costing** — a full date-ordered replay, so a backdated or
  corrected supplier bill re-prices the draws it should and no others.
- **Negative stock is allowed**, deliberately — it is the signal that a supplier bill
  has not been entered yet, and it is reported separately from Low Stock so nobody
  reorders a part that is sitting on the shelf.
- **Supplier Shops** — restock bills with pro-rata discount apportionment, payments,
  and a per-supplier catalog.

### Customer documents
- **Print-ready invoices** — one A4 sheet on the workshop's own letterhead, rendered
  the same on screen as on paper (narrow screens scale the page rather than
  rearranging it). Fully self-contained: **no CDN**, so a bill never prints unstyled
  on a bad connection, and every control lives outside the printable sheet.
- **One parts list** — spare-shop purchases and warehouse draws merged into a single
  section. A warehouse draw bills under its *category*, never the branded product, so
  the bill does not publish the workshop's supply chain. The unit price shown is
  always derived from the customer total; the workshop's cost never reaches the bill.
- **Estimates** — quotations on the same letterhead, built by the same module, with a
  searchable history (`EST-26-001`). **Connected to nothing on purpose:** an estimate
  creates no job card, moves no stock and touches no ledger or report.
- **Sequential billing** — thread-safe numbers (`JB-26-001`).

### Photos
- **A separate subsystem the rest of the app does not know exists** — no column
  points at a photo, and nothing in the analytics or the printed documents can reach
  one. Photos upload independently of the form POST, so storage being slow or down
  never blocks a job card from saving.
- **Three surfaces** — car photos on a saved job card, a box per Spare Parts row, and
  a read-only box on Purchase History.
- **The bytes never touch Django** — the browser PUTs straight to S3-compatible
  storage on a presigned URL. **Entirely optional:** with no credentials configured
  the section is simply absent.

### Layout
- **One nav, three devices** — a single fixed bar (Admin · Completed · Live · Alerts ·
  Manage for Office and Owner; Floor · New · Inventory · Menu for mechanics) that
  renders at the top on a laptop and **at the bottom on phones**, with 44px touch
  targets and labels that shed gracefully. "Manage" opens an off-canvas drawer holding
  every other destination, grouped by section and filtered by role.
- **Outcome sounds** — four synthesised tones riding on Django's own message tags, so
  one attribute covers every action in the system. Per-device toggle, default on.
- **Installable (PWA)** — with icons generated from a single source file, and an
  offline page for bad workshop wifi.

### Data management
- **Archive, don't delete** — Spare Shops, Fleet Accounts, Supplier Shops and staff are
  deactivated rather than destroyed, so their linked history survives. Archiving is
  blocked where it would strand unpaid debt.
- **Deletion History** — job cards and financial transactions are permanently deleted,
  each snapshotted first to an Owner-only, read-only audit log. No restore: reversing
  stale deletions would corrupt running balances.
- **Data Cleanup** — rename, merge and delete duplicate master-list entries with
  cascade updates to historical job cards.
- **Car Profiles** — vehicle history grouped by registration, with per-car totals and
  an Owner-only gross-margin figure.

---

## Tech stack

| Layer | Choice |
|---|---|
| **Backend** | Python 3.13 · Django 5.2 |
| **Database** | PostgreSQL in development *and* production. SQLite is kept only for bulk dummy-data seeding and for the test suite, which selects it automatically. |
| **Hosting** | Railway — app and PostgreSQL in one project. See [`RAILWAY_OPERATIONS.md`](RAILWAY_OPERATIONS.md). |
| **Frontend** | Bootstrap 5, vanilla JavaScript, CSS3. Server-rendered Django templates with page-scoped inline JS and **no build step** — a deliberate, recorded choice. |
| **Static assets** | WhiteNoise via `STORAGES` with a manifest (Django 5.1 removed `STATICFILES_STORAGE` and ignores it silently). |
| **Config** | `python-decouple`; `DJANGO_ENV` has no default, so an unset value fails loudly. |
| **Notifications** | Owner-only in-app feed — **14 events**, of which **10 are CRITICAL** and also push to a phone. |
| **Outbound** | Exactly two kinds, both optional: password-reset email (Resend's HTTPS API in production, SMTP in development) and Web Push. The app runs correctly with neither configured. |

**Dependencies** (`requirements.txt`): Django, Pillow, python-decouple,
psycopg2-binary, whitenoise, gunicorn, coverage, pywebpush. No HTTP client library —
both outbound calls are written against stdlib `urllib`/`hmac`.

---

## Installation

**Prerequisites:** Python 3.13+, pip.

1. **Clone**
   ```bash
   git clone https://github.com/Irshaduu/WorkshopOS.git
   ```

2. **Virtual environment**
   ```bash
   python -m venv venv
   ```
   Then activate it — `venv\Scripts\activate` on Windows, `source venv/bin/activate`
   elsewhere.

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure**
   - Create a `.env` file — the full variable list is in
     [`CLAUDE.md`](CLAUDE.md) § Environment variables. The deployment table is
     [`RAILWAY_OPERATIONS.md`](RAILWAY_OPERATIONS.md) §3.
   - Set `DJANGO_ENV` in your shell — **required, there is no default.**
     ```bash
     export DJANGO_ENV=development
     ```

5. **Migrate**
   ```bash
   python manage.py migrate
   ```

6. **Seed the master data** *(brands, models, spare parts — needed before any demo
   seeding)*
   ```bash
   python manage.py load_master_data
   ```

7. **Run**
   ```bash
   python manage.py runserver
   ```

---

## Project structure

```
WorkshopOS (Titan)/
├── formulad_workshop/       # project config
│   └── settings/            # base.py, development.py, production.py
├── workshop/                # core app — job cards, billing, cashbook, analytics
│   ├── views/               # 18-module views package
│   ├── analysis_engine.py   # all Analysis money math (pure, testable)
│   ├── invoice.py           # both customer documents
│   ├── settlement.py        # what is still unfilled before a bill is settled
│   ├── master_data.py       # the one rename/merge implementation
│   ├── money.py             # rupee bounds, read from the column
│   ├── spare_dates.py       # ordered/received pair validation
│   ├── photos.py            # storage backend + URL signing
│   └── templates/
├── inventory/               # stock, categories & supplier shops
├── templates/               # 403 / 404 / 500
├── static/
└── manage.py
```

Exact model, route and template counts live in
[`MASTER_BLUEPRINT.md`](MASTER_BLUEPRINT.md) as the single source of truth.

---

## Testing

```bash
python manage.py test workshop inventory
```

**53 files, 1,508 tests** covering security, models, views, signals, financial logic,
supplier and spare-shop operations, salary settlement, the printed documents and
photos. Expect 20–80 minutes; the suite always runs on SQLite, so it never touches
hosted Postgres.

JavaScript tests are a **second** command, using Node's built-in runner — no npm, no
bundler:

```bash
node --test workshop/tests/js/
```

> **Convention: fix the code, not the tests.** A failing test — especially a security
> or financial one — is a signal the implementation regressed.

---

## Operations

- **Backups** — `python manage.py backup_db` follows whichever database is active
  (`pg_dump` for PostgreSQL, a file copy for SQLite), keeping the 14 most recent.
  ⚠ On Railway this writes to the container's **ephemeral** filesystem, so the file
  does not survive the next deploy — see
  [`RAILWAY_OPERATIONS.md`](RAILWAY_OPERATIONS.md) §6 for the procedure that persists.
- **Before real books go in** — `python manage.py purge_business_data --yes` clears
  every business table (it never touches logins, groups or the master lists).
- **Deployment** — [`GO_LIVE_RUNBOOK.md`](GO_LIVE_RUNBOOK.md) is the one-time
  checklist; [`RAILWAY_OPERATIONS.md`](RAILWAY_OPERATIONS.md) is the ongoing platform
  reference.

---

## Roadmap

See [`TITAN_MASTER_HANDOVER.md`](TITAN_MASTER_HANDOVER.md) §VI for the authoritative
list, and §VII for what is **deliberately out of scope**.

---

**Version 8** · pre-go-live · security hardened · in active development
