# WorkshopOS

A workshop management system for a single premium automotive workshop. Job cards,
inventory, spare and supplier shops, fleet billing, estimates, invoicing, a cashbook
and owner analytics, in one Django application.

> **[SYSTEM_MAP.pdf](SYSTEM_MAP.pdf)** — the whole system on one page: every section
> as a card, every flow as a line.

---

## Features

### Job cards

- Customer, vehicle, concerns, work performed and parts on one card.
- Parts arrive by one of two routes — bought from a spare shop for the job, or drawn
  off the warehouse shelf — and stay distinguishable all the way to the bill.
- Live progress on the dashboard and on a workshop board showing who is holding which
  car and which parts are still coming.
- Autocomplete for brands, models, spares and concerns, learning new entries as they
  are typed and matching case-insensitively.
- One active job card per registration number at a time.
- A settled card locks; changing one takes an explicit unlock, and the bill is
  reconciled afterwards.

### Billing and money

- Sequential bill numbers (`JB-26-001`), safe under concurrent writes.
- **Spare shops** — per-shop ledgers, running balances, and lump-sum payments spread
  across outstanding items oldest first.
- **Fleet accounts** — repeat and fleet customers billed across many cars, with
  surplus carried forward as advance credit and reversals guarded to newest-first.
- **Unassigned spares** — record a purchase before there is a job card to attach it
  to. Mechanics can log the part; the office prices it when the shop's bill arrives.
- **Cashbook** — one searchable chronological ledger of money out and in.
- **Owner analytics** — a profit page reading `turnover − expenses` over any date
  range, plus a deeper breakdown by mechanic, spare, vehicle, fleet account and shop.
- Audit views for large discounts and for every permanent deletion.

### Inventory

- Stock moves on its own: restock bills add, job-card draws remove. Nothing is edited
  by hand.
- Weighted-average costing, replayed in date order, so a backdated or corrected
  supplier bill re-prices the draws it should and leaves the rest alone.
- Stock may go negative, and is reported apart from low stock — a negative balance
  means a supplier bill has not been entered yet, not that anything needs reordering.
- **Supplier shops** — restock bills with pro-rata discount handling, payments, and a
  catalog per supplier.

### Customer documents

- Print-ready A4 invoices on the workshop's own letterhead, rendered the same on
  screen as on paper.
- Estimates on the same letterhead, with a searchable history (`EST-26-001`).
- Warehouse parts are billed under their category rather than the branded product, so
  a customer's bill does not name the workshop's suppliers.

### Photos

- Car photos on a saved job card, and a set per spare part.
- Uploaded from the browser straight to S3-compatible storage.
- Entirely optional — with no storage configured, the feature is absent and nothing
  else changes.

### Access and history

- Three roles — **Owner**, **Office**, **Floor** — enforced on every view, with the
  navigation and per-page controls filtered to match. Floor is shown no prices.
- Per-account and per-network sign-in lockout, live session monitoring with remote
  revoke, and an owner alert feed that can also push to a phone.
- Accounts other records depend on are archived rather than destroyed. Transactions
  are deleted permanently, each snapshotted first into a read-only audit log.

### Interface

- One navigation bar, at the top on a laptop and at the bottom on a phone, with a
  drawer holding everything else. Built for three devices: office laptop, workshop
  tablet, owners' phones.
- Installable as a PWA, with an offline page for unreliable workshop wifi.

---

## Tech stack

| Layer | |
|---|---|
| **Backend** | Python 3.13 · Django 5.2 |
| **Database** | PostgreSQL |
| **Frontend** | Server-rendered Django templates, Bootstrap 5, vanilla JavaScript — no build step and no npm |
| **Static files** | WhiteNoise with a content-hashed manifest; every frontend asset is self-hosted |
| **Outbound** | Password-reset codes by email, and Web Push for owner alerts. Both optional — the app runs correctly with neither configured |

**Dependencies:** Django, Pillow, python-decouple, psycopg2-binary, whitenoise,
gunicorn, coverage, pywebpush.

---

## Getting started

**Prerequisites:** Python 3.13+, PostgreSQL, pip.

```bash
git clone https://github.com/Irshaduu/WorkshopOS.git
cd WorkshopOS
python -m venv venv
```

Activate it — `venv\Scripts\activate` on Windows, `source venv/bin/activate`
elsewhere — then:

```bash
pip install -r requirements.txt
```

Create a `.env` file. `SECRET_KEY` is the only setting with no default; the database
values fall back to a local PostgreSQL instance.

```
SECRET_KEY=your-secret-key
DEBUG=True
DB_NAME=titan_db
DB_USER=titan_user
DB_PASSWORD=your-password
DB_HOST=localhost
DB_PORT=5432
DB_SSLMODE=prefer
```

Set the environment selector in your shell — it has no default, so an unset value
stops the app rather than guessing a database:

```bash
export DJANGO_ENV=development
```

Then migrate, load the reference lists, and run:

```bash
python manage.py migrate && python manage.py load_master_data && python manage.py runserver
```

---

## Project layout

```
WorkshopOS/
├── formulad_workshop/    # project config; settings split by environment
├── workshop/             # job cards, billing, estimates, cashbook, analytics, auth
│   ├── views/            # views package, one module per area
│   ├── analysis_engine.py
│   ├── invoice.py        # both customer documents
│   ├── settlement.py
│   ├── master_data.py
│   ├── money.py
│   ├── spare_dates.py
│   └── photos.py
├── inventory/            # warehouse stock, categories and supplier shops
├── templates/            # error pages
├── static/
└── manage.py
```

---

## Tests

```bash
python manage.py test workshop inventory
```

Over 1,500 tests covering the financial rules, access control, stock signals, the
printed documents, and the supplier, fleet and salary flows. The suite runs on SQLite,
so it never touches a live database.

JavaScript tests run separately, on Node's built-in runner:

```bash
node --test "workshop/tests/js/*.test.js"
```
