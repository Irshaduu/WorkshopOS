# 🔧 WorkshopOS: API & Core Engineering Patterns (v5.0)

This document outlines the **"Elite" industrial patterns** used in WorkshopOS to ensure 1M+ record optimization and zero-failure security.

---

## 🚀 I. High-Performance Engineering (1M+ Records)

### 1. Database Optimization (O(1) Access)
Every view that handles lists of objects (Job Cards, Inventory, Search) must use **Server-Side Pagination** and **Greedy Query Mapping**.

- **Pattern**: Always use `select_related` for ForeignKeys and `prefetch_related` for ManyToMany/Reverse relations.
- **Goal**: Reach sub-50ms database retrieval even with 1,000,000+ records.
- **Example**:
  ```python
  JobCard.objects.all().select_related('lead_mechanic').prefetch_related('spares', 'labours')
  ```

### 2. The "Tiny Search" Pattern
To find any vehicle among millions, always filter by **B-Tree indexed fields**.
- **Indexed Fields**: `registration_number`, `bill_number`, `brand_name`, `model_name`, `admitted_date`.
- **Search Execution**: Use `Q` objects with `icontains` for partial matches, but always lead with an indexed field if possible.

---

## 🛡️ II. Steel Gate Security Logic

### 1. The FailedAttempt Logic (IP-Lockout)
Instead of standard session-based security, WorkshopOS uses the `FailedAttempt` model.
- **Mechanism**: Captures the visitor's `REMOTE_ADDR`.
- **Lockout Threshold**: 5 consecutive failures (Login or OTP).
- **Cooldown**: 30-minute automated expiry.
- **Key View**: `workshop.auth_views.staff_login_view`

### 2. The Buddy Watch System
The security system triggers collaborative oversight via `auth_views.py`.
- **Alert Pulse**: Whenever an Owner logs in, an asynchronous alert is triggered to both owners.
- **Staff Monitoring**: Owners receive real-time identifiers (IP, Device Name) for every Staff/Office login.

---

## 📦 III. Warehouse Pulse (Real-time Signals)

The inventory system is "Living"—stock counts update automatically based on workshop activity via **Django Signals**.

### 1. Stock Delta Calculation
Located in `inventory/signals.py`.
- **`pre_save`**: Captures the original quantity before modification.
- **`post_save` / `post_delete`**: Calculates the delta and updates the `Item.current_stock` instantly.
- **Reliability**: Verified by the Titan Standard `InventorySignalTests`.

---

## 📊 IV. Pagination & Rendering

All list-based views MUST utilize the `Paginator` class.
- **Floor Standard**: 50 records per page (for quick scroll on mobile).
- **Office/Management Standard**: 21 records per page (for grid layouts).
- **Template Fragment**: Use `workshop/includes/pagination.html` for consistent UI.

---

**WorkshopOS: Industrial Grade. Zero Regression.** 🏁🛡️🏎️💨📋✨
