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
access. Backed by **59 test files / 1,921 tests** covering security, views, signals,
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
groups (10 handlers)**, all using the same pre_save-snapshot + post_save-delta pattern:

1. **Workshop consumption** (3) — replacement, quantity adjustment, deletion. Deducts
   for `source='INVENTORY'` rows only, resolved through the `item` FK.
2. **JobCard soft-delete reversal** (2) — **dormant.** Job cards are hard-deleted and
   the delete guard forbids deleting a card that still holds spares.
3. **Supplier restocking** (5) — three on `SupplierRestockItem` (creation, edit,
   deletion), the **only** thing that moves `Item.avg_cost`; plus a
   `SupplierRestockBill` pre/post_save pair that re-costs the bill's lines when its
   **date** or its **discount** changes, since neither of those lives on a line.

**Warehouse stock is allowed to go negative**, deliberately — a negative balance is
self-healing and is the signal that a Supplies Shop bill is missing. See `CLAUDE.md`.

### 9. Owner Analysis & Reports

Two pages: **`/analysis/`** (Profit — `Turnover − Expenses = Profit` for one date
window, used for profit distribution, deliberately plain) and **`/analysis/insights/`**
(Deep Analysis — mechanics, spare parts, inventory, vehicles, fleet, shops,
cashbook, operations, one AJAX-loaded section at a time).

- **Turnover** = car bills (`total_bill_amount − discount_amount`) + cashbook income.
  A discount is money never earned, so it reduces turnover rather than appearing as an
  expense; for a settled card the result equals `received_amount` to the rupee.
- **Expenses** are four non-overlapping streams, all on ONE basis — what the work done
  in this period cost: Spare Shops, Inventory Used, Salary & Advance, General Cashbook.
  A part is charged when it is **fitted to a car**, whichever shelf it came off.
- **A Supplies Shop bill is NOT an expense.** Buying stock turns cash into goods on a
  shelf; it raises the payable and the shelf and nothing else. A supplier *payment*
  moves the payable again. Neither touches profit.
- **The same profit is then stated a second way**, in the owner's own terms: Labour +
  Spare Parts margin + Inventory margin + Cashbook Income = Gross Earnings, less salary
  and general cashbook. It closes with **no reconciling line** — which is only true
  because both halves charge stock at the same moment. A bridging row reappearing there
  means the two bases have drifted apart. → `TheProfitIsAlsoSaidTheOwnersWayTests`
- **Changed 2026-08-25, on the owner's decision.** The bill used to be the expense and
  the draw excluded — which put the two parts routes on two different bases (the spare
  route has always charged on fitting) and made monthly profit lumpy. The trade: profit
  now leans on `avg_cost`, so the uncosted-draw warning is load-bearing.
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
node --test "workshop/tests/js/*.test.js"
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
- **Both environments run PostgreSQL** — development on a local instance, production
  on Railway's own Postgres in the same project as the app. SQLite is used only for bulk seeding
  (`USE_SQLITE=true`) and automatically for `manage.py test`.
- **Modular views**: the `workshop` app's views live in a `views/` package of **18
  focused modules**, with full backward compatibility via re-exports in `__init__.py`.
  **Seven** further modules hold **no views at all** and exist so that one rule has
  exactly one implementation — see `CLAUDE.md` § Architecture.
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
| 7 | **Repo & docs cleanup** | Unreferenced files removed; every count in `MASTER_BLUEPRINT.md` re-derived from the code. **This is recurring, not finished** — the 2026-08-22 pass found the docs describing access rules the code had outgrown (the whole Supplier-Shops module and Control Hub had been tightened to Office/Owner while three docs still said Floor could reach them), a Trash-with-restore screen that no longer exists, a `CarModel.sample_image` field that never did, and six counts that had drifted (10 signal handlers reported as 8, 11 forms as 12, 16 `notify()` call sites as 18, 13 template filters as 12, 11 commands as 9, 30 models as 28). **Re-derive before quoting; do not trust a number because it is written down.** |
| 8 | **Photos** | Car photos on a saved job card, a box per Spare Parts row, and a read-only box on Purchase History. Storage is S3-compatible (Cloudflare R2, or Supabase as the no-card fallback), reached by the browser directly on presigned URLs — the app has no upload path and no media backend. Optional: with no credentials the section is simply absent. |
| 9 | **One origin, and a page that says it is loading** | Delivered 2026-08-21 as two commits. Every typed rupee amount now goes through `workshop/money.py` — the four payment screens had kept hand-rolled parsing, so `Infinity` settled a bill at an infinite receipt and 11 digits 500'd on Postgres. `GZipMiddleware` is on (211 KB → 55 KB on the job card form), which matters because `no-store` makes every page uncacheable. Every third-party asset is self-hosted from `static/vendor/`. And a 3px progress bar reports navigations, plus in-page updates that outlast 250 ms — the installed PWA is `display: standalone`, so it has no address bar or tab spinner of its own. Along the way: two JS tests that could never have passed now do, and a 300 ms debounce was removed from filter and pager taps. |

| 10 | **Owner Withdrawals** | Delivered 2026-08-31. Cash the owners take out for themselves had nowhere correct to go, and the likeliest place for it to land — the Cashbook — is the one place that breaks the profit figure, because `cashbook_expense()` feeds the equation. `OwnerWithdrawal` reaches exactly one figure in the whole engine, `cash_position()`'s money-out list, and nothing in `build_profit_report`. Owner-only end to end; both owners' totals printed and never netted; no edit, because delete is always available and every correction then lands in Deletion History. The same pass closed a defect in six screens: `parse_money` refuses a zero *before* it quantises, so `0.004` came back as `0.00` — a 500 on the three columns carrying a positive-amount constraint, and a zero-rupee row written on the one that does not. |

| 11 | **Deposit & Rent, and how far back money may be filed** | Delivered 2026-09-04. The workshop pays its rent in daily cash instalments to a collector who keeps his own book, and the office worked out what to hand over on paper every morning — `(target − paid) ÷ days left`. `/rent/` is that sum. **The rent and the deposits are two different numbers**: the rent is what a month COST, the deposits are how it gets PAID, and collapsing them would make monthly profit swing on a cash-flow decision. Nothing is stored but the rate and the deposits — no target column, no carry column — so one expression derives the pace, the carry-forward, a skipped day and a backdated rent change. Built for twenty years with **no cap and no pager**: one month of log, and history as collapsed year blocks. ⚠ It touched `analysis_engine` **nowhere** on delivery, deliberately — rent still reached the Profit page as a Cashbook category, so switching it on moved no reported figure by a rupee, and a test asserted that boundary so moving rent onto its own line would have to be a decision rather than a side effect.<br><br>The same pass closed the other end of the money-date range. `is_future()` had guarded one side for months; nothing guarded the other, and that is the quiet direction — a figure dated three years back rewrites a month nobody scrolls to. `money_dates.too_far_back()` floors **six** forms at the 1st of last month for Office (a **calendar month**, never a day count: a rolling 14 days breaks exactly when the office reconciles last month in the first days of this one). Owners are unbound, because a go-live opening figure and an audit correction are legitimately older — so above that line prevention becomes **detection**: two CRITICAL events reach the *other* owner, and because an alert is a feed the actor never sees, a row keyed into a closed month now says so **on the row, permanently**. The Cashbook also asks before taking a wage, an owner's name or anything to do with rent, with the owner names read from `owner_accounts()` rather than hard-coded. |

| 11b | **Rent became the fifth expense stream** | Delivered 2026-09-04, and the workflow forced it rather than anybody choosing it. The boundary above held on one assumption about PEOPLE — that the office would keep keying the monthly rent bill into the Cashbook. Once they started recording rent in its own section instead, "no figure moves" quietly became **"rent is in the books nowhere"**: September 2026 carried ₹35,000 of real rent and the Profit page charged ₹900 of it, while May–August carried ₹45,000 Cashbook rows against a stored rate of ₹35,000 — two different rents in one system, neither page aware of the other. All Time was worse: it opened on 2026-02-07 against a ledger reaching back to October 2023, hiding **₹10,15,000** of rent while claiming to cover everything.<br><br>The split is now the app's **fourth instance** of a rule it already followed three times: what the month COST is the rate, charged in whole months → the expense; what was HANDED OVER is the deposits, by the day the cash moved → Cash Tracking; the gap → a position tile. The arithmetic lives in `rent.py` and the engine calls it, so the Profit page and the Deposit & Rent page cannot drift. ⚠ Rent is the **only stream that needs a cap**, because it is the only one not summed from rows — a 1 Jan – 31 Dec window would otherwise charge twelve months in September, ₹1,05,000 of expense that has not happened. A Cashbook category named like rent is now a double count and is **flagged, never filtered**, matched on word boundaries because this workshop calls its electricity bill "Current bill".<br><br>The same pass closed a **go-live defect** found on the way: `purge_business_data` — the command the runbook says to run against production — had never cleared `OwnerWithdrawal`, `RentRate` or `RentDeposit`, all three added after it was written. It reported success either way, leaving ₹12,60,000 of fabricated rent and ₹12,32,500 of fabricated cash out on the development data. Setting the rent from the go-live month is now an opening-balance step, because nothing on any screen looks broken without it. |

### Open

| # | Item | State |
|---|---|---|
| 11 | **Hosting & go-live** | *In progress.* The system runs on Railway at a temporary URL; static serving, build commands and the email transport are done. **Remaining:** the production project on the Hobby plan under the workshop's own account, DNS for `app.formuladservice.in`, Resend domain verification, Cloudflare, and the go-live steps. Procedure: `GO_LIVE_RUNBOOK.md`. |
| 12 | **Deep debug pass** | The serious pre-handover sweep, to run once everything is wired on the real infrastructure. |
| 13 | **Frontend polish** | Ongoing. Raise the visual/UX bar to match the backend's rigor. |
| 14 | **Stability / security / performance / code-quality hardening** | Ongoing across both apps. |
| 15 | **Keep every financial and security rule under test** | Not a coverage percentage. The existing tests already cover the money and the access rules, which is where the risk is; chasing a number buys tests for template rendering and Django's own internals. **Add a test when a rule is added or a bug is fixed, not to move a metric.** |

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

- **DONE (2026-08-21): the app no longer loads anything from a third party.**
  It previously pulled Bootstrap's CSS, its icon font and its JS bundle from
  `cdn.jsdelivr.net`, Chart.js from the same, and the Barlow families from
  `fonts.googleapis.com` — 16 references across 14 templates, **none carrying an
  `integrity=` attribute**. All of it is now served from `static/vendor/`, at the
  exact versions those tags were pinned to.

  Recorded because the reasoning outlives the change:

  1. *Availability.* The HTML arrives from our own origin while a subresource
     fails, so the page rendered BROKEN rather than not at all — unstyled if the
     CSS dropped, and with the drawer, every modal and every ⋮ dropdown dead if
     the JS dropped. Narrower than "flaky wifi": it needed our origin to work
     while jsdelivr specifically did not, which is what a **cold cache on go-live
     day** looks like — the one day every device in the workshop loads the app for
     the first time.
  2. *Supply chain.* With no SRI, a compromised CDN would have executed arbitrary
     JavaScript on every page, including the settle screen. SRI was considered and
     rejected: it fixes tampering while making availability slightly worse, since
     a mismatched file is blocked outright.

  It costs nothing at runtime — WhiteNoise serves them content-hashed and
  pre-compressed, so after the first visit they are free, and two third-party
  origins leave the critical path.

  ⚠ **The Railway Build Command is now load-bearing.** `collectstatic` is not in
  the `Procfile`; it is a Build Command set by hand per project
  (`GO_LIVE_RUNBOOK.md` §1.2), and it does **not** travel with the repo. Without
  it these assets are never collected and the manifest storage 500s every page.
  It is set on the throwaway demo project; **the real production project needs it
  set again**, along with the Pre-Deploy `migrate`.

  Nothing here is hand-edited — `scratchpad/vendor_assets.py` refetches the lot,
  the same rule `build_app_icons.py` follows.

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
   JavaScript. `node --test "workshop/tests/js/*.test.js"` covers one deliberately DOM-free
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
