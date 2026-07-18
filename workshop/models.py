from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_out
from django.dispatch import receiver
from django.utils import timezone

# -----------------------------------------------------------------------------
# 0. AUTHENTICATION & USERS
# -----------------------------------------------------------------------------

class UserProfile(models.Model):
    """
    Extends the base Django User with workshop-specific identity.
    
    Attributes:
        user (OneToOneField): Link to standard Django User.
        mobile_number (CharField): Verified mobile used for:
          1. Alternative login identifier (last-10-digit normalised match)
          2. OTP delivery target (forgot-password flow)
          3. Security alert recipient (Twilio SMS on every login)
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    mobile_number = models.CharField(max_length=20, blank=True, null=True, help_text="Used for Owner OTP login")

    def __str__(self):
        return f"{self.user.username}'s Profile"


# -----------------------------------------------------------------------------
# SECURITY MODELS
# -----------------------------------------------------------------------------
class FailedAttempt(models.Model):
    """
    Tracks failed login attempts by IP address to prevent brute-force attacks.
    Part of the 'Steel Gate' security suite. Unlike session-based lockouts, 
    this cannot be bypassed by clearing browser cookies.
    
    Attributes:
        ip_address (GenericIPAddressField): Unique network identity of the visitor.
        failures (PositiveIntegerField): Consecutive failed login or OTP attempts.
        last_attempt (DateTimeField): Timestamp of the most recent failure.
    """
    ip_address = models.GenericIPAddressField(unique=True)
    failures = models.PositiveIntegerField(default=0)
    last_attempt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"IP {self.ip_address}: {self.failures} failures"

class UserSession(models.Model):
    """
    Tracks active login sessions for HQ Command Center monitoring.
    Allows owners (Sahad/Rijas) to identify and revoke unauthorized access.
    
    Attributes:
        user (ForeignKey): The authenticated user (Owner, Office, or Floor).
        session_key (CharField): The unique Django session identifier.
        ip_address (GenericIPAddressField): The visitor's network IP.
        user_agent (TextField): Raw browser identification string.
        last_activity (DateTimeField): Indexed timestamp for session cleanup & monitoring.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_key = models.CharField(max_length=40, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    last_activity = models.DateTimeField(auto_now=True, db_index=True)

    def __str__(self):
        return f"Session {self.session_key} for {self.user.username}"

    @staticmethod
    def get_device_name(user_agent_string):
        """
        Parses a User-Agent string into a premium, specific device name.
        Used for both the dashboard display and real-time security alerts.
        """
        ua = (user_agent_string or "")
        ua_lower = ua.lower()
        
        # 1. Identify specific Mobile Hardware
        device = "Desktop"
        if "iphone" in ua_lower:
            device = "iPhone"
        elif "ipad" in ua_lower:
            device = "iPad"
        elif "android" in ua_lower:
            if "sm-" in ua_lower or "samsung" in ua_lower:
                device = "Samsung Galaxy"
            elif "pixel" in ua_lower:
                device = "Google Pixel"
            elif "nexus" in ua_lower:
                device = "Nexus"
            else:
                device = "Android Phone"
        elif "macintosh" in ua_lower and "mobile" not in ua_lower:
            device = "Macbook"
        elif "windows" in ua_lower:
            device = "Windows PC"
        elif "linux" in ua_lower and "android" not in ua_lower:
            device = "Linux Workstation"
            
        # 2. Browser Name
        browser = "Web Browser"
        if 'edg/' in ua_lower or 'edge/' in ua_lower:
            browser = "Microsoft Edge"
        elif 'chrome' in ua_lower:
            browser = "Google Chrome"
        elif 'firefox' in ua_lower:
            browser = "Mozilla Firefox"
        elif 'safari' in ua_lower and 'chrome' not in ua_lower:
            browser = "Apple Safari"
        elif 'iphone' in ua_lower:
            # Standard iPhone assumption for non-Chrome/Edge browsers
            browser = "Apple Safari"
            
        return f"{browser} on {device}"

    @property
    def device_info(self):
        """Returns the specific device string for the dashboard."""
        return self.get_device_name(self.user_agent)
# -----------------------------------------------------------------------------
# 1. STUDY SECTION MODELS
# These models act as the "Master Lists" for autocomplete suggestions.
# -----------------------------------------------------------------------------

class Mechanic(models.Model):
    """
    Represents a mechanic working in the shop.
    Used for tracking who performed jobs without requiring individual logins.
    """
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True, help_text="Disable if the mechanic leaves the company")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CarBrand(models.Model):
    """
    Represents a Car Brand (e.g., Toyota, BMW).
    Used for the Study section grid and autocomplete source.
    """
    name = models.CharField(max_length=100, unique=True)
    logo_image = models.ImageField(upload_to='brands/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CarModel(models.Model):
    """
    Represents a Car Model (e.g., Corolla, 3 Series) linked to a Brand.
    Used for the Study section grid and autocomplete source.
    """
    brand = models.ForeignKey(CarBrand, on_delete=models.CASCADE, related_name='models')
    name = models.CharField(max_length=100, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('brand', 'name')
        ordering = ['name']

    def __str__(self):
        return f"{self.brand.name} {self.name}"


class SparePart(models.Model):
    """
    Represents a common Spare Part name (e.g., Oil Filter, Brake Pad).
    Used as the master list for autocomplete suggestions.
    """
    name = models.CharField(max_length=150, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ConcernSolution(models.Model):
    """
    Knowledge base for common Concerns.
    """
    concern = models.TextField(help_text="e.g., Sound when applying brake", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.concern[:50]}..."


class SpareShop(models.Model):
    """
    Master list of spare parts suppliers/shops.
    Used for financial tracking of what the workshop owes to each shop.
    Listed shops appear in the Job Card spare-parts dropdown.
    The cascade payment algorithm distributes lump-sum payments oldest-invoice-first.
    """
    name = models.CharField(max_length=150, unique=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.CharField(max_length=300, blank=True, null=True)
    total_purchased_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_trashed = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def update_totals(self):
        """
        Calculates and caches the sum of all purchased parts (shop cost) vs total payments.
        Uses pure SQL aggregation for efficiency.
        """
        from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Value
        from django.db.models.functions import Coalesce
        from decimal import Decimal

        purchases = self.spare_items.aggregate(
            total=Coalesce(
                Sum(
                    ExpressionWrapper(
                        Coalesce(F('unit_price'), Value(Decimal('0'), output_field=DecimalField())) * 
                        Coalesce(F('quantity'), Value(Decimal('1'), output_field=DecimalField())),
                        output_field=DecimalField()
                    )
                ),
                Value(Decimal('0'), output_field=DecimalField()),
                output_field=DecimalField()
            )
        )['total']

        payments = self.payments.filter(is_trashed=False).aggregate(
            total=Coalesce(Sum('amount'), Value(Decimal('0'), output_field=DecimalField()), output_field=DecimalField())
        )['total']

        self.total_purchased_amount = purchases
        self.total_paid_amount = payments
        self.save(update_fields=['total_purchased_amount', 'total_paid_amount'])

    @property
    def get_pending_balance(self):
        return self.total_purchased_amount - self.total_paid_amount

    def __str__(self):
        return self.name


# -----------------------------------------------------------------------------
# 2. JOB CARD SECTION MODELS
# These handle the daily work. loosely coupled to Study models via text fields.
# -----------------------------------------------------------------------------

class JobCard(models.Model):
    """
    The Industrial Heart of WorkshopOS. Manages the end-to-end lifecycle 
    of a vehicle service, from admission to billing.
    
    Key Features:
    - Auto-Generating Bill Numbers (JB-26-001)
    - Triple-Tier Security States (Active, Delivered, Billed)
    - Soft-Delete 'Trash' Architecture for 100% data integrity.
    - Denormalized Financials for sub-50ms dashboard loading.
    """
    # Bill Number (Auto-generated)
    bill_number = models.CharField(
        max_length=20, 
        unique=True, 
        blank=True,
        null=True,
        help_text="Auto-generated bill number (e.g. JB-26-001)"
    )
    
    # Dates
    admitted_date = models.DateField(db_index=True)
    discharged_date = models.DateField(db_index=True, blank=True, null=True, help_text="Auto-filled when job is marked as delivered")
    
    # Delivery Status (separate from planning date)
    delivered = models.BooleanField(default=False, db_index=True, help_text="Actually delivered (marked via Delivered button)")
    
    # On Hold Status (for jobs waiting for parts or paused)
    on_hold = models.BooleanField(default=False, help_text="Job is on hold (waiting for parts, etc.)")

    # Soft Delete (Trash System)
    is_deleted = models.BooleanField(default=False, db_index=True, help_text="Hide from main list (moved to trash)")


    # Vehicle Details (Text fields with Autocomplete)
    brand_name = models.CharField(max_length=100, db_index=True)
    model_name = models.CharField(max_length=100, db_index=True)
    registration_number = models.CharField(max_length=50, db_index=True)
    mileage = models.CharField(max_length=20, blank=True, null=True, help_text="e.g. 50000 or 50k")

    # NEW: Car Color
    COLOR_CHOICES = [
        ('Black', 'Black'),
        ('White', 'White'),
        ('Silver', 'Silver'),
        ('Grey', 'Grey'),
        ('Red', 'Red'),
        ('Light Blue', 'Light Blue'),
        ('Blue', 'Blue'),
        ('Dark Blue', 'Dark Blue'),
        ('Yellow', 'Yellow'),
        ('Light Green', 'Light Green'),
        ('Green', 'Green'),
        ('Dark Green', 'Dark Green'),
        ('Brown', 'Brown'),
        ('Dark Brown', 'Dark Brown'),
        ('Other', 'Other'),
    ]
    car_color = models.CharField(max_length=50, choices=COLOR_CHOICES, blank=True, null=True)
    car_color_other = models.CharField(max_length=100, blank=True, null=True, help_text="Specific color name if 'Other' is selected")

    # Customer Details
    customer_name = models.CharField(max_length=150, db_index=True, blank=True, null=True)
    customer_contact = models.CharField(max_length=20, blank=True, null=True)

    # Assignment
    lead_mechanic = models.ForeignKey(Mechanic, on_delete=models.SET_NULL, null=True, blank=True, related_name='job_cards', help_text="The main mechanic assigned to this job")
    bulk_payer = models.ForeignKey('BulkPayer', on_delete=models.SET_NULL, null=True, blank=True, related_name='job_cards', help_text="Assigned bulk payer")

    # Financials (NEW - Optimized for 1M+ records)
    total_bill_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Denormalized total for instant dashboard loading")
    received_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Amount actually received from customer")
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Internal discount tracking (calculated on Paid status)")
    
    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending (Unpaid)'),
        ('PAID', 'Fully Paid'),
        ('PARTIAL', 'Partially Paid'),
        ('BULK_PAID', 'Bulk Paid'),
    ]
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='PENDING', db_index=True)
    
    PAYMENT_METHOD_CHOICES = [
        ('CASH', 'Cash'),
        ('UPI', 'UPI / QR Code'),
        ('CARD', 'Credit/Debit Card'),
        ('TRANSFER', 'Bank Transfer'),
    ]
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True, null=True)

    # Meta
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        # High-performance composite index for the dashboard query pattern.
        # Covered: (is_deleted=False, delivered=False) sorted by updated_at DESC.
        indexes = [
            models.Index(fields=['is_deleted', 'delivered', '-updated_at']),
        ]
        verbose_name = "Job Card"
        verbose_name_plural = "Job Cards"

    def clean(self):
        """
        Normalize key text fields before saving to prevent ghost duplicates.
        e.g., 'kl-01 ab 1234' and 'KL-01 AB 1234' would create two separate
        records without this normalization. (AUD-0016, AUD-0027)
        """
        if self.registration_number:
            # Strip whitespace and uppercase: 'kl 01 ab 1234' → 'KL 01 AB 1234'
            self.registration_number = self.registration_number.strip().upper()
        if self.brand_name:
            # Strip and title-case: 'toyota  ' → 'Toyota'
            self.brand_name = ' '.join(self.brand_name.split()).title()

    def save(self, *args, **kwargs):
        """
        Auto-generate bill number if not set.
        Thread-safe implementation to prevent duplicate numbers
        when multiple users create job cards simultaneously.
        AUD-0016/0027: Always call clean() to normalize casing even on
        direct .save() calls (e.g. management commands, shell).
        """
        # Normalize fields regardless of whether called via form or directly
        self.clean()
        from django.db import transaction
        
        if not self.bill_number:
            with transaction.atomic():
                # Get year (2 digits)
                year = str(self.admitted_date.year)[2:]  # 2026 → "26"
                
                # Lock and count existing bills for this year
                # select_for_update() prevents race conditions
                last_job = JobCard.objects.select_for_update().filter(
                    bill_number__startswith=f'JB-{year}-'
                ).order_by('-bill_number').first()
                
                if last_job and last_job.bill_number:
                    # Extract number from last bill (e.g., "JB-26-005" → 5)
                    try:
                        last_num = int(last_job.bill_number.split('-')[-1])
                        next_num = last_num + 1
                    except (ValueError, IndexError):
                        # Fallback if bill number format is unexpected
                        next_num = JobCard.objects.filter(
                            bill_number__startswith=f'JB-{year}-'
                        ).count() + 1
                else:
                    # First bill of the year
                    next_num = 1
                
                # Create bill number (pad with zeros)
                self.bill_number = f'JB-{year}-{str(next_num).zfill(3)}'
        
        super().save(*args, **kwargs)
    
    def update_totals(self):
        """
        Calculates and saves the denormalized total_bill_amount.
        This eliminates expensive on-the-fly calculations for 1M+ records.
        """
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        
        spare_total = self.spares.aggregate(total=Coalesce(Sum('total_price'), 0, output_field=models.DecimalField()))['total']
        labour_total = self.labours.aggregate(total=Coalesce(Sum('amount'), 0, output_field=models.DecimalField()))['total']
        
        new_total = spare_total + labour_total
        if self.total_bill_amount != new_total:
            self.total_bill_amount = new_total
            # Use update to avoid triggering save() recursion if called from save()
            JobCard.objects.filter(pk=self.pk).update(total_bill_amount=new_total)
            if self.bulk_payer_id:
                self.bulk_payer.update_totals()

    def __str__(self):
        return f"{self.bill_number or f'#{self.id}'}"

    @property
    def get_car_color_hex(self):
        """Returns the CSS/Hex color code for the car_color choice."""
        if self.car_color == 'Other' and self.car_color_other:
            # Check if it looks like a hex code (starts with #)
            if self.car_color_other.startswith('#'):
                return self.car_color_other
        
        mapping = {
            'Black': '#000000',
            'White': '#f8fafc',  # Off-white for better visibility
            'Silver': '#94a3b8', # Deeper metallic silver
            'Grey': '#64748b',   # Slate Grey
            'Red': '#dc2626',
            'Light Blue': '#38bdf8',
            'Blue': '#2563eb',
            'Dark Blue': '#1d4ed8',
            'Yellow': '#eab308',
            'Light Green': '#4ade80',
            'Green': '#16a34a',
            'Dark Green': '#15803d',
            'Brown': '#78350f',
            'Dark Brown': '#451a03',
        }
        return mapping.get(self.car_color, '#475569') # Solid Slate for unassigned

    @property
    def get_car_color_display(self):
        """Returns the color name (either standard choice or 'Other' text)."""
        if self.car_color == 'Other':
            return self.car_color_other or 'Other'
        return self.car_color or 'Unknown'

    @property
    def get_total_amount(self):
        """Calculates total bill amount. Returns the denormalized value for performance."""
        return self.total_bill_amount or 0

    @property
    def get_balance_amount(self):
        """Calculates remaining balance."""
        return max(0, self.get_total_amount - (self.received_amount or 0))

    @property
    def get_completion_percentage(self):
        """
        Calculates completion percentage based on FIXED concerns.
        AUD-0045: Uses annotated total_concerns and fixed_concerns if available to prevent N+1 queries.
        """
        if hasattr(self, 'total_concerns') and hasattr(self, 'fixed_concerns'):
            if self.total_concerns == 0:
                return 0
            return int((self.fixed_concerns / self.total_concerns) * 100)

        total = self.concerns.count()
        if total == 0:
            return 0
        fixed = self.concerns.filter(status='FIXED').count()
        return int((fixed / total) * 100)


class JobCardConcern(models.Model):
    """
    Specific concerns reported by the customer for a specific Job Card.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('WORKING', 'Working'),
        ('FIXED', 'Fixed'),
    ]

    job_card = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name='concerns')
    concern_text = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')

    def save(self, *args, **kwargs):
        if self.concern_text:
            self.concern_text = self.concern_text.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.concern_text[:50]} ({self.get_status_display()})"


class JobCardSpareItem(models.Model):
    """
    Spare parts used in the job.
    Tracks ordering workflow with shop and dates.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ORDERED', 'Ordered'),
        ('RECEIVED', 'Received'),
    ]

    job_card = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name='spares', null=True, blank=True)
    spare_part_name = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    quantity = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    
    # Pricing (unit_price = shop cost, total_price = customer price with markup)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Shop price (cost)")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Customer price (with markup)")
    
    # Order tracking
    shop_name = models.CharField(max_length=100, blank=True, null=True, help_text="Shop where part was ordered (text copy for display)")
    shop = models.ForeignKey('SpareShop', on_delete=models.SET_NULL, null=True, blank=True, related_name='spare_items', help_text="Linked SpareShop profile")
    ordered_date = models.DateField(blank=True, null=True, db_index=True, help_text="Auto-filled when status → ORDERED")
    received_date = models.DateField(blank=True, null=True, db_index=True, help_text="Auto-filled when status → RECEIVED")
    original_vehicle_info = models.CharField(max_length=255, blank=True, null=True, help_text="Stores car details if unassigned from a job card")

    def save(self, *args, **kwargs):
        if self.spare_part_name:
            self.spare_part_name = self.spare_part_name.strip()
        super().save(*args, **kwargs)
        if self.job_card:
            self.job_card.update_totals()
        if self.shop:
            self.shop.update_totals()

    def delete(self, *args, **kwargs):
        job_card = self.job_card
        shop = self.shop
        super().delete(*args, **kwargs)
        if job_card:
            job_card.update_totals()
        if shop:
            shop.update_totals()

    def __str__(self):
        return f"{self.spare_part_name} ({self.quantity})"


class JobCardLabourItem(models.Model):
    """
    Labour charges added to a Job Card (independent of spares).
    """
    job_card = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name='labours')
    job_description = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True) 

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.job_card.update_totals()

    def delete(self, *args, **kwargs):
        job_card = self.job_card
        super().delete(*args, **kwargs)
        job_card.update_totals()

    def __str__(self):
        return self.job_description


class BulkPayer(models.Model):
    """
    Persistent Bulk Payment group for fleet/repeat customers.
    Car dealers and close customers who accumulate bills over time
    and pay in large lump sums (₹50K-₹1L+). The cascade payment
    algorithm distributes payments oldest-first.
    """
    customer_name = models.CharField(max_length=150, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_trashed = models.BooleanField(default=False, db_index=True)
    
    total_billed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    advance_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Running credit from overpayments. Applied automatically on the next payment."
    )

    class Meta:
        ordering = ['customer_name']

    def update_totals(self):
        """
        Calculates and caches the sum of all assigned job cards vs total payments.
        """
        from django.db.models import Sum, DecimalField, Value
        from django.db.models.functions import Coalesce
        from decimal import Decimal

        billed = self.job_cards.aggregate(
            total=Coalesce(Sum('total_bill_amount'), Value(Decimal('0'), output_field=DecimalField()), output_field=DecimalField())
        )['total']

        payments = self.payment_history.filter(is_trashed=False).aggregate(
            total=Coalesce(Sum('amount'), Value(Decimal('0'), output_field=DecimalField()), output_field=DecimalField())
        )['total']

        self.total_billed_amount = billed
        self.total_paid_amount = payments
        self.save(update_fields=['total_billed_amount', 'total_paid_amount'])

    @property
    def get_pending_balance(self):
        return self.total_billed_amount - self.total_paid_amount

    def __str__(self):
        return self.customer_name


class BulkPaymentHistory(models.Model):
    """
    Audit trail for every bulk payment transaction.
    Records who paid, how much, when, and which job cards were affected.
    Each record can be individually deleted (reversed).
    """
    PAYMENT_METHODS = [
        ('CASH', 'Cash'),
        ('UPI', 'UPI'),
        ('CARD', 'Card'),
        ('TRANSFER', 'Bank Transfer'),
    ]
    bulk_payer = models.ForeignKey(BulkPayer, on_delete=models.CASCADE, related_name='payment_history')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='CASH')
    jobs_affected = models.PositiveIntegerField(default=0)
    details = models.TextField(blank=True, help_text="JSON snapshot of which jobs got how much")
    is_trashed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.bulk_payer:
            self.bulk_payer.update_totals()

    def delete(self, *args, **kwargs):
        bulk_payer = self.bulk_payer
        super().delete(*args, **kwargs)
        if bulk_payer:
            bulk_payer.update_totals()

    def __str__(self):
        return f"₹{self.amount} → {self.bulk_payer.customer_name} ({self.created_at:%d %b %Y})"


class SpareShopPayment(models.Model):
    """
    Audit trail for every payment made to a spare shop.
    Supports both individual item (Pay Now) and lump-sum cascade payments.
    Each record stores a JSON snapshot so it can be fully reversed by the Owner.
    """
    PAYMENT_METHODS = [
        ('CASH', 'Cash'),
        ('UPI', 'UPI'),
        ('CARD', 'Card'),
        ('TRANSFER', 'Bank Transfer'),
    ]
    shop = models.ForeignKey(SpareShop, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='CASH')
    note = models.CharField(max_length=255, blank=True, null=True, help_text="Optional description or reference")
    is_trashed = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.shop:
            self.shop.update_totals()

    def delete(self, *args, **kwargs):
        shop = self.shop
        super().delete(*args, **kwargs)
        if shop:
            shop.update_totals()

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # AUD-0030: Database-level guard against negative payment amounts.
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name='workshop_spareshoppayment_amount_positive'
            ),
        ]

    def __str__(self):
        return f"₹{self.amount} → {self.shop.name} ({self.created_at:%d %b %Y})"

# -----------------------------------------------------------------------------
# CASHBOOK / GENERAL EXPENSES & INCOME
# -----------------------------------------------------------------------------
class CashbookEntry(models.Model):
    """
    General Ledger for tracking day-to-day workshop expenses and miscellaneous income.
    Data is utilized by the Owner Analysis module for true profit calculations.
    """
    ENTRY_TYPES = [
        ('INCOME', 'Income (Cash In)'),
        ('EXPENSE', 'Expense (Cash Out)'),
    ]
    PAYMENT_METHODS = [
        ('CASH', 'Cash'),
        ('UPI', 'UPI'),
        ('CARD', 'Card'),
        ('TRANSFER', 'Bank Transfer'),
    ]

    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPES)
    category = models.CharField(max_length=100) # Free text: Expense name or Income name
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='CASH')
    description = models.TextField(blank=True, null=True) # Optional note
    date = models.DateField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['-date', '-created_at']),
            models.Index(fields=['entry_type', '-date']),
        ]
        constraints = [
            # AUD-0030: Database-level guard — cashbook amounts must be positive.
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name='workshop_cashbookentry_amount_positive'
            ),
        ]

    def __str__(self):
        return f"{self.get_entry_type_display()} - {self.category}: ₹{self.amount} ({self.get_payment_method_display()})"



@receiver(user_logged_out)
def on_user_logout(sender, request, user, **kwargs):
    """
    When an owner logs out manually, immediately delete their UserSession record
    so the 'Active Now' dashboard stays 100% accurate.
    """
    if user:
        UserSession.objects.filter(session_key=request.session.session_key).delete()
