# WorkshopOS (Titan) — Workshop Management System

A premium, comprehensive Django-based workshop management system for a single automotive workshop. Manage job cards, inventory, customer vehicles, spare shop finances, bulk/fleet payments, and invoicing in one platform.

> Full technical reference (models, routes, templates): [`MASTER_BLUEPRINT.md`](MASTER_BLUEPRINT.md) · Workflow walkthrough: [`OPERATIONAL_BLUEPRINT.md`](OPERATIONAL_BLUEPRINT.md) · Status & roadmap: [`TITAN_MASTER_HANDOVER.md`](TITAN_MASTER_HANDOVER.md) · Coding conventions: [`CLAUDE.md`](CLAUDE.md)

## Features

### Role-Based Access Control (RBAC)
- **Three-Tier Permissions** — Dedicated access levels for **Owner**, **Office**, and **Floor (Mechanic)** roles.
- **Secure Admin Hub** — Password-protected Owner login with direct access and real-time security alerts.
- **Owner Analysis & Reports** — Owner-only. A protected **Profit** page (Total Turnover − Total Expenses = Profit, by month/year/custom range) used for profit distribution, plus a separate **Deep Analysis** page covering mechanics, spares, vehicles, fleet accounts, shops and operations.
- **Role-specific UI** — Dynamic navigation and information visibility based on user groups.

### Job Card Management
- **Digital Job Cards** — Create and manage service records with customer details, vehicle information, and work performed.
- **Real-time Status Tracking** — Progress bars and visual status cues on the Dashboard and Live Report views.
- **Auto-Learning Database** — System automatically captures new concerns and spare parts for future smart-suggestions (case-insensitive & whitespace-normalized).
- **Safety Hardened** — Double-confirmation modals for renames and deletes, and merge alerts to protect historical data.
- **Duplicate Prevention** — Only one active job card is allowed per registration number at a time, enforced on create, edit, and completion-undo alike — no bypass.

### Finance & Suppliers
- **Spare Shops Management** — Dedicated module for tracking parts suppliers, monitoring outstanding balances, and managing lump-sum supplier payments with cascade distribution.
- **Unassigned Spares Hub** — Add legacy stock/balances directly to a shop without linking to a job card. Move parts between job cards and the Unassigned pool. Import unassigned parts into new job cards.
- **Inline Shop Price Editing** — Update the shop-paid price of any spare item directly from the ledger page.
- **Bulk Payer Management ("Fleet Account" in the UI)** — Manage repeat/fleet customers with oldest-first cascading payments, automatic advance-credit carry-forward on overpayment, and a 2-step UI for bulk bill transfers.
- **Pending Bills Dashboard** — Centralized view of all unpaid/partially-paid jobs across the system.
- **Paid Bills Dashboard** — Dedicated ledger for all fully settled jobs with time-range and payment-method filters (Owner only).
- **Financial Audits** — Built-in tracking for High Discounts and Deleted Bulk Payers for financial accountability.
- **Payment Reversal** — Every bulk payment records a JSON snapshot enabling precise, surgical reversal by the Owner.
- **General Ledger (Cashbook)** — Standalone income & expense tracking for daily workshop overhead, with calendar-aligned date filters and net balance totals. Office and Owner only.

### Inventory System
- **Stock Management** — Track parts and consumables with low-stock alerts and percentage-based color coding.
- **Consumption Tracking** — Automatically records part usage from job cards via Django Signals (real-time delta sync).
- **Category Organization** — Group inventory items for easier management and restocking.
- **Supplier Shops** — Dedicated supplier management module for tracking inventory suppliers, creating restock bills, recording payments, and maintaining a per-supplier catalog. Stock auto-increases on restock and auto-reverses on bill deletion via signals.

### Dashboard & Layout
- **Live Report Dashboard** — High-visibility "Floor" view for mechanics and "Live Report" for office staff.
- **One Nav, Three Devices** — A single fixed top bar (Floor · New · Completed · Notifications · Manage) renders identically on laptop (Office), tablet (Floor) and mobile (Owners), with 44px touch targets and labels that shed gracefully on narrow phones. "Manage" opens an off-canvas drawer holding every other destination, grouped by section and filtered by role.
- **Skeleton Loading** — Shimmer animations for a smooth loading experience.

### Estimates
- **Quotations on the workshop's own letterhead** — Write a quote before any work is agreed, print it, and keep every one in a searchable history (`EST-26-001`).
- **The same document as the bill** — Both are built by one module, so an estimate and the invoice that follows it agree on the letterhead, the layout and how labour is subtotalled. Where they differ is deliberate: an estimate prints only the quantities and unit prices somebody actually typed, because it describes work that has not happened yet.
- **Connected to nothing, on purpose** — An estimate creates no job card, moves no stock, and touches no ledger or report. A quote is a proposal; counting it would be counting work the workshop has not done.
- **Suggested pricing** — Typing a part name shows what it last sold for (average of its last five bills) in the Unit Price box's placeholder. A suggestion only — never filled in, never saved.
- **Nothing is required** — A quote saves however little is filled in, and the car's colour is recorded with the same picker as a Job Card so it can be spotted at a glance in the history.

### Invoice & Billing
- **Print-ready invoices** — One A4 sheet matching the workshop's own letterhead, rendered the same on screen as on paper (narrow screens scale the page rather than rearranging it). Fully self-contained: no CDN, so a bill never prints unstyled on a bad connection.
- **One parts list** — Spare-shop purchases and warehouse draws merged into a single "PART NAME" section. A warehouse draw is billed under its category ("Engine Oil"), never the branded product it was bought as, and the unit price shown is always derived from the customer total — the workshop's own cost never reaches the bill.
- **Clean print view** — Every control lives outside the printable sheet, not merely hidden by CSS. Long bills paginate with repeating column headings and no split rows.
- **Cost Analytics** — Automatic calculations for parts and labour.
- **Sequential Billing** — Thread-safe billing numbers (e.g., `JB-26-001`).

### Data Management
- **Deactivate & Archive** — Accounts (Spare Shops, Fleet Accounts, Supplier Shops, Mechanics) are deactivated/reactivated rather than destroyed, so all linked job-card and financial history is preserved.
- **Deletion History** — Job cards and financial transactions are permanently deleted (with a guard that blocks deleting a job card still holding financial data), each recorded in an Owner-only, read-only audit log. No restore — reversing stale deletions would corrupt running balances.
- **Data Cleanup Tool** — Rename, merge, and delete duplicate entries across master lists with cascade updates.
- **Car Profiles** — Vehicle history tracking grouped by registration number with chronological visit numbering.

## Tech Stack

- **Backend**: Python 3.13 / Django 5.2 LTS
- **Database**: PostgreSQL in both development and production as of 2026-07-27. SQLite is kept only for bulk dummy-data seeding and for running the test suite, which selects it automatically.
- **Hosting**: Railway — the app and its PostgreSQL database in one project. See [`RAILWAY_OPERATIONS.md`](RAILWAY_OPERATIONS.md).
- **Frontend**: Bootstrap 5, vanilla JavaScript, CSS3. Server-rendered Django templates with page-scoped inline JS and **no build step** — a deliberate choice, recorded in [`CLAUDE.md`](CLAUDE.md).
- **Security**: `python-decouple` for environment variables, role-based decorators, per-account and per-IP lockout
- **Static Assets**: WhiteNoise, configured through `STORAGES` (Django 5.1 removed `STATICFILES_STORAGE` and ignores it silently)
- **Notifications**: in-app feed behind the nav bell, owner-only — 13 events covering sign-ins, large discounts (over ₹3,500), permanent deletions, salary activity, archives, and account security (lockouts, password resets, reset-code abuse). The nine CRITICAL ones also push to a phone. Twilio/Telegram were removed 2026-07-29. Outbound integrations are transactional email for password-reset codes, sent over **Resend's HTTPS API** (Railway blocks outbound SMTP below its Pro plan), and Web Push — both optional, and the app runs correctly with neither configured.

## Installation

### Prerequisites
- Python 3.13+
- pip

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/Irshaduu/WorkshopOS.git
   cd WorkshopOS
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   - Create a `.env` file with the required variables — see `CLAUDE.md` for the full list (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `OWNER_*`, the `EMAIL_*` block for password-reset codes in development, and the PostgreSQL settings). Production instead uses `RESEND_API_KEY`; the full deployment variable table is in [`RAILWAY_OPERATIONS.md`](RAILWAY_OPERATIONS.md) §3.
   - Set `DJANGO_ENV=development` in your shell/session (required — there is no default; see `CLAUDE.md`).

5. **Run migrations**
   ```bash
   python manage.py migrate
   ```

6. **Create superuser**
   ```bash
   python manage.py createsuperuser
   ```

7. **Run development server**
   ```bash
   python manage.py runserver
   ```

## Project Structure

```
WorkshopOS (Titan)/
├── formulad_workshop/      # Django project configuration & split settings
│   └── settings/           # base.py, development.py, production.py
├── workshop/               # Core application — job cards, billing, cashbook, analytics
│   ├── views/               # Modular views package
│   ├── analysis_views.py    # Owner Profit + Deep Analysis views
│   ├── analysis_engine.py   # All Analysis money math (pure, testable)
│   ├── cashbook_views.py    # Standalone Cashbook ledger
│   └── templates/
├── inventory/               # Inventory, stock & supplier shops app
│   ├── views.py
│   ├── views_suppliers.py
│   └── templates/
├── templates/               # Root templates (403, 404, 500 error pages)
├── static/                  # Global static assets
├── requirements.txt         # Django, Pillow, python-decouple, twilio, whitenoise, psycopg2-binary
└── manage.py
```

Exact model/route/template counts live in [`MASTER_BLUEPRINT.md`](MASTER_BLUEPRINT.md) — kept there as the single source of truth rather than restated here.

## 🛡️ Reliability, Performance & Security

WorkshopOS is backed by an automated test suite (**39 files, 956 tests**, counted 2026-08-10) covering security, models, views, signals, financial logic, and supplier/spare-shop operations, and follows deliberate performance patterns (server-side pagination, indexed lookups, N+1-safe querying) and a layered security model (IP-based lockout, RBAC, session monitoring with remote revoke). Full detail: [`TITAN_MASTER_HANDOVER.md`](TITAN_MASTER_HANDOVER.md).

## 🛠️ Operational Tooling
- **Database Backups** — `python manage.py backup_db` follows whichever database is active: `pg_dump` for PostgreSQL, a file copy for SQLite, keeping the 14 most recent. **On Railway it writes into the container's ephemeral filesystem, so the file does not survive the next deploy** — see [`RAILWAY_OPERATIONS.md`](RAILWAY_OPERATIONS.md) §6 for the backup procedure that actually persists.
- **Production Static Serving** — `WhiteNoiseMiddleware` serves static files directly from the application layer.
- **Deployment** — [`GO_LIVE_RUNBOOK.md`](GO_LIVE_RUNBOOK.md) is the one-time go-live checklist; [`RAILWAY_OPERATIONS.md`](RAILWAY_OPERATIONS.md) is the ongoing platform reference.

## 🔜 Roadmap

See [`TITAN_MASTER_HANDOVER.md`](TITAN_MASTER_HANDOVER.md) § Roadmap for the current, authoritative priority list.

---

**Version**: 8
**Last Updated**: 2026-07-23
**Status**: 🛡️ SECURITY HARDENED | 🔧 IN ACTIVE DEVELOPMENT
