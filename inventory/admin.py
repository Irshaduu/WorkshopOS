from django.contrib import admin
from .models import (
    Category, Item, ConsumptionRecord,
    SupplierShop, ShopCatalogItem,
    SupplierRestockBill, SupplierRestockItem, SupplierPayment,
)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'current_stock', 'average_stock', 'usage_count')
    list_filter = ('category',)
    search_fields = ('name',)

@admin.register(ConsumptionRecord)
class ConsumptionRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'item', 'quantity', 'date')
    list_filter = ('date', 'user')

# -----------------------------------------------------------------------------
# Supplier / Supplies Shops Admin
# -----------------------------------------------------------------------------

@admin.register(SupplierShop)
class SupplierShopAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'total_billed_amount', 'total_paid_amount', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)

@admin.register(ShopCatalogItem)
class ShopCatalogItemAdmin(admin.ModelAdmin):
    list_display = ('shop', 'item', 'created_at')
    list_filter = ('shop',)
    search_fields = ('shop__name', 'item__name')

@admin.register(SupplierRestockBill)
class SupplierRestockBillAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'bill_date', 'total_amount')
    list_filter = ('supplier', 'bill_date')
    search_fields = ('supplier__name',)

@admin.register(SupplierRestockItem)
class SupplierRestockItemAdmin(admin.ModelAdmin):
    list_display = ('bill', 'item', 'quantity', 'total_price')
    list_filter = ('bill__supplier',)
    search_fields = ('item__name',)

@admin.register(SupplierPayment)
class SupplierPaymentAdmin(admin.ModelAdmin):
    list_display = ('supplier', 'amount', 'payment_method', 'date', 'is_trashed')
    list_filter = ('payment_method', 'is_trashed', 'supplier')
    search_fields = ('supplier__name', 'note')
