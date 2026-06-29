# WorkshopOS — Workshop Management System

A premium, comprehensive Django-based workshop management system designed to streamline automotive service operations. Manage job cards, inventory, customer vehicles, spare shop finances, bulk payments, and invoicing all in one professional platform.

## Features

### Role-Based Access Control (RBAC)
- **Three-Tier Permissions** — Dedicated access levels for **Owner**, **Office**, and **Floor (Mechanic)** roles.
- **Secure Admin Hub** — Password-protected Owner login with direct access and real-time security alerts.
- **Owner Analysis & Reports** — A high-performance, mobile-first analytics dashboard exclusive to Owners, featuring hero KPIs and asynchronous data zones.
- **Role-specific UI** — Dynamic navigation and information visibility based on user groups.

### Job Card Management
- **Digital Job Cards** — Create and manage service records with customer details, vehicle information, and work performed.
- **Real-time Status Tracking** — Progress bars and visual status cues on the Dashboard and Live Report views.
- **Auto-Learning Database** — System automatically captures new concerns and spare parts for future smart-suggestions (Case-insensitive & Whitespace Normalized).
- **Safety Hardened** — Double-confirmation modals for renames and deletes, and Dynamic Merge Alerts to protect historical data.
- **Duplicate Detection** — 3-attempt confirmation system prevents accidental duplicate entries for active vehicles.

### Finance & Suppliers
- **Spare Shops Management** — Dedicated module for tracking parts suppliers, monitoring outstanding balances, and managing lump-sum supplier payments with cascade distribution.
- **Unassigned Spares Hub** — Add legacy stock/balances directly to a shop without linking to a job card. Move parts between job cards and the Unassigned pool. Import unassigned parts into new job cards.
- **Inline Shop Price Editing** — Update the shop-paid price of any spare item directly from the ledger page.
- **Bulk Payer Management** — Dedicated module for managing repeating/fleet customers with cascading bulk payments chronologically (oldest-first).
- **Pending Bills Dashboard** — Centralized view of all unpaid/partially-paid jobs across the system.
- **Payment Reversal** — Every bulk payment records a JSON snapshot enabling precise, surgical reversal by the Owner.
- **General Ledger (Cashbook)** — Standalone income & expense tracking module for recording daily workshop overhead (rent, electricity, scrap sales, etc.) with date-range filters and net balance totals. Office and Owner only.

### Inventory System
- **Stock Management** — Track parts and consumables with low-stock alerts and percentage-based color coding.
- **Consumption Tracking** — Automatically record part usage from job cards via Django Signals (real-time delta sync).
- **Category Organization** — Group inventory items for easier management and restocking.
- **Supplies Shops** — Dedicated supplier management module for tracking inventory suppliers, creating restock bills, recording supplier payments, and maintaining a per-supplier catalog. Stock levels auto-increase on restock and auto-reverse on bill deletion via signals.

### Dashboard & Layout
- **Live Report Dashboard** — High-visibility "Floor" view for mechanics and "Live Report" for office staff.
- **Mobile Optimized** — Premium responsive design with a native-app feel and bottom navigation on mobile.
- **Skeleton Loading** — Professional shimmer animations for a smooth, high-performance user experience.

### Invoice & Billing
- **Professional Invoices** — Auto-generated, itemized invoices with company branding.
- **Cost Analytics** — Automatic calculations for parts, labour, and tax.
- **Sequential Billing** — Standardized, thread-safe billing numbers (e.g., JB-26-001).

### Data Management
- **Soft-Delete & Restore** — Full trash system with Owner-only restore and permanent delete.
- **Unified Tabbed Trash** — Single trash page with tabbed views for Job Cards, Bulk Payers, Payments, Spare Shops, and Shop Payments.
- **Data Cleanup Tool** — Rename, merge, and delete duplicate entries across master lists with cascade updates.
- **Car Profiles** — Vehicle history tracking grouped by registration number with chronological visit numbering.

## Tech Stack

- **Backend**: Python 3.13 / Django 5.2 LTS
- **Database**: SQLite (development & personal backup) · PostgreSQL (🔜 future production)
- **Frontend**: Bootstrap 5, Vanilla JavaScript, CSS3
- **Security**: python-decouple for environment variables, role-based decorators, IP-based lockout
- **Notifications**: Twilio SMS + Telegram Bot API (⚠️ current system — new notification system planned)

## Installation

### Prerequisites
- Python 3.13+
- pip

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/Irshaduu/formula-d-workshop.git
   cd formula-d-workshop
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
   - Create a `.env` file with required variables (see Configuration section below).
   - Set your `SECRET_KEY`, `DEBUG=True`, and `TIME_ZONE="Asia/Kolkata"`.

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
├── workshop/               # Core application (90 URL routes, 90+ views)
│   ├── views/              # Modular views package (12 modules)
│   ├── cashbook_views.py   # Standalone Cashbook ledger (4 views)
│   ├── templates/          # 63 HTML templates
│   └── static/             # App-specific CSS & JS
├── inventory/              # Inventory, stock & supplies shops app (33 URL routes)
│   ├── views.py            # 13 core inventory views
│   ├── views_suppliers.py  # 20 supplier shop views
│   └── templates/          # 18 HTML templates (6 core + 12 supplier)
├── static/                 # Global static assets
├── requirements.txt        # Python dependencies (Django, Pillow, python-decouple)
├── db.sqlite3              # SQLite database
└── manage.py               # Django management script
```

## 🛡️ Titan Standard: Automated Reliability
WorkshopOS is backed by an **automated test suite** across **19 test files** covering security, models, views, API endpoints, signals, middleware, financial logic, cashbook operations, supplier management, spare shop operations, and owner analytics.
- **Security Coverage**: Verified IP-lockouts, OTP authentication, and real-time session revocation.
- **Warehouse Pulse**: Verified stock-delta signals (Creation, Update, Name Change, Deletion).
- **Model Integrity**: Verified lifecycle transitions for Job Cards, User Sessions, Spare Shops, and Unassigned Spares.

## 🚀 Performance Engineering
Designed for scale with practical, measured optimizations:
- **O(1) Memory Usage**: Server-side pagination (45 records per page for lists, 10 for categories) ensures constant speed regardless of database size.
- **B-Tree Database Indexing**: Critical fields (`registration_number`, `admitted_date`, `is_deleted`, `delivered`, `updated_at`) are indexed for fast retrieval.
- **Query Hardening**: All views utilize `select_related` and `prefetch_related` to eliminate N+1 latency.
- **Composite Indexes**: Dashboard query pattern covered by multi-field composite index (`is_deleted`, `delivered`, `-updated_at`).

## 🔐 Steel Gate Security
- **Network-Level Defense**: `FailedAttempt` tracks login failures by IP address, not just cookies.
- **Collaborative Alert System**: Instant cross-notifications to both Owners on every login event via SMS + Telegram (⚠️ current system — new notification system planned).
- **HQ Command Switch**: One-click termination of any unauthorized session from the management dashboard.

## 🔜 Coming Soon
- **PostgreSQL Production Database** — Multi-million record production-grade deployment.
- **New Notification System** — Replacement for current SMS/Telegram bot architecture.

---

**Version**: 7.0  
**Last Updated**: June 2026  
**Status**: 🛡️ SECURITY HARDENED | 🔧 IN ACTIVE DEVELOPMENT
