# 🏛️ TITAN MASTER HANDOVER: WorkshopOS (v8)

> [!IMPORTANT]
> **Status**: 🛡️ SECURITY HARDENED | 🔧 IN ACTIVE DEVELOPMENT (pre-go-live)
> **Last Updated**: 2026-08-10
> **Version**: 8
>
> This is the **mission, status, and roadmap** doc — the single authoritative "Coming Soon" list lives here; other docs link to it instead of keeping their own copy. For exact model/route/template tables see `MASTER_BLUEPRINT.md`; for workflow narrative see `OPERATIONAL_BLUEPRINT.md`; for day-to-day coding conventions see `CLAUDE.md`.

---

## 🏎️ I. THE MISSION

**WorkshopOS** is engineered for a single premium automotive workshop — appointment-driven, high-value vehicles, not high-volume throughput. That distinction matters: the system is built to be fast and correct for a small, hands-on team, not to demonstrate generic "web scale."

- **The Standard**: Functional integrity across all mission-critical operations. The system is backed by a test suite of **40 files / 1,042 tests** (recounted 2026-08-12; re-count rather than trusting this line) covering security, views, signals, financial logic, cashbook operations, spare-shop management, salary settlement, the owner profit engine, and the email transport behind password reset.

---

## 🛡️ II. CORE ARCHITECTURE (The "Steel Gate")

> [!WARNING]
> *This section documents the mission-critical security and data-integrity logic of WorkshopOS. These systems are foundational and must never be broken or bypassed.*

### 1. Sign-in Lockouts — two units (rebuilt 2026-07-28)
- **Primary — per account (`AccountLockout`)**: 5 consecutive failures lock **that one account** for 15 minutes.
- **Backstop — per IP (`FailedAttempt`)**: 20 failures lock the network for 15 minutes. Counted strictly by direct `REMOTE_ADDR`; `X-Forwarded-For` is intentionally ignored to prevent spoofed-IP bypass.
- **Why the split**: the IP threshold used to be 5, and that was the wrong unit for this business. The laptop, the tablet and both owners' phones leave through one connection, so five fumbled attempts on the Floor tablet locked the owners out of their own devices — the attack and the collateral damage were indistinguishable. The account gate is now the precise instrument; the IP gate only catches a spray across many accounts. **Don't lower it back.**
- **Integrity Check**: `workshop/tests/test_login.py` (24 tests) and `workshop/tests/tests.py`.
  *Note for developers: tests must call `FailedAttempt.objects.all().delete()` in `setUp` to prevent cross-test contamination.*

### 1b. Login — one door (rebuilt 2026-07-28, merged to one face 2026-08-12)
- **`/login/` is the only sign-in page, and any role uses it.** It reads `Sign In` / `Identifier` and names no roles. `/admin-login/` redirects to it, kept alive for the owners' bookmarks.
- There were two faces — a blue "Staff Sign In" and a red "Admin Sign In" on the same view. They **gated nothing**, since either accepted any role; they only announced to anyone who typed the address that privileged accounts exist and where their door is. The staff face went further and named the lower tiers in its placeholder. See CLAUDE.md → "Signed-out pages" for the full reasoning and what must not be re-added.
- **Forgot Password is on that one door**, having previously rendered only on the owner face — while the nav bar links to `/login/`, so an owner arriving the ordinary way had no recovery route on screen.
- The old fake "Invalid credentials" shown to owners on the staff face is long gone — the owner door was one button away, so it protected nothing while guaranteeing a confusing support call.
- **Sign in with username, email, or mobile.** `resolve_user_by_identifier` tries each in order and **fails closed** if more than one account matches.
- **Owners sign in by their email address only** (2026-08-12). A mobile number resolves by its last ten digits, so the workshop's published phone was a valid owner identifier — and being nameable at this form is what lets someone lock an owner out five tries at a time. Office and Floor still use their usernames; the password-reset flow still accepts any identifier, on purpose. **Tell the owners this**: typing a username gets "Invalid credentials", which cannot be worded more helpfully without confirming the account exists.
- **RBAC now returns 403, not a login redirect, for signed-in users.** Anonymous visitors still get the sign-in page with `?next=` (validated against open redirects by `_safe_next`). A signed-in user lacking the role gets `templates/403.html`. Both cases used to redirect to a login form, so an Office user opening an Owner page saw a sign-in screen while already signed in.

### 1c. Notifications — in-app feed (added 2026-07-29)
- The nav bell is real: an owner-only feed at `/notifications/` with an unread badge, mark-one-on-open, mark-all-read, and a 14-day sweep of *read* rows (unread are never swept).
- **13 events** (recounted 2026-08-10; eight at launch), all Owner-audience, all declared in one file (`workshop/notifications.py`). **CRITICAL — these push to a phone:** `ACCOUNT_LOCKED`, `PASSWORD_RESET`, `RESET_CODE_LIMIT`, `RESET_CODE_ATTEMPTS_SPENT`, `USER_CREATED`, `USER_DELETED`, `STAFF_PASSWORD_SET`, `HIGH_DISCOUNT`, `RECORD_DELETED`. **INFO — feed only:** `LOGIN`, `ACCOUNT_ARCHIVED`, `SALARY_ADVANCE`, `SALARY_SETTLED`. The five added since launch are all security events: the three Control Hub actions that hand over or revoke access, and the two password-reset abuse signals, which are the only events raised with **no actor** so they reach every owner including the one targeted.
- **`RECORD_DELETED` hooks `DeletionLog.record()`** — the single choke point every permanent delete already passes through, so one call covers all eleven entity types and anything added later for free.
- **The actor is excluded from their own events**, which roughly halves volume with two owners, and Floor receives nothing at all. Notification fatigue is the failure mode here: a bell that cries wolf stops being read, and the events that matter (large discount, permanent delete) are exactly the ones that would be missed.
- `HIGH_DISCOUNT` uses `JobCard.HIGH_DISCOUNT_AMOUNT` (**a flat ₹3,500 since 2026-08-10**, replacing the old 30% `HIGH_DISCOUNT_RATIO`), the same constant as `audit_high_discounts` and the settle dialog, so the audit page, the alert and the confirmation cannot disagree about what "large" means.
- This replaced the SMS/Telegram broadcast described in §2.

### 1d. Web Push — ✅ Added (2026-07-29)
- **`CRITICAL` events push to subscribed devices; `INFO` events wait in the bell.** Push is a *delivery layer* over `Notification` rows that are already written — if the keys are missing, the service is down, or nobody has subscribed, the feed is completely unaffected. That is why it was built last.
- **One subscription per device**, toggled by the small bell in the notification panel header — on (filled, blue) or silent (struck through). Browsers require a real user gesture before they will even ask for permission, which is why it is a button and not automatic.
- **`sw.js` is served from the origin root** by a Django view, never from `/static/` — a service worker's scope is its own directory, so a `/static/sw.js` would only control `/static/` and never receive an app push. Verified live: scope resolves to the site root.
- **Nothing in the request path waits on the network**: `transaction.on_commit` → background thread, so a rolled-back action never announces itself and saving a payment isn't slowed by two HTTPS round-trips.
- Dead endpoints (404/410) are deleted on sight; transient failures are counted and dropped after 3.
- **iOS caveat, unavoidable and by design of Safari**: Web Push only works once the app is added to the Home Screen. In a normal Safari tab the API is absent. The UI detects this and says so in plain language rather than appearing broken.
- **Optional everywhere.** A deploy without `VAPID_*` keys is valid.

### 2. Twilio & Telegram — ✅ Removed (2026-07-29)
- Both channels are **gone**: `send_twilio_sms`, `send_telegram_msg`, `send_titan_security_alert`, the `twilio` and `requests` dependencies, the `TWILIO_*` / `TELEGRAM_BOT_TOKEN` / `OWNER_n_CHAT_ID` env keys, and the root-level `verify_twilio.py` / `verify_alerts.py` scripts.
- Removed **last**, deliberately: the password-reset half went first (§2b, emailed codes), then the in-app feed took over login alerts (§1c) and was verified working, and only then was the channel deleted. A notification channel is never removed before its replacement is proven — otherwise the owners are left with no alert at all in the gap.
- **The app makes exactly two kinds of outbound network call**, both optional and neither on the request path: the password-reset code (SMTP in development, **Resend's HTTPS API in production** since 2026-08-10 — Railway blocks outbound SMTP below its Pro plan) and **Web Push**. Don't reintroduce a messaging integration; push is a delivery layer over the existing `Notification` rows, not a parallel system.

### 2b. Password Recovery — ✅ Rebuilt (2026-07-28)
- **Change Password** (`/change-password/`, Owner-only): a signed-in owner sets a new password with no email involved. This is the **handover path** — an owner gets a temp password verbally, signs in, replaces it. Go-live therefore does not depend on SMTP being configured.
  - **No link to it anywhere in the UI** (owner request, 2026-07-29): owners sign out and use Forgot Password instead. The route is kept precisely because it is the no-email path — **don't delete it as dead code**; the consequence would be that handover requires working email on the day. Reachable directly at `/change-password/`, and `test_change_password.py` asserts both the absent link and the live route.
- **Forgot Password**: a **6-digit code emailed** to the owner's registered address (`User.email`), replacing the old `.env`-driven SMS/Telegram OTP. Identify by username, email, or mobile.
- **Why a code and not Django's built-in reset link**: on iOS an installed PWA has its own cookie jar, so a link tapped in the mail app completes the reset in a *different* session and leaves the app signed out. A code has no such dependency. Recorded in `CLAUDE.md`'s deliberate decisions — do not "simplify" it back.
- Every limit (10-min expiry, single use, 5 attempts, 60s resend, 3/hour) is counted **per account in the database**, not in the session, because a session counter is defeated by clearing cookies — which would let someone burn the sending quota. Responses are identical whether or not the account exists.
- Owner identity now lives in the **database**, not `.env` — so adding a third owner or changing an address needs no code change and no deploy (`sync_owner_identity`, `set_owner_email`).

### 3. Hardware Fingerprinting & Session Command (`UserSession`)
- **Device Parsing**: Decodes raw HTTP User-Agent strings into human-readable device names (e.g., *Apple Safari on iPhone*).
- **The HQ Kill Switch**: From the management dashboard, Owners have full visibility over active staff sessions (40-day window) and can remotely terminate any unauthorized session.

### 4. The Warehouse Pulse (Stock Delta Engine)
- **Mechanism**: Django Signals (`inventory/signals.py`) orchestrate stock synchronization across **three independent groups (8 handlers)**, all using the same pre_save-snapshot + post_save-delta pattern: Workshop Consumption (3 — replacement, quantity adjustment, deletion), JobCard Soft-Delete Reversal (2 — historically returned a soft-deleted card's spare stock and re-deducted on restore; **now dormant**, since job cards are hard-deleted and the delete guard forbids deleting a card that still holds spares), and Supplier Restocking (3 — creation, edit, deletion).

### 5. Owner Analysis & Reports — ✅ Rebuilt from scratch (2026-07-27)
- The old 7-zone placeholder system was **deleted entirely** and replaced with two pages:
  - **`/analysis/` — Profit.** `Total Turnover − Total Expenses = Profit` for one date window, with the equation shown literally. This is the page the owners use for **profit distribution**, so it is deliberately kept plain and protected. Filters: This Month / Last Month / This Year / Last Year / All Time / Custom.
  - **`/analysis/insights/` — Deep Analysis.** Mechanics, Spares, Vehicles, Fleet, Shops, Operations — one AJAX-loaded section at a time.
- **Turnover** = Car Bills (`total_bill_amount − discount_amount`) + Cashbook Income. A discount is money never earned, so it reduces turnover rather than appearing as an expense; for a settled card the result equals `received_amount` to the rupee.
- **Expenses** are four non-overlapping money-out streams: Spare Shops (parts bought per job), Supplies Shops (warehouse restock bills), Salary & Advance (wages, advance-aware), General Cashbook (rent/power/etc., broken down by category).
- **The double-count rule**: a warehouse-drawn spare is *already* paid for by its restock bill, so its cost is never charged again. Counting all job-spare cost on top of restock bills would have overstated expenses by ~₹9.8M against live data. Guarded by `DoubleCountRuleTests`.
- **Separation of concerns**: all money math lives in `workshop/analysis_engine.py` as pure functions; views only resolve the window and render, so a charting bug can never become a profit bug. `monthly_series()` is asserted to total exactly to `build_profit_report()`.
- **XSS Prevention**: Strict prohibition of legacy `{{ variable|safe }}`. All JavaScript data injections use Django's `json_script` serialization.

### 6. Billing Architecture & Bulk Payer / "Fleet Account" Cascade
- **Locking**: `select_for_update()` inside `transaction.atomic()` ensures atomic operations when a payment cascades across multiple unpaid job cards, oldest-first.
- **Advance credit**: `BulkPayer.advance_balance` banks any surplus when a lump-sum payment exceeds what's currently owed, and is automatically pooled into the next payment — `total_balance` can legitimately show negative (in credit). The UI labels this feature "Fleet Account"; the model/field/URL names are unchanged.
- **Financial Precision**: All monetary columns strictly enforce `DecimalField(max_digits=10, decimal_places=2)`. `FloatField` is prohibited.
- **Referential Integrity / deletion model**: Accounts that other records reference (Spare Shops, Fleet Accounts, Supplier Shops, Mechanics) are **deactivated/archived**, never hard-deleted — protecting their financial ledgers. Transactions and job cards are **permanently deleted but snapshotted first** to the Owner-only, read-only `DeletionLog` (Deletion History); a guard blocks deleting a job card that still holds spares/labour/payment. The only `on_delete=PROTECT` in the codebase is inventory `Category → Item`. *(An earlier version of this doc claimed financial FKs use `PROTECT`/`ProtectedError` — that was inaccurate; they are `CASCADE`/`SET_NULL`, and safety now comes from the deactivate-vs-log-and-delete structure.)*
- **Dedicated Ledgers**: Split `Pending Bills` / `Paid Bills` architectures with time-range filters (see `OPERATIONAL_BLUEPRINT.md` §13) and strictly enforced RBAC.

---

## 🚀 III. PERFORMANCE ENGINEERING

WorkshopOS uses deliberate, standard performance patterns rather than ad hoc queries — appropriate headroom for a workshop's real volume, not a claim of internet-scale throughput we haven't measured:

> [!TIP]
> **Performance Guardrails**
> - **Server-Side Pagination**: All major list views paginate (45 items for lists, 10 for category grids) instead of loading full tables.
> - **Query Hardening**: `select_related`/`prefetch_related` used throughout to eliminate N+1 query latency.
> - **Zero-Query Properties**: Methods like `get_completion_percentage` check for pre-annotated fields before hitting the database.
> - **Denormalized Financials**: `JobCard.total_bill_amount` is a physical database column, updated via `update_totals()` during part/labour saves rather than computed at read time.
> - **Indexing**: `db_index=True` on high-traffic lookup fields (`is_deleted`, `registration_number`, `admitted_date`, `completed`, `updated_at`), plus a composite index on `[is_deleted, completed, -updated_at]` for the dashboard query pattern.
>
> These are real, verifiable-in-code optimizations. No load testing at extreme scale (e.g. 1M+ rows) has been performed against this dataset — if that claim is ever needed for a specific deployment, it should be backed by an actual benchmark, not asserted here.

---

## 🔧 IV. OPERATIONAL COMMANDS

*Run these commands to verify system integrity at any time.*

- **Full Integrity Audit**:
  ```bash
  .\venv\Scripts\python.exe manage.py test workshop inventory
  ```
- **Test Coverage**: 28 test files / 470 tests — workshop (25, in the `workshop/tests/` package) and inventory (3).

---

## 🧹 V. THE PRISTINE WORKSPACE

- **Core-Only Architecture**: The repository root contains application code, migration files, and documented standards.
- **Environment Isolation**: Secrets live in `.env` — `SECRET_KEY`, the PostgreSQL credentials, and the SMTP sending mailbox (an App Password, not an account password). Owner *identity* deliberately does **not**: usernames, mobiles and email addresses are database rows, so adding an owner or changing an address needs no deploy.
- **Split Settings**: `settings/` package selects development or production via `DJANGO_ENV`, which has no default — an unset value raises `ImproperlyConfigured` rather than silently choosing a database. **Both environments run on PostgreSQL** — development on Neon, production on Railway's own Postgres in the same project as the app; SQLite is used only for bulk dummy-data seeding (`USE_SQLITE=true`) and automatically for `manage.py test`.
- **Modular Views**: The `workshop` app's views live in a `views/` package (17 focused modules), maintaining full backward compatibility via re-exports in `__init__.py`.
- **Deployment**: Railway (app + PostgreSQL in one project), behind `app.formuladservice.in`. Mail leaves over Resend's HTTPS API — Railway blocks outbound SMTP below the Pro plan. Procedure in `GO_LIVE_RUNBOOK.md`, ongoing operation in `RAILWAY_OPERATIONS.md`.

---

## 🔜 VI. ROADMAP — CURRENT PRIORITIES

*The single authoritative list. Update here first; other docs link to this section instead of keeping their own copy.*

In the order set as of 2026-07-23:

1. ✅ **Documentation accuracy pass** — bring CLAUDE.md, MASTER_BLUEPRINT.md, OPERATIONAL_BLUEPRINT.md, README.md, and this handover back in sync with the actual codebase after several undocumented commits. *(This update.)*
2. ✅ **Staff Registration** (added 2026-07-26) — `Mechanic` model gained a `role` field (Mechanic / Assistant Mechanic / Office Staff / General Helper), giving the workshop one staff roster instead of a mechanics-only list. Lives at `/manage/?section=staff`; only Mechanic/Assistant Mechanic feed the Job Card mechanic picker. See CLAUDE.md's "Deliberate decisions" for why the model keeps the `Mechanic` name.
3. ✅ **Salary & Advance** (added 2026-07-27) — cash advances recorded the day they happen, plus a month-end settlement that freezes each staff member's salary/leave/advance/net into a `SalaryPaymentLine`. Built against the same staff roster from #2, so a person's history survives a role change. Lives at `/salary-advance/`; a settled month's figures never move afterwards, even if the salary changes later.
4. ✅ **Estimates** (added 2026-08-05) — quotations on the workshop's own letterhead: write one, print it, keep every one in a searchable history (`EST-26-001`). Built on the invoice's printing module (`workshop/invoice.py` now owns `build_invoice` *and* `build_estimate`), so a quote and the bill that follows it can never disagree about a blank quantity, an unpriced part, or how labour is subtotalled. **Deliberately connected to nothing else** — no job card, no stock, no ledger, no report; see CLAUDE.md's "Deliberate decisions" for why, and for why an estimate delete writes no `DeletionLog` row. Includes a suggested unit price (average of a part's last five bills) shown as a *placeholder only*.
5. ~~**Attendance**~~ — **moved out of scope 2026-08-10.** Not planned; see §VII. Leave days stay typed at settlement, which is what a seven-person shop needs.
6. **Noted fixes** — already-identified issues to be resolved during hardening:
   - ~~**Supplier-Shop RBAC asymmetry**~~ (flagged 2026-07-23) — ✅ **resolved.** Verified 2026-08-10: every view in `inventory/views_suppliers.py` is `@office_required`, including all seven destructive ones (`remove_shop_catalog_item`, `update_bill_discount`, `shop_restock_bill`, `edit_restock_bill`, `delete_restock_bill`, `add_shop_payment`, `delete_shop_payment`). Floor has no supplier access at all. This entry described `@staff_required` and was stale.
   - *(Add further noted issues here as they're identified, so "fix later" items have one durable home.)*
7. ~~**Auth & notifications rebuild**~~ — ✅ **complete 2026-07-29**, delivered in six ordered phases so each left a working system:
   - Owner identity moved from `.env` into the database; the `Owner` group was **empty** and every group-based query silently returned nobody (§II.2b)
   - **Change Password** for signed-in owners — the handover path, needs no email at all
   - **Emailed 6-digit reset code**, DB-backed and throttled per account, replacing the SMS/Telegram OTP (§II.2b)
   - **Login rebuilt**: one engine behind two faces, sign in by username/email/mobile, per-account lockout, 403 instead of a redirect loop (§II.1, §II.1b)
   - **Control Hub locked to Owners** + one-click unlock for locked staff accounts
   - **In-app Notification feed** — eight events at the time; **13 as of 2026-08-10** (§II.1c) — and only then —
   - **Twilio and Telegram deleted** (§II.2). The order was the point: the replacement was proven before the channel was removed.
   - **Web Push** (§II.1d) — added once the app was hosted on Render (HTTPS is a hard requirement). CRITICAL events only; the in-app feed remains the source of truth.
   - *Remaining*: enable it on each owner's real device. On iPhone that means **Add to Home Screen first**, then open the installed app and use the button on `/notifications/` — Safari does not expose Web Push to a normal browser tab.
8. ~~**Owner Analysis & Reports — full rebuild**~~ — ✅ **done 2026-07-27**: rebuilt from scratch as a protected Profit page plus a separate Deep Analysis page (see §II.5).
9. ✅ **PostgreSQL migration** (done 2026-07-27) — both `development` and `production` now run on PostgreSQL. Development is Neon (Singapore); production moves to Railway's own Postgres at go-live, co-located with the app. SQLite is retained for exactly two jobs: bulk dummy-data seeding (`USE_SQLITE=true`, then `copy_sqlite_to_postgres`) and the test suite, which forces SQLite automatically so the runner never CREATEs/DROPs a database on hosted Postgres. Still pre-go-live: the instance holds demo data, and `purge_business_data` is the documented step before real books go in.
10. **Frontend polish** — raise the visual/UX bar across the app to match the backend's rigor.
11. **Stability, security, performance, and code quality hardening** — pushing all four toward production-grade across both apps.
12. **Keep every financial and security rule under test** — not a coverage percentage. The **956** existing tests (counted 2026-08-10) already cover the money and the access rules, which is where the risk is; chasing a number buys tests for template rendering and Django's own internals. Add a test when a rule is added or a bug is fixed, not to move a metric.
13. **Deep debug pass**.
14. ✅ **Repo cleanup** (done 2026-08-10) — five unreferenced files removed: `API_DOCUMENTATION.md` and `TECH_INFO.md` (both described Twilio/Telegram, soft-delete-with-Trash and SMS 2FA as the *current* system, and the second opened by telling AI agents to copy those exact patterns), `TITAN_BLUEPRINT.html` (a v7 render), `migrate_to_postgres.py` (superseded by `copy_sqlite_to_postgres`) and `_phase3_audit.py` (a scratch script). Every count in `MASTER_BLUEPRINT.md` was recounted from the code in the same pass — models, routes, templates, migrations and test files had all drifted.
15. **Hosting** — *in progress.* The system runs on Railway at a temporary URL; static-file serving, the build/pre-deploy commands and the email transport are done. Remaining: the production project on the Hobby plan, DNS for `app.formuladservice.in`, Resend verification, and the go-live steps themselves. Procedure: `GO_LIVE_RUNBOOK.md`. Ongoing operation: `RAILWAY_OPERATIONS.md`.
16. **Post-review task list** — the owner's own verified list, 2026-08-10. Supersedes the
    Antigravity-generated `QA_VERIFICATION_REPORT.txt`, which was built from an older
    draft and was **deleted on 2026-08-10** once every one of its 17 items had been
    checked against the code; the four that were still open moved to `TECH_DEBT.md` as
    `AUD-0089`–`AUD-0092`. **Statuses below were re-verified against the code —
    15 of 18 done.** Items 1–15 came from the owner on 2026-08-10 and were verified
    that day; 16–18 were raised on 2026-08-11. Still open: **2** (push / install
    banner, re-test after the domain move), **3** (mobile scale — a design decision,
    not a bug) and **11** (master-data click-through, a proposal to weigh).
    1. ✅ **Repeated OTP requests and wrong-OTP submissions raise no notification —
       FIXED 2026-08-10.** `RESET_CODE_LIMIT` and `RESET_CODE_ATTEMPTS_SPENT`, both
       CRITICAL, both raised with **no actor** so they reach every owner including the
       one targeted. Only the hourly limit fires (the 60-second cooldown is a
       double-tapped button), de-duped to one per account per hour so the form cannot be
       used to buzz two phones. The visitor's response is byte-identical either way —
       `test_raising_an_alert_changes_nothing_the_visitor_can_see`.
    2. ⏳ Push not working on Android on Render's free tier; the "Install as app" banner does
       not appear on `railway.app`. **Both may resolve with the move to a custom domain on
       Railway — re-test before investigating.** Partly addressed already: the service
       worker is now registered on **every** page load rather than only when an owner
       enables push, which is what Chrome's automatic install prompt requires. Note
       registration, install state and push subscriptions are all **per-origin**, so
       every device must re-enable push after the move regardless. Verification steps are
       `GO_LIVE_RUNBOOK.md` §(post-deploy device checks).
    3. ⏳ Mobile scale/layout: correct in the browser's device emulator, too small on a real
       phone. **Still open — and it is a design decision, not a bug.** Measurements have
       been taken; the approach has not been chosen.
    4. ✅ **High-discount alert at ₹3,500, not 30% — DONE 2026-08-10.**
       `JobCard.HIGH_DISCOUNT_AMOUNT = Decimal('3500')` replaced `HIGH_DISCOUNT_RATIO`
       and is read by `audit_high_discounts`, the `HIGH_DISCOUNT` event and the settle
       dialog, so none can disagree about where the line is. The settle screen now also
       says what the shortfall *becomes* — a discount — with the confirmation firing only
       past the threshold. Guarded by `ALargeDiscountIsConfirmedBeforeItHappensTests`.
    5. ✅ **Bill and Estimate PDF filenames — DONE.** `document_title()` in
       `workshop/invoice.py` builds the `<title>`, which is what the browser's "Save as
       PDF" uses as the filename, for both documents from one function. Covered by tests
       in `test_invoice.py` and `test_estimate.py`.
    6. ✅ **Spare Shop ⋮ to the top-right on mobile — DONE 2026-08-10.** Settled on the
       second attempt: below 768px the actions take their own row, aligned right, so the
       shop *name* keeps the full width. The first attempt pinned them beside the title
       and truncated the name, which is the one piece of text that says what you are
       looking at.
    7. ✅ **Supplies Shop payment-history delete inside a ⋮ menu — DONE 2026-08-10.** Both
       payment histories now share markup exactly, and the tests assert the **parity**
       rather than either implementation. Two real defects surfaced while doing it: the
       spare-shop delete was gated on `Owner` while its view is `@office_required` (so it
       was hidden from the role whose job it is), and the "Bulk Pay" badge printed on
       every supplier payment, distinguishing nothing.
    8. ✅ **Job Card "Stock" and "Shop" buttons opening a new window — FIXED.** No
       `target="_blank"` remains in `jobcard_form.html`; the surviving mention is the
       comment recording why it was removed.
    9. ✅ **Home header blank instead of `0` — FIXED.** `{{ page_obj.paginator.count }}`
       renders directly. The cause was `|default:` treating a legitimate `0` as falsy and
       falling back to an attribute a `Page` object does not have, which `string_if_invalid`
       then rendered as an empty string.
    10. ✅ **Job Card placeholders too prominent — DONE.** `::placeholder` is now smaller
        (`0.86em`, so it tracks the input's own size), lighter and muted, and fades
        further on focus. Colour and font-size only — nothing that touches layout, and no
        `transition: all` near a form control.
    11. ⏳ **Master data: replace rename/merge with delete-only plus click-through.** Proposal
        is to drop editing and merging entirely, and instead click a master-list entry to
        see the job cards using it and correct them one at a time. Trade to weigh: it
        removes all bulk mutation (and the merge that is *not cleanly undoable*), at the
        cost of manual work per card. Note the current merge is deliberately scoped to
        `source=SHOP` and uses `.update()` so no signals fire — per-card editing goes
        through the normal save path, so an `INVENTORY` row would behave differently.
    12. ✅ **Salary shown against a settled month — FIXED 2026-08-10.** Two defects, one
        root cause: the settlement screen and its POST loop both walked the live roster
        regardless of whether the month was settled, so anyone hired after a settlement
        appeared on it priced at today's salary. A month captioned "Closed — paid and
        settled" showed a "Pay now" of ₹5,55,000 that was never paid, and re-saving the
        newest settlement would have written that figure as a real line. Stored data was
        never affected — `salary_expense` reads `SalaryPaymentLine` only. A settled month
        now renders from its own lines, the POST skips anyone without one, and the
        template gates on the frozen figure rather than the live salary (which also fixed
        a settled card rendering blank after a salary was cleared). See CLAUDE.md,
        "A SETTLED month is a closed set of people"; guarded by
        `ASettledMonthIsAClosedSetOfPeopleTests`.
    13. ✅ **Protected pages visible via the browser Back button after logout — FIXED
        2026-08-10.** `NoStoreMiddleware` sets `no-store` on authenticated responses. The
        session was genuinely dead all along; this was the browser's back/forward cache
        repainting a fully-rendered page — the dashboard, a customer's bill, the Profit
        page — on a laptop now in someone else's hands. Nothing server-side can undo that
        after the page has been sent, so telling the browser not to keep it is the only
        lever. Accepted cost: Back re-fetches instead of restoring instantly.
    14. ✅ **Salary-advance notification destination — FULLY fixed 2026-08-11.** Two
        halves, and the first one alone did nothing for the owner. `SALARY_ADVANCE` was
        repointed at the section with that person's history opened, instead of
        `salary_advance_staff_detail` — the AJAX fragment the modal fetches, which
        extends no base template and so rendered as a bare unstyled scrap with no nav
        and no way back. But **a notification stores its `url` and keeps it forever**,
        so every alert raised before that change still arrived at the old view; the
        owner reported it after the "fix" precisely because the alert they tapped
        predated it. The URL itself now serves a **real page**, and returns the bare
        fragment only when `X-Requested-With: XMLHttpRequest` is set, so the modal is
        unchanged. Guarded by `TheStaffAdvancePageOpensAsAPageTests`. The general rule
        this produced is in `CLAUDE.md`: before changing a notification's url, ask what
        happens to the ones already sent.
        **Redesigned the same day, on the owner's own account of the four-second
        read:** who is this → how much just now → how much this month, and nothing
        else. The first version added the staff role, a month-grouped history list and
        a row-cap notice, all correct and all in the way — the history already lives in
        the ⋮ modal one tap away. The notification now names the exact advance with
        `?advance=<pk>`; where that is missing (every alert sent before 2026-08-11) the
        newest stands in and is labelled "Latest advance" instead of "Advance given".
    15. ✅ **Sound on important actions — DONE 2026-08-10, scoped as asked.** Four
        synthesised tones (Web Audio, **no audio files and no dependency**) riding on
        Django's own message tags, so one attribute covers every outcome in the app and
        anything added later. Deliberately **not** wired per-button: ~180 call sites is
        180 chances to attach the wrong tone, and each would fire at *click* time,
        announcing "done" before the server had done anything. `info`/`debug` are silent,
        which is what stops the two that matter from being tuned out. Per-device toggle in
        the drawer, default ON. Three views that had been silent (`mark_completed`,
        `undo_completed`, `toggle_hold`) now report themselves, which is what earns them a
        sound.

    *Raised 2026-08-11, after the list above:*

    16. ✅ **A PAID box on a settled bill — DONE 2026-08-11.** Small green box under
        TOTAL, outside the totals table, carrying what was actually received; absent
        entirely until the card reaches PAID/BULK_PAID. `settlement()` in
        `workshop/invoice.py` decides — see `CLAUDE.md` for why the template must not,
        and why the discount is deliberately never printed.
    17. ✅ **SUBTOTAL amounts in bold — DONE 2026-08-11.** Applied to the estimate as
        well as the invoice: the two are byte-identical here and are handed to the same
        customer days apart, so letting them diverge on typography is the drift
        `workshop/invoice.py` exists to prevent, showing up in the stylesheets instead.
    18. ✅ **The real logo on the bill — DONE 2026-08-11.** The wordmark was Arial
        Black italic: a close approximation (the logo *is* a heavy oblique grotesque —
        it is **not** Racing Sans One, which has flared calligraphic terminals) but
        never the logo, and it printed "Diagnosis & Service" where the mark reads
        **Diagnosis&Service**. It is now the client's own artwork as inline SVG from one
        include shared by the invoice and the estimate.
        **Two traced SVGs were tried and both were dropped**, which is the part worth
        keeping. The first was 98% anti-aliasing noise — letterforms shredded into grey
        bands (`#d4d4d4` 40%, `#aaa` 26%, `#555` 11%) across 20 colours. The second was
        genuinely clean (19 paths, all near-black or near-red, no greys) and shipped for
        half a day — but a trace approximates letterforms by construction, and it
        rendered at **3.73:1 against the artwork's true 4.40:1**, a 15% vertical stretch
        the owner saw at once. The owner's own optimized PNG went in instead, inlined as
        a data URI so nothing is ever fetched.
        Measured against the running bill by splitting its letterhead ink into bands:
        the real lockup is **55.6mm × 13.3mm**; ours renders **56 × 12.74mm**. The file
        needed cropping to its ink (the canvas carried padding), its 253-grey background
        lifted to white, and resampling to 600 DPI — then a 16-colour palette took it
        from 130KB to 38.6KB. Regenerate with `scratchpad/build_logo_png.py`.
        *Not changed:* the page's own margins are 12mm against the reference bill's
        14.7mm left / 17.9mm top. That is the sheet's pre-existing padding, not the
        logo, and moving it would shift every element on both documents.


---

## 🚫 VII. DELIBERATELY OUT OF SCOPE

*Recorded 2026-08-10. This system is built for one workshop, to that workshop's
actual working rhythm. The following are **not missing features** — each was
considered and left out. They will be built only if the client asks for them.*

| Not built | Why |
|---|---|
| **GST / tax invoicing** | The workshop does not bill under GST. No tax fields, no HSN codes, no GSTIN anywhere in billing — see `workshop/invoice.py` for what the customer actually receives. |
| **Customer-facing notifications** (SMS / WhatsApp / email to car owners) | The app makes exactly **one** kind of outbound network call: SMTP, for owner password-reset codes. Twilio and Telegram were deleted on 2026-07-29. Do not reintroduce a messaging integration. |
| **Attendance tracking** | Leave days are typed once per person at month-end settlement. For seven staff, that is less work than maintaining a daily attendance record. |
| **Multi-mechanic assignment** | A job card has one `lead_mechanic`. Work is assigned verbally on the floor; the card records who owns the job, not everyone who touched it. |
| **Car photos / attachments** | No upload, no file storage, no image handling anywhere in the app. This is also why the deployment needs no media backend. |

**For reviewers — human or AI:** proposing any of the above is proposing
**scope**, not reporting a defect. If a review flags one as "missing", the
correct response is to point here, not to build it. The same applies to the
frontend architecture note in `CLAUDE.md` § Deliberate decisions.

---

## 💡 VIII. AI & DEVELOPER INSTRUCTIONS (The "Titan" Creed)

1. **Maintain the Standard**: "Fix the code, not the tests." If a test fails, the logic is likely wrong. Never bypass a security test.
2. **Industrial Grade Aesthetics**: No placeholders. No generic colors. Use harmonious color palettes (HSL), responsive layouts, and professional typography. The UI must match the premium quality of the backend.
3. **Titan Integrity**: Every new feature **must** be accompanied by new `assertEqual` tests covering edge cases. **One honest exception: nothing in the suite executes JavaScript** — there is no Playwright, Selenium or jest, and none is planned. So a JS change leaves the suite green whether or not it broke, and must be verified by hand in the browser on the page it touches. Treat that as a reason to keep JS changes small, not as a reason to add a test runner.
4. **Communicate like a Titan**: Commit messages and documentation must be concise, professional, and confident — and accurate. Overstated or unverified claims (e.g. performance numbers with no benchmark behind them) undermine the doc's credibility; state what's actually true.
5. **Keep docs in sync**: When a change touches more than trivia (new model/field, new route, new workflow, a roadmap item completed), update the owning doc in the same session — see the doc ownership map in `CLAUDE.md`. This is what let the docs drift four commits stale before this update; don't let it happen again.

> **WorkshopOS: Stable. Secure. Scale-Ready.** 🛰️🏎️💨
