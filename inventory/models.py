# inventory/models.py
from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

# A plain module holding three numbers and a parser — it imports no model, so
# importing it here at module level cannot create a cycle with workshop.models.
from workshop.pricing import DEFAULT_MARKUP_PERCENT, MAX_MARKUP_PERCENT

class Category(models.Model):
    """
    Groups Inventory Items (e.g., Engine Parts, Fluids, Electrical).
    Used for navigation and bulk stock reporting.
    """
    name = models.CharField(max_length=100, db_index=True)

    class Meta:
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name

class Item(models.Model):
    """
    A specific part or consumable in the workshop warehouse.
    
    Attributes:
        category (ForeignKey): Link to parent group.
        name (CharField): Part name (matches SparePart master list).
        average_stock (DecimalField): Threshold for low-stock warnings.
        current_stock (DecimalField): Real-time quantity on hand (supports fractional
            units like 1.5 L of oil, stored exactly — no float drift).
        usage_count (PositiveIntegerField): Popularity score for smart-sorting.
    """
    category = models.ForeignKey(
        Category,
        related_name='items',
        # PROTECT prevents accidental deletion of a category that still has items.
        # Without this, deleting a category would silently wipe all its inventory
        # records and any linked billing/restock history. (AUD-0024)
        on_delete=models.PROTECT
    )
    name = models.CharField(max_length=200, db_index=True)
    average_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Ideal stock level for calculation")
    current_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    usage_count = models.PositiveIntegerField(default=0, help_text="Cached popularity score (frequency of use)")
    # Weighted-average purchase cost per unit. Maintained ONLY by restock
    # receipts (issuing stock at the average leaves the average unchanged), and
    # always by full replay — see inventory/costing.py. Never edited by hand.
    avg_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Weighted-average purchase cost per unit, derived from restock bills")
    # How much the job card suggests adding on top of `avg_cost` for the
    # customer's unit price — a MARKUP (₹1,000 → ₹1,400 at 40), never a margin.
    # A SUGGESTION ONLY: nothing on the server reads this to price anything (see
    # workshop/pricing.py), and changing it moves no saved price on any card.
    #
    # Whole numbers only, 0 to 999, set on Add Product and Edit Product.
    # `db_default` as well as `default`, per the migration rule: a row inserted
    # by code that predates this column still gets 40 rather than failing.
    markup_percent = models.PositiveSmallIntegerField(
        default=DEFAULT_MARKUP_PERCENT, db_default=DEFAULT_MARKUP_PERCENT,
        help_text="Markup on cost suggested for the customer unit price on a job card (whole %, 0–999)")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['category', 'name'],
                name='unique_category_item_idx'
            ),
            # The views refuse anything outside 0–999; this is the last line of
            # defence for a row written some other way. PositiveSmallIntegerField
            # already refuses a negative on PostgreSQL.
            models.CheckConstraint(
                condition=models.Q(markup_percent__lte=MAX_MARKUP_PERCENT),
                name='inventory_item_markup_percent_max',
            ),
        ]
        ordering = ['-usage_count', 'name']

    def __str__(self):
        return f"{self.category.name} - {self.name}"

    def stock_percentage(self):
        """Calculates health percentage for visual progress bars."""
        if self.average_stock <= 0:
            return 100 # Default to full/green if no average set
        return (self.current_stock / self.average_stock) * 100

    def stock_status_color(self):
        """Returns the Tailwind/Bootstrap compatible hex color for stock health."""
        pct = self.stock_percentage()
        if pct < 25:
            return "#ef4444" # Red (Critical)
        elif pct < 50:
            return "#eab308" # Yellow (Warning)
        else:
            return "#22c55e" # Green (Healthy)

class ConsumptionRecord(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=timezone.now)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.item.name} ({self.quantity})"


class SupplierShop(models.Model):
    name = models.CharField(max_length=150, unique=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.CharField(max_length=300, blank=True, null=True)
    total_billed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # WHAT THE WORKSHOP ALREADY OWED THIS SHOP ON GO-LIVE DAY, typed once on
    # Legacy Data → Opening Balances, exactly as the shop's own book says. It
    # is a debt from the Excel years and nothing else: no bill, no stock, no
    # expense and no cash. `update_totals()` adds it to the billed side, so the
    # balance on every screen follows it, and the payment waterfall treats it
    # as the OLDEST debt — see `paid_beyond_opening`.
    # `db_default` as well as `default`, per the migration rule.
    opening_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0'), db_default=Decimal('0'),
        help_text="Owed to this shop on go-live day, from before the system")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            # A shop the workshop had paid AHEAD is not supported: the screen
            # refuses a negative, and this is the last line of defence.
            models.CheckConstraint(
                condition=models.Q(opening_balance__gte=0),
                name='inventory_suppliershop_opening_balance_non_negative'
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def get_pending_balance(self):
        return self.total_billed_amount - self.total_paid_amount

    @property
    def opening_balance_left(self):
        """How much of the go-live opening balance is still unpaid.

        Payments pay the oldest debt first and the opening balance IS the
        oldest, so every rupee paid comes off it before it reaches a bill. The
        shop page shows the line only while this is above zero.
        """
        left = self.opening_balance - self.total_paid_amount
        return left if left > 0 else Decimal('0')

    @property
    def paid_beyond_opening(self):
        """What the payments leave for the BILLS once the opening balance is paid.

        The payment waterfall allocates this, not `total_paid_amount`, oldest
        bill first. Negative while the opening balance is still being paid off,
        which correctly leaves every bill UNPAID.
        """
        return self.total_paid_amount - self.opening_balance

    def update_totals(self):
        """
        Recompute what this shop has billed and been paid.

        ⚠ THE BILLED SIDE IS FLOORED PER BILL, and the expression is IMPORTED.
        This was a fourth hand-rolled copy of `total_amount − discount_amount`
        — the exact defect CLAUDE.md records fixing in `supplier_billed`,
        `monthly_series` and `_insight_shops` — and it was the copy that had
        been left behind, so the model and the Profit page disagreed about the
        same bill.

        A discount larger than the bill it sits on makes that bill NEGATIVE,
        and here that subtracts from the shop's own balance: a real debt on
        other bills reads as smaller than it is, or vanishes. That breaks the
        rule that money owed is always reachable from exactly one screen, and
        it also lets `deactivate_supplier_shop` archive a shop the workshop
        still owes, because the guard reads this figure.

        The entry forms reject that input, but it is still reachable: this very
        method's sibling `SupplierRestockBill.update_totals()` recomputes
        `total_amount` from the bill's lines WITHOUT re-checking the discount,
        so deleting a line from an already-discounted bill pushes the discount
        above the new total.

        Imported locally rather than at module level: `analysis_engine` imports
        `workshop.models`, and this is the same guard `JobCard.update_totals`
        uses for `SHOP_LINE_COST`.
        """
        from django.db.models import Sum
        from django.db.models.functions import Coalesce

        from workshop.analysis_engine import SUPPLIER_BILL_COST

        billed = self.bills.aggregate(
            total=Coalesce(Sum(SUPPLIER_BILL_COST), 0, output_field=models.DecimalField())
        )['total']
        # The go-live debt joins the billed side HERE, so every reader of the
        # cached total — this shop's pages, the Profit page's payable tile,
        # Deep Analysis and the archive guard — follows it with no change.
        billed += self.opening_balance or Decimal('0')

        # Paid amount = Sum(amount) where is_trashed=False
        paid = self.payments.filter(is_trashed=False).aggregate(
            total=Coalesce(Sum('amount'), 0, output_field=models.DecimalField())
        )['total']
        
        if self.total_billed_amount != billed or self.total_paid_amount != paid:
            self.total_billed_amount = billed
            self.total_paid_amount = paid
            SupplierShop.objects.filter(pk=self.pk).update(
                total_billed_amount=billed, 
                total_paid_amount=paid
            )


class ShopCatalogItem(models.Model):
    shop = models.ForeignKey(SupplierShop, on_delete=models.CASCADE, related_name='catalog_items')
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='shop_catalogs')
    is_active = models.BooleanField(default=True, db_index=True, help_text="Deactivated catalog entries stay listed (greyed) but are excluded from restock bills")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('shop', 'item')

    def __str__(self):
        return f"{self.shop.name} - {self.item.name}"


class SupplierRestockBill(models.Model):
    supplier = models.ForeignKey(SupplierShop, on_delete=models.CASCADE, related_name='bills')
    bill_date = models.DateField(default=timezone.now, db_index=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-bill_date', '-created_at']
        constraints = [
            # AUD-0030: Database-level guard against negative bill amounts.
            # The app validates this in views too, but DB constraints are the last line of defence.
            models.CheckConstraint(
                check=models.Q(total_amount__gte=0),
                name='inventory_restockbill_total_amount_non_negative'
            ),
            models.CheckConstraint(
                check=models.Q(discount_amount__gte=0),
                name='inventory_restockbill_discount_amount_non_negative'
            ),
        ]

    def __str__(self):
        return f"Bill {self.id} - {self.supplier.name} ({self.bill_date})"

    @property
    def get_effective_amount(self):
        """What this bill actually costs after its discount.

        Floored at zero. A discount larger than the bill is always a typo, and
        letting it through produced a NEGATIVE expense that *increased* reported
        profit — a mistyped extra zero silently made the workshop look richer.
        The views reject that input outright; this floor is the second line of
        defence, so any row that already carries it cannot corrupt the Profit page.
        """
        effective = self.total_amount - self.discount_amount
        return effective if effective > Decimal('0') else Decimal('0')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.supplier.update_totals()

    def delete(self, *args, **kwargs):
        supplier = self.supplier
        super().delete(*args, **kwargs)
        supplier.update_totals()

    def update_totals(self):
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        new_total = self.items.aggregate(total=Coalesce(Sum('total_price'), 0, output_field=models.DecimalField()))['total']
        if self.total_amount != new_total:
            self.total_amount = new_total
            SupplierRestockBill.objects.filter(pk=self.pk).update(total_amount=new_total)
            self.supplier.update_totals()

            # The bill total is the denominator when a discount is shared out across
            # lines, so changing it changes every line's real unit cost. This has to
            # be triggered here rather than by a signal: the total is written with
            # `.update()` (no signal), and it is only known AFTER the lines have
            # saved — so a line's own post_save runs while the total is still stale
            # or zero, and would apportion against the wrong denominator.
            if self.discount_amount and self.discount_amount > Decimal('0'):
                from .costing import recompute_average_cost
                for item in Item.objects.filter(restock_items__bill=self).distinct():
                    recompute_average_cost(item)


class SupplierRestockItem(models.Model):
    bill = models.ForeignKey(SupplierRestockBill, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='restock_items')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.item.name} x {self.quantity}"

    @property
    def per_unit_price(self):
        """Gross price per unit, as written on the supplier's bill. Display only —
        costing uses `effective_unit_price` below."""
        if self.quantity and self.quantity > 0:
            return (self.total_price / self.quantity).quantize(Decimal('0.01'))
        return Decimal('0')

    @property
    def effective_unit_price(self):
        """
        What this line ACTUALLY cost per unit, after its share of the bill's
        discount. This is the figure warehouse costing must use.

        A bill-level discount is apportioned pro-rata across its lines by value,
        so a ₹2,000 discount on a ₹12,000 bill makes every line 1/6 cheaper.
        Without this, `avg_cost` was computed from gross prices while the Profit
        page expensed the discounted amount — the same purchase carried two
        different costs, and every discounted item looked less profitable than it
        really was.

        Falls back to the gross price when the bill total is zero (nothing to
        apportion against).
        """
        gross = self.per_unit_price
        if not self.bill_id:
            return gross
        total = self.bill.total_amount or Decimal('0')
        if total <= Decimal('0'):
            return gross
        # Full precision: the costing replay rounds once, at the end.
        return (self.total_price / self.quantity) * (self.bill.get_effective_amount / total) \
            if self.quantity and self.quantity > 0 else Decimal('0')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.bill.update_totals()

    def delete(self, *args, **kwargs):
        bill = self.bill
        super().delete(*args, **kwargs)
        bill.update_totals()


class OpeningStock(models.Model):
    """
    What was already on the shelf on go-live day — one row per product, typed
    once on Legacy Data → Opening Stock.

    It is a RECEIPT, like a line of a Supplies Shop bill, with one difference
    that is the whole point: it belongs to no shop, so it raises the shelf and
    creates NO balance. What the workshop owed for those goods is typed
    separately as a shop's `opening_balance` — on go-live day the two have no
    connection, because instalments and usage had long gone their own ways.

    Stock moves through the signals in `inventory/signals.py`, never from a
    view. The cost is REQUIRED: without it every part fitted before the next
    Supplies Shop bill would be charged ₹0 on the Profit page, permanently,
    because a later-dated bill never reaches back in the costing replay. In
    that replay this row is ALWAYS the first event, whatever day it was typed —
    it is the position the system started from.
    """
    item = models.OneToOneField(Item, on_delete=models.CASCADE, related_name='opening_stock')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0),
                                   name='inventory_openingstock_quantity_positive'),
            models.CheckConstraint(condition=models.Q(unit_cost__gt=0),
                                   name='inventory_openingstock_unit_cost_positive'),
        ]

    def __str__(self):
        return f"Opening stock: {self.item.name} x {self.quantity}"


class SupplierPayment(models.Model):
    PAYMENT_METHODS = [
        ('CASH', 'Cash'),
        ('UPI', 'UPI'),
        ('CARD', 'Card'),
        ('TRANSFER', 'Bank Transfer'),
    ]
    supplier = models.ForeignKey(SupplierShop, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='CASH')
    date = models.DateField(default=timezone.now, db_index=True)
    note = models.CharField(max_length=255, blank=True, null=True)
    is_trashed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']
        constraints = [
            # AUD-0030: Database-level guard against negative payment amounts.
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name='inventory_supplierpayment_amount_positive'
            ),
        ]

    def __str__(self):
        return f"₹{self.amount} → {self.supplier.name} ({self.date})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.supplier.update_totals()

    def delete(self, *args, **kwargs):
        supplier = self.supplier
        super().delete(*args, **kwargs)
        supplier.update_totals()
