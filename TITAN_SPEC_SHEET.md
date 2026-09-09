# Titan Spec Sheet

**WorkshopOS "Titan"** — every file, line, model and rule, counted from the
repository rather than estimated.

| | |
|---|---|
| **Measured** | 9 September 2026, branch `main`, including this document |
| **Stack** | Django 5.2 monolith, PostgreSQL in development and production |
| **Apps** | 2 — `workshop` (core business logic), `inventory` (stock + supplier shops) |
| **History** | 246 commits, 11 January – 9 September 2026 |

| Files | Lines | Tests | Models | Routes | Screens | Dependencies |
|---:|---:|---:|---:|---:|---:|---:|
| **432** | **138,901** | **2,366** | **41** | **301** | **118** | **8** |

> Every figure below was re-measured from the working tree. Nothing is carried
> over from documentation.

---

## 01 — Totals: files and lines

433 files are tracked in version control. One of them is a 6.6 MB SQLite
database committed so the throwaway demo deploy has data to serve; it is not
code and is excluded from every count below, leaving **432**. The four
categories do not overlap and add up exactly to that total. Line counts exclude
binary files (fonts, images, PDFs), which are measured by size instead.

| Category | Files | Lines | Size | Share of lines |
|---|---:|---:|---:|---:|
| **Back end** | **246** | **68,561** | 3,087 KB | 49.4% |
| &nbsp;&nbsp;Application code | 89 | 28,751 | 1,295 KB | 20.7% |
| &nbsp;&nbsp;Test suite | 70 | 37,124 | 1,690 KB | 26.7% |
| &nbsp;&nbsp;Database migrations | 87 | 2,686 | 103 KB | 1.9% |
| **Front end** | **127** | **49,847** | 2,281 KB | 35.9% |
| &nbsp;&nbsp;Django templates (screens) | 115 | 43,920 | 2,059 KB | 31.6% |
| &nbsp;&nbsp;Shared JavaScript | 9 | 3,076 | 135 KB | 2.2% |
| &nbsp;&nbsp;Shared stylesheets | 2 | 2,549 | 75 KB | 1.8% |
| &nbsp;&nbsp;JavaScript test suite | 1 | 302 | 12 KB | 0.2% |
| **Documentation** | **14** | **16,140** | 1,720 KB | 11.6% |
| &nbsp;&nbsp;Markdown documents | 10 | 14,679 | 874 KB | 10.6% |
| &nbsp;&nbsp;System map (2 HTML + 2 PDF) | 4 | 1,461 | 845 KB | 1.1% |
| **Other** | **45** | **4,353** | 1,576 KB | 3.1% |
| &nbsp;&nbsp;Vendored libraries (Bootstrap, icons, Chart.js) | 5 | 2,252 | 609 KB | 1.6% |
| &nbsp;&nbsp;Build tooling (icon, system-map, vendoring, seed scripts) | 6 | 1,827 | 86 KB | 1.3% |
| &nbsp;&nbsp;Config (Procfile, render blueprint, manifest, requirements, robots, error pages) | 11 | 274 | 9 KB | 0.2% |
| &nbsp;&nbsp;Self-hosted fonts | 16 | — | 550 KB | — |
| &nbsp;&nbsp;App icons & letterhead artwork | 7 | — | 322 KB | — |
| **TOTAL** | **432** | **138,901** | **8.46 MB** | **100%** |

Two rows carry a file that holds no tests or no migration: the test suite's 70
files include one empty package initialiser, so **69 files actually carry
tests**, and the 87 migration files are 85 numbered migrations plus the two
package initialisers. Both are counted where they live so the table adds up.

The three shared error pages (403 / 404 / 500, 93 lines) sit in the config row
rather than with the screens: they extend no layout and load nothing at all, so
that an error page cannot break for the reason it is being shown. Counting them
as screens gives the **118** in the headline.

**Three working files sit outside version control** and are deliberately not
counted above: the environment secrets file, the local technical-debt register
(196 lines, 3,924 words), and the error log.

---

## 02 — Language usage

Measured across 120,328 lines of code written for this project. Third-party
libraries and documentation are excluded. Crucially, the CSS and JavaScript
written *inside* template files is counted as CSS and JavaScript, not as HTML —
which is why these percentages differ from a naive file-extension count.

| Language | Lines | Share |
|---|---:|---:|
| **Python** — business logic, data model, tests | 70,388 | **58.5%** |
| **CSS** — incl. 18,256 lines written inside templates | 20,805 | **17.3%** |
| **HTML / Django templates** — markup only | 20,154 | **16.8%** |
| **JavaScript** — incl. 5,603 lines written inside templates | 8,981 | **7.5%** |
| **TOTAL OWN CODE** | **120,328** | **100%** |

Inline styling and scripting inside templates accounts for **704 KB of CSS
across 67 templates** and **259 KB of JavaScript across 42 templates**.

### Python, broken down

| Purpose | Lines | Share of Python |
|---|---:|---:|
| Test suite | 37,124 | 52.7% |
| Application code | 28,751 | 40.8% |
| Database migrations | 2,686 | 3.8% |
| Build tooling | 1,827 | 2.6% |
| **TOTAL PYTHON** | **70,388** | **100%** |

> **1.29 lines of test for every line of application code.** The test suite is
> larger than the software it tests. That ratio is the single number that best
> describes how this system was built, and it has *risen* as the system grew —
> it was 1.22 : 1 in August.

---

## 03 — Back end, by layer

89 files of application code, excluding tests and migrations. Every row adds up
to the total; nothing is double-counted.

| Layer | Files | Lines |
|---|---:|---:|
| Views — `workshop/views/` package | 21 | 8,304 |
| **Rule modules** — each owns exactly one question | 13 | 5,230 |
| Models, forms, costing & signals | 5 | 4,508 |
| Management commands | 14 | 3,646 |
| Views — flat modules (auth, analysis, cashbook, admin, cleanup) | 5 | 3,134 |
| Views — inventory app | 2 | 1,412 |
| Routing, admin & template tags | 6 | 907 |
| Platform — notifications, push, mail, middleware, access control | 6 | 883 |
| Settings, WSGI/ASGI, entry point | 7 | 586 |
| App registry & package initialisers | 10 | 141 |
| **TOTAL** | **89** | **28,751** |

### The thirteen rule modules — the structural idea of this codebase

| Module | The one question it answers | Lines |
|---|---|---:|
| `analysis_engine.py` | What is the profit, and the cash movement, for this date window? | 1,787 |
| `service_history.py` | Every figure and every name on the service-history sheet | 704 |
| `rent.py` | How much should we hand the rent collector today? | 649 |
| `invoice.py` | What does the customer see? (owns both the bill and the estimate) | 430 |
| `photos.py` | Where do the bytes go, and how is the URL signed? | 393 |
| `master_data.py` | What does renaming or merging a name actually do? | 367 |
| `settlement.py` | What is still unfilled before this bill is settled? | 293 |
| `mileage.py` | Can this hand-typed odometer reading be believed? | 166 |
| `money_dates.py` | Which day did this money move, and how far back may it be filed? | 134 |
| `delete_window.py` | Has this record been in the books too long for Office to delete? | 126 |
| `money.py` | Is this typed rupee amount acceptable for its column? | 71 |
| `spare_dates.py` | Is this ordered / received date pair the right way round? | 61 |
| `return_to.py` | Where does this page send you when you leave it? | 49 |
| **13 MODULES** | | **5,230** |

> Four of these were added after the August measurement, and each for the same
> reason as the first nine: a second implementation had just become necessary,
> or had just been found. `return_to.py` is the clearest case — it is 49 lines
> and it exists because the identical host check had been copy-pasted into two
> view modules and a third was about to need it.

### Ten largest single files

| File | Lines |
|---|---:|
| `workshop/models.py` | 2,480 |
| `workshop/analysis_engine.py` | 1,787 |
| `workshop/forms.py` | 1,225 |
| `workshop/views/jobcard.py` | 1,157 |
| `workshop/views/spare_shop.py` | 1,136 |
| `inventory/views_suppliers.py` | 1,129 |
| `workshop/views/salary_advance.py` | 1,002 |
| `workshop/auth_views.py` | 959 |
| `workshop/analysis_views.py` | 934 |
| `workshop/management/commands/seed_dummy_data.py` | 761 |

---

## 04 — Front end, by section

115 templates under the two apps — every screen in the system, plus the partials
they include. Each carries its own page-specific styling and scripting inline,
which is why the line counts run high: a template is a complete screen, not a
fragment.

### Workshop app — 95 templates, 40,126 lines

| Section | Files | Lines |
|---|---:|---:|
| Job Card — *the central record* | 16 | 10,935 |
| Car Profiles — *incl. the two customer documents a profile hands over* | 6 | 4,349 |
| Shared shell — *base layout, home, About* | 3 | 2,800 |
| Spare Shops | 5 | 2,743 |
| Analysis & Reports | 10 | 2,527 |
| Estimates | 5 | 2,314 |
| Shared includes | 10 | 2,274 |
| Cashbook | 4 | 1,990 |
| Salary & Advance | 5 | 1,787 |
| Dashboard | 1 | 1,561 |
| Invoice — *one file, the printed bill* | 1 | 1,319 |
| Control Hub — *accounts, staff, security* | 4 | 1,131 |
| Deposit & Rent | 1 | 982 |
| Completed | 2 | 881 |
| Owner Withdrawals | 1 | 874 |
| Sign-in & password recovery | 5 | 672 |
| Master Lists | 11 | 565 |
| Deletion History | 2 | 234 |
| Notifications | 3 | 188 |
| **SUBTOTAL** | **95** | **40,126** |

> **Car Profiles went from 3 files and 1,342 lines to 6 and 4,349** — the single
> largest movement in this table since August, and all of it is the two documents
> a customer asks for when they are **selling the car**: a printed service
> history, and every bill for that car in one PDF.

### Inventory app — 20 templates, 3,794 lines

| Section | Files | Lines |
|---|---:|---:|
| Supplies Shops — *restock bills, catalog, payments* | 13 | 2,551 |
| Stock — *list, low stock, history, categories* | 7 | 1,243 |
| **SUBTOTAL** | **20** | **3,794** |

### Shared front-end assets

| File | Role | Lines |
|---|---|---:|
| `workshop/static/css/analysis.css` | Owner analysis styling | 1,389 |
| `static/css/style.css` | Global styling — the payment card, the back control, the question card | 1,160 |
| `workshop/static/js/script.js` | Form rows, autocomplete, shared behaviour | 556 |
| `workshop/static/js/photos.js` | Camera capture & upload queue | 551 |
| `workshop/static/js/confirm.js` | The shared question card, and one-press-one-post | 413 |
| `static/js/notifications.js` | Alert panel & push subscription | 332 |
| `workshop/static/js/spare_autofill.js` | Spare status & date derivation | 297 |
| `workshop/static/js/sound.js` | Five synthesised outcome tones | 289 |
| `workshop/static/js/photos-core.js` | Pure logic, DOM-free — the one unit-tested module | 277 |
| `workshop/static/js/estimate.js` | Estimate line editing | 235 |
| `workshop/templates/workshop/sw.js` | Service worker — push delivery, offline notice | 126 |
| **11 SHARED FILES** | | **5,625** |

> **No build step, no npm, no bundler.** Server-rendered Django templates with
> page-scoped inline styling and scripting. Eleven shared JavaScript and CSS
> files exist, and the rule for admission is simply "used on more than one
> page". This is a settled architectural decision, documented with its
> reasoning, not an unaddressed backlog item.
>
> `style.css` grew from 847 lines to 1,160 by *absorbing* duplication rather
> than adding features: the payment card that had been four near-copies, the
> back control that had been seventeen controls in seven treatments, and the
> question card that replaced twenty-one browser dialogs.

---

## 05 — Test suite

2,366 tests, counted by asking Django's own test runner to build the suite — not
by counting function names, which misses tests inherited from shared base
classes.

| Tests | Test classes | Test files | Lines | Test : app code |
|---:|---:|---:|---:|---:|
| **2,366** | **454** | **69** | **37,124** | **1.29 : 1** |

A full run takes 20 to 80 minutes and always executes against SQLite, in memory.
That is not a convenience: the runner creates and drops an entire database, and
there is deliberately no flag that would let it be pointed at real data by
accident.

### Twelve largest test files

| File | What it guards | Lines |
|---|---|---:|
| `test_analysis.py` | The profit engine, cash tracking and every insight | 2,916 |
| `test_master_salary_hub_integrity.py` | Salary months, settlement locking, advances | 2,440 |
| `test_jobcard_form_ux.py` | The job card form — the busiest screen | 1,875 |
| `test_invoice.py` | The printed bill handed to a customer | 1,559 |
| `test_rent.py` | The rent ledger, and how far back money may be filed | 1,548 |
| `test_photos.py` | Evidence photos, signing, retention | 1,259 |
| `test_service_history_view.py` | The printed service record, set from the bill | 1,237 |
| `tests_suppliers.py` *(inventory)* | Supplies shops & restock bills | 1,164 |
| `test_fleet_cashbook_integrity.py` | Fleet account balances & the cashbook | 1,125 |
| `test_estimate.py` | Quotations | 1,113 |
| `test_notifications.py` | The alert catalogue | 1,045 |
| `test_jobcard_detail_view.py` | The read-only job card | 1,006 |

**There is also a second, separate test runner for JavaScript** — Node's
built-in one, added with no npm, no package file and no dependencies. It covers
one module, `photos-core.js`, because that module was deliberately written to be
coverable: pure functions, no browser, no network.

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
| Data models | 41 | 33 in the workshop app, 8 in inventory |
| Model fields | 292 | 244 workshop, 48 inventory |
| URL routes | 301 | Every reachable address, Django admin included; 169 are the app's own |
| View-module functions | 265 | 177 public screens, 88 private helpers |
| Database migrations | 85 | 77 workshop, 8 inventory |
| Form classes | 11 | Excludes 6 formsets |
| Access-control decorators applied | 174 | 32 Owner-only, 114 Office, 28 all staff |
| Database signal handlers | 12 | 10 for stock & costing, 2 for sessions & photos |
| Atomic transaction blocks | 43 | Every money movement is all-or-nothing |
| Row locks (`select_for_update`) | 8 | Guards cascade payments against races |
| Notification events | 16 | 13 critical (push to phone), 3 informational |
| Notification call sites | 18 | Across 9 modules, one catalogue |
| Permanently-deletable record types | 14 | Each writes a snapshot before deletion |
| Management commands | 14 | Backup, seeding, purge, owner identity, roles, photo sweep |
| Template tags & filters | 16 | Custom, shared across screens |
| Runtime dependencies | 8 | Django, Pillow, psycopg2, WhiteNoise, gunicorn, decouple, pywebpush, coverage |
| Commits | 246 | 11 January – 9 September 2026 |

⚠ **Two rows changed definition rather than value.** Atomic blocks and row locks
are now counted across *application code only*; the August figures of 62 and 10
included the test suite, which sets up its own transactions and locks in order
to prove the real ones hold. Counting a test's transaction as a system
transaction flatters the number and describes nothing.

---

## 07 — Documentation

143,487 words across ten documents, plus a generated one-page system map in
light and dark themes, committed as both HTML and print-exact PDF. Each document
owns a defined set of facts; none restates another's.

| Document | Owns | Lines | Words |
|---|---|---:|---:|
| `CLAUDE.md` | Day-to-day working rules and every deliberate decision | 8,969 | 90,598 |
| `OPERATIONAL_BLUEPRINT.md` | Workflow narrative — who does what, screen by screen | 1,467 | 12,171 |
| `MASTER_BLUEPRINT.md` | The numbers — models, routes, templates, settings | 1,067 | 14,905 |
| `RAILWAY_OPERATIONS.md` | Ongoing platform reference — deploys, backups, cost | 694 | 4,520 |
| `GO_LIVE_RUNBOOK.md` | The one-time go-live procedure and rollback | 608 | 4,409 |
| `TITAN_SPEC_SHEET.md` | This document — every figure, counted from the repository | 755 | 7,255 |
| `TITAN_MASTER_HANDOVER.md` | Mission, roadmap, and what is deliberately out of scope | 456 | 6,527 |
| `master_data_export.md` | The workshop's own brand / model / spare list | 316 | 1,065 |
| `README.md` | Outward-facing summary — features, stack, install | 228 | 1,555 |
| `WorkshopOS_Complete_Structure.md` | The section tree, as the owner describes it | 119 | 482 |
| **10 DOCUMENTS** | | **14,679** | **143,487** |

`TECH_DEBT.md` (196 lines, 3,924 words) is deliberately untracked: it lists what
is known to be wrong and not yet scheduled, which is working state rather than a
published fact.

**`CLAUDE.md` is the unusual one.** 49 major sections carrying **559 stated
rules**, **326 flagged hazards** and **190 pointers to the test that guards each
rule**. It records not just what the system does but which apparent bugs are
deliberate business decisions — so the next person to touch the code cannot
"fix" the business by accident. It has more than doubled in hazards since
August, and every one of those entries was written after a real failure.

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

**One rule, one implementation.** Thirteen modules contain no screens at all.
Each owns exactly one question — the profit maths, the printed document, the
service record, the rent calculation, the settlement checklist, the rename rule,
rupee validation, which day money moved, date-pair validation, the deletion
window, odometer parsing, photo signing, where a page sends you when you leave
it. Nothing else in 138,901 lines is permitted to answer those questions a
second time. The reason is specific: the cost of a spare part was once
calculated in five different places, giving a shop's own page and the profit
page two different answers for the same debt.
*13 modules · 5,230 lines*

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
*14 record types · one shared entry point · every delete notifies both owners.*

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
| **138,901** | lines of code across 432 files, in a single deployable system |
| **2,366** | automated tests — 1.29 lines of test for every line of application code |
| **41** | data models holding 292 fields, reachable through 301 addresses |
| **118** | templates, each built to work on a laptop, a tablet and a phone |
| **13** | rule modules, each the only place in the system that answers its question |
| **174** | access checks across three roles — owner, office, floor |
| **43** | all-or-nothing transaction blocks guarding every movement of money |
| **143,487** | words of documentation across ten documents and a drawn system map |
| **8** | runtime dependencies — no build step, no package manager, no bundler |
| **246** | commits over eight months, by one developer |

> **The one-line version:** a 138,000-line Django system running a premium
> automotive workshop end to end — job cards, inventory, supplier and spare-shop
> ledgers, fleet billing, payroll, rent, estimates, evidence photography, printed
> service records, owner withdrawals and owner analytics — with a test suite
> larger than the application it tests, and every deliberate business decision
> written down with the failure it prevents.

---

## Method

Counted from the working tree on 9 September 2026, on branch `main`, including
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
  querying it. Decorator, transaction and row-lock counts exclude test files —
  two of those excluded the test suite for the first time in this revision, and
  the change is called out in section 06 rather than being allowed to look like
  a fall.
- **Corrections made at source by this run**, in the documents that own each
  fact rather than only here:
  - `MASTER_BLUEPRINT.md` — ten counts in the architecture diagram and file tree
    (models 30→33, view modules 18→21, routes 123→136, filters 13→16, commands
    11→14, templates 83→95, migrations 71→77, test files 49→64, tests
    2,337→2,366, events 14→16); the closing **Total** line, stale on every
    figure; nine test files described nowhere; the inventory model table
    misnumbered 1-3 then 6-10.
  - `TITAN_MASTER_HANDOVER.md` — the test figure (59 files / 1,921), the event
    catalogue, the views package, and a roadmap whose Open items had drifted
    into numbering already used by Delivered ones.
  - **Four documents stated that an owner's sign-in raises only an
    informational alert.** It was changed to critical on 29 August. This was the
    most serious finding of the pass: not a stale count but a confidently stated,
    security-relevant claim that was false.
  - `GO_LIVE_RUNBOOK.md` — a step warning **not** to run `setup_groups`, which
    had been fixed three days earlier and is now the recommended step; and a
    smoke test that predated the rent ledger, both customer documents, the
    question card and the back control.
  - `README.md` — the test figure, two rule modules missing from the project
    layout, and the light-theme system map dropped so only the dark one is
    offered.
  - `SYSTEM_MAP` (all five generated outputs) — the Car Profiles card still
    described itself as "history by registration" after growing two customer
    documents. The map's checker is geometric and reads no words, so nothing
    caught it.
