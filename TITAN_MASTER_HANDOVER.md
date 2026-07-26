# 🏛️ TITAN MASTER HANDOVER: WorkshopOS (v7.2)

> [!IMPORTANT]
> **Status**: 🛡️ SECURITY HARDENED | 🔧 IN ACTIVE DEVELOPMENT
> **Last Updated**: 2026-07-23 (commit `a34537c`)
> **Version**: 7.2
>
> This is the **mission, status, and roadmap** doc — the single authoritative "Coming Soon" list lives here; other docs link to it instead of keeping their own copy. For exact model/route/template tables see `MASTER_BLUEPRINT.md`; for workflow narrative see `OPERATIONAL_BLUEPRINT.md`; for day-to-day coding conventions see `CLAUDE.md`.

---

## 🏎️ I. THE MISSION

**WorkshopOS** is engineered for a single premium automotive workshop — appointment-driven, high-value vehicles, not high-volume throughput. That distinction matters: the system is built to be fast and correct for a small, hands-on team, not to demonstrate generic "web scale."

- **The Standard**: Functional integrity across all mission-critical operations. The system is backed by a test suite spanning **19 test files** covering security, views, signals, financial logic, cashbook operations, spare shop management, and owner analytics.

---

## 🛡️ II. CORE ARCHITECTURE (The "Steel Gate")

> [!WARNING]
> *This section documents the mission-critical security and data-integrity logic of WorkshopOS. These systems are foundational and must never be broken or bypassed.*

### 1. IP-Based Security & Lockout (`FailedAttempt`)
- **Mechanism**: The system captures and tracks login failures strictly by the direct **Network IP** (`REMOTE_ADDR`). `X-Forwarded-For` proxy headers are intentionally ignored to prevent client-side IP spoofing bypasses.
- **The Rule**: 5 consecutive failed attempts trigger a global 15-minute lockout for that IP address.
- **Integrity Check**: Verified in `workshop/tests/test_auth.py`.
  *Note for developers: Tests must call `FailedAttempt.objects.all().delete()` in `setUp` to prevent cross-test contamination.*

### 2. Dual-Channel Notification System (⚠️ Legacy — Replacement Planned, see Roadmap §VI)
- **Mechanism**: Any successful authentication triggers alert broadcasts to **both** business owners via Telegram Bot API + Twilio SMS, carrying username, device fingerprint, and network IP.
- This is explicitly legacy — don't invest further in it. Full mechanism details live in `CLAUDE.md` and `MASTER_BLUEPRINT.md` §3.

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
- **Test Coverage**: 19 test files across workshop (16, in the `workshop/tests/` package) and inventory (3).

---

## 🧹 V. THE PRISTINE WORKSPACE

- **Core-Only Architecture**: The repository root contains application code, migration files, and documented standards.
- **Environment Isolation**: All critical credentials (Owner mobile numbers, Telegram Chat IDs, Twilio keys) are strictly segregated into the `.env` file.
- **Split Settings**: `settings/` package auto-selects development (SQLite) or production (PostgreSQL, configured but not yet the live database) via `DJANGO_ENV`.
- **Modular Views**: The `workshop` app's views live in a `views/` package (13 focused modules), maintaining full backward compatibility via re-exports in `__init__.py`.

---

## 🔜 VI. ROADMAP — CURRENT PRIORITIES

*The single authoritative list. Update here first; other docs link to this section instead of keeping their own copy.*

In the order set as of 2026-07-23:

1. ✅ **Documentation accuracy pass** — bring CLAUDE.md, MASTER_BLUEPRINT.md, OPERATIONAL_BLUEPRINT.md, README.md, and this handover back in sync with the actual codebase after several undocumented commits. *(This update.)*
2. ✅ **Staff Registration** (added 2026-07-26) — `Mechanic` model gained a `role` field (Mechanic / Assistant Mechanic / Office Staff / General Helper), giving the workshop one staff roster instead of a mechanics-only list. Lives at `/manage/?section=staff`; only Mechanic/Assistant Mechanic feed the Job Card mechanic picker. See CLAUDE.md's "Deliberate decisions" for why the model keeps the `Mechanic` name.
3. ✅ **Salary & Advance** (added 2026-07-27) — cash advances recorded the day they happen, plus a month-end settlement that freezes each staff member's salary/leave/advance/net into a `SalaryPaymentLine`. Built against the same staff roster from #2, so a person's history survives a role change. Lives at `/salary-advance/`; a settled month's figures never move afterwards, even if the salary changes later.
4. **Attendance** (next) — likely against the same staff roster; the leave-days figure currently typed by hand at settlement is the natural thing for it to feed.
5. **Noted fixes** — already-identified issues to be resolved during hardening:
   - **Supplier-Shop RBAC asymmetry** (flagged 2026-07-23): every Supplier-Shop view in the Inventory app is `@staff_required`, so Floor mechanics can create/delete supplier restock bills and delete supplier payment records — broader than the sibling Spare-Shop module, which restricts destructive actions to Office/Owner. Decide whether Floor should keep full access (small-shop trust) or whether destructive supplier ops should require Office/Owner; if tightening, add tests. See `OPERATIONAL_BLUEPRINT.md` §5B.
   - *(Add further noted issues here as they're identified, so "fix later" items have one durable home.)*
6. **New OTP system** — a proper OTP-based flow, superseding today's ad hoc SMS+Telegram forgot-password OTP and informing the eventual replacement of the legacy dual-channel login-alert system (§II.2).
7. ~~**Owner Analysis & Reports — full rebuild**~~ — ✅ **done 2026-07-27**: rebuilt from scratch as a protected Profit page plus a separate Deep Analysis page (see §II.5).
8. **PostgreSQL migration** — cut the live database over from SQLite to PostgreSQL. `settings/production.py` is already fully configured for this; the migration itself hasn't happened yet.
9. **Frontend polish** — raise the visual/UX bar across the app to match the backend's rigor.
10. **Stability, security, performance, and code quality hardening** — pushing all four toward production-grade across both apps.
11. **Test coverage toward 100%**.
12. **Deep debug pass**.
13. **Repo cleanup** — get the workspace hosting-ready (see §V).
14. **Hosting** — deploy the live system.

---

## 💡 VII. AI & DEVELOPER INSTRUCTIONS (The "Titan" Creed)

1. **Maintain the Standard**: "Fix the code, not the tests." If a test fails, the logic is likely wrong. Never bypass a security test.
2. **Industrial Grade Aesthetics**: No placeholders. No generic colors. Use harmonious color palettes (HSL), responsive layouts, and professional typography. The UI must match the premium quality of the backend.
3. **Titan Integrity**: Every new feature **must** be accompanied by new `assertEqual` tests covering edge cases.
4. **Communicate like a Titan**: Commit messages and documentation must be concise, professional, and confident — and accurate. Overstated or unverified claims (e.g. performance numbers with no benchmark behind them) undermine the doc's credibility; state what's actually true.
5. **Keep docs in sync**: When a change touches more than trivia (new model/field, new route, new workflow, a roadmap item completed), update the owning doc in the same session — see the doc ownership map in `CLAUDE.md`. This is what let the docs drift four commits stale before this update; don't let it happen again.

> **WorkshopOS: Stable. Secure. Scale-Ready.** 🛰️🏎️💨
