# WorkshopOS: API & Core Engineering Patterns (v6.3)

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
- **Indexed Fields**: `registration_number`, `bill_number`, `brand_name`, `model_name`, `admitted_date`, `is_deleted`, `delivered`, `updated_at`.
- **Search Execution**: Use `Q` objects with `icontains` for partial matches. Split multi-word queries for cross-column matching.

### 3. Composite Database Index
The dashboard query pattern is covered by a composite index for maximum performance:
```python
class Meta:
    indexes = [
        models.Index(fields=['is_deleted', 'delivered', '-updated_at']),
    ]
```

### 4. Denormalized Financials
`JobCard.total_bill_amount` is a physical column, not a computed value. Updated automatically via `update_totals()` on every spare/labour save or delete. 
*(Note: Now strictly enforced across all dashboard and pending payment views using `F('total_bill_amount')` to eliminate correlated subquery N+1 bottlenecks).*

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
- **Floor Standard**: 50 records per page (for quick scroll on mobile).
- **Office/Management Standard**: 21 records per page (for grid layouts).
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
4. Each item status transitions: PENDING → PARTIAL → PAID
5. Create `PaymentHistory` record (with JSON snapshot for **Bulk Payments** only)
6. Reversal reads the saved record to subtract precise amounts

> **Note**: Spare Shop payments do not use JSON snapshots. Their history is stored as a simple ledger entry. Only `BulkPaymentHistory` stores a JSON `details` field for reversal.

---

## 🔜 VII. Coming Soon

- **PostgreSQL Production Database** — Configured in `settings/production.py`
- **Admin Data Analysis & Reports** — Visual analytics for Owners
- **New Notification System** — Replacing current SMS/Telegram architecture

---

**WorkshopOS: Practical. Secure. In Active Development.** 🏁🛡️🏎️
