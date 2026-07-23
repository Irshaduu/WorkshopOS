# WorkshopOS: API & Core Engineering Patterns (v7.1)

This document outlines the core technical patterns used in WorkshopOS.

---

## I. High-Performance Engineering

### 1. Database Optimization
Every view that handles lists of objects (Job Cards, Inventory, Search) must use **Server-Side Pagination** and **Greedy Query Mapping**.

- **Goal**: Reach sub-50ms database retrieval even with 1,000,000+ records.
- **Pagination Rule**: Standardized at 45 items per page across dashboard and lists to align with UI grid dimensions.
- **Example**:
  ```python
  JobCard.objects.all().select_related('lead_mechanic').prefetch_related('spares', 'labours')
  ```

### 2. The "Tiny Search" Pattern
To find any vehicle among millions, always filter by **B-Tree indexed fields**.
- **Indexed Fields**: `registration_number`, `bill_number`, `brand_name`, `model_name`, `admitted_date`, `is_deleted`, `completed`, `updated_at`.
- **Search Execution**: Use `Q` objects with `icontains` for partial matches. Split multi-word queries for cross-column matching.

### 3. Composite Database Index
The dashboard query pattern is covered by a composite index for maximum performance:
```python
class Meta:
    indexes = [
        models.Index(fields=['is_deleted', 'completed', '-updated_at']),
    ]
```

### 4. Denormalized Financials
`JobCard.total_bill_amount` is a physical column, not a computed value. Updated automatically via `update_totals()` on every spare/labour save or delete. 
*(Note: Now strictly enforced across all dashboard and pending payment views using `F('total_bill_amount')` to eliminate correlated subquery N+1 bottlenecks).*

### 5. Architectural Isolation (Dedicated Ledgers)
To maintain peak performance as data grows, the billing architecture strictly isolates **Pending Bills** from **Paid Bills**. This separation (introduced in v7.1) eliminates complex conditional database filtering, enabling each dedicated ledger to process its dataset efficiently with specialized time-range filters and identical search patterns.

### 6. Zero-Query Property Methods (N+1 Prevention)
Model properties like `JobCard.get_completion_percentage` actively check if the required aggregate data (e.g., `total_concerns`, `fixed_concerns`) has been pre-annotated onto the instance by the view. If present, it calculates the result in-memory. It only falls back to a database `.count()` if the annotations are missing. This pattern eliminates severe N+1 query bottlenecks on heavy dashboards.

### 7. Absolute Financial Precision
To prevent floating-point arithmetic drift (especially critical in the Cascade Payment Algorithm), all monetary columns (e.g., `total_price`, `grand_total`, `received_amount`) must strictly use `models.DecimalField(max_digits=10, decimal_places=2)`. `FloatField` is strictly prohibited for any financial data.

### 8. Referential Integrity & Safe Deletions
WorkshopOS prioritizes historical audit trails over cascading deletions. 
- **Rule**: Critical foreign keys (e.g., `lead_mechanic` on JobCards) use `on_delete=models.PROTECT`.
- **Handling**: Views must explicitly catch `django.db.models.ProtectedError` and return a user-friendly error message rather than allowing a 500 Server Error or cascading the deletion of historical financial records.

### 9. Case-Insensitive Auto-Learning (Taxonomy)
When the system automatically learns new Spares, Concerns, Brands, or Models from user input (e.g., during Job Card creation), the uniqueness check must always use `__iexact` (case-insensitive exact match) to prevent taxonomy fragmentation (e.g., creating both "Toyota" and "toyota").

---

## 🛡️ II. Steel Gate Security Logic

### 1. The FailedAttempt Logic (IP-Lockout)
Instead of standard session-based security, WorkshopOS uses the `FailedAttempt` model.
- **Mechanism**: Captures the visitor's `REMOTE_ADDR` (supports `X-Forwarded-For` for reverse proxies).
- **Lockout Threshold**: 5 consecutive failures (Login or OTP).
- **Cooldown**: 15-minute automated expiry.
- **Key Views**: `workshop.auth_views.staff_login_view`, `workshop.auth_views.admin_login_view`

### 2. The Alert System (⚠️ Current — New System Planned)
The security system triggers collaborative oversight via `auth_views.py`.
- **Alert Pulse**: Whenever ANY user (Staff or Owner) logs in, both owners receive alerts.
- **Staff Monitoring**: Owners receive real-time identifiers (IP, Device Name) for every login.
- **Channels**: Telegram Bot API (primary) + Twilio SMS (parallel).
- **⚠️ Note**: This notification architecture is subject to replacement.

### 3. Financial Audit Trails (v7.1)
The system automatically logs specialized security audits for critical financial events:
- **High Discounts**: Flags job cards where the received payment amount is significantly lower than the total bill.
- **Deleted Bulk Payers**: Tracks manually deleted bulk payer records for absolute financial accountability.

### 4. Template Rendering & XSS Prevention
Data passed from views into JavaScript contexts (especially in analytics dashboards) strictly avoids the legacy `{{ variable|safe }}` filter. Instead, WorkshopOS exclusively uses Django's native `json_script` tag to safely serialize data into a `<script type="application/json">` block, preventing Stored XSS attacks via unescaped quotes in user inputs.

---

## 📦 III. Warehouse Pulse (Real-time Signals)

The inventory system is "Living"—stock counts update automatically based on workshop activity via **Django Signals**.

### 1. Stock Delta Calculation
Located in `inventory/signals.py`.
- **`pre_save`**: Snapshots the original quantity and part name before modification.
- **`post_save`**: Calculates the delta and updates `Item.current_stock`. Handles three scenarios:
  - **Part Rename**: Restore old stock, deduct new stock
  - **Quantity Change**: Deduct only the difference (delta)
  - **New Entry**: Deduct full quantity
- **`post_delete`**: Restores full quantity to warehouse on deletion.
- **Reliability**: Verified by `inventory/test_signals.py`.

### 2. Supplier Restock Stock Sync
Located in `inventory/signals.py` (second group of handlers).
- **`pre_save`**: Snapshots the original quantity before modification.
- **`post_save`**: Calculates the delta and *increases* `Item.current_stock`. Handles:
  - **New restock item**: Increase stock by full qty
  - **Quantity change**: Adjust stock by delta only
- **`post_delete`**: Reverses the full stock increase when a restock item or its parent bill is deleted.
- **Key Symmetry**: Workshop signals deduct stock; Supplier signals increase stock. Both use the same pre_save/post_save delta pattern.
- **Reliability**: Verified by `inventory/tests_suppliers.py` (8 signal-specific tests).

---

## 📊 IV. Pagination & Rendering

All list-based views MUST utilize the `Paginator` class.
- **Standard**: 45 records per page (job card lists, pending payments, completed).
- **Inventory Standard**: 10 categories per page (heavy nested view).
- **Template Fragment**: Use `workshop/includes/pagination.html` for consistent UI.

---

## 🔄 V. Autocomplete API Endpoints

| Endpoint | Source Models | Features |
|----------|-------------|----------|
| `/api/autocomplete/brands/` | `CarBrand` | Simple name search |
| `/api/autocomplete/models/` | `CarModel` | Dependent on brand (filter by brand parameter) |
| `/api/autocomplete/spares/` | `SparePart` + `inventory.Item` | Dual-source with inventory priority (yellow highlight) |
| `/api/autocomplete/concerns/` | `ConcernSolution` | Concern text search |

All endpoints return JSON arrays and support the `?q=` query parameter with minimum 1-character input.

---

## 💰 VI. Cascade Payment Algorithm

Used in both **Bulk Payer** and **Spare Shop** payment systems:

1. Lock pending items with `select_for_update()` inside `transaction.atomic()`
2. Order by oldest first (`created_date`, `pk`)
3. Distribute payment amount across items until exhausted
4. Each item status transitions: PENDING → PARTIAL → PAID (or BULK_PAID)
5. Create `PaymentHistory` record (with JSON snapshot for **Bulk Payments** only)
6. Reversal reads the saved record to subtract precise amounts
7. Trigger automated recalculation of bulk payer ledgers upon bill deletion or restoration

> **Note**: Spare Shop payments do not use JSON snapshots. Their history is stored as a simple ledger entry. Only `BulkPaymentHistory` stores a JSON `details` field for reversal.

---

## 🛠️ VII. Operational Tooling

### 1. Automated SQLite Backups
The custom management command `python manage.py backup_db` securely clones the live `db.sqlite3` file into a timestamped archive within the `/backups` directory. It automatically prunes the stack to retain only the 7 most recent backups, preserving disk space while ensuring rapid recovery.

### 2. Production Static Serving
The middleware stack integrates **WhiteNoise** (`WhiteNoiseMiddleware`) to seamlessly serve static assets directly from the application server in production environments without requiring a separate web server setup.

---

## 🔜 VIII. Coming Soon

- **New Notification System** — Replacing current SMS/Telegram architecture

---

**WorkshopOS: Practical. Secure. In Active Development.** 🏁🛡️🏎️
