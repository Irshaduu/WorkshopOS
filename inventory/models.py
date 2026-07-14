# inventory/models.py
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
        average_stock (FloatField): Threshold for low-stock warnings.
        current_stock (FloatField): Real-time quantity on hand.
        usage_count (FloatField): Popularity score for smart-sorting.
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
    average_stock = models.FloatField(default=0, help_text="Ideal stock level for calculation")
    current_stock = models.FloatField(default=0)
    usage_count = models.FloatField(default=0, help_text="Cached popularity score (frequency of use)")

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
    quantity = models.FloatField()
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
        from django.db.models import Sum, F
        from django.db.models.functions import Coalesce
        
        # Billed amount = Sum(total_amount - discount_amount)
        billed = self.bills.aggregate(
            total=Coalesce(Sum(F('total_amount') - F('discount_amount')), 0, output_field=models.DecimalField())
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
        return self.total_amount - self.discount_amount

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


class SupplierRestockItem(models.Model):
    bill = models.ForeignKey(SupplierRestockBill, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='restock_items')
    quantity = models.FloatField()
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.item.name} x {self.quantity}"

    @property
    def per_unit_price(self):
        if self.quantity and self.quantity > 0:
            return round(float(self.total_price) / float(self.quantity), 2)
        return 0

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
