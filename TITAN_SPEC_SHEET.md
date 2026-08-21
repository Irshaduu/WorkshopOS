# Titan Spec Sheet

**WorkshopOS "Titan"** — every file, line, model and rule, counted from the
repository rather than estimated.

| | |
|---|---|
| **Measured** | 22 August 2026, branch `main`, including this document |
| **Stack** | Django 5.2 monolith, PostgreSQL in development and production |
| **Apps** | 2 — `workshop` (core business logic), `inventory` (stock + supplier shops) |
| **History** | 181 commits, 11 January – 22 August 2026 |

| Files | Lines | Tests | Models | Routes | Screens | Dependencies |
|---:|---:|---:|---:|---:|---:|---:|
| **379** | **97,578** | **1,524** | **38** | **288** | **106** | **8** |

> Every figure below was re-measured from the working tree. Nothing is carried
> over from documentation.

---

## 01 — Totals: files and lines

379 files tracked in version control. The four categories do not overlap and
add up exactly to the total. Line counts exclude binary files (fonts, images,
PDFs), which are measured by size instead.

| Category | Files | Lines | Size | Share of lines |
|---|---:|---:|---:|---:|
| **Back end** | **212** | **47,387** | 2,067 KB | 48.6% |
| &nbsp;&nbsp;Application code | 76 | 20,822 | 897 KB | 21.3% |
| &nbsp;&nbsp;Test suite | 56 | 24,237 | 1,079 KB | 24.8% |
| &nbsp;&nbsp;Database migrations | 80 | 2,328 | 90 KB | 2.4% |
| **Front end** | **116** | **37,677** | 1,632 KB | 38.6% |
| &nbsp;&nbsp;Django templates (screens) | 106 | 33,429 | 1,486 KB | 34.3% |
| &nbsp;&nbsp;Shared JavaScript | 8 | 2,502 | 106 KB | 2.6% |
| &nbsp;&nbsp;Shared stylesheets | 2 | 1,746 | 40 KB | 1.8% |
| **Documentation** | **13** | **9,235** | 904 KB | 9.5% |
| &nbsp;&nbsp;Markdown documents | 9 | 8,479 | 475 KB | 8.7% |
| &nbsp;&nbsp;System map (2 HTML + 2 PDF) | 4 | 756 | 429 KB | 0.8% |
| **Other** | **38** | **3,279** | 1,524 KB | 3.4% |
| &nbsp;&nbsp;Vendored libraries (Bootstrap, icons, Chart.js) | 5 | 2,250 | 609 KB | 2.3% |
| &nbsp;&nbsp;Build tooling (icon + system-map generators) | 4 | 892 | 41 KB | 0.9% |
| &nbsp;&nbsp;Config (Procfile, manifest, requirements, robots) | 6 | 137 | 3 KB | 0.1% |
| &nbsp;&nbsp;Self-hosted fonts | 16 | — | 550 KB | — |
| &nbsp;&nbsp;App icons & letterhead artwork | 7 | — | 322 KB | — |
| **TOTAL** | **379** | **97,578** | **5.98 MB** | **100%** |

**Three working files sit outside version control** and are deliberately not
counted above: the environment secrets file, the local technical-debt register
(155 lines), and the error log.

---

## 02 — Language usage

Measured across 85,956 lines of code written for this project. Third-party
libraries and documentation are excluded. Crucially, the CSS and JavaScript
written *inside* template files is counted as CSS and JavaScript, not as HTML —
which is why these percentages differ from a naive file-extension count.

| Language | Lines | Share |
|---|---:|---:|
| **Python** — business logic, data model, tests | 47,978 | **55.8%** |
| **CSS** — incl. 15,086 lines written inside templates | 16,832 | **19.6%** |
| **HTML / Django templates** — markup only | 14,183 | **16.5%** |
| **JavaScript** — incl. 4,160 lines written inside templates | 6,963 | **8.1%** |
| **TOTAL OWN CODE** | **85,956** | **100%** |

Inline styling and scripting inside templates accounts for **551 KB of CSS
across 60 templates** and **188 KB of JavaScript across 36 templates**.

### Python, broken down

| Purpose | Lines | Share of Python |
|---|---:|---:|
| Test suite | 23,936 | 49.9% |
| Application code | 20,822 | 43.4% |
| Database migrations | 2,328 | 4.9% |
| Build tooling | 892 | 1.9% |
| **TOTAL PYTHON** | **47,978** | **100%** |

> **1.15 lines of test for every line of application code.** The test suite is
> larger than the software it tests. That ratio is the single number that best
> describes how this system was built.

---

## 03 — Back end, by layer

76 files of application code, excluding tests and migrations. Every row adds up
to the total; nothing is double-counted.

| Layer | Files | Lines |
|---|---:|---:|
| Views — `workshop/views/` package | 19 | 6,449 |
| Models, forms, costing & signals | 5 | 4,086 |
| Management commands | 11 | 2,372 |
| Views — flat modules (auth, analysis, cashbook, admin, cleanup) | 5 | 2,357 |
| **Rule modules** — each owns exactly one question | 7 | 2,232 |
| Views — inventory app | 2 | 1,244 |
| Routing, admin & template tags | 6 | 781 |
| Platform — notifications, push, mail, middleware, access control | 6 | 726 |
| Settings, WSGI/ASGI, entry point | 7 | 542 |
| App registry & package initialisers | 8 | 33 |
| **TOTAL** | **76** | **20,822** |

### The seven rule modules — the structural idea of this codebase

| Module | The one question it answers | Lines |
|---|---|---:|
| `analysis_engine.py` | What is the profit for this date window? | 623 |
| `invoice.py` | What does the customer see? (owns both the bill and the estimate) | 429 |
| `photos.py` | Where do the bytes go, and how is the URL signed? | 392 |
| `master_data.py` | What does renaming or merging a name actually do? | 366 |
| `settlement.py` | What is still unfilled before this bill is settled? | 292 |
| `money.py` | Is this typed rupee amount acceptable for its column? | 70 |
| `spare_dates.py` | Is this ordered / received date pair the right way round? | 60 |
| **7 MODULES** | | **2,232** |

### Ten largest single files

| File | Lines |
|---|---:|
| `workshop/models.py` | 2,129 |
| `workshop/forms.py` | 1,185 |
| `workshop/views/spare_shop.py` | 1,068 |
| `workshop/views/jobcard.py` | 1,007 |
| `inventory/views_suppliers.py` | 962 |
| `workshop/auth_views.py` | 896 |
| `workshop/views/salary_advance.py` | 793 |
| `workshop/management/commands/seed_dummy_data.py` | 751 |
| `workshop/views/bulk_payer.py` | 636 |
| `workshop/analysis_engine.py` | 623 |

---

## 04 — Front end, by section

106 templates — every screen in the system. Each carries its own page-specific
styling and scripting inline, which is why the line counts run high: a template
is a complete screen, not a fragment.

### Workshop app — 83 templates, 29,781 lines

| Section | Files | Lines |
|---|---:|---:|
| Job Card — *the central record* | 16 | 9,955 |
| Spare Shops | 5 | 2,438 |
| Estimates | 5 | 2,258 |
| Invoice — *one file, the printed bill* | 1 | 1,912 |
| Cashbook | 4 | 1,711 |
| Shared shell — *base layout & home* | 2 | 1,685 |
| Salary & Advance | 5 | 1,507 |
| Analysis & Reports | 8 | 1,357 |
| Car Profiles | 3 | 1,342 |
| Dashboard | 1 | 1,286 |
| Control Hub — *accounts, staff, security* | 4 | 1,103 |
| Completed | 2 | 875 |
| Shared includes | 6 | 741 |
| Sign-in & password recovery | 5 | 667 |
| Master Lists | 11 | 554 |
| Deletion History | 2 | 232 |
| Notifications | 3 | 158 |
| **SUBTOTAL** | **83** | **29,781** |

### Inventory app — 20 templates, 3,550 lines

| Section | Files | Lines |
|---|---:|---:|
| Supplies Shops — *restock bills, catalog, payments* | 13 | 2,296 |
| Stock — *list, low stock, history, categories* | 7 | 1,254 |
| **SUBTOTAL** | **20** | **3,550** |

Plus 3 shared error pages (403 / 404 / 500), 90 lines.

### Shared front-end assets

| File | Role | Lines |
|---|---|---:|
| `workshop/static/js/script.js` | Form rows, autocomplete, shared behaviour | 549 |
| `workshop/static/js/photos.js` | Camera capture & upload queue | 515 |
| `workshop/static/js/spare_autofill.js` | Spare status & date derivation | 284 |
| `workshop/static/js/photos-core.js` | Pure logic, DOM-free — the one unit-tested module | 276 |
| `workshop/static/js/sound.js` | Four synthesised outcome tones | 269 |
| `static/js/notifications.js` | Alert panel & push subscription | 264 |
| `workshop/static/js/estimate.js` | Estimate line editing | 234 |
| `workshop/templates/workshop/sw.js` | Service worker — push delivery, offline notice | 111 |
| `workshop/static/css/analysis.css` | Owner analysis styling | 1,388 |
| `static/css/style.css` | Global styling | 358 |
| **10 SHARED FILES** | | **4,248** |

> **No build step, no npm, no bundler.** Server-rendered Django templates with
> page-scoped inline styling and scripting. Seven shared JavaScript files exist,
> and the rule for admission is simply "used on more than one page". This is a
> settled architectural decision, documented with its reasoning, not an
> unaddressed backlog item.

---

## 05 — Test suite

1,524 tests, counted by asking Django's own test runner to build the suite — not
by counting function names, which misses tests inherited from shared base
classes (that method reports 1,516).

| Tests | Test classes | Test files | Lines | Test : app code |
|---:|---:|---:|---:|---:|
| **1,524** | **295** | **56** | **24,237** | **1.15 : 1** |

### Twelve largest test files

| File | What it guards | Lines |
|---|---|---:|
| `test_master_salary_hub_integrity.py` | Salary months, settlement locking, advances | 1,994 |
| `test_jobcard_form_ux.py` | The job card form — the busiest screen | 1,874 |
| `test_invoice.py` | The printed bill handed to a customer | 1,467 |
| `test_photos.py` | Evidence photos, signing, retention | 1,167 |
| `test_estimate.py` | Quotations | 1,112 |
| `tests_suppliers.py` | Supplies shops & restock bills | 903 |
| `test_fleet_cashbook_integrity.py` | Fleet account balances & the cashbook | 738 |
| `test_password_reset.py` | Emailed recovery codes & throttles | 700 |
| `tests.py` *(inventory)* | Stock movement | 647 |
| `test_notifications.py` | The alert catalogue | 602 |
| `test_ui_regressions.py` | Layout rules that previously broke | 571 |
| `test_settlement_preflight.py` | What is unfilled before a bill is settled | 548 |

**There is also a second, separate test runner for JavaScript** — Node's
built-in one, added with no npm, no package file and no dependencies. It covers
one module, `photos-core.js`, because that module was deliberately written to be
coverable: pure functions, no browser, no network.

> **Two documented counts were found stale by this measurement:** the test total
> (documented 1,519, actual **1,524**) and the inventory signal handlers
> (documented 8, actual **10** — the `SupplierRestockBill` pre/post-save pair was
> added and never counted). Both have since been corrected in the documentation.

---

## 06 — System structure

The moving parts, counted by loading the application and asking it directly
rather than by reading source.

| What | Count | Detail |
|---|---:|---|
| Data models | 38 | 30 in the workshop app, 8 in inventory |
| Model fields | 268 | 220 workshop, 48 inventory |
| URL routes | 288 | Every reachable address in the system |
| View functions | 231 | 162 public screens, 69 private helpers |
| Database migrations | 78 | 70 workshop, 8 inventory |
| Form classes | 21 | Excludes formsets |
| Access-control decorators applied | 174 | 25 Owner-only, 116 Office, 33 all staff |
| Database signal handlers | 12 | 10 for stock & costing, 2 for sessions & photos |
| Atomic transaction blocks | 52 | Every money movement is all-or-nothing |
| Row locks (`select_for_update`) | 10 | Guards cascade payments against races |
| Notification events | 14 | 10 critical (push to phone), 4 informational |
| Notification call sites | 18 | Across 8 modules, one catalogue |
| Permanently-deletable record types | 11 | Each writes a snapshot before deletion |
| Management commands | 11 | Backup, seeding, purge, owner identity, photo sweep |
| Template tags & filters | 13 | Custom, shared across screens |
| Runtime dependencies | 8 | Django, Pillow, psycopg2, WhiteNoise, gunicorn, decouple, pywebpush, coverage |
| Commits | 181 | 11 January – 22 August 2026 |

---

## 07 — Documentation

76,864 words across ten documents, plus a generated one-page system map in
light and dark themes, committed as both HTML and print-exact PDF. Each document
owns a defined set of facts; none restates another's.

| Document | Owns | Lines | Words |
|---|---|---:|---:|
| `CLAUDE.md` | Day-to-day working rules and every deliberate decision | 3,813 | 36,434 |
| `OPERATIONAL_BLUEPRINT.md` | Workflow narrative — who does what, screen by screen | 1,102 | 8,104 |
| `MASTER_BLUEPRINT.md` | The numbers — models, routes, templates, settings | 983 | 11,117 |
| `RAILWAY_OPERATIONS.md` | Ongoing platform reference — deploys, backups, cost | 643 | 4,153 |
| `TITAN_SPEC_SHEET.md` | This document — every figure, counted from the repository | 521 | 4,454 |
| `GO_LIVE_RUNBOOK.md` | The one-time go-live procedure and rollback | 511 | 3,396 |
| `TITAN_MASTER_HANDOVER.md` | Mission, roadmap, and what is deliberately out of scope | 406 | 3,823 |
| `master_data_export.md` | The workshop's own brand / model / spare list | 315 | 1,065 |
| `README.md` | Outward-facing summary — features, stack, install | 185 | 975 |
| `TECH_DEBT.md` *(local only)* | Known issues not yet scheduled | 155 | 3,343 |
| **10 DOCUMENTS** | | **8,634** | **76,864** |

**`CLAUDE.md` is the unusual one.** 43 major sections carrying **400 stated
rules**, **51 flagged hazards** and **97 pointers to the test that guards each
rule**. It records not just what the system does but which apparent bugs are
deliberate business decisions — so the next person to touch the code cannot
"fix" the business by accident.

**The system map is drawn, not written.** One build script emits both the light
and dark versions from a single set of coordinates, so the two can never
disagree. A checker then verifies five things the eye cannot catch at that
density: connectors cutting through unrelated cards, connectors missing their
target, anything off-canvas, overlaps, and long same-colour lines running too
close.

---

## 08 — Specifications & what makes it unusual

Not a feature list — those are in the README. These are the engineering
decisions that distinguish this system from a generic business application, each
one traceable to a real failure it prevents.

### Architecture

**One rule, one implementation.** Seven modules contain no screens at all. Each
owns exactly one question — the profit maths, the printed document, the
settlement checklist, the rename rule, rupee validation, date-pair validation,
photo signing. Nothing else in 97,578 lines is permitted to answer those
questions a second time. The reason is specific: the cost of a spare part was
once calculated in five different places, giving a shop's own page and the profit
page two different answers for the same debt.
*7 modules · 2,232 lines*

### Money and stock integrity

**A part is paid for exactly once.** A spare reaches a car by one of two routes —
bought from a spare shop for that job, or drawn from warehouse stock already paid
for by a supplier bill. Charging both would overstate expenses by roughly ₹9.8M
against the test data. The route is **stored**, never inferred. It used to be
guessed from a name match, and the guess was made differently in two places, so
the shelf count drifted downward until a restock bill covered it up.
*Guarded by a dedicated test class.*

**Stock is allowed to go negative.** A job card records a part the mechanic has
already physically taken. Refusing that record does not put the part back on the
shelf — it only stops a mechanic mid-shift and makes the system disagree with
reality. The old clamp at zero never prevented an overdraw; it destroyed the
evidence of one, and silently invented three units of stock when the missing bill
arrived. A negative balance is self-healing and is the signal that a supplier
bill has not been keyed.
*Reported separately from "low stock" — the two counts are disjoint.*

**Cost is a full replay, never an increment.** Warehouse cost is a weighted
average, recomputed date-ordered from every receipt each time one changes. There
is deliberately no fast incremental path, because a moving average is
path-dependent and cannot be un-averaged — a fast version and a correcting
version would be two answers to one number. It matches how the workshop actually
operates: a supplier delivers, keeps their own book, and the bill is keyed weeks
later when the collector comes.
*Per-batch cost is still retained, so true FIFO remains reconstructable.*

**Every typed rupee passes one gate.** A figure too large for its column is
silently accepted by SQLite and rejected by PostgreSQL with a server error.
`Infinity` passes a naive "greater than zero" check and poisons every total that
touches the column. `NaN` makes that same check raise, crashing the page. One
corrupts, one crashes. A single module refuses all three before either can
happen, and reads the acceptable bound from the database column itself rather
than restating it.
*Wired into all four payment screens.*

**A settled month cannot be walked backwards.** Salary months have three states:
open, locked, and closed. Closure is a **stored one-way flag**, not a computed
"is this the latest?". The computed version looked tidier and was a ratchet that
turned both ways — deleting the newest settlement handed the frontier back to the
month before it, so an entire history could be unwound one delete at a time. It
was observed doing exactly that: thirteen settled months down to ten.
*Enforced in the view, not only the template.*

### Security

**Two lockouts, in different units.** Five failed attempts lock a single account
for fifteen minutes. Twenty failures from one network address is the backstop,
and forwarded-address headers are ignored so the count cannot be spoofed. The
network threshold was deliberately *raised* from five, because the unit was wrong
for this business: the laptop, the tablet and both owners' phones leave through
one connection, so five fumbled attempts on the shop-floor tablet locked the
owners out of their own devices.
*Per-account lockout is what stops guessing; the network gate catches a spray.*

**Owners are nameable only by email.** Sign-in accepts a username, an email or a
mobile number — except for owner accounts. The workshop's published phone number
was a valid owner identifier, and five wrong guesses locks an account, so anyone
who could name an owner could lock that owner out on demand. The refusal is
enforced at the authentication call itself, not merely hidden at the form —
otherwise a refused identifier would still have been handed straight to the login
backend.
*Password recovery is deliberately left generous — different threat, different rule.*

**A six-digit code, not a reset link.** Django's built-in emailed reset link is
less code and better tested, and it was the original plan. It was rejected for
one reason: on iOS an installed home-screen app has its own cookie jar, so a link
tapped in the mail app completes the reset in Safari and returns the owner to the
app still signed out. The code is placed in the email *subject* line, so it is
readable from the phone's notification banner without opening the mail app. The
owners read these on iPhones.
*Throttled, single-use, expiring — and it clears the account lockout.*

**Signed-in pages are never stored.** Logging out flushes the session, so the next
request is bounced — but the browser Back button never makes a request. It
restores the page from cache, fully rendered: the dashboard, a customer's bill,
the profit page, on a laptop now in someone else's hands. Nothing server-side can
undo that after the page has been sent, so authenticated responses are marked
never-store.
*Accepted cost: Back re-fetches instead of restoring instantly. Owner accounts
also cannot enter the Django admin at all, deliberately.*

### Records and evidence

**Two verbs for removal, never one.** Accounts that other records point at —
shops, fleet accounts, staff — are archived, never deleted, because deleting one
would cascade away its entire financial ledger. Transactions are permanently
deleted, but every deletion first writes a snapshot to an owner-only, read-only
history. There is deliberately no restore: reviving stale financial data corrupts
running balances.
*11 record types · one shared entry point · every delete notifies both owners.*

**Photographs never touch the server.** The browser uploads straight to object
storage on a signed URL, so an upload on poor workshop wifi never occupies a web
worker. The signing is written against the standard library and pinned to
Amazon's own published test vector — the only way to verify it without a live
bucket. Signing and recording are separate steps, in that order. The obvious
design records first, which leaves a row pointing at a photo that does not exist.
This way **a row always means a real photograph**, and the cost is an
unreferenced file that a sweep collects.
*Photos are frozen when the bill is settled — money and evidence stop moving together.*

### Customer-facing documents

**The printed bill loads nothing.** No external stylesheet, no external script,
no icon font. Everything inline, including the letterhead as embedded artwork at
600 DPI. A framework update shipping upstream could otherwise move a column on a
customer's bill, and a workshop printing on a dropped connection would get an
unstyled page. The bill and the estimate are produced by **one module**, so where
they agree they agree exactly — and they diverge in precisely two columns, for a
stated reason: a bill records work that happened, an estimate describes work that
has not.
*Screen controls live outside the printed sheet entirely, not merely hidden.*

### Interface

**Three devices, three roles, one interface.** Office works on a laptop, the
floor on a tablet, the owners on phones. Every hover effect is gated to devices
that actually have a pointer; touch targets are sized by input method rather than
screen width, because the shop-floor tablet is wider than many laptops. The
navigation bar moves to the bottom of the screen on phones — the same element,
repositioned, because the top edge is the hardest place on a phone for a thumb.
*One navigation menu in the whole app, deliberately.*

**Four tones, wired to nothing.** Success, error, warning and question each have
a synthesised tone, carried by an attribute on the message banner. Because the app
already tags every outcome, one attribute covers every action in the system — and
anything added later. Per-button sounds were rejected: roughly 180 places to
attach the wrong tone, each firing at click time, announcing "done" before the
server had done anything.
*Informational messages are silent — a tone for everything trains people to hear nothing.*

### Alerts

**One catalogue, one entry point.** Fourteen events, defined in a single file,
raised from eighteen places. Severity is a delivery tier rather than decoration:
ten push to the owners' phones, four land only in the in-app feed. A
notification's address is permanent, so the rule is that a bad one is fixed by
making that address work — never by repointing the next alert, which does nothing
for every alert already sent.
*Push runs off the request path entirely — a dead push service cannot slow a payment.*

### Discipline

**Traps are written down.** 46 hazards are documented, each one a failure that
produced no exception, no console error and a green test suite — a running CSS
transition outranking an important rule; a cached deletion list in a form set; a
date built in UTC reporting yesterday for an entire Indian morning. They are
recorded so the next person does not have to rediscover them, and 94 rules carry
a pointer to the exact test that guards them.

**Built for its actual load.** Roughly fifty cars a month, seven staff, two
owners. That is why access control needs only three tiers, why the cashbook is
weighted for a ledger that is 98% expenses, and why performance is judged against
realistic load rather than generic assumptions. Scope deliberately left out — tax
handling, customer-facing messaging, attendance, multi-mechanic assignment — is
written down as excluded, so proposing one is understood as proposing scope, not
reporting a defect.
*Same database engine in development and production, so differences surface while cheap.*

---

## 09 — Headline figures

| | |
|---:|---|
| **97,578** | lines of code across 379 files, in a single deployable system |
| **1,524** | automated tests — 1.15 lines of test for every line of application code |
| **38** | data models holding 268 fields, reachable through 288 addresses |
| **106** | screens, each built to work on a laptop, a tablet and a phone |
| **7** | rule modules, each the only place in the system that answers its question |
| **174** | access checks across three roles — owner, office, floor |
| **52** | all-or-nothing transaction blocks guarding every movement of money |
| **76,864** | words of documentation across ten documents and a drawn system map |
| **8** | runtime dependencies — no build step, no package manager, no bundler |
| **181** | commits over seven months, by one developer |

> **The one-line version:** a 97,000-line Django system running a premium
> automotive workshop end to end — job cards, inventory, supplier and spare-shop
> ledgers, fleet billing, payroll, estimates, evidence photography and owner
> analytics — with a test suite larger than the application it tests, and every
> deliberate business decision written down with the failure it prevents.

---

## Method

Counted from the working tree on 22 August 2026, on branch `main`, including
this document.

- File counts cover files tracked in version control; three untracked working
  files are named in section 01 and excluded.
- Line counts exclude binary files, which are reported by size.
- Vendored third-party libraries are counted as files but excluded from language
  percentages.
- CSS and JavaScript written inside template files is attributed to those
  languages, not to HTML.
- The test count comes from Django's own suite builder; model, route and event
  counts come from loading the application and querying it.
- Two documented counts were found stale by the first run of this measurement —
  the test total and the inventory signal-handler count. Both have since been
  corrected at source.
