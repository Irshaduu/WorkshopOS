# Titan Spec Sheet

**WorkshopOS "Titan"** — every file, line, model and rule, counted from the
repository rather than estimated.

| | |
|---|---|
| **Measured** | 20 September 2026, branch `main`, including this document |
| **Stack** | Django 5.2 monolith, PostgreSQL in development and production |
| **Apps** | 2 — `workshop` (core business logic), `inventory` (stock + supplier shops) |
| **History** | 259 commits, 11 January – 20 September 2026 |

| Files | Lines | Tests | Models | Routes | Screens | Dependencies |
|---:|---:|---:|---:|---:|---:|---:|
| **464** | **152,392** | **2,692** | **46** | **312** | **125** | **9** |

> Every figure below was re-measured from the working tree. Nothing is carried
> over from documentation.

---

## 01 — Totals: files and lines

**464** files are tracked in version control, and every one of them is counted
below — the 6.6 MB SQLite database that used to sit outside the totals was
deleted with the throwaway demo deploy it existed to feed. The four categories
do not overlap and add up exactly to the total. Line counts exclude binary files
(fonts, images, PDFs), which are measured by size instead.

| Category | Files | Lines | Size | Share of lines |
|---|---:|---:|---:|---:|
| **Back end** | **268** | **75,636** | 3,440 KB | 49.7% |
| &nbsp;&nbsp;Application code | 97 | 31,730 | 1,439 KB | 20.8% |
| &nbsp;&nbsp;Test suite | 78 | 41,082 | 1,888 KB | 27.0% |
| &nbsp;&nbsp;Database migrations | 93 | 2,824 | 113 KB | 1.9% |
| **Front end** | **138** | **54,260** | 2,514 KB | 35.6% |
| &nbsp;&nbsp;Django templates (screens) | 122 | 47,084 | 2,239 KB | 30.9% |
| &nbsp;&nbsp;Shared JavaScript | 10 | 3,525 | 155 KB | 2.3% |
| &nbsp;&nbsp;Shared stylesheets | 2 | 2,658 | 82 KB | 1.7% |
| &nbsp;&nbsp;JavaScript test suite | 4 | 993 | 37 KB | 0.7% |
| **Documentation** | **14** | **18,351** | 1,914 KB | 12.0% |
| &nbsp;&nbsp;Markdown documents | 10 | 16,846 | 1,051 KB | 11.0% |
| &nbsp;&nbsp;System map (2 HTML + 2 PDF) | 4 | 1,505 | 863 KB | 1.0% |
| **Other** | **44** | **4,143** | 1,569 KB | 2.7% |
| &nbsp;&nbsp;Vendored libraries (Bootstrap, icons, Chart.js) | 5 | 2,250 | 609 KB | 1.5% |
| &nbsp;&nbsp;Build tooling (icon, system-map, vendoring scripts) | 5 | 1,535 | 75 KB | 1.0% |
| &nbsp;&nbsp;Config (Procfile, manifest, requirements, robots, service worker, error pages) | 11 | 358 | 14 KB | 0.2% |
| &nbsp;&nbsp;Self-hosted fonts | 16 | — | 550 KB | — |
| &nbsp;&nbsp;App icons & letterhead artwork | 7 | — | 322 KB | — |
| **TOTAL** | **464** | **152,392** | **9.21 MB** | **100%** |

Two rows carry a file that holds no tests or no migration: the test suite's 78
files include one empty package initialiser, so **77 files actually carry
tests**, and the 93 migration files are 91 numbered migrations plus the two
package initialisers. Both are counted where they live so the table adds up.

The three shared error pages (403 / 404 / 500, 93 lines) sit in the config row
rather than with the screens: they extend no layout and load nothing at all, so
that an error page cannot break for the reason it is being shown. Counting them
as screens gives the **125** in the headline.

**Three working files sit outside version control** and are deliberately not
counted above: the environment secrets file, the local technical-debt register
(212 lines, 4,072 words), and the error log.

---

## 02 — Language usage

Measured across 131,638 lines of code written for this project. Third-party
libraries and documentation are excluded. Crucially, the CSS and JavaScript
written *inside* template files is counted as CSS and JavaScript, not as HTML —
which is why these percentages differ from a naive file-extension count.

| Language | Lines | Share |
|---|---:|---:|
| **Python** — business logic, data model, tests | 77,171 | **58.6%** |
| **CSS** — incl. 19,301 lines written inside templates | 21,959 | **16.7%** |
| **HTML / Django templates** — markup only | 21,495 | **16.3%** |
| **JavaScript** — incl. 6,495 lines written inside templates | 11,013 | **8.4%** |
| **TOTAL OWN CODE** | **131,638** | **100%** |

Inline styling and scripting inside templates accounts for **777 KB of CSS
across 71 templates** and **303 KB of JavaScript across 45 templates**.

### Python, broken down

| Purpose | Lines | Share of Python |
|---|---:|---:|
| Test suite | 41,082 | 53.2% |
| Application code | 31,730 | 41.1% |
| Database migrations | 2,824 | 3.7% |
| Build tooling | 1,535 | 2.0% |
| **TOTAL PYTHON** | **77,171** | **100%** |

> **1.29 lines of test for every line of application code.** The test suite is
> larger than the software it tests. That ratio is the single number that best
> describes how this system was built, and it has held as the system grew — it
> was 1.22 : 1 in August and 1.29 : 1 in September, across two further
> features and 326 more tests.

---

## 03 — Back end, by layer

97 files of application code, excluding tests and migrations. Every row adds up
to the total; nothing is double-counted.

| Layer | Files | Lines |
|---|---:|---:|
| Views — `workshop/views/` package | 24 | 9,436 |
| **Rule modules** — each owns exactly one question | 18 | 6,393 |
| Models, forms, costing & signals | 5 | 5,100 |
| Management commands | 18 | 3,757 |
| Views — flat modules (auth, analysis, cashbook, admin, cleanup) | 5 | 3,129 |
| Views — inventory app | 2 | 1,490 |
| Routing, admin & template tags | 6 | 926 |
| Platform — notifications, push, mail, middleware, access control | 5 | 836 |
| Settings, WSGI/ASGI, entry point | 8 | 591 |
| App registry & package initialisers | 6 | 72 |
| **TOTAL** | **97** | **31,730** |

### The eighteen rule modules — the structural idea of this codebase

| Module | The one question it answers | Lines |
|---|---|---:|
| `analysis_engine.py` | What is the profit, and the cash movement, for this date window? | 1,786 |
| `service_history.py` | Every figure and every name on the service-history sheet | 832 |
| `rent.py` | How much should we hand the rent collector today? | 648 |
| `invoice.py` | What does the customer see? (owns both the bill and the estimate) | 502 |
| `photos.py` | Where do the bytes go, and how is the URL signed? | 392 |
| `master_data.py` | What does renaming or merging a name actually do? | 391 |
| `old_bills.py` | Is this pre-system bill's number, date and money acceptable? | 342 |
| `settlement.py` | What is still unfilled before this bill is settled? | 304 |
| `old_bill_pdf.py` | What does this bill's own saved PDF say, box by box? | 257 |
| `mileage.py` | Can this hand-typed odometer reading be believed? | 165 |
| `vehicle_ids.py` | Is this a chassis code and a VIN, and what did this car last have? | 150 |
| `money_dates.py` | Which day did this money move, and how far back may it be filed? | 133 |
| `delete_window.py` | Has this record been in the books too long for Office to delete? | 125 |
| `known_car.py` | What does the workshop already know about this number plate? | 119 |
| `money.py` | Is this typed rupee amount acceptable for its column? | 70 |
| `pricing.py` | What markup is suggested, and is this typed markup usable? | 69 |
| `spare_dates.py` | Is this ordered / received date pair the right way round? | 60 |
| `return_to.py` | Where does this page send you when you leave it? | 48 |
| **18 MODULES** | | **6,393** |

> Nine of these were added after the August measurement, and each for the same
> reason as the first nine: a second implementation had just become necessary,
> or had just been found. `return_to.py` is the clearest case — it is 48 lines
> and it exists because the identical host check had been copy-pasted into two
> view modules and a third was about to need it.
>
> **`pricing.py` is the one that is defined by what it does *not* contain.**
> It holds the three markup numbers and the parser for a typed markup, and
> deliberately no price function: the suggested customer price is worked out in
> the browser and reaches a bill only when a person presses Save. A price the
> server computed would have billed parts the floor added with no price,
> silenced the settle check, repriced an unlocked settled card, and moved old
> bills every time a late supplier bill re-costed the shelf.

### Ten largest single files

| File | Lines |
|---|---:|
| `workshop/models.py` | 2,797 |
| `workshop/analysis_engine.py` | 1,786 |
| `workshop/forms.py` | 1,353 |
| `inventory/views_suppliers.py` | 1,208 |
| `workshop/views/jobcard.py` | 1,205 |
| `workshop/views/spare_shop.py` | 1,157 |
| `workshop/views/salary_advance.py` | 1,001 |
| `workshop/auth_views.py` | 958 |
| `workshop/analysis_views.py` | 933 |
| `workshop/views/car_profiles.py` | 853 |

---

## 04 — Front end, by section

122 templates under the two apps — every screen in the system, plus the partials
they include. Each carries its own page-specific styling and scripting inline,
which is why the line counts run high: a template is a complete screen, not a
fragment.

### Workshop app — 102 templates, 43,275 lines

| Section | Files | Lines |
|---|---:|---:|
| Job Card — *the central record* | 16 | 11,701 |
| Car Profiles — *incl. the two customer documents a profile hands over* | 6 | 4,851 |
| Shared shell — *base layout, home, About* | 3 | 2,970 |
| Spare Shops | 5 | 2,754 |
| Analysis & Reports | 10 | 2,517 |
| Estimates | 5 | 2,326 |
| Shared includes | 10 | 2,294 |
| Cashbook | 4 | 1,986 |
| Salary & Advance | 5 | 1,782 |
| Dashboard | 1 | 1,560 |
| Invoice — *one file, the printed bill* | 1 | 1,391 |
| Control Hub — *accounts, staff, security, data cleanup* | 4 | 1,127 |
| Deposit & Rent | 1 | 1,060 |
| Old Bills — *the Excel years, typed in for history* | 3 | 1,010 |
| Completed | 2 | 881 |
| Owner Withdrawals | 1 | 873 |
| Sign-in & password recovery | 5 | 667 |
| Master Lists | 11 | 555 |
| Legacy Data — *the go-live starting position* | 4 | 553 |
| Deletion History | 2 | 232 |
| Notifications | 3 | 185 |
| **SUBTOTAL** | **102** | **43,275** |

> **Two whole sections appeared here since the September measurement**, and both
> exist because the system is about to be installed in a workshop that is
> already running rather than starting from nothing: **Old Bills**, where the
> years billed in Excel are typed in so a car's history reaches back to its
> first visit, and **Legacy Data**, the two screens that carry the opening shelf
> count and what each shop was owed on day one — filled in once and then locked
> for good.

### Inventory app — 20 templates, 3,809 lines

| Section | Files | Lines |
|---|---:|---:|
| Supplies Shops — *restock bills, catalog, payments* | 13 | 2,573 |
| Stock — *list, low stock, history, categories* | 7 | 1,236 |
| **SUBTOTAL** | **20** | **3,809** |

### Shared front-end assets

| File | Role | Lines |
|---|---|---:|
| `workshop/static/css/analysis.css` | Owner analysis styling | 1,388 |
| `static/css/style.css` | Global styling — the payment card, the back control, the question card | 1,270 |
| `workshop/static/js/script.js` | Form rows, autocomplete, shared behaviour | 590 |
| `workshop/static/js/photos.js` | Camera capture & upload queue | 550 |
| `workshop/static/js/confirm.js` | The shared question card, and one-press-one-post | 412 |
| `static/js/notifications.js` | Alert panel & push subscription | 331 |
| `workshop/static/js/spare_autofill.js` | Spare status & date derivation | 296 |
| `workshop/static/js/old-bill-core.js` | Reading a typed date and amount — **unit-tested** | 295 |
| `workshop/static/js/sound.js` | Five synthesised outcome tones | 288 |
| `workshop/static/js/photos-core.js` | Pure logic, DOM-free — **unit-tested** | 276 |
| `workshop/static/js/pricing-core.js` | Markup arithmetic in whole paise — **unit-tested** | 253 |
| `workshop/static/js/estimate.js` | Estimate line editing | 234 |
| `workshop/templates/workshop/sw.js` | Service worker — push delivery, offline notice | 125 |
| **13 SHARED FILES** | | **6,308** |

> **No build step, no npm, no bundler.** Server-rendered Django templates with
> page-scoped inline styling and scripting. Thirteen shared JavaScript and CSS
> files exist, and the rule for admission is simply "used on more than one
> page". This is a settled architectural decision, documented with its
> reasoning, not an unaddressed backlog item.
>
> **Three of the JavaScript files are marked unit-tested, and that is a
> different rule from the other ten.** They are not shared because two pages
> need them — `pricing-core.js` and `old-bill-core.js` are each used by one
> screen. They are separate files because they are *provable*: pure functions,
> no browser, no network, so Node's built-in runner can execute them. Each was
> extracted before it was written, because each computes something that can be
> wrong without looking wrong — a price rounded by floating point, a comma read
> as a decimal point.
>
> `style.css` grew from 847 lines to 1,270 by *absorbing* duplication rather
> than adding features: the payment card that had been four near-copies, the
> back control that had been seventeen controls in seven treatments, and the
> question card that replaced twenty-one browser dialogs.

---

## 05 — Test suite

2,692 tests, counted by asking Django's own test runner to build the suite — not
by counting function names, which misses tests inherited from shared base
classes.

| Tests | Test classes | Test files | Lines | Test : app code |
|---:|---:|---:|---:|---:|
| **2,692** | **516** | **77** | **41,082** | **1.29 : 1** |

A full run takes 40 minutes to two and a half hours and always executes against
SQLite, in memory. That is not a convenience: the runner creates and drops an
entire database, and there is deliberately no flag that would let it be pointed
at real data by accident. The spread is machine load rather than a signal — four
runs on one day in September measured 70, 42, 76 and 69 minutes, and the slowest
recorded run, on 20 September, took 2 h 30 m on a machine also serving a
development server.

### Twelve largest test files

| File | What it guards | Lines |
|---|---|---:|
| `test_analysis.py` | The profit engine, cash tracking and every insight | 2,915 |
| `test_master_salary_hub_integrity.py` | Salary months, settlement locking, advances | 2,439 |
| `test_jobcard_form_ux.py` | The job card form — the busiest screen | 1,883 |
| `test_rent.py` | The rent ledger, and how far back money may be filed | 1,628 |
| `test_invoice.py` | The printed bill handed to a customer | 1,558 |
| `test_service_history_view.py` | The printed service record, set from the bill | 1,536 |
| `test_photos.py` | Evidence photos, signing, retention | 1,258 |
| `tests_suppliers.py` *(inventory)* | Supplies shops & restock bills | 1,163 |
| `test_fleet_cashbook_integrity.py` | Fleet account balances & the cashbook | 1,124 |
| `test_estimate.py` | Quotations | 1,112 |
| `test_old_bills.py` | The Excel years, and one JB number sequence across both | 1,047 |
| `test_notifications.py` | The alert catalogue | 1,044 |

**There is also a second, separate test runner for JavaScript** — Node's
built-in one, with no npm, no package file and no dependencies. It covers three
modules — `photos-core.js`, `pricing-core.js` and `old-bill-core.js` — because
each was deliberately written to be coverable: pure functions, no browser, no
network. 61 assertions, and the markup-arithmetic expectations in
`pricing-core.test.js` were each produced by Python first, so the browser and
the server can never round a customer's price differently.

⚠ **Nothing in the Django suite executes a line of CSS or JavaScript.** That is
why several test files assert on *markup and source* rather than behaviour: a
dialog that opens behind another element, a card with no colour, a form that
quietly lost its question, and a page that pasted its own bespoke back link all
leave every functional test green. Those assertions are the only thing that
notices.

---

## 06 — System structure

The moving parts, counted by loading the application and asking it directly
rather than by reading source.

| What | Count | Detail |
|---|---:|---|
| Data models | 46 | 37 in the workshop app, 9 in inventory |
| Model fields | 329 | 273 workshop, 56 inventory — concrete columns only |
| URL routes | 312 | Every reachable address, Django admin included; 181 are the app's own |
| View-module functions | 293 | 188 public screens, 105 private helpers |
| Database migrations | 91 | 81 workshop, 10 inventory |
| Form classes | 14 | Excludes 6 formsets |
| Access-control decorators applied | 187 | 36 Owner-only, 121 Office, 30 all staff |
| Database signal handlers | 15 | 13 for stock & costing, 2 for sessions & photos |
| Atomic transaction blocks | 61 | 45 `with` blocks and 16 decorators — every money movement is all-or-nothing |
| Row locks (`select_for_update`) | 10 | Guards cascade payments against races |
| Notification events | 16 | 13 critical (push to phone), 3 informational |
| Notification call sites | 23 | Across 9 modules, one catalogue |
| Permanently-deletable record types | 15 | Each writes a snapshot before deletion |
| Management commands | 15 | Backup, seeding, purge, owner identity, roles, photo sweep, legacy unlock |
| Template tags & filters | 15 | Custom, shared across screens |
| Runtime dependencies | 9 | Django, Pillow, psycopg2, WhiteNoise, gunicorn, decouple, pywebpush, coverage, pypdf |
| Commits | 259 | 11 January – 20 September 2026 |

⚠ **Every count here is across *application code only*** — tests, migrations and
build scripts are excluded. That matters most on the transaction and row-lock
rows: the test suite opens its own transactions and takes its own locks in order
to prove the real ones hold, and counting those as system transactions flatters
the number while describing nothing.

⚠ **The stock signal handlers went from 10 to 13 in this revision**, and the
number was stated on the printed system map as well as here. Opening stock
added a fourth group of three — a go-live shelf count moves the shelf the same
way a delivery does, because *stock only ever moves through a signal* and a
screen that wrote `current_stock` itself would be the first exception in the
system.

---

## 07 — Documentation

171,958 words across ten documents, plus a generated one-page system map in
light and dark themes, committed as both HTML and print-exact PDF. Each document
owns a defined set of facts; none restates another's.

| Document | Owns | Lines | Words |
|---|---|---:|---:|
| `CLAUDE.md` | Day-to-day working rules and every deliberate decision | 10,512 | 107,812 |
| `MASTER_BLUEPRINT.md` | The numbers — models, routes, templates, settings | 1,120 | 18,519 |
| `OPERATIONAL_BLUEPRINT.md` | Workflow narrative — who does what, screen by screen | 1,718 | 15,196 |
| `TITAN_SPEC_SHEET.md` | This document — every figure, counted from the repository | 863 | 8,696 |
| `TITAN_MASTER_HANDOVER.md` | Mission, roadmap, and what is deliberately out of scope | 470 | 7,490 |
| `GO_LIVE_RUNBOOK.md` | The one-time go-live procedure and rollback | 713 | 5,462 |
| `RAILWAY_OPERATIONS.md` | Ongoing platform reference — deploys, backups, cost | 759 | 5,301 |
| `README.md` | Outward-facing summary — features, stack, install | 245 | 1,787 |
| `master_data_export.md` | The workshop's own brand / model / spare list | 315 | 1,065 |
| `WorkshopOS_Complete_Structure.md` | The section tree, as the owner describes it | 131 | 630 |
| **10 DOCUMENTS** | | **16,846** | **171,958** |

`TECH_DEBT.md` (212 lines, 4,072 words) is deliberately untracked: it lists what
is known to be wrong and not yet scheduled, which is working state rather than a
published fact.

**`CLAUDE.md` is the unusual one.** 54 major sections carrying **756 stated
rules**, **427 flagged hazards** and **209 pointers to the test that guards each
rule**. It records not just what the system does but which apparent bugs are
deliberate business decisions — so the next person to touch the code cannot
"fix" the business by accident. Every one of those hazard entries was written
after a real failure, and the count has not stopped rising: 326 in September,
427 now.

**The system map is drawn, not written.** One build script emits both the light
and dark versions from a single set of coordinates, and a third copy as a Django
partial that the in-app About page includes, so no two can disagree. A checker
then verifies six things the eye cannot catch at that density: connectors
cutting through unrelated cards, connectors missing their target, anything
off-canvas, overlaps, long same-colour lines running too close, and every tap
landing on the bus it feeds.

⚠ **The checker is geometric and reads no words.** A card's caption goes stale
exactly the way a count does, and nothing catches it — found in this pass, on
the one card that had grown two whole documents since its caption was written.

---

## 08 — Specifications & what makes it unusual

Not a feature list — those are in the README. These are the engineering
decisions that distinguish this system from a generic business application, each
one traceable to a real failure it prevents.

### Architecture

**One rule, one implementation.** Eighteen modules contain no screens at all.
Each owns exactly one question — the profit maths, the printed document, the
service record, the rent calculation, the settlement checklist, the rename rule,
rupee validation, which day money moved, date-pair validation, the deletion
window, odometer parsing, photo signing, what this car already told us, whether
a VIN is a VIN, what markup to suggest, whether a pre-system bill is acceptable,
what that bill's own PDF says, and where a page sends you when you leave it.
Nothing else in 152,392 lines is permitted to answer those questions a second
time. The reason is specific: the cost of a spare part was once calculated in
five different places, giving a shop's own page and the profit page two
different answers for the same debt.
*18 modules · 6,393 lines*

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

**What a month COST and how it got PAID are two different numbers.** The
workshop rents its premises for a fixed monthly sum and pays it in daily cash
instalments to a collector who keeps his own book. The rent is a fixed expense;
the deposits are cash movement. Collapse them and a month where the office had a
good week reports a *higher rent* than a month where it did not, so monthly
profit swings on a cash-flow decision rather than on what the month cost — and
the owners read monthly profit to decide distribution. It is the fourth time the
system has drawn this line, not a new idea: wages are dated by the salary month
and not the day the cash left, a supplier payment never touches profit while the
stock draw does, and a spare-shop payment never touches profit while the part
fitted does.
*Nothing is stored but the rate and the deposits; every other figure is derived.*

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

**Money is dated at both ends.** Nothing that moves money can be dated ahead of
today — a job card mistyped into next year lifts a whole job out of the month
that earned it and then *hides* it, because the year-to-date window ends on a
calendar boundary the card now sits past. The other end is quieter and needed
its own rule: a figure dated three years back rewrites the running position of
every month since, on rows nobody scrolls to, and reports nothing. Office may
file back to the 1st of last month — a **calendar month, never a day count**,
because the office reconciles last month in the first days of this one and a
"14 days" rule would refuse exactly that correction.
*One implementation, six screens, and the owner's exception is logged rather than silent.*

**The system installs into a workshop that is already running, and says so in
the schema.** Parts are on the shelf and money is owed to every shop on the day
it starts, and neither can arrive through a daily screen — stock only moves
through a supplies bill, and a shop's balance is built from purchases recorded
against it. Two go-live screens take the starting position once. The opening
shelf count is **always the first event in the costing replay**, whatever day it
was typed, so a part drawn before the count was finished is still costed from
it; and its cost is **required**, because without one every part used before
that product's next bill would be charged ₹0 on the profit page permanently — a
later-dated bill never reaches back. An opening balance is the oldest debt a
shop has, so all three payment waterfalls clear it first.
*The shelf and the balances are typed separately: on day one they have no connection, and inventing one would be a guess.*

**And then that door is bolted from the inside.** Once the figures match the
count and the shops' books, an owner locks both screens behind three
confirmations that get louder. Afterwards they still *show* their figures and
refuse every change from everyone, owners included, enforced in the view rather
than by hiding the form. Nothing in the application can undo it — only a command
run on the server by whoever holds the deployment. **The lock is a database row
rather than a hosting setting**, and that was the owner's own correction to the
first design: a setting lives on the hosting account, so a backup restored
somewhere else, or the whole system moved to another host, would come back
silently open.
*The figures a business is measured from should not stay editable because nobody got round to closing them.*

**The server never works out a price.** A customer price is suggested at a
markup — 40% on a bought-in part, the product's own figure off the shelf — and
that arithmetic lives in the browser, in whole paise, written into ordinary
boxes that reach a bill only when a person presses Save. Computing it on save
was the obvious build and would have moved money four ways nobody decided: it
would price the parts the floor records with no price, silence the settle
check's "no customer price", reprice an unlocked settled card, and move old
bills every time a late supplier bill re-costed the shelf. A test asserts the
server prices nothing; if it fails, the arithmetic has moved back.
*`700 × 1.1` is `770.0000000000001` in JavaScript, which is why the maths is integers and why it has its own unit tests.*

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

**Getting in always reaches a phone.** Every sign-in raises a critical alert to
the *other* owner. An owner's own sign-in was informational until August, on the
reasoning that it is routine — until the obvious question was asked and had a bad
answer: **a sign-in on an owner account with a stolen password reached no phone
at all.** The reset flow was alarmed; simply knowing the password was not. It is
safe at critical for a measured reason rather than a hopeful one: the session
cookie lasts forty days, so a signed-in phone stays signed in and this fires on a
genuinely new device — one or two a month across two owners.
*The actor is always excluded, so what arrives is always "somebody else signed in".*

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
*15 record types · one shared entry point · every delete notifies both owners.*

**The years before the system are typed in, and joined to nothing.** About eight
hundred bills were written in Excel before this existed, and they are entered so
that a car's profile, its printed service history and its all-bills PDF reach
back to its first visit rather than starting on the day the workshop switched
over. They touch **no** profit figure, no cash figure, no stock and no shop
balance: those months happened outside the system, and counting them now would
rewrite periods nobody can check. A test holds an allow-list of the only files
permitted to mention the model at all and fails the moment another one does —
adding a file to that list is a decision, not a fix for a red test. The bills
also shared the system's own numbering, so one sequence now spans both: a live
job card skips any number an old bill holds, and an old bill cannot take a
number that belongs to the live series.
*Where the bill was saved as a PDF, the PDF fills the form — and saves nothing until a person has looked at it.*

**A correction and an anomaly are different acts.** Office can delete a money
record entered in the last seven days; anything older is an owner's to remove.
The window is measured from when the row was *entered*, never from the date the
money carries — because back-dating is normal here, and on the money date Office
would be refused permission to delete their own typo thirty seconds after making
it. The control is still offered rather than hidden, and the refusal names the
rule, the age of the row and who to ask.
*An escalation, not a wall — no approval queue, no second sign-off.*

**Where prevention stops, detection starts.** Every guard in the system escalates
to an owner, and nothing can refuse an owner — so at that boundary the model
changes rather than the rule getting stricter. An owner who files money into a
closed month sends a critical alert to the *other* owner within seconds, the row
itself carries a permanent visible mark, and a separate view lists what was
filed backwards most recently. That last part exists because the owner tested it,
knew they had back-dated something, and still could not find it: the row's mark
is only visible once the right month is open.
*An approval queue in a two-owner workshop is machinery nobody would use.*

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

**Every bill for one car is the same bill, not a copy that looks like one.** The
tempting build for "send me all my invoices" is a second template laying a bill
out the same way. It would look right on the day and drift on some later one — a
column width, a rounding, a label — and the *customer* would find it, holding
both documents at once. So the markup was extracted the way the arithmetic
already had been, and a test renders one job card through both routes and asserts
the two sheets match **character for character**.

**A document is set from another document, not by eye.** The service-history
sheet wore the bill's letterhead and still read as generic. Every size and every
colour on it was legal against the stated rule, audited and true. Measuring the
two *rendered* documents element by element — rather than reading either
stylesheet — found what neither audit could: **the sheet was set in bold and the
bill is not**, 166 bold elements against the bill's five, with eleven-point
regular painted not once. Green went with it, because green means *money* in this
system and this document carries no payment state at all.
*The rule now has three clauses: no size, no colour, and no weight the bill does not already use.*

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

**Every page carries its own way out.** The installed app has no address bar and
no browser Back button, and a laptop has no system back gesture either. There
were seventeen back controls in seven visual treatments across two placements —
including four that called browser history, which does nothing at all on the
first page of a session, and one page that rendered no way out whatsoever. They
are now one control that **names its destination**. A global back button in the
navigation bar was asked for and deliberately not built: measured at 375 px the
bar is five equal columns with no free slot, a sixth tab costs every existing tab
17% of its width, and roughly twenty pages would then carry two back affordances.
*The measurement is the argument — the request was reasonable and the numbers refused it.*

**The app asks its own questions.** Twenty-one browser dialogs were replaced by
one card. They opened with "127.0.0.1:8000 says", which is the browser talking
rather than the app, and rendered the question, the reason and the way out as one
flat grey block that can carry no icon, no colour and no field. The replacement
found a real defect on the way in: a card inherits the visibility rules of the
screen it opens on, and the Mark-Completed card — pressed mostly from the
shop-floor tablet — was explaining what would happen to *the bill*, to somebody
shown no money anywhere in the system.
*Two native dialogs survive, both deliberate fallbacks for a page whose script never arrived.*

**One press is one post.** Reported from the shop: on a slow connection the same
Confirm was tapped again and again, and every tap was another submission — a
payment deleted twice, an advance deleted twice. The app-wide guard listens for a
submit *event*, and nine dialogs post by calling the form directly, which fires
none — so the screens where a second press costs the most had no guard at all.
The fix wraps the browser's own form-submit method once, rather than restating
the rule in nine templates.
*Painted with pointer-events, never `disabled` — a disabled control is dropped from the payload.*

**Five tones, wired to nothing.** Success, error, warning, question and shutter
each have a synthesised tone, and the first four are carried by an attribute on
the message banner. Because the app already tags every outcome, one attribute
covers every action in the system — and anything added later. Per-button sounds
were rejected: roughly 230 places to attach the wrong tone, each firing at click
time, announcing "done" before the server had done anything.
*Informational messages are silent — a tone for everything trains people to hear nothing.*

### Alerts

**One catalogue, one entry point.** Sixteen events, defined in a single file,
raised from eighteen places. Severity is a delivery tier rather than decoration:
thirteen push to the owners' phones, three land only in the in-app feed. A
notification's address is permanent, so the rule is that a bad one is fixed by
making that address work — never by repointing the next alert, which does nothing
for every alert already sent.
*Push runs off the request path entirely — a dead push service cannot slow a payment.*

**A row is three strings, and each answers a different question.** What happened,
what category it belongs to, and the context read second. The loud line carries
what *differs* between rows — it used to carry the category, so nine consecutive
alerts opened with "Record deleted" in bold and the ₹1,00,000 sat in smaller,
greyer type underneath. The same fault reached the lock screen, where a push had
been sending the category as its bold line.
*A push carries no replace-key: it once carried a constant one, so each alert deleted the one before it.*

### Discipline

**Traps are written down.** 326 hazards are documented, each one a failure that
produced no exception, no console error and a green test suite — a running CSS
transition outranking an important rule; a cached deletion list in a form set; a
date built in UTC reporting yesterday for an entire Indian morning; a framework
that centres a dialog only above a certain screen width. They are recorded so the
next person does not have to rediscover them, and 190 rules carry a pointer to
the exact test that guards them.

**A number written down is a number going stale.** Every count in these documents
is re-derived rather than trusted, because they have drifted repeatedly and
always the same way: a feature lands and the tables are not recounted. The pass
that produced this revision found ten counts stale in a single diagram, an entire
summary line stale on every figure it carried, nine test files documented
nowhere, and — worse than any count — four documents still describing a security
alert that had been changed eleven days earlier.
*The lesson is written into the documents themselves: re-derive before quoting.*

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
| **152,392** | lines of code across 464 files, in a single deployable system |
| **2,692** | automated tests — 1.29 lines of test for every line of application code |
| **46** | data models holding 329 fields, reachable through 312 addresses |
| **125** | screens, each built to work on a laptop, a tablet and a phone |
| **18** | rule modules, each the only place in the system that answers its question |
| **187** | access checks across three roles — owner, office, floor |
| **61** | all-or-nothing transaction blocks guarding every movement of money |
| **171,958** | words of documentation across ten documents and a drawn system map |
| **9** | runtime dependencies — no build step, no package manager, no bundler |
| **259** | commits over nine months, by one developer |

> **The one-line version:** a 152,000-line Django system running a premium
> automotive workshop end to end — job cards, inventory, supplier and spare-shop
> ledgers, fleet billing, payroll, rent, estimates, evidence photography, printed
> service records, owner withdrawals and owner analytics — with a test suite
> larger than the application it tests, and every deliberate business decision
> written down with the failure it prevents.
>
> It is not being switched on in an empty workshop, either: the years billed in
> a spreadsheet are typed in as history, and the shelf and the shop debts that
> already existed on day one are entered once and then locked.

---

## Method

Counted from the working tree on 20 September 2026, on branch `main`, including
this document.

- File counts cover files tracked in version control, and this revision excludes
  nothing: the committed demo database that sat outside the previous totals was
  deleted with the throwaway demo deploy. Three untracked working files are
  named in section 01 and are still excluded.
- Line counts exclude binary files, which are reported by size. A line is
  `len(text.splitlines())` — the previous revision's counter added a phantom
  line to every file, which is worth about 460 lines across the tree and is why
  a few figures here are marginally lower than a naive recount would suggest.
- Vendored third-party libraries are counted as files but excluded from language
  percentages.
- CSS and JavaScript written inside template files is attributed to those
  languages, not to HTML.
- The test count comes from Django's own suite builder; model, route, field,
  form, event and decorator counts come from loading the application and
  querying it. Decorator, transaction and row-lock counts exclude test files.
  ⚠ **The transaction figure is not comparable with the last revision's 43.**
  That count matched only `with` blocks; this one matches decorators as well,
  which is what the row now says. The underlying code did not jump.
- **Corrections made at source by this run**, in the documents that own each
  fact rather than only here:
  - **`SYSTEM_MAP` (all five generated outputs) — a number on the sheet was
    simply false.** The STOCK SIGNALS card had read `10 handlers` since the day
    it was drawn; opening stock's fourth group took it to 13 in September. The
    map's checker is geometric and reads no words, so nothing caught it — the
    same failure as the Car Profiles caption in the last revision, on the card
    next to it. The sheet also gained a **LEGACY DATA** card.
  - ⚠ **And adding that card broke a connector, which the checker *did* catch.**
    The logistics zone is a 3×4 grid that held eleven cards, so the twelfth slot
    read as spare room — while a line quietly hopped through it. Check 1 refused
    the drawing rather than shipping a connector through a card. **An empty cell
    on that sheet may be carrying a route, and nothing says so**, which is now
    written down where the cell is.
  - **The in-app About page described neither Legacy Data, nor Old Bills, nor
    the suggested customer price** — three owner-facing features, one of them
    the largest addition since the customer documents. Its test asserts that
    every card on the map is described in words, and the map had no card for any
    of them, so nothing failed. The map card closes that loop for Legacy Data;
    the other two were added by reading rather than by a test.
  - `CLAUDE.md` — two written-down counts deleted rather than corrected
    ("Nine families, ~43 cards" on the About page, and "between 56 of them" in
    the map builder). Both are printed by the build on every run, which is the
    file's own rule for the map's revision stamp and was not being applied to
    its own prose.
  - `WorkshopOS_Complete_Structure.md` — the Legacy Data screens, and the date
    the outline claims to have been derived on.
  - `scratchpad/build_system_map.py` — a comment describing a deliberate empty
    slot in the financial zone, long after two cards filled that row. The same
    failure as the paragraph above, from the other side: a note claiming space
    that is gone, beside a space nothing claimed.
