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

### Invoice & Billing
- **Professional Invoices** — Auto-generated, itemized invoices.
- **Cost Analytics** — Automatic calculations for parts and labour.
- **Sequential Billing** — Thread-safe billing numbers (e.g., `JB-26-001`).

### Data Management
- **Deactivate & Archive** — Accounts (Spare Shops, Fleet Accounts, Supplier Shops, Mechanics) are deactivated/reactivated rather than destroyed, so all linked job-card and financial history is preserved.
- **Deletion History** — Job cards and financial transactions are permanently deleted (with a guard that blocks deleting a job card still holding financial data), each recorded in an Owner-only, read-only audit log. No restore — reversing stale deletions would corrupt running balances.
- **Data Cleanup Tool** — Rename, merge, and delete duplicate entries across master lists with cascade updates.
- **Car Profiles** — Vehicle history tracking grouped by registration number with chronological visit numbering.

## Tech Stack

- **Backend**: Python 3.13 / Django 5.2 LTS
- **Database**: PostgreSQL (Neon) in both development and production as of 2026-07-27. SQLite is kept only for bulk dummy-data seeding and for running the test suite, which selects it automatically.
- **Frontend**: Bootstrap 5, vanilla JavaScript, CSS3
- **Security**: `python-decouple` for environment variables, role-based decorators, IP-based lockout
- **Static Assets**: WhiteNoise for production static serving
- **Notifications**: in-app feed behind the nav bell, owner-only (login, large discounts, permanent deletions, salary activity, archives). Twilio/Telegram were removed 2026-07-29; SMTP for password-reset codes is the only outbound integration.

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
   - Create a `.env` file with the required variables — see `CLAUDE.md` for the full list (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `OWNER_*`, the `EMAIL_*` block for password-reset codes, and the PostgreSQL settings).
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

WorkshopOS is backed by an automated test suite (19 files) covering security, models, views, signals, financial logic, and supplier/spare-shop operations, and follows deliberate performance patterns (server-side pagination, indexed lookups, N+1-safe querying) and a layered security model (IP-based lockout, RBAC, session monitoring with remote revoke). Full detail: [`TITAN_MASTER_HANDOVER.md`](TITAN_MASTER_HANDOVER.md).

## 🛠️ Operational Tooling
- **Automated SQLite Backups** — `python manage.py backup_db` for secure, rotated archiving of the local SQLite file (keeps the 7 most recent). Note: this backs up SQLite only; PostgreSQL backups are handled by Neon.
- **Production Static Serving** — `WhiteNoiseMiddleware` serves static files directly from the application layer.

## 🔜 Roadmap

See [`TITAN_MASTER_HANDOVER.md`](TITAN_MASTER_HANDOVER.md) § Roadmap for the current, authoritative priority list.

---

**Version**: 8
**Last Updated**: 2026-07-23
**Status**: 🛡️ SECURITY HARDENED | 🔧 IN ACTIVE DEVELOPMENT
