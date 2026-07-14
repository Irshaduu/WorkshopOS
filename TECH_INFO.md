# Titan Technical Architecture Blueprint

> **FOR FUTURE AI AGENTS:** This document contains the exact technical implementations used in "Titan" (WorkshopOS). Each section is a self-contained, portable architecture. When the user says *"Implement the Titan [X] feature,"* copy these exact patterns. Do NOT hallucinate alternatives. Every code block is production-tested.

> **Stack:** Django 5.2 LTS · Python 3.13 · Vanilla JS · Bootstrap 5.3 · SQLite (dev) / PostgreSQL (production deployment)  
> **Notifications:** Twilio SMS + Telegram Bot API (⚠️ current system — new system planned)

---

## Table of Contents

1. [AJAX Live Search with Debounce](#1-ajax-live-search-with-debounce)
2. [Multi-Word Split-Word Q-Object Search](#2-multi-word-split-word-q-object-search)
3. [AJAX Partial-View Pagination](#3-ajax-partial-view-pagination)
4. [Smart Reset (Context-Aware Refresh)](#4-smart-reset-context-aware-refresh)
5. [Date Range Filtering with Dropdown UI](#5-date-range-filtering-with-dropdown-ui)
6. [Unified Tabbed Trash Dashboard](#6-unified-tabbed-trash-dashboard)
7. [Soft-Delete & Restore Architecture](#7-soft-delete--restore-architecture)
8. [Bulk Cascade Payment Algorithm](#8-bulk-cascade-payment-algorithm)
9. [JSON Snapshot Payment Reversal](#9-json-snapshot-payment-reversal)
10. [Pure SQL Ledger Aggregation (Quantity-Aware)](#10-pure-sql-ledger-aggregation-quantity-aware)
11. [Subquery-Based Financial Annotations (Anti-Cartesian)](#11-subquery-based-financial-annotations-anti-cartesian)
12. [Dynamic Django Inline Formsets (JS)](#12-dynamic-django-inline-formsets-js)
13. [Debounced Autocomplete API with Multi-Source Priority](#13-debounced-autocomplete-api-with-multi-source-priority)
14. [Dependent Autocomplete (Brand → Model Filtering)](#14-dependent-autocomplete-brand--model-filtering)
15. [Auto-Learn Master Lists](#15-auto-learn-master-lists)
16. [Auto-Sync FK from Text Field](#16-auto-sync-fk-from-text-field)
17. [Thread-Safe Auto-Incrementing Bill Numbers](#17-thread-safe-auto-incrementing-bill-numbers)
18. [Denormalized Totals for Dashboard Speed](#18-denormalized-totals-for-dashboard-speed)
19. [Duplicate Entry Prevention (3-Attempt Confirmation)](#19-duplicate-entry-prevention-3-attempt-confirmation)
20. [Role-Based Access Control (RBAC) Decorators](#20-role-based-access-control-rbac-decorators)
21. [Template-Level Permission Gating](#21-template-level-permission-gating)
22. [IP-Based Brute Force Lockout (Steel Gate)](#22-ip-based-brute-force-lockout-steel-gate)
23. [Owner 2FA: OTP via SMS + Telegram (Dual Channel)](#23-owner-2fa-otp-via-sms--telegram-dual-channel)
24. [Real-Time Security Alert Broadcast](#24-real-time-security-alert-broadcast)
25. [Session Tracking Middleware & Remote Revoke](#25-session-tracking-middleware--remote-revoke)
26. [Car Profile System (Group-By Registration)](#26-car-profile-system-group-by-registration)
27. [Chronological Visit Numbering](#27-chronological-visit-numbering)
28. [BootstrapFormMixin (Auto-Styling Forms)](#28-bootstrapformmixin-auto-styling-forms)
29. [Custom Template Filters (clean_qty, divide, has_group)](#29-custom-template-filters-clean_qty-divide-has_group)
30. [Smart Redirect with Context Preservation](#30-smart-redirect-with-context-preservation)
31. [Composite Database Indexes for Dashboard Speed](#31-composite-database-indexes-for-dashboard-speed)
32. [N+1 Query Resolution (Page-Scoped Lookups)](#32-n1-query-resolution-page-scoped-lookups)
33. [Supplier Restock Stock Signals](#33-supplier-restock-stock-signals)
34. [Supplier Waterfall Bill Status](#34-supplier-waterfall-bill-status)
35. [Unassigned Spares Hub](#35-unassigned-spares-hub)
36. [Zero-Query Properties (N+1 Prevention)](#36-zero-query-properties-n1-prevention)
37. [Absolute Financial Precision (Decimal vs Float)](#37-absolute-financial-precision-decimal-vs-float)
38. [Referential Integrity & Safe Deletions](#38-referential-integrity--safe-deletions)
39. [Template Rendering & XSS Prevention](#39-template-rendering--xss-prevention)

---

## 1. AJAX Live Search with Debounce
**What It Does:** User types into a search bar → results update instantly without page reload.

**Frontend (Vanilla JS):**
```javascript
const searchInput = document.getElementById('searchInput');
const resultsContainer = document.getElementById('resultsContainer');
let debounceTimer;

function updateResults(query, page = 1, event) {
    if (event) event.preventDefault();
    const url = `?q=${encodeURIComponent(query)}&page=${page}`;

    const delay = (event && event.type === 'submit') ? 0 : 300;
    if (debounceTimer) clearTimeout(debounceTimer);

    debounceTimer = setTimeout(() => {
        fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(res => res.text())
            .then(html => {
                resultsContainer.innerHTML = html;
                window.history.pushState({}, '', url); // Sync URL bar for bookmarking
            });
    }, delay);
}

searchInput.addEventListener('input', function () { updateResults(this.value); });
```

**Why 300ms debounce?** Prevents firing a request on every keystroke. Waits until the user pauses typing.

---

## 2. Multi-Word Split-Word Q-Object Search
**What It Does:** Typing "Honda City Brake" finds the exact record even though "Honda" is in the brand column, "City" is in the model column, and "Brake" is in the spare part column.

**Backend (Django):**
```python
q = request.GET.get('q', '').strip()
queryset = MyModel.objects.all()

if q:
    for word in q.split():   # Split "Honda City" into ["Honda", "City"]
        queryset = queryset.filter(
            Q(field_1__icontains=word) |
            Q(field_2__icontains=word) |
            Q(field_3__icontains=word) |
            Q(field_4__icontains=word)
        )
```

**Why split?** A single `icontains="Honda City"` would search for the EXACT phrase. Splitting allows cross-column matching. Each word is ANDed (the for-loop), but within each word, columns are ORed (the `|`).

---

## 3. AJAX Partial-View Pagination
**What It Does:** Clicking page 2, 3, etc. swaps out only the table/cards area — no full page reload. URL bar stays synced so the user can bookmark or share the link.

**Backend (Django View):**
```python
def my_list_view(request):
    queryset = MyModel.objects.all()
    paginator = Paginator(queryset, 45)   # 45 items per page
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {'items': page_obj, 'page_obj': page_obj}

    # Return ONLY the table partial for AJAX requests
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'app/my_list_partial.html', context)

    # Return the full page (with header, nav, scripts) for normal requests
    return render(request, 'app/my_list_full.html', context)
```

**Frontend (Vanilla JS):**
```javascript
resultsContainer.addEventListener('click', function (e) {
    const link = e.target.closest('.page-link');
    if (link && !link.parentElement.classList.contains('disabled')) {
        e.preventDefault();
        const url = new URL(link.href);
        updateResults(searchInput.value || '', url.searchParams.get('page'));
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
});
```

**Template Structure:**
- `my_list_full.html` — extends `base.html`, includes navbar, sidebar, scripts, and `{% include "my_list_partial.html" %}`.
- `my_list_partial.html` — contains ONLY the card grid + pagination controls. No `<html>`, no `<head>`, no scripts.

---

## 4. Smart Reset (Context-Aware Refresh)
**What It Does:** When the user is searching/filtering via AJAX, their search state is preserved. But if they hit the browser's "Refresh" (F5), the page resets to a safe default (e.g., "Today" filter, empty search).

**Backend:**
```python
is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

if not is_ajax:
    # Hard Refresh: Reset to defaults
    filter_type = 'today'   # or 'all' depending on the page
    q = ''
else:
    # AJAX: Respect the user's current search/filter state
    filter_type = request.GET.get('filter', 'today')
    q = request.GET.get('q', '').strip()
```

**Why?** Without this, a user might refresh a page showing "Last Year" data and panic thinking all those old records are current. Smart Reset always brings them back to the most relevant default view.

---

## 5. Date Range Filtering with Dropdown UI
**What It Does:** A dropdown with "Today / This Week / This Month / This Year / Custom Range" that filters results by date.

**Backend:**
```python
today = date.today()

if filter_type == 'today':
    queryset = queryset.filter(date_field=today)
elif filter_type == 'week':
    queryset = queryset.filter(date_field__gte=today - timedelta(days=7))
elif filter_type == 'month':
    queryset = queryset.filter(date_field__gte=today - timedelta(days=30))
elif filter_type == 'year':
    queryset = queryset.filter(date_field__gte=today - timedelta(days=365))
elif filter_type == 'custom':
    start = request.GET.get('start_date')
    end = request.GET.get('end_date')
    if start and end:
        queryset = queryset.filter(date_field__gte=start, date_field__lte=end)
# elif filter_type == 'all': no filter applied
```

**Frontend:** A Bootstrap dropdown menu where each item triggers `updateResults()` via AJAX. The "Custom" option reveals a hidden date-picker form.

---

## 6. Unified Tabbed Trash Dashboard
**What It Does:** One single `/trash/` page with tabs (Job Cards, Bulk Payers, Payments, Spare Shops, Shop Payments). Each tab has its own search bar, pagination, and restore/delete buttons. All powered by a single Django view.

**Backend (Single View, Multi-Tab):**
```python
def trash_list(request):
    tab = request.GET.get('tab', 'jobcards')
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip()

    context = {
        'active_tab': tab,
        'q': q,
        'jobcard_trash_count': JobCard.objects.filter(is_deleted=True).count(),
        'bulkpayer_trash_count': BulkPayer.objects.filter(is_trashed=True).count(),
        # ... counts for badge numbers on each tab
    }

    if tab == 'jobcards':
        qs = JobCard.objects.filter(is_deleted=True)
        if q:
            for word in q.split():
                qs = qs.filter(Q(field__icontains=word) | ...)
        page_obj = Paginator(qs, 45).get_page(request.GET.get('page'))
        context['page_obj'] = page_obj
        if is_ajax:
            return render(request, 'trash_jobcards_partial.html', context)

    elif tab == 'bulkpayers':
        # Same pattern, different model and partial template
        ...

    return render(request, 'trash_list.html', context)
```

**Key Design:** The tab name is passed as `?tab=jobcards`. Each tab renders its own `_partial.html`. This avoids 5 separate views and 5 separate URLs.

---

## 7. Soft-Delete & Restore Architecture
**What It Does:** Deleting a record doesn't actually delete it. It sets a boolean flag (`is_deleted=True` or `is_trashed=True`). The record disappears from all main views but appears in the Trash.

**Model:**
```python
class MyModel(models.Model):
    is_deleted = models.BooleanField(default=False, db_index=True)
```

**All main queries filter it out:**
```python
# Active records only
MyModel.objects.filter(is_deleted=False)

# Trash only
MyModel.objects.filter(is_deleted=True)
```

**Restore:**
```python
def restore(request, pk):
    obj = get_object_or_404(MyModel, pk=pk)
    obj.is_deleted = False
    obj.save()
    return redirect('trash_list')
```

**Permanent Delete (Owner only):**
```python
@owner_required
def permanent_delete(request, pk):
    if request.method == 'POST':
        obj = get_object_or_404(MyModel, pk=pk, is_deleted=True)
        obj.delete()  # Actually removed from database
    return redirect('trash_list')
```

---

## 8. Bulk Cascade Payment Algorithm
**What It Does:** A client owes money for 15 jobs. They hand you ₹50,000. The system distributes this money across jobs, oldest first, fully paying as many as possible until the money runs out.

**Backend:**
```python
from django.db import transaction

with transaction.atomic():
    pending_items = (
        MyModel.objects.select_for_update()         # Lock rows to prevent race conditions
        .filter(payment_status__in=['PENDING', 'PARTIAL'])
        .annotate(
            balance=ExpressionWrapper(
                F('total_bill_amount') - F('received_amount'),
                output_field=DecimalField()
            )
        )
        .order_by('created_at', 'pk')              # Oldest first
    )

    remaining = lump_sum
    history_details = []

    for item in pending_items:
        if remaining <= 0:
            break

        balance = item.balance
        if balance <= 0:
            continue

        if remaining >= balance:
            # Fully pay this item
            paid = balance
            item.received_amount += balance
            item.payment_status = 'BULK_PAID'  # Or 'PAID' depending on model
            remaining -= balance
        else:
            # Partial payment — all money used up
            paid = remaining
            item.received_amount += remaining
            item.payment_status = 'PARTIAL'
            remaining = Decimal('0')

        item.save(update_fields=['received_amount', 'payment_status'])
        history_details.append({'item_id': item.pk, 'paid': str(paid)})

    # Save the history snapshot for future reversal
    PaymentHistory.objects.create(
        amount=lump_sum,
        details=json.dumps(history_details)
    )
```

**Critical:** `select_for_update()` inside `transaction.atomic()` prevents two users from paying the same bill simultaneously.

---

## 9. Payment Reversal
**What It Does:** Reverses a payment by reading saved record details.

**Backend:**
```python
with transaction.atomic():
    details = json.loads(payment.details)
    for entry in details:
        try:
            item = MyModel.objects.select_for_update().get(pk=entry['item_id'])
            reversed_amount = Decimal(str(entry['paid']))
            item.received_amount = max(Decimal('0'), item.received_amount - reversed_amount)

            # Recalculate status
            if item.received_amount <= 0:
                item.payment_status = 'PENDING'
            else:
                item.payment_status = 'PARTIAL'

            item.save()
        except MyModel.DoesNotExist:
            continue  # Item was deleted, skip gracefully

    payment.is_trashed = True
    payment.save()
```

**Why `max(Decimal('0'), ...)`?** Prevents negative balances from glitches or double-reversals.

---

## 10. Pure SQL Ledger Aggregation (Quantity-Aware)
**What It Does:** Calculates total purchases, total paid, and balance owed for hundreds of suppliers without Python loops. All math runs inside the database.

**Backend:**
```python
queryset = Supplier.objects.annotate(
    total_purchases=Coalesce(
        Sum(
            ExpressionWrapper(
                F('items__unit_price') * Coalesce(
                    F('items__quantity'),
                    Value(1, output_field=DecimalField())
                ),
                output_field=DecimalField()
            )
        ),
        Value(0, output_field=DecimalField())
    ),
    total_paid=Coalesce(
        Sum('items__paid_amount'),
        Value(0, output_field=DecimalField())
    )
).annotate(
    balance=ExpressionWrapper(
        F('total_purchases') - F('total_paid'),
        output_field=DecimalField()
    )
)
```

**Critical:** Always multiply `unit_price × quantity`. Always wrap in `Coalesce()` to prevent NULL crashes. Always use `ExpressionWrapper` with `output_field` for mixed-type math.

---

## 11. Subquery-Based Financial Annotations (Anti-Cartesian)
**What It Does:** When a JobCard has BOTH spares and labours, a naive `annotate(Sum('spares__price'), Sum('labours__amount'))` creates a Cartesian product (double-counting). Subqueries solve this.

**Backend:**
```python
spares_subquery = SpareItem.objects.filter(
    job_card=OuterRef('pk')
).values('job_card').annotate(total=Sum('total_price')).values('total')

labours_subquery = LabourItem.objects.filter(
    job_card=OuterRef('pk')
).values('job_card').annotate(total=Sum('amount')).values('total')

queryset = JobCard.objects.annotate(
    annotated_spares=Coalesce(Subquery(spares_subquery), Value(Decimal('0'))),
    annotated_labour=Coalesce(Subquery(labours_subquery), Value(Decimal('0')))
).annotate(
    total_bill=ExpressionWrapper(
        F('annotated_spares') + F('annotated_labour'),
        output_field=DecimalField()
    ),
    balance=ExpressionWrapper(
        F('total_bill') - F('received_amount'),
        output_field=DecimalField()
    )
)
```

**When to use:** ANY time you need to sum from TWO or more related tables on the same parent model.

---

## 12. Dynamic Django Inline Formsets (JS)
**What It Does:** User clicks "Add Row" → a new blank row appears in the table (concerns, spares, or labour). No page reload.

**Django (forms.py):**
```python
JobCardSpareFormSet = inlineformset_factory(
    JobCard,
    JobCardSpareItem,
    fields=['spare_part_name', 'quantity', 'unit_price', 'total_price', ...],
    extra=0,            # Start with 0 empty rows (user adds manually)
    can_delete=True,    # Shows a checkbox to mark rows for deletion
    validate_min=False,
    widgets={
        'spare_part_name': forms.TextInput(attrs={
            'class': 'form-control autocomplete-spare',
            'autocomplete': 'off',
        }),
        # ...
    }
)
```

**Frontend (Vanilla JS):**
```javascript
function addFormRow(prefix, listId, emptyFormId) {
    const totalFormsInput = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
    const listContainer = document.getElementById(listId);
    const emptyFormTemplate = document.getElementById(emptyFormId);

    const currentCount = parseInt(totalFormsInput.value);
    const newRow = emptyFormTemplate.firstElementChild.cloneNode(true);

    // Replace __prefix__ with the actual index
    const regex = new RegExp('__prefix__', 'g');
    newRow.innerHTML = newRow.innerHTML.replace(regex, currentCount);

    listContainer.appendChild(newRow);
    totalFormsInput.value = currentCount + 1;

    // Re-initialize autocomplete on the new row
    initializeAutocompleteInContainer(newRow);
}
```

**Template:** The `empty_form` is rendered inside a hidden `<div id="empty-spare-form">` and used as a clone template.

---

## 13. Debounced Autocomplete API with Multi-Source Priority
**What It Does:** User types "Bra" into a spare part field → the dropdown shows results from TWO sources: Inventory items (highlighted yellow, highest priority) and Master List items (normal).

**Backend API (Django):**
```python
def autocomplete_spares(request):
    q = request.GET.get('q', '')
    if len(q) < 1:
        return JsonResponse([], safe=False)

    results = []

    # Source 1: Inventory (Priority — shown in yellow)
    inventory_items = Item.objects.filter(name__icontains=q).values_list('name', flat=True)[:5]
    for name in inventory_items:
        results.append({"name": name, "source": "inventory"})

    # Source 2: Master List (Normal — deduplicated)
    master_items = SparePart.objects.filter(
        name__icontains=q
    ).exclude(name__in=inventory_items).values_list('name', flat=True)[:10]
    for name in master_items:
        results.append({"name": name, "source": "master"})

    return JsonResponse(results, safe=False)
```

**Frontend (Vanilla JS):**
```javascript
function fetchSuggestions(type, query, inputObj, suggestionsBox) {
    let url = `/api/autocomplete/${type}/?q=${encodeURIComponent(query)}`;

    fetch(url)
        .then(response => response.json())
        .then(data => {
            suggestionsBox.innerHTML = '';
            data.forEach(item => {
                const name = typeof item === 'object' ? item.name : item;
                const source = typeof item === 'object' ? item.source : 'master';

                const el = document.createElement('a');
                el.classList.add('list-group-item', 'list-group-item-action');

                if (source === 'inventory') {
                    el.classList.add('list-group-item-warning', 'fw-bold');
                    el.innerHTML = `<i class="bi bi-box-seam me-2"></i>${name}`;
                } else {
                    el.textContent = name;
                }

                el.addEventListener('click', function (e) {
                    e.preventDefault();
                    inputObj.value = name;
                    suggestionsBox.innerHTML = '';
                });

                suggestionsBox.appendChild(el);
            });
        });
}
```

---

## 14. Dependent Autocomplete (Brand → Model Filtering)
**What It Does:** When the user types a Brand (e.g., "BMW"), the Model autocomplete only shows models belonging to BMW.

**Frontend:**
```javascript
if (type === 'models') {
    const brandInput = document.querySelector('.autocomplete-brand');
    if (brandInput && brandInput.value) {
        url += `&brand=${encodeURIComponent(brandInput.value)}`;
    }
}
```

**Backend:**
```python
def autocomplete_models(request):
    q = request.GET.get('q', '')
    brand = request.GET.get('brand', '')

    qs = CarModel.objects.filter(name__icontains=q)
    if brand:
        qs = qs.filter(brand__name__icontains=brand)

    return JsonResponse(list(qs.values_list('name', flat=True)[:10]), safe=False)
```

---

## 15. Auto-Learn Master Lists
**What It Does:** When a mechanic types a new spare part name that doesn't exist in the master list, the system automatically adds it after saving. Next time, it appears in autocomplete.

**Backend (inside `jobcard_create` and `jobcard_edit`):**
```python
saved_spares = spare_formset.save()

for spare in saved_spares:
    if spare.spare_part_name:
        name = spare.spare_part_name.strip()
        if name and not SparePart.objects.filter(name__iexact=name).exists():
            SparePart.objects.create(name=name)
```

**Why `name__iexact`?** Case-insensitive check prevents "Oil Filter" and "oil filter" from being stored as duplicates.

---

## 16. Auto-Sync FK from Text Field
**What It Does:** The user selects a shop name from a dropdown (text). The system automatically links the correct `SpareShop` foreign key record behind the scenes.

**Backend (after saving formsets):**
```python
for spare in jobcard.spares.all():
    if spare.shop_name and spare.shop_name.strip():
        shop_obj = SpareShop.objects.filter(
            name__iexact=spare.shop_name.strip(),
            is_trashed=False
        ).first()
        JobCardSpareItem.objects.filter(pk=spare.pk).update(shop=shop_obj)
    else:
        JobCardSpareItem.objects.filter(pk=spare.pk).update(shop=None)
```

**Why?** Keeps the display layer (text) and the relational layer (FK) in sync without requiring complex form logic.

---

## 17. Thread-Safe Auto-Incrementing Bill Numbers
**What It Does:** Generates sequential bill numbers like `JB-26-001`, `JB-26-002`, etc. Thread-safe — two users creating bills at the same instant will never get the same number.

**Model `save()` override:**
```python
def save(self, *args, **kwargs):
    if not self.bill_number:
        with transaction.atomic():
            year = str(self.admitted_date.year)[2:]  # 2026 → "26"

            last_job = JobCard.objects.select_for_update().filter(
                bill_number__startswith=f'JB-{year}-'
            ).order_by('-bill_number').first()

            if last_job and last_job.bill_number:
                try:
                    last_num = int(last_job.bill_number.split('-')[-1])
                    next_num = last_num + 1
                except (ValueError, IndexError):
                    next_num = JobCard.objects.filter(
                        bill_number__startswith=f'JB-{year}-'
                    ).count() + 1
            else:
                next_num = 1

            self.bill_number = f'JB-{year}-{str(next_num).zfill(3)}'

    super().save(*args, **kwargs)
```

**Critical:** `select_for_update()` locks the row during the read so a concurrent request waits instead of reading the same number.

---

## 18. Denormalized Totals for Dashboard Speed
**What It Does:** Instead of calculating `SUM(spares) + SUM(labours)` on every dashboard load for thousands of cards, the total is pre-calculated and stored on the JobCard itself.

**Model:**
```python
total_bill_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

def update_totals(self):
    spare_total = self.spares.aggregate(
        total=Coalesce(Sum('total_price'), 0, output_field=DecimalField())
    )['total']
    labour_total = self.labours.aggregate(
        total=Coalesce(Sum('amount'), 0, output_field=DecimalField())
    )['total']

    new_total = spare_total + labour_total
    if self.total_bill_amount != new_total:
        self.total_bill_amount = new_total
        JobCard.objects.filter(pk=self.pk).update(total_bill_amount=new_total)
```

**Triggered automatically** from `JobCardSpareItem.save()` and `JobCardLabourItem.save()`:
```python
def save(self, *args, **kwargs):
    super().save(*args, **kwargs)
    self.job_card.update_totals()
```

---

## 19. Duplicate Entry Prevention (3-Attempt Confirmation)
**What It Does:** If someone tries to create a job card for a vehicle that already has an active (undelivered) job, the system warns them. They must press "Save" 3 times to force-create it.

**Backend:**
```python
existing_job = JobCard.objects.filter(
    registration_number__iexact=registration,
    delivered=False
).exclude(pk=jobcard.pk).first()

if existing_job:
    session_key = f'duplicate_confirm_{registration}'
    confirm_count = request.session.get(session_key, 0)

    if confirm_count < 2:
        request.session[session_key] = confirm_count + 1
        messages.warning(request, f'{registration} has an active job.')
        return render(request, 'jobcard_form.html', {...})  # Re-render form with data intact
    else:
        del request.session[session_key]  # 3rd attempt: proceed with save
```

---

## 20. Role-Based Access Control (RBAC) Decorators
**What It Does:** Three decorator levels that protect views based on the user's Django Group membership.

**decorators.py:**
```python
from django.contrib.auth.decorators import user_passes_test

def is_owner(user):
    return user.groups.filter(name='Owner').exists() or user.is_superuser

def is_office_or_owner(user):
    return user.groups.filter(name__in=['Office', 'Owner']).exists() or user.is_superuser

def is_floor_office_owner(user):
    return user.groups.filter(name__in=['Floor', 'Office', 'Owner']).exists() or user.is_superuser

# Usage:
@owner_required       # Only owners (delete, trash, reverse payments)
@office_required      # Office + Owners (create jobs, billing, invoices)
@staff_required       # Everyone including Floor (view dashboard, view jobs)
```

---

## 21. Template-Level Permission Gating
**What It Does:** Show/hide buttons and sections in templates based on user role.

**Custom Template Filter:**
```python
@register.filter(name='has_group')
def has_group(user, group_name):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=group_name).exists()
```

**Template Usage:**
```html
{% load custom_filters %}

{% if request.user|has_group:"Owner" %}
    <button class="btn btn-danger">Delete Permanently</button>
{% endif %}

{% if request.user|has_group:"Office" or request.user|has_group:"Owner" %}
    <button>Update Bill Status</button>
{% endif %}
```

---

## 22. IP-Based Brute Force Lockout (Steel Gate)
**What It Does:** After 5 failed login attempts from the same IP, the IP is blocked for 15 minutes. Cannot be bypassed by clearing cookies.

**Backend:**
```python
class FailedAttempt(models.Model):
    ip_address = models.GenericIPAddressField(unique=True)
    failures = models.PositiveIntegerField(default=0)
    last_attempt = models.DateTimeField(auto_now=True)

def check_ip_lockout(request):
    ip = get_client_ip(request)
    attempt = FailedAttempt.objects.filter(ip_address=ip).first()
    if attempt and attempt.failures >= 5:
        lockout_expiry = attempt.last_attempt + timedelta(minutes=15)
        if timezone.now() < lockout_expiry:
            return True  # BLOCKED
        else:
            attempt.failures = 0
            attempt.save()
    return False

def record_login_failure(request):
    ip = get_client_ip(request)
    FailedAttempt.objects.get_or_create(ip_address=ip)
    # ATOMIC UPDATE: Prevents race conditions during concurrent brute-force attacks
    FailedAttempt.objects.filter(ip_address=ip).update(failures=F('failures') + 1)

def reset_login_failures(request):
    ip = get_client_ip(request)
    FailedAttempt.objects.filter(ip_address=ip).update(failures=0)
```

**IP Detection (Hardened against spoofing):**
> 🛡️ **SECURITY NOTE:** This function deliberately ignores `X-Forwarded-For` proxy headers, pulling strictly from the unforgeable `REMOTE_ADDR` to permanently block client-side IP spoofing bypasses.
```python
def get_client_ip(request):
    """
    Returns the direct client IP.
    Only use REMOTE_ADDR — never trust client-supplied headers without a
    verified trusted proxy configuration.
    """
    return request.META.get('REMOTE_ADDR', '0.0.0.0')
```

---

## 23. Owner Forgot Password: OTP via SMS + Telegram
**What It Does:** Security system for password resets using OTP codes via SMS/Telegram.
> ⚠️ **SECURITY NOTE:** OTP is currently stored in plaintext in the session store (`django_session` table). Anyone with database read access can read active OTPs. Should be stored as a hash (`hashlib.sha256`) in future iterations.

| **Forgot Password** | `/forgot-password/` → OTP via SMS/Telegram → `/reset-password/` |
| **OTP Authentication** | 6-digit, 5-min expiry, 3 attempts max, 60s cooldown |

**OTP Generation:**
```python
from django.utils.crypto import get_random_string

otp = get_random_string(length=6, allowed_chars='0123456789')

request.session['pwd_reset_otp'] = otp
request.session['pwd_reset_expire'] = time.time() + 300  # 5-minute expiry
```

**Dual-Channel Dispatch:**
```python
# Channel 1: Twilio SMS
send_twilio_sms(mobile, f"Your Login Code: {otp}")

# Channel 2: Telegram Bot API (Free, instant)
def send_telegram_msg(chat_id, message):
    token = config('TELEGRAM_BOT_TOKEN')
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'})
```

---

## 24. Real-Time Security Alert Broadcast
**What It Does:** Every time ANY user (Staff or Owner) logs in, both owners receive an instant alert via SMS + Telegram showing the username, device type, and IP address.

**Backend:**
```python
def send_titan_security_alert(user, request):
    device_name = UserSession.get_device_name(request.META.get('HTTP_USER_AGENT'))
    ip = request.META.get('REMOTE_ADDR')

    msg = (
        f"[SECURITY ALERT]: {user.username} logged into HQ Portal.\n"
        f"Device: {device_name}\n"
        f"IP: {ip}\n"
        f"If unexpected, REVOKE access now!"
    )

    for name, mobile, chat_id in owner_recipients:
        send_telegram_msg(chat_id, msg)
        send_twilio_sms(mobile, msg)
```

**Device Detection** (parses User-Agent string):
```python
@staticmethod
def get_device_name(user_agent_string):
    ua = (user_agent_string or "").lower()
    device = "Desktop"
    if "iphone" in ua: device = "iPhone"
    elif "samsung" in ua: device = "Samsung Galaxy"
    elif "android" in ua: device = "Android Phone"
    elif "macintosh" in ua: device = "Macbook"
    # ...
    browser = "Web Browser"
    if 'chrome' in ua: browser = "Google Chrome"
    elif 'safari' in ua: browser = "Apple Safari"
    # ...
    return f"{browser} on {device}"
```

---

## 25. Session Tracking Middleware & Remote Revoke
**What It Does:** Every authenticated request updates a `UserSession` record. The owner can see all active devices in a security dashboard and remotely kill any session.

**Middleware:**
```python
class SessionTrackingMiddleware:
    def __call__(self, request):
        if request.user.is_authenticated:
            session_key = request.session.session_key
            if not session_key:
                request.session.save()
                session_key = request.session.session_key

            UserSession.objects.update_or_create(
                session_key=session_key,
                defaults={
                    'user': request.user,
                    'ip_address': get_client_ip(request),
                    'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                }
            )
        return self.get_response(request)
```

**Remote Kill:**
```python
def terminate_session(request, session_id):
    user_session = get_object_or_404(UserSession, pk=session_id)
    # Kill the Django session (logs them out)
    Session.objects.filter(session_key=user_session.session_key).delete()
    # Remove tracking record
    user_session.delete()
```

---

## 26. Car Profile System (Group-By Registration)
**What It Does:** Groups all job cards by registration number to show a "Vehicle History" card. Shows total visits, latest service date, and links to each visit.

**Backend:**
```python
cars_query = JobCard.objects.values('registration_number').annotate(
    total_visits=Count('id'),
    latest_date=Max('admitted_date'),
    latest_id=Max('id')
).order_by('-latest_date')
```

**N+1 Resolution:** Only fetch full JobCard details for the IDs visible on the current page:
```python
latest_ids = [car['latest_id'] for car in page_obj]
details_map = {jc.id: jc for jc in JobCard.objects.filter(id__in=latest_ids)}
```

---

## 27. Chronological Visit Numbering
**What It Does:** Shows "Visit 1 of 5", "Visit 2 of 5" labels on each bill for a car.

**Backend:**
```python
bills_list = list(bills)
total_visits = len(bills_list)
for i, bill in enumerate(bills_list):
    bill.visit_number = total_visits - i   # Latest = highest number
```

---

## 28. BootstrapFormMixin (Auto-Styling Forms)
**What It Does:** Automatically adds `form-control` class to every form field. Preserves existing CSS classes (like `autocomplete-brand`).

```python
class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            bootstrap_class = 'form-control'
            if isinstance(field.widget, forms.CheckboxInput):
                bootstrap_class = 'form-check-input'

            existing_class = field.widget.attrs.get('class', '')
            new_class = f"{existing_class} {bootstrap_class}" if existing_class else bootstrap_class

            field.widget.attrs.update({
                'class': new_class,
                'placeholder': field.label
            })

# Usage:
class MyForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = MyModel
        fields = ['name', 'email']
```

---

## 29. Custom Template Filters (clean_qty, divide, has_group)
**What It Does:** Reusable template filters for common display transformations.

```python
# templatetags/custom_filters.py

@register.filter
def clean_qty(value):
    """1.00 → 1, 1.50 → 1.5 (removes unnecessary decimals)"""
    if value is None: return ""
    f_val = float(value)
    return int(f_val) if f_val == int(f_val) else f_val

@register.filter
def divide(value, arg):
    """Template-safe division with zero protection"""
    if not arg or float(arg) == 0: return 0
    return float(value) / float(arg)

@register.filter
def multiply(value, arg):
    return float(value) * float(arg)

@register.filter
def get_range(value):
    """{% for i in 20|get_range %} → loops 0-19"""
    return range(int(value))
```

**Template usage:** `{{ spare.quantity|clean_qty }}` → displays `4` instead of `4.00`.

---

## 30. Smart Redirect with Context Preservation
**What It Does:** After saving, the system redirects the user back to where they came from. If they came from the Live Report (mini dashboard), they go back there instead of the default edit page.

```python
# Pass context via URL param
<a href="{% url 'jobcard_edit' pk %}?next=mini">Edit</a>

# In the view, after successful save:
next_url = request.GET.get('next')
if next_url == 'mini':
    return redirect('live_report')
return redirect('jobcard_edit', pk=jobcard.pk)
```

---

## 31. Composite Database Indexes for Dashboard Speed
**What It Does:** A single SQL index that covers the exact query pattern used by the dashboard, turning a 500ms query into a 5ms query on millions of records.

```python
class Meta:
    indexes = [
        models.Index(fields=['is_deleted', 'delivered', '-updated_at']),
    ]
```

**Matches the dashboard query:** `JobCard.objects.filter(is_deleted=False, delivered=False).order_by('-updated_at')` — the database can use this single index to filter AND sort without scanning the entire table.

---

## 32. N+1 Query Resolution (Page-Scoped Lookups)
**What It Does:** Instead of running a separate query for each of the 45 cards on the page (N+1 problem), batch-fetch all needed data in a single query.

**Pattern:**
```python
# Step 1: Get the page (e.g., 45 cards)
page_obj = paginator.get_page(page_number)

# Step 2: Extract unique values from THIS PAGE ONLY
unique_regs = list(set(card.registration_number for card in page_obj))

# Step 3: One single query for all 45 cards' visit counts
reg_counts = dict(
    JobCard.objects.filter(registration_number__in=unique_regs)
    .values('registration_number')
    .annotate(total=Count('id'))
    .values_list('registration_number', 'total')
)

# Step 4: Attach results to each card
for card in page_obj:
    card.total_visits = reg_counts.get(card.registration_number, 1)
```

**Why page-scoped?** Instead of computing visit counts for ALL 10,000 cars, we only compute for the 45 that are actually visible.

---

## 33. Supplier Restock Stock Signals
**What It Does:** When a restock bill is created from a supplier, the warehouse stock automatically increases. Editing or deleting the bill adjusts stock accordingly — the exact mirror of how workshop consumption deducts stock.

**Backend (Django Signals):**
```python
# inventory/signals.py

@receiver(pre_save, sender=SupplierRestockItem)
def track_old_restock_quantity(sender, instance, **kwargs):
    """Snapshots the old quantity before update (same pattern as workshop signals)."""
    if instance.pk:
        try:
            old_instance = SupplierRestockItem.objects.get(pk=instance.pk)
            instance._old_quantity = old_instance.quantity or 0
        except SupplierRestockItem.DoesNotExist:
            instance._old_quantity = 0
    else:
        instance._old_quantity = 0

@receiver(post_save, sender=SupplierRestockItem)
def update_stock_on_restock_save(sender, instance, created, **kwargs):
    """Increases stock by the delta (full qty on create, difference on edit)."""
    new_qty = float(instance.quantity or 0)
    old_qty = float(getattr(instance, '_old_quantity', 0))
    diff = new_qty - old_qty

    if diff != 0 and instance.item:
        instance.item.current_stock += diff
        instance.item.save()

@receiver(post_delete, sender=SupplierRestockItem)
def restore_stock_on_restock_delete(sender, instance, **kwargs):
    """Reverses the full stock increase when a restock item is deleted."""
    if instance.item and instance.quantity:
        instance.item.current_stock -= float(instance.quantity)
        instance.item.save()
```

**Key Symmetry:** Workshop signals *decrease* stock on create and *restore* on delete. Supplier signals *increase* stock on create and *reverse* on delete. Both use the same pre_save snapshot + post_save delta pattern.

---

## 34. Supplier Waterfall Bill Status
**What It Does:** Each supplier restock bill shows its payment status (Covered / Partial / Unpaid) using a running waterfall. Total payments are distributed across bills oldest-first, and each bill's status is determined by how much of the running sum covers its effective amount.

**Backend (Python in view):**
```python
def _annotate_bill_status(bills, total_paid):
    """Absolute running-sum waterfall over bills (oldest first)."""
    running = Decimal('0')
    for bill in bills:
        effective = bill.total_amount - (bill.discount_amount or 0)
        running += effective
        if total_paid >= running:
            bill.pay_status = 'covered'
        elif total_paid > running - effective:
            bill.pay_status = 'partial'
        else:
            bill.pay_status = 'unpaid'
```

**Why running-sum instead of cascade allocation?** Unlike the Bulk Payer / Spare Shop cascade which modifies `received_amount` on each item, the supplier system keeps bills immutable. Payment status is calculated on-the-fly from `total_paid` vs cumulative bill totals. This avoids needing JSON snapshots for supplier payment reversal — simply soft-delete the payment and recalculate.

---

## 35. Unassigned Spares Hub
**What It Does:** Allows spares to exist in a shop's ledger without being assigned to a job card. Useful for legacy balances or bulk purchases. Spares can be detached from a job card and remain unassigned, preserving original vehicle info, and can be managed from a central hub.

**Backend (Unassigning logic):**
```python
def spare_shop_unassign_item(request, item_pk):
    item = get_object_or_404(JobCardSpareItem, pk=item_pk)
    old_jc = item.job_card

    if old_jc:
        # Preserve vehicle details before unassigning
        brand = old_jc.brand_name or ""
        model = old_jc.model_name or ""
        reg = old_jc.registration_number or ""
        info = f"{brand} {model}".strip()
        if reg:
            info += f" ({reg})"
        item.original_vehicle_info = info.strip()
        
    item.job_card = None
    item.save()
    
    # Must manually refresh old job card's totals since the FK is now broken
    if old_jc:
        old_jc.update_totals()
```

**Why manual `update_totals()`?** Because the model's `save()` method usually triggers `update_totals()` on its `self.job_card`. Once `job_card` is `None`, the model cannot update the old job card, so the view must do it manually to ensure dashboard denormalization stays accurate.

---

## 36. Zero-Query Properties (N+1 Prevention)

Model properties that calculate aggregates (like percentages) must actively check for pre-annotated fields before hitting the database.

```python
    @property
    def get_completion_percentage(self):
        # 1. First, check if the values were pre-annotated in the queryset (O(1) memory lookup)
        if hasattr(self, 'total_concerns') and hasattr(self, 'fixed_concerns'):
            total = self.total_concerns
            fixed = self.fixed_concerns
        else:
            # 2. Fallback to costly database query (N+1 risk)
            total = self.concerns.count()
            fixed = self.concerns.filter(status='FIXED').count()
            
        if total == 0:
            return 0
        return int((fixed / total) * 100)
```
**Why?** This guarantees high performance when rendering dashboards with 45+ items, but safely falls back to a query if a single object is instantiated outside of a dashboard context.

---

## 37. Absolute Financial Precision (Decimal vs Float)

When developing billing, invoice, or ledger modules, floating-point arithmetic is strictly prohibited to prevent decimal drift.

```python
# BAD (Will drift over time)
amount = models.FloatField(default=0.0)

# GOOD (Titan Standard)
from decimal import Decimal
amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
```
**Cascade Payment Implication:** The oldest-first bulk payment algorithm relies on subtracting exact amounts until `0.00` is reached. Float drift can cause a balance of `0.000000001`, leaving a bill permanently "Partially Paid".

---

## 38. Referential Integrity & Safe Deletions

In financial systems, destroying historical records via cascading deletion is dangerous.

```python
# Models
lead_mechanic = models.ForeignKey(Mechanic, on_delete=models.PROTECT, null=True)

# Views
from django.db.models import ProtectedError

try:
    mechanic.delete()
    messages.success(request, f"Mechanic '{mechanic.name}' deleted successfully.")
except ProtectedError:
    messages.error(request, "Cannot delete this mechanic because they are assigned to existing job cards.")
```
**Why?** If a mechanic is fired, deleting them must NOT delete the 500 job cards (and revenue history) they completed. Catch `ProtectedError` and return a user-friendly message rather than a 500 Server Error.

---

## 39. Template Rendering & XSS Prevention

When passing data from Django to JavaScript (such as rendering charting data for dashboards), the legacy `{{ variable|safe }}` is strictly prohibited due to Stored XSS vulnerabilities.

```html
<!-- BAD (Vulnerable to unescaped quotes) -->
<script>
    const data = {{ chart_data|safe }};
</script>

<!-- GOOD (Titan Standard) -->
{{ chart_data|json_script:"chart-data" }}
<script>
    const data = JSON.parse(document.getElementById('chart-data').textContent);
</script>
```

---

> **END OF BLUEPRINT.** This document covers every technical pattern in the Titan system (v7.1). To use in a new project, tell your AI agent: *"Read TECH_INFO.md section [number] and implement it here."*  
> **Note:** Sections 23-24 (SMS/Telegram notifications) document the current system which may be replaced with a new notification architecture.
