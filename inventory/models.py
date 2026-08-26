# inventory/models.py
from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['category', 'name'], 
                name='unique_category_item_idx'
            )
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
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def get_pending_balance(self):
        return self.total_billed_amount - self.total_paid_amount

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
