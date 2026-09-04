# WorkshopOS

A workshop management system for a single premium automotive workshop. Job cards,
inventory, spare and supplier shops, fleet billing, estimates, invoicing, a cashbook,
payroll, evidence photos and owner analytics, in one Django application.

> **[SYSTEM_MAP_DARK.pdf](SYSTEM_MAP_DARK.pdf)** — the whole system on one page: every
> section as a card, every flow as a line. Also in a light theme,
> [SYSTEM_MAP.pdf](SYSTEM_MAP.pdf).
>
> **[TITAN_SPEC_SHEET.md](TITAN_SPEC_SHEET.md)** — every file, model, route and rule,
> counted from the repository rather than estimated.

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
- **Cashbook** — one searchable chronological ledger of money out and in, each entry
  dated by the day the money moved rather than the day it was typed.
- **Salary and advances** — advances recorded the day the cash is handed over, and a
  month-end settlement that freezes each person's salary, leave, advance and net pay.
  A settled month never re-prices itself, and only the most recent one can be
  corrected.
- **Deposit and rent** — the premises are paid for in daily cash instalments to a
  collector, so the page says what to hand over today: whatever is left of the
  month's rent over the days left. Pay more today and tomorrow asks for less; skip a
  day and it asks for a little more. The rent and the deposits stay two separate
  numbers — what a month cost, and how it got paid — and twenty years of history
  folds away behind one line per year.
- **Owner withdrawals** — cash the owners take out for themselves, recorded where it
  belongs. It is not a business expense and the profit figure never moves because of
  it: profit is what is available to take. It shows as cash out, and nowhere else.
- **Owner analytics** — a profit page reading `turnover − expenses` over any date
  range, the same profit again broken down by what earned it, cash movement kept
  visibly separate from profit, plus a deeper breakdown by mechanic, spare, vehicle,
  fleet account and shop.
- Audit views for large discounts and for every permanent deletion, and a seven-day
  window on money deletes — a recent mistake is the office's to correct, anything
  older is an owner's to remove.

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
- Uploaded from the browser straight to S3-compatible storage on a presigned URL;
  the bytes never pass through the application.
- Entirely optional. With no storage configured the section is absent in production
  and nothing else changes; a development server falls back to local disk so the
  feature can still be worked on.

### Access and history

- Three roles — **Owner**, **Office**, **Floor** — enforced on every view, with the
  navigation and per-page controls filtered to match. Floor is shown no prices, and
  who the customer is stays with Office and the owners.
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
│   ├── analysis_engine.py   # the profit and cash figures
│   ├── invoice.py           # both customer documents
│   ├── settlement.py        # what is unfilled before a bill is settled
│   ├── master_data.py       # renaming and merging a name
│   ├── delete_window.py     # how old a record may be for Office to delete it
│   ├── money.py             # is this typed amount usable
│   ├── money_dates.py       # which day did this money move
│   ├── spare_dates.py       # ordered before received
│   └── photos.py            # object keys and URL signing
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

1,921 tests covering the financial rules, access control, stock signals, the printed
documents, and the supplier, fleet and salary flows. The suite runs on SQLite, so it
never touches a live database. A full run takes 20 to 80 minutes.

JavaScript tests run separately, on Node's built-in runner:

```bash
node --test "workshop/tests/js/*.test.js"
```
