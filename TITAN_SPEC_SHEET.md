# Titan Spec Sheet

**WorkshopOS "Titan"** — every file, line, model and rule, counted from the
repository rather than estimated.

| | |
|---|---|
| **Measured** | 31 August 2026, branch `main`, including this document |
| **Stack** | Django 5.2 monolith, PostgreSQL in development and production |
| **Apps** | 2 — `workshop` (core business logic), `inventory` (stock + supplier shops) |
| **History** | 224 commits, 11 January – 31 August 2026 |

| Files | Lines | Tests | Models | Routes | Screens | Dependencies |
|---:|---:|---:|---:|---:|---:|---:|
| **406** | **118,678** | **1,921** | **39** | **293** | **111** | **8** |

> Every figure below was re-measured from the working tree. Nothing is carried
> over from documentation.

---

## 01 — Totals: files and lines

407 files are tracked in version control. One of them is a 6.6 MB SQLite
database committed so the throwaway demo deploy has data to serve; it is not
code and is excluded from every count below, leaving **406**. The four
categories do not overlap and add up exactly to that total. Line counts exclude
binary files (fonts, images, PDFs), which are measured by size instead.

| Category | Files | Lines | Size | Share of lines |
|---|---:|---:|---:|---:|
| **Back end** | **228** | **58,135** | 2,608 KB | 49.0% |
| &nbsp;&nbsp;Application code | 83 | 25,073 | 1,120 KB | 21.1% |
| &nbsp;&nbsp;Test suite | 60 | 30,534 | 1,388 KB | 25.7% |
| &nbsp;&nbsp;Database migrations | 85 | 2,528 | 98 KB | 2.1% |
| **Front end** | **119** | **43,408** | 1,972 KB | 36.6% |
| &nbsp;&nbsp;Django templates (screens) | 108 | 38,247 | 1,784 KB | 32.2% |
| &nbsp;&nbsp;Shared JavaScript | 8 | 2,625 | 114 KB | 2.2% |
| &nbsp;&nbsp;Shared stylesheets | 2 | 2,235 | 61 KB | 1.9% |
| &nbsp;&nbsp;JavaScript test suite | 1 | 301 | 12 KB | 0.3% |
| **Documentation** | **14** | **12,851** | 1,498 KB | 10.8% |
| &nbsp;&nbsp;Markdown documents | 10 | 11,411 | 660 KB | 9.6% |
| &nbsp;&nbsp;System map (2 HTML + 2 PDF) | 4 | 1,440 | 838 KB | 1.2% |
| **Other** | **45** | **4,284** | 1,572 KB | 3.6% |
| &nbsp;&nbsp;Vendored libraries (Bootstrap, icons, Chart.js) | 5 | 2,250 | 608 KB | 1.9% |
| &nbsp;&nbsp;Build tooling (icon, system-map, vendoring, seed scripts) | 6 | 1,770 | 82 KB | 1.5% |
| &nbsp;&nbsp;Config (Procfile, render blueprint, manifest, requirements, robots, error pages) | 11 | 264 | 9 KB | 0.2% |
| &nbsp;&nbsp;Self-hosted fonts | 16 | — | 550 KB | — |
| &nbsp;&nbsp;App icons & letterhead artwork | 7 | — | 321 KB | — |
| **TOTAL** | **406** | **118,678** | **7.47 MB** | **100%** |

The three shared error pages (403 / 404 / 500, 90 lines) sit in the config row
rather than with the screens: they extend no layout and load nothing at all, so
that an error page cannot break for the reason it is being shown.

**Three working files sit outside version control** and are deliberately not
counted above: the environment secrets file, the local technical-debt register
(177 lines, 3,733 words), and the error log.

---

## 02 — Language usage

Measured across 103,313 lines of code written for this project. Third-party
libraries and documentation are excluded. Crucially, the CSS and JavaScript
written *inside* template files is counted as CSS and JavaScript, not as HTML —
which is why these percentages differ from a naive file-extension count.

| Language | Lines | Share |
|---|---:|---:|
| **Python** — business logic, data model, tests | 59,905 | **58.0%** |
| **CSS** — incl. 16,144 lines written inside templates | 18,379 | **17.8%** |
| **HTML / Django templates** — markup only | 17,390 | **16.8%** |
| **JavaScript** — incl. 4,713 lines written inside templates | 7,639 | **7.4%** |
| **TOTAL OWN CODE** | **103,313** | **100%** |

Inline styling and scripting inside templates accounts for **606 KB of CSS
across 60 templates** and **213 KB of JavaScript across 39 templates**.

### Python, broken down

| Purpose | Lines | Share of Python |
|---|---:|---:|
| Test suite | 30,534 | 51.0% |
| Application code | 25,073 | 41.9% |
| Database migrations | 2,528 | 4.2% |
| Build tooling | 1,770 | 3.0% |
| **TOTAL PYTHON** | **59,905** | **100%** |

> **1.22 lines of test for every line of application code.** The test suite is
> larger than the software it tests. That ratio is the single number that best
> describes how this system was built.

---

## 03 — Back end, by layer

83 files of application code, excluding tests and migrations. Every row adds up
to the total; nothing is double-counted.

| Layer | Files | Lines |
|---|---:|---:|
| Views — `workshop/views/` package | 21 | 7,417 |
| Models, forms, costing & signals | 5 | 4,358 |
| Management commands | 14 | 3,359 |
| **Rule modules** — each owns exactly one question | 9 | 3,300 |
| Views — flat modules (auth, analysis, cashbook, admin, cleanup) | 5 | 2,882 |
| Views — inventory app | 2 | 1,393 |
| Platform — notifications, push, mail, middleware, access control | 6 | 856 |
| Routing, admin & template tags | 5 | 849 |
| Settings, WSGI/ASGI, entry point | 10 | 627 |
| App registry & package initialisers | 6 | 32 |
| **TOTAL** | **83** | **25,073** |

### The nine rule modules — the structural idea of this codebase

| Module | The one question it answers | Lines |
|---|---|---:|
| `analysis_engine.py` | What is the profit, and the cash movement, for this date window? | 1,516 |
| `invoice.py` | What does the customer see? (owns both the bill and the estimate) | 429 |
| `photos.py` | Where do the bytes go, and how is the URL signed? | 392 |
| `master_data.py` | What does renaming or merging a name actually do? | 366 |
| `settlement.py` | What is still unfilled before this bill is settled? | 292 |
| `delete_window.py` | Has this record been in the books too long for Office to delete? | 113 |
| `money.py` | Is this typed rupee amount acceptable for its column? | 70 |
| `money_dates.py` | Which day did this money move? | 62 |
| `spare_dates.py` | Is this ordered / received date pair the right way round? | 60 |
| **9 MODULES** | | **3,300** |

### Ten largest single files

| File | Lines |
|---|---:|
| `workshop/models.py` | 2,334 |
| `workshop/analysis_engine.py` | 1,516 |
| `workshop/forms.py` | 1,224 |
| `workshop/views/jobcard.py` | 1,156 |
| `workshop/views/spare_shop.py` | 1,119 |
| `inventory/views_suppliers.py` | 1,111 |
| `workshop/views/salary_advance.py` | 1,001 |
| `workshop/auth_views.py` | 958 |
| `workshop/analysis_views.py` | 917 |
| `workshop/management/commands/seed_dummy_data.py` | 756 |

---

## 04 — Front end, by section

108 templates under the two apps — every screen in the system, plus the partials
they include. Each carries its own page-specific styling and scripting inline,
which is why the line counts run high: a template is a complete screen, not a
fragment.

### Workshop app — 88 templates, 34,456 lines

| Section | Files | Lines |
|---|---:|---:|
| Job Card — *the central record* | 16 | 10,555 |
| Shared shell — *base layout, home, About* | 3 | 2,655 |
| Spare Shops | 5 | 2,603 |
| Analysis & Reports | 10 | 2,410 |
| Estimates | 5 | 2,258 |
| Invoice — *one file, the printed bill* | 1 | 1,912 |
| Salary & Advance | 5 | 1,777 |
| Cashbook | 4 | 1,719 |
| Shared includes | 7 | 1,457 |
| Car Profiles | 3 | 1,342 |
| Dashboard | 1 | 1,286 |
| Control Hub — *accounts, staff, security* | 4 | 1,103 |
| Completed | 2 | 877 |
| Owner Withdrawals | 1 | 863 |
| Sign-in & password recovery | 5 | 667 |
| Master Lists | 11 | 555 |
| Deletion History | 2 | 232 |
| Notifications | 3 | 185 |
| **SUBTOTAL** | **88** | **34,456** |

### Inventory app — 20 templates, 3,791 lines

| Section | Files | Lines |
|---|---:|---:|
| Supplies Shops — *restock bills, catalog, payments* | 13 | 2,537 |
| Stock — *list, low stock, history, categories* | 7 | 1,254 |
| **SUBTOTAL** | **20** | **3,791** |

### Shared front-end assets

| File | Role | Lines |
|---|---|---:|
| `workshop/static/css/analysis.css` | Owner analysis styling | 1,388 |
| `static/css/style.css` | Global styling, incl. the shared payment card | 847 |
| `workshop/static/js/script.js` | Form rows, autocomplete, shared behaviour | 549 |
| `workshop/static/js/photos.js` | Camera capture & upload queue | 538 |
| `static/js/notifications.js` | Alert panel & push subscription | 331 |
| `workshop/static/js/sound.js` | Five synthesised outcome tones | 288 |
| `workshop/static/js/spare_autofill.js` | Spare status & date derivation | 284 |
| `workshop/static/js/photos-core.js` | Pure logic, DOM-free — the one unit-tested module | 276 |
| `workshop/static/js/estimate.js` | Estimate line editing | 234 |
| `workshop/templates/workshop/sw.js` | Service worker — push delivery, offline notice | 125 |
| **10 SHARED FILES** | | **4,860** |

> **No build step, no npm, no bundler.** Server-rendered Django templates with
> page-scoped inline styling and scripting. Nine shared JavaScript and CSS files
> exist, and the rule for admission is simply "used on more than one page". This
> is a settled architectural decision, documented with its reasoning, not an
> unaddressed backlog item.

---

## 05 — Test suite

1,921 tests, counted by asking Django's own test runner to build the suite — not
by counting function names, which misses tests inherited from shared base
classes.

| Tests | Test classes | Test files | Lines | Test : app code |
|---:|---:|---:|---:|---:|
| **1,921** | **367** | **59** | **30,534** | **1.22 : 1** |

A full run takes 20 to 80 minutes and always executes against SQLite, in memory.
That is not a convenience: the runner creates and drops an entire database, and
there is deliberately no flag that would let it be pointed at real data by
accident.

### Twelve largest test files

| File | What it guards | Lines |
|---|---|---:|
| `test_analysis.py` | The profit engine, cash tracking and every insight | 2,893 |
| `test_master_salary_hub_integrity.py` | Salary months, settlement locking, advances | 2,439 |
| `test_jobcard_form_ux.py` | The job card form — the busiest screen | 1,874 |
| `test_invoice.py` | The printed bill handed to a customer | 1,467 |
| `test_photos.py` | Evidence photos, signing, retention | 1,258 |
| `tests_suppliers.py` *(inventory)* | Supplies shops & restock bills | 1,154 |
| `test_estimate.py` | Quotations | 1,112 |
| `test_fleet_cashbook_integrity.py` | Fleet account balances & the cashbook | 1,106 |
| `test_notifications.py` | The alert catalogue | 1,044 |
| `test_jobcard_detail_view.py` | The read-only job card | 1,005 |
| `test_spare_shop_flow.py` | Spare shop ledgers and unassigned purchases | 709 |
| `test_password_reset.py` | Emailed recovery codes & throttles | 700 |

**There is also a second, separate test runner for JavaScript** — Node's
built-in one, added with no npm, no package file and no dependencies. It covers
one module, `photos-core.js`, because that module was deliberately written to be
coverable: pure functions, no browser, no network.

---

## 06 — System structure

The moving parts, counted by loading the application and asking it directly
rather than by reading source.

| What | Count | Detail |
|---|---:|---|
| Data models | 39 | 31 in the workshop app, 8 in inventory |
| Model fields | 280 | 232 workshop, 48 inventory |
| URL routes | 293 | Every reachable address in the system |
| View-module functions | 250 | 169 public screens, 81 private helpers |
| Database migrations | 83 | 75 workshop, 8 inventory |
| Form classes | 11 | Excludes formsets |
| Access-control decorators applied | 165 | 30 Owner-only, 107 Office, 28 all staff |
| Database signal handlers | 12 | 10 for stock & costing, 2 for sessions & photos |
| Atomic transaction blocks | 62 | Every money movement is all-or-nothing |
| Row locks (`select_for_update`) | 10 | Guards cascade payments against races |
| Notification events | 14 | 11 critical (push to phone), 3 informational |
| Notification call sites | 18 | Across 8 modules, one catalogue |
| Permanently-deletable record types | 12 | Each writes a snapshot before deletion |
| Management commands | 13 | Backup, seeding, purge, owner identity, photo sweep |
| Template tags & filters | 15 | Custom, shared across screens |
| Runtime dependencies | 8 | Django, Pillow, psycopg2, WhiteNoise, gunicorn, decouple, pywebpush, coverage |
| Commits | 224 | 11 January – 31 August 2026 |

---

## 07 — Documentation

104,949 words across ten documents, plus a generated one-page system map in
light and dark themes, committed as both HTML and print-exact PDF. Each document
owns a defined set of facts; none restates another's.

| Document | Owns | Lines | Words |
|---|---|---:|---:|
| `CLAUDE.md` | Day-to-day working rules and every deliberate decision | 6,473 | 64,568 |
| `OPERATIONAL_BLUEPRINT.md` | Workflow narrative — who does what, screen by screen | 1,180 | 9,130 |
| `MASTER_BLUEPRINT.md` | The numbers — models, routes, templates, settings | 993 | 11,780 |
| `RAILWAY_OPERATIONS.md` | Ongoing platform reference — deploys, backups, cost | 644 | 4,167 |
| `TITAN_SPEC_SHEET.md` | This document — every figure, counted from the repository | 575 | 5,140 |
| `GO_LIVE_RUNBOOK.md` | The one-time go-live procedure and rollback | 512 | 3,416 |
| `TITAN_MASTER_HANDOVER.md` | Mission, roadmap, and what is deliberately out of scope | 421 | 4,151 |
| `master_data_export.md` | The workshop's own brand / model / spare list | 315 | 1,065 |
| `README.md` | Outward-facing summary — features, stack, install | 203 | 1,237 |
| `WorkshopOS_Complete_Structure.md` | The section tree, as the owner describes it | 95 | 295 |
| **10 DOCUMENTS** | | **11,411** | **104,949** |

`TECH_DEBT.md` (177 lines, 3,733 words) is deliberately untracked: it lists what
is known to be wrong and not yet scheduled, which is working state rather than a
published fact.

**`CLAUDE.md` is the unusual one.** 45 major sections carrying **568 stated
rules**, **148 flagged hazards** and **156 pointers to the test that guards each
rule**. It records not just what the system does but which apparent bugs are
deliberate business decisions — so the next person to touch the code cannot
"fix" the business by accident.

**The system map is drawn, not written.** One build script emits both the light
and dark versions from a single set of coordinates, and a third copy as a Django
partial that the in-app About page includes, so no two can disagree. A checker
then verifies six things the eye cannot catch at that density: connectors
cutting through unrelated cards, connectors missing their target, anything
off-canvas, overlaps, long same-colour lines running too close, and every tap
landing on the bus it feeds.

---

## 08 — Specifications & what makes it unusual

Not a feature list — those are in the README. These are the engineering
decisions that distinguish this system from a generic business application, each
one traceable to a real failure it prevents.

### Architecture

**One rule, one implementation.** Nine modules contain no screens at all. Each
owns exactly one question — the profit maths, the printed document, the
settlement checklist, the rename rule, rupee validation, which day money moved,
date-pair validation, the deletion window, photo signing. Nothing else in
118,678 lines is permitted to answer those questions a second time. The reason
is specific: the cost of a spare part was once calculated in five different
places, giving a shop's own page and the profit page two different answers for
the same debt.
*9 modules · 3,300 lines*

### Money and stock integrity

**A part is paid for exactly once.** A spare reaches a car by one of two routes —
bought from a spare shop for that job, or drawn from warehouse stock already paid
for by a supplier bill. Charging both would overstate expenses by roughly ₹9.8M
against the test data. The route is **stored**, never inferred. It used to be
guessed from a name match, and the guess was made differently in two places, so
the shelf count drifted downward until a restock bill covered it up.
*Guarded by a dedicated test class.*

**Profit and cash are never added together.** They differ by five things at once
— stock bought but unused, stock used but bought earlier, bills unpaid, bills
paid from an earlier period, customer bills unpaid — so subtracting one from the
other produces a number that is not anything. Both appear on the same page and
are drawn as different kinds of object so they cannot be confused. Money taken
out by the owners is the sharpest case: it is real cash leaving the drawer and
**not an expense**, because profit is what is available to take and taking it
cannot make it smaller. Recorded as an expense, the error compounds — the page
reports less left to distribute, over money already distributed, and the next
distribution is decided from the smaller figure.
*It reaches exactly one figure in the whole engine: cash out.*

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
than restating it. The gate is deliberately not allowed to round a figure up
into validity — that would be the system saving a number nobody typed — so each
screen makes the final call itself, in one line.
*Wired into all six screens where money is typed.*

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
*12 record types · one shared entry point · every delete notifies both owners.*

**A correction and an anomaly are different acts.** Office can delete a money
record entered in the last seven days; anything older is an owner's to remove.
The window is measured from when the row was *entered*, never from the date the
money carries — because back-dating is normal here, and on the money date Office
would be refused permission to delete their own typo thirty seconds after making
it. The control is still offered rather than hidden, and the refusal names the
rule, the age of the row and who to ask.
*An escalation, not a wall — no approval queue, no second sign-off.*

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

**A control drawn on four screens is one control.** The card that records a
payment appears on the spare shop, the Supplies Shop, the fleet account and the
owner withdrawals page. It was four near-copies kept in step by hand, and they
had already drifted three different ways — one of them 397 px of content in a
343 px box. It is now one declaration in the shared stylesheet; a variant sets
two colour values and nothing else.
*Red moves money out, green takes money in — the same rule the profit page uses.*

**Five tones, wired to nothing.** Success, error, warning, question and shutter
each have a synthesised tone, and the first four are carried by an attribute on
the message banner. Because the app already tags every outcome, one attribute
covers every action in the system — and anything added later. Per-button sounds
were rejected: roughly 180 places to attach the wrong tone, each firing at click
time, announcing "done" before the server had done anything.
*Informational messages are silent — a tone for everything trains people to hear nothing.*

### Alerts

**One catalogue, one entry point.** Fourteen events, defined in a single file,
raised from eighteen places. Severity is a delivery tier rather than decoration:
eleven push to the owners' phones, three land only in the in-app feed. A
notification's address is permanent, so the rule is that a bad one is fixed by
making that address work — never by repointing the next alert, which does nothing
for every alert already sent.
*Push runs off the request path entirely — a dead push service cannot slow a payment.*

### Discipline

**Traps are written down.** 148 hazards are documented, each one a failure that
produced no exception, no console error and a green test suite — a running CSS
transition outranking an important rule; a cached deletion list in a form set; a
date built in UTC reporting yesterday for an entire Indian morning. They are
recorded so the next person does not have to rediscover them, and 156 rules carry
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
| **118,678** | lines of code across 406 files, in a single deployable system |
| **1,921** | automated tests — 1.22 lines of test for every line of application code |
| **39** | data models holding 280 fields, reachable through 293 addresses |
| **111** | templates, each built to work on a laptop, a tablet and a phone |
| **9** | rule modules, each the only place in the system that answers its question |
| **165** | access checks across three roles — owner, office, floor |
| **62** | all-or-nothing transaction blocks guarding every movement of money |
| **104,949** | words of documentation across ten documents and a drawn system map |
| **8** | runtime dependencies — no build step, no package manager, no bundler |
| **224** | commits over eight months, by one developer |

> **The one-line version:** a 118,000-line Django system running a premium
> automotive workshop end to end — job cards, inventory, supplier and spare-shop
> ledgers, fleet billing, payroll, estimates, evidence photography, owner
> withdrawals and owner analytics — with a test suite larger than the application
> it tests, and every deliberate business decision written down with the failure
> it prevents.

---

## Method

Counted from the working tree on 31 August 2026, on branch `main`, including
this document.

- File counts cover files tracked in version control. The committed demo
  database (`db.sqlite3`, 6.6 MB) is tracked but excluded as data rather than
  code; three untracked working files are named in section 01 and also excluded.
- Line counts exclude binary files, which are reported by size.
- Vendored third-party libraries are counted as files but excluded from language
  percentages.
- CSS and JavaScript written inside template files is attributed to those
  languages, not to HTML.
- The test count comes from Django's own suite builder; model, route, field,
  form, event and decorator counts come from loading the application and
  querying it. Decorator counts exclude test files.
- Nine documented counts were found stale by this run and have been corrected at
  source: the test total (recorded 1,916 and 1,685 in two documents, actual
  1,921) and test file count (54, actual 59); the views package (19 modules,
  actual 20); workshop models (30, actual 31); URL routes (156, actual 162);
  template files (106, actual 111); admin registrations (18, actual 20). Two
  statements were also wrong rather than merely stale: the Cashbook was
  described as displaying a net balance, which was removed, and Paid Bills as
  showing owners a grand total, which no role sees any more.
