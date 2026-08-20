# TITAN MASTER HANDOVER — WorkshopOS

> **Status:** pre-go-live · security hardened · in active development
> **Version:** 8

This is the **mission, status and roadmap** doc. The single authoritative "what's
next" list lives here; other docs link to it rather than keeping their own copy.

| For | See |
|---|---|
| exact model / route / template tables | `MASTER_BLUEPRINT.md` |
| workflow narrative — how a car moves through the system | `OPERATIONAL_BLUEPRINT.md` |
| day-to-day coding conventions and the deliberate decisions | `CLAUDE.md` |
| known-but-unscheduled problems | `TECH_DEBT.md` *(local, gitignored)* |
| the one-time go-live procedure | `GO_LIVE_RUNBOOK.md` |
| ongoing platform operation | `RAILWAY_OPERATIONS.md` |

---

## I. The mission

**WorkshopOS** is built for **one** premium automotive workshop — appointment-driven,
high-value vehicles, roughly 50 cars a month, seven staff, two owners. Not a
high-volume chain garage.

That distinction is load-bearing throughout. It is why RBAC needs three tiers and not
a permission matrix, why labour is quoted whole instead of costed by the hour, why
leave days are typed once a month instead of tracked daily, and why performance is
judged against real volume rather than generic "web scale".

**The standard:** functional integrity across every operation that touches money or
access. Backed by **53 test files / 1,508 tests** covering security, views, signals,
financial logic, cashbook, spare shops, salary settlement, the profit engine, the
printed documents, photos and the email transport behind password reset.

⚠ Re-count rather than trusting that figure — it has gone stale repeatedly. The
counter is in `CLAUDE.md` § Testing conventions.

---

## II. Core architecture — the "Steel Gate"

> This section is the mission-critical security and data-integrity logic. These
> systems are foundational and must not be broken or bypassed. Full reasoning for
> each rule is in `CLAUDE.md`; this is the map.

### 1. Sign-in lockouts — two units

- **Primary, per account (`AccountLockout`)** — 5 consecutive failures lock **that
  one account** for 15 minutes.
- **Backstop, per IP (`FailedAttempt`)** — 20 failures lock the network. Counted
  strictly by direct `REMOTE_ADDR`; `X-Forwarded-For` is ignored to prevent
  spoofed-IP bypass.

**Why the split:** the IP threshold used to be 5, and that was the wrong unit for
this business. The laptop, the tablet and both owners' phones leave through one
connection, so five fumbled attempts on the Floor tablet locked the owners out of
their own devices — the attack and the collateral damage were indistinguishable. The
account gate is the precise instrument now; the IP gate only catches a spray across
many accounts. **Don't lower it back.**

→ `workshop/tests/test_login.py`, `workshop/tests/tests.py`. Tests touching either
must clear `FailedAttempt.objects.all()` in `setUp`.

### 2. Login — one door

**`/login/` is the only sign-in page and any role uses it.** It reads `Sign In` /
`Identifier` and names no roles. `/admin-login/` redirects to it, kept alive for the
owners' bookmarks.

There were two faces — a blue "Staff Sign In" and a red "Admin Sign In" on one view.
They **gated nothing**, since either accepted any role; they only announced to anyone
who typed the address that privileged accounts exist and where their door is.

- **Sign in with username, email, or mobile.** `resolve_user_by_identifier` tries each
  in order and **fails closed** if more than one account matches.
- **Owners sign in by email address only.** A mobile resolves by its last ten digits,
  so the workshop's *published* phone was a valid owner identifier — and being
  nameable at this form is what lets someone lock an owner out five tries at a time.
  Office and Floor still use usernames; the reset flow still accepts any identifier,
  on purpose.
  ⚠ **Tell the owners:** typing a username gets "Invalid credentials", which cannot
  be worded more helpfully without confirming the account exists.
- **RBAC returns 403, not a login redirect, for signed-in users.** Anonymous visitors
  still get the sign-in page with `?next=`, validated against open redirects by
  `_safe_next`.

### 3. Password recovery

- **Change Password** (`/change-password/`, Owner-only) — a signed-in owner sets a new
  password with no email involved. This is the **handover path**: an owner gets a temp
  password verbally, signs in, replaces it. **Go-live therefore does not depend on
  email working on the day.** There is deliberately **no link to it in the UI**;
  don't delete it as dead code.
- **Forgot Password** — a **6-digit code emailed** to the owner's registered address.
  Chosen over Django's built-in reset *link* because on iOS an installed PWA has its
  own cookie jar, so a link tapped in the mail app completes the reset in a
  *different* session and leaves the app signed out. **The owners read this on
  iPhones.** Do not "simplify" it back.
- Every limit (10-min expiry, single use, 5 attempts, 60s resend, 3/hour) is counted
  **per account in the database**, not in the session — a session counter is defeated
  by clearing cookies. Responses are identical whether or not the account exists.
- **A reset clears `AccountLockout`.** Owners cannot be unlocked from Control Hub, so
  the emailed code is a locked-out owner's only self-service route back.
- **Owner identity lives in the database, not `.env`** — adding an owner or changing
  an address needs no deploy (`sync_owner_identity`, `set_owner_email`).

### 4. Notifications — one catalogue

The nav bell is an owner-only feed at `/notifications/` with an unread badge,
mark-one-on-open, mark-all-read, and a 14-day sweep of *read* rows.

**14 events, all Owner-audience, all declared in `workshop/notifications.py`.**

| Severity | Behaviour | Events |
|---|---|---|
| **CRITICAL** (10) | push to a phone + feed | `STAFF_LOGIN`, `ACCOUNT_LOCKED`, `PASSWORD_RESET`, `RESET_CODE_LIMIT`, `RESET_CODE_ATTEMPTS_SPENT`, `USER_CREATED`, `USER_DELETED`, `STAFF_PASSWORD_SET`, `HIGH_DISCOUNT`, `RECORD_DELETED` |
| **INFO** (4) | feed only | `LOGIN`, `ACCOUNT_ARCHIVED`, `SALARY_ADVANCE`, `SALARY_SETTLED` |

- **`RECORD_DELETED` hooks `DeletionLog.record()`** — the single choke point every
  permanent delete already passes through, so one call covers all eleven entity types
  and anything added later.
- **The actor is excluded from their own events**, which roughly halves volume with
  two owners; **Floor receives nothing at all**. Notification fatigue is the failure
  mode here: a bell that cries wolf stops being read, and the events that matter
  (large discount, permanent delete) are exactly the ones that would be missed.
- **An Office or Floor sign-in pushes; an owner sign-in does not.** A staff account is
  used on shared shop-floor devices and is the one the owners cannot see being used.
- **The two reset-abuse events are the only ones raised with no actor**, so they reach
  both owners including the one targeted, de-duped to one per account per hour.
- `HIGH_DISCOUNT` uses `JobCard.HIGH_DISCOUNT_AMOUNT` — **a flat ₹3,500** — the same
  constant as `audit_high_discounts` and the settle dialog, so none can disagree about
  what "large" means.

### 5. Web Push — a delivery layer, never a source of truth

- **CRITICAL events push to subscribed devices; INFO events wait in the bell.** Push
  sits over `Notification` rows that are already written — missing keys, a dead
  service, or nobody subscribed leaves the feed completely unaffected. That is why it
  was built last.
- **One subscription per device**, toggled by the small bell in the panel header.
- **`sw.js` is served from the origin root** by a Django view, never `/static/` — a
  service worker's scope is its own directory.
- **Nothing in the request path waits on the network**: `transaction.on_commit` → a
  background thread.
- **iOS caveat, unavoidable:** Web Push works only once the app is added to the Home
  Screen. In a normal Safari tab the API is absent, and the UI says so.
- ⚠ **Registration, install state and subscriptions are all per-origin** — every
  device must re-enable push after a change of host or domain.
- **Optional everywhere.** A deploy without `VAPID_*` keys is valid.

### 6. Outbound network calls

**The app makes exactly two kinds, both optional and neither on the request path:**
the password-reset email, and Web Push. There is no SMS or chat integration, and none
is to be added.

Mail leaves over **Resend's HTTPS API in production** (Railway blocks outbound SMTP
below its Pro plan) and SMTP in development. There is exactly one `send_mail()` call
site, so the flow, the throttles and the tests are identical on both.

### 7. Session command (`UserSession`)

Device parsing turns raw User-Agent strings into human-readable names (*Apple Safari
on iPhone*). Owners get full visibility over active staff sessions (40-day window) and
can remotely terminate any of them from the management dashboard.

### 8. The warehouse pulse — stock delta engine

Django signals in `inventory/signals.py` orchestrate stock across **three independent
groups (8 handlers)**, all using the same pre_save-snapshot + post_save-delta pattern:

1. **Workshop consumption** (3) — replacement, quantity adjustment, deletion. Deducts
   for `source='INVENTORY'` rows only, resolved through the `item` FK.
2. **JobCard soft-delete reversal** (2) — **dormant.** Job cards are hard-deleted and
   the delete guard forbids deleting a card that still holds spares.
3. **Supplier restocking** (3) — creation, edit, deletion. The **only** thing that
   moves `Item.avg_cost`.

**Warehouse stock is allowed to go negative**, deliberately — a negative balance is
self-healing and is the signal that a Supplies Shop bill is missing. See `CLAUDE.md`.

### 9. Owner Analysis & Reports

Two pages: **`/analysis/`** (Profit — `Turnover − Expenses = Profit` for one date
window, used for profit distribution, deliberately plain) and **`/analysis/insights/`**
(Deep Analysis — mechanics, spares, vehicles, fleet, shops, operations, one
AJAX-loaded section at a time).

- **Turnover** = car bills (`total_bill_amount − discount_amount`) + cashbook income.
  A discount is money never earned, so it reduces turnover rather than appearing as an
  expense; for a settled card the result equals `received_amount` to the rupee.
- **Expenses** are four non-overlapping streams: Spare Shops, Supplies Shops, Salary &
  Advance, General Cashbook.
- **The double-count rule** — a warehouse-drawn spare is *already* paid for by its
  restock bill, so its cost is never charged again. Counting all job-spare cost on top
  would overstate expenses by ~₹9.8M against the seeded data.
  → `DoubleCountRuleTests`
- **All money math lives in `analysis_engine.py`** as pure functions; views only
  resolve the window and render, so a charting bug can never become a profit bug.
  `monthly_series()` is asserted to total exactly to `build_profit_report()`.
- **XSS:** no `{{ variable|safe }}`. All JS data injection uses `json_script`.

### 10. Billing & the Fleet Account cascade

- **Locking**: `select_for_update()` inside `transaction.atomic()`, oldest-first, when
  a payment cascades across multiple unpaid job cards.
- **Advance credit**: `BulkPayer.advance_balance` banks any surplus and is pooled into
  the next payment, so `total_balance` can legitimately show negative (in credit).
- **Financial precision**: every monetary column is
  `DecimalField(max_digits=10, decimal_places=2)`. `FloatField` is prohibited.
- **Deletion model**: accounts that other records reference (Spare Shops, Fleet
  Accounts, Supplier Shops, Mechanics) are **archived, never hard-deleted**;
  transactions and job cards are **permanently deleted but snapshotted first** to the
  Owner-only `DeletionLog`. A guard blocks deleting a job card that still holds
  spares, labour or a received payment. The only `on_delete=PROTECT` in the codebase
  is inventory `Category → Item`.
- **Dedicated ledgers**: split Pending / Paid Bills with time-range filters and
  enforced RBAC.

---

## III. Performance engineering

Deliberate, standard patterns — appropriate headroom for a workshop's real volume, not
a claim of internet-scale throughput.

- **Server-side pagination** on all major list views (45 items; 10 for category grids).
- **Query hardening** — `select_related`/`prefetch_related` throughout.
- **Zero-query properties** — methods like `get_completion_percentage` check for
  pre-annotated fields before hitting the database.
- **Denormalized financials** — `JobCard.total_bill_amount` is a physical column
  updated via `update_totals()`, not computed at read time.
- **Indexing** — `db_index=True` on high-traffic lookups (`is_deleted`, `completed`,
  `registration_number`, `admitted_date`, `paid_date`, `brand_name`, `model_name`),
  plus a composite `[is_deleted, completed, -updated_at]` for the dashboard query
  pattern, and per-model composites on notifications, photos, cashbook and the
  deletion log.

> **No load testing at extreme scale has been performed.** If that claim is ever
> needed for a deployment, back it with an actual benchmark rather than asserting it
> here.

**One known hot spot:** the job-card form costs ~7 queries per spare row, mostly
`auth_group` lookups from the per-row role checks. `AUD-0096` / `AUD-0046` in
`TECH_DEBT.md`.

---

## IV. Operational commands

```bash
.\venv\Scripts\python.exe manage.py test workshop inventory
```

```bash
node --test workshop/tests/js/
```

Everything else — seeding, backups, the owner-identity commands, the SQLite→Postgres
copy — is in `CLAUDE.md` § Commands, which is the one place they are documented.

---

## V. The workspace

- **Core-only repository root**: application code, migrations and documented
  standards. Nothing else.
- **Environment isolation**: secrets live in `.env` — `SECRET_KEY`, the PostgreSQL
  credentials, the mail transport key. Owner *identity* deliberately does **not**:
  usernames, mobiles and email addresses are database rows.
- **Split settings**: `settings/` selects development or production via `DJANGO_ENV`,
  which has **no default** — an unset value raises `ImproperlyConfigured` rather than
  silently choosing a database.
- **Both environments run PostgreSQL** — development on Neon, production on Railway's
  own Postgres in the same project as the app. SQLite is used only for bulk seeding
  (`USE_SQLITE=true`) and automatically for `manage.py test`.
- **Modular views**: the `workshop` app's views live in a `views/` package of **18
  focused modules**, with full backward compatibility via re-exports in `__init__.py`.
  Six further modules hold **no views at all** and exist so that one rule has exactly
  one implementation — see `CLAUDE.md` § Architecture.
- **Deployment**: Railway (app + PostgreSQL in one project) behind
  `app.formuladservice.in`.

---

## VI. Roadmap

### Delivered

| # | Item | Notes |
|---|---|---|
| 1 | **Staff Registration** | `Mechanic.role` turned a mechanics-only table into one staff roster at `/manage/?section=staff`. Only Mechanic / Assistant Mechanic feed the job-card picker. |
| 2 | **Salary & Advance** | Advances recorded the day they happen, plus a month-end settlement that freezes salary / leave / advance / net into a `SalaryPaymentLine`. A settled month's figures never move afterwards. |
| 3 | **Estimates** | Quotations on the workshop's own letterhead, with a searchable history (`EST-26-001`). Built on `workshop/invoice.py`, so a quote and the bill that follows it cannot disagree. Deliberately connected to nothing else. |
| 4 | **Auth & notifications rebuild** | Delivered in six ordered phases so each left a working system: owner identity into the DB → Change Password → emailed reset code → login rebuilt → Control Hub locked to Owners → in-app feed. Web Push followed once the app was hosted. |
| 5 | **Owner Analysis rebuild** | The 7-zone placeholder system was deleted entirely and replaced with the two pages in §II.9. |
| 6 | **PostgreSQL migration** | Both environments. SQLite retained for exactly two jobs. |
| 7 | **Repo & docs cleanup** | Unreferenced files removed; every count in `MASTER_BLUEPRINT.md` recounted from the code. |
| 8 | **Photos** | Car photos on a saved job card, a box per Spare Parts row, and a read-only box on Purchase History. Storage is S3-compatible (Cloudflare R2, or Supabase as the no-card fallback), reached by the browser directly on presigned URLs — the app has no upload path and no media backend. Optional: with no credentials the section is simply absent. |

### Open

| # | Item | State |
|---|---|---|
| 9 | **Hosting & go-live** | *In progress.* The system runs on Railway at a temporary URL; static serving, build commands and the email transport are done. **Remaining:** the production project on the Hobby plan under the workshop's own account, DNS for `app.formuladservice.in`, Resend domain verification, Cloudflare, and the go-live steps. Procedure: `GO_LIVE_RUNBOOK.md`. |
| 10 | **Deep debug pass** | The serious pre-handover sweep, to run once everything is wired on the real infrastructure. |
| 11 | **Frontend polish** | Ongoing. Raise the visual/UX bar to match the backend's rigor. |
| 12 | **Stability / security / performance / code-quality hardening** | Ongoing across both apps. |
| 13 | **Keep every financial and security rule under test** | Not a coverage percentage. The existing tests already cover the money and the access rules, which is where the risk is; chasing a number buys tests for template rendering and Django's own internals. **Add a test when a rule is added or a bug is fixed, not to move a metric.** |

### Carried into go-live

Three items are verified-open and belong to the go-live sequence rather than to
development:

- **Push and the PWA install banner need re-testing on the real domain.**
  Registration, install state and subscriptions are per-origin, so every device must
  re-enable push after the move regardless. Steps: `GO_LIVE_RUNBOOK.md`.
- **Resend delivery must be confirmed before go-live, not after.** Password reset is
  the *only* self-service recovery an owner has. `AUD-0090` in `TECH_DEBT.md`.
- **Mobile type scale** — correct in the browser's device emulator, reported as too
  small on a real phone. This is a **design decision, not a bug**; measurements have
  been taken and the owner is answering it screen by screen.

One product question is still owed to the owner: **master-list rename/merge could be
replaced with delete-only plus click-through** to the job cards using an entry.
`AUD-0085` and the note in `TECH_DEBT.md` carry the trade.

---

## VII. Deliberately out of scope

This system is built for one workshop, to that workshop's actual working rhythm. The
following are **not missing features** — each was considered and left out. They will
be built only if the client asks.

| Not built | Why |
|---|---|
| **GST / tax invoicing** | The workshop does not bill under GST. No tax fields, no HSN codes, no GSTIN anywhere in billing. |
| **Customer-facing notifications** (SMS / WhatsApp / email to car owners) | The app makes two kinds of outbound call, both for the owners' own accounts. Do not add a messaging integration. |
| **Attendance tracking** | Leave days are typed once per person at month-end settlement. For seven staff that is less work than maintaining a daily record. |
| **Multi-mechanic assignment** | A job card has one `lead_mechanic`. Work is assigned verbally on the floor; the card records who owns the job, not everyone who touched it. |
| **General file attachments** (PDFs, documents on a job card) | Photos are a camera workflow with a fixed shape and a hard count limit. An open attachment store is a different problem with different retention, virus-scanning and naming questions. |

> **For reviewers — human or AI:** proposing any of the above is proposing **scope**,
> not reporting a defect. If a review flags one as "missing", the correct response is
> to point here. The same applies to the frontend-architecture decision in
> `CLAUDE.md`.

---

## VIII. Working conventions — the "Titan" creed

1. **Fix the code, not the tests.** If a test fails, the logic is likely wrong. Never
   bypass a security test.
2. **Every new rule gets a test.** One honest exception: the Django suite executes no
   JavaScript. `node --test workshop/tests/js/` covers one deliberately DOM-free
   module; everything else in the frontend must be verified by hand in the browser on
   the page it touches. Treat that as a reason to keep JS changes small — **not** as a
   reason to add a build toolchain.
3. **Industrial-grade aesthetics.** No placeholders, no generic colours. The UI must
   match the premium quality of the backend, and must work on all three devices.
4. **State what is true.** Overstated or unverified claims — performance numbers with
   no benchmark, counts nobody recounted — undermine the doc's credibility. This is
   what let these docs drift stale before.
5. **Keep docs in sync in the same session.** New model/field, new route, new workflow,
   roadmap item completed → update the owning doc. The ownership map is in `CLAUDE.md`.
