from django.contrib import admin
from .models import (
    CarBrand,
    CarModel,
    SparePart,
    ConcernSolution,
    JobCard,
    JobCardConcern,
    JobCardSpareItem,
    JobCardLabourItem,
    UserProfile,
    Mechanic,
    BulkPayer,
    BulkPaymentHistory
)

# -------------------------
# AUTHENTICATION & USERS
# -------------------------

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'mobile_number')
    search_fields = ('user__username', 'mobile_number')


@admin.register(Mechanic)
class MechanicAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


# -------------------------
# STUDY SECTION
# -------------------------

@admin.register(CarBrand)
class CarBrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)
    ordering = ('name',)
    exclude = ('logo_image',)  # Hide image upload - using default car symbol


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = ('name', 'brand', 'created_at')
    list_filter = ('brand',)
    search_fields = ('name', 'brand__name')
    ordering = ('brand__name', 'name')


@admin.register(SparePart)
class SparePartAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(ConcernSolution)
class ConcernSolutionAdmin(admin.ModelAdmin):
    list_display = ('concern', 'created_at')
    search_fields = ('concern',)
    ordering = ('-created_at',)


# -------------------------
# JOB CARD SECTION
# -------------------------

class JobCardConcernInline(admin.TabularInline):
    model = JobCardConcern
    extra = 1


class JobCardSpareItemInline(admin.TabularInline):
    model = JobCardSpareItem
    extra = 1


class JobCardLabourItemInline(admin.TabularInline):
    model = JobCardLabourItem
    extra = 1


@admin.register(JobCard)
class JobCardAdmin(admin.ModelAdmin):
    list_display = (
        'registration_number',
        'customer_name',
        'brand_name',
        'model_name',
        'updated_at'
    )
    list_filter = ('brand_name',)
    search_fields = (
        'registration_number',
        'customer_name',
        'brand_name',
        'model_name'
    )
    ordering = ('-updated_at',)
    date_hierarchy = 'updated_at'

    inlines = [JobCardConcernInline, JobCardSpareItemInline, JobCardLabourItemInline]


@admin.register(BulkPayer)
class BulkPayerAdmin(admin.ModelAdmin):
    list_display = ('customer_name', 'is_trashed', 'created_at')
    list_filter = ('is_trashed',)
    search_fields = ('customer_name',)


@admin.register(BulkPaymentHistory)
class BulkPaymentHistoryAdmin(admin.ModelAdmin):
    list_display = ('bulk_payer', 'amount', 'payment_method', 'jobs_affected', 'created_at')
    list_filter = ('payment_method',)
    search_fields = ('bulk_payer__customer_name',)

