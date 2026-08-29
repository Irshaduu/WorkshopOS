import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
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
        mobile_number (CharField): Alternative login identifier, matched on the
          last 10 digits so stored/typed formats need not agree. Unique, because
          login resolves an identifier to exactly one account — two profiles
          sharing a number would make that resolution ambiguous.

    Store an absent number as NULL, never as "". `unique=True` permits any number
    of NULLs but only one empty string, so a blank-string default would collide
    on the second account that has no mobile.

    Password-reset codes go to `User.email`, not here — see the OTP flow.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    mobile_number = models.CharField(
        max_length=20, blank=True, null=True, unique=True,
        help_text="Optional alternative login identifier. Leave empty for Office/Floor accounts.",
    )

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

class Notification(models.Model):
    """
    One row per recipient per event — the in-app feed behind the nav bell.

    **Fanned out on write, deliberately.** A single row plus read-receipts would
    normalise better, but this workshop has two owners: writing N rows makes the
    unread count one indexed query and mark-as-read a single update, instead of
    an anti-join on every page load.

    **No ForeignKey to the thing it is about.** Most of these announce a
    *deletion*, and a FK would cascade the notification away with its subject —
    the record of "job card #412 was deleted" would vanish exactly when it
    mattered. `object_type` / `object_id` are a soft reference, and `body`
    carries a frozen human-readable label, the same discipline
    `DeletionLog.snapshot` uses.

    Created inside the caller's transaction (same database, cheap); anything
    external — push, email — belongs on `transaction.on_commit`, never inline.
    """
    SEVERITY_CRITICAL = 'CRITICAL'
    SEVERITY_INFO = 'INFO'
    SEVERITY_CHOICES = [
        (SEVERITY_CRITICAL, 'Critical'),
        (SEVERITY_INFO, 'Info'),
    ]

    # Read notifications are cleared after a fortnight. They have already been
    # seen, and the permanent record of anything that mattered lives in
    # DeletionLog, the audit pages or the ledgers — this table is a feed, not an
    # archive, and keeping it short is what stops it becoming one.
    RETENTION_DAYS = 14

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    event = models.CharField(max_length=32, db_index=True)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default=SEVERITY_INFO)
    title = models.CharField(max_length=120)
    # THE ROW IS THREE STRINGS, AND EACH ANSWERS A DIFFERENT QUESTION.
    #
    #   body    "Biljo · ₹1,00,000 payment deleted"   <- the loud line
    #   title   "Record deleted"                       <- the category
    #   detail  "Spare-Shop Payment"                   <- the context
    #
    # `body` is a COMPLETE STATEMENT ending in what happened, so the loud line
    # can be understood on its own without reading anything under it. `detail`
    # exists because that statement has to stay short: the device a sign-in came
    # from, the kind of record deleted, the remedy for a lockout — all real, none
    # of it worth the loud line. Before this column those facts were crammed into
    # `body`, which is what made the headline wrap to three lines on a phone.
    body = models.CharField(max_length=255, blank=True)
    detail = models.CharField(
        max_length=255, blank=True,
        help_text="Supporting context, printed under the headline beside the category",
    )
    url = models.CharField(max_length=200, blank=True, help_text="Deep link to the thing this is about")
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='notifications_caused', help_text="Who did it, if anyone",
    )
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'read_at']),
            models.Index(fields=['recipient', '-created_at']),
        ]

    def __str__(self):
        return f"{self.title} -> {self.recipient.username}"

    @property
    def is_unread(self):
        return self.read_at is None

    @property
    def actor_label(self):
        """
        Who caused this — unless the body has already said so.

        The feed row prints the actor at the end of its quiet second line, and
        on the two sign-in events the actor IS the subject — so without this the
        row reads "Floor signed in" over "Staff signed in · Floor · Chrome on
        Windows PC". The same name twice, on the events that fire most often.

        A general rule rather than a per-event exception in the template: if the
        body opens with the actor's name, the second line has nothing left to
        add. Everywhere else the actor is the fact the body does NOT carry —
        which of the two owners deleted the payment, who created the login.

        Costs no query: both views that render a row already `select_related`
        the actor.
        """
        if not self.actor_id:
            return ''
        name = self.actor.username
        if self.body and self.body.lower().startswith(name.lower()):
            return ''
        return name

    @classmethod
    def unread_count(cls, user):
        if not getattr(user, 'is_authenticated', False):
            return 0
        return cls.objects.filter(recipient=user, read_at__isnull=True).count()

    @classmethod
    def mark_all_read(cls, user):
        return cls.objects.filter(recipient=user, read_at__isnull=True).update(
            read_at=timezone.now()
        )

    @classmethod
    def purge_old(cls):
        """
        Drop read notifications past the retention window.

        Unread ones are never swept, however old — an owner who has not looked
        in three months should still find what they missed. Run from the feed
        view on visit, the same cheap pattern `manage_dashboard` uses for ghost
        sessions.
        """
        cutoff = timezone.now() - timedelta(days=cls.RETENTION_DAYS)
        deleted, _ = cls.objects.filter(
            read_at__isnull=False, created_at__lt=cutoff
        ).delete()
        return deleted


class PushSubscription(models.Model):
    """
    One browser's permission to receive Web Push, per device.

    **Per device, not per user.** An owner with a phone and a laptop has two
    rows; revoking notifications on one must not silence the other. `endpoint`
    is the push service's own URL for that browser instance and is what makes it
    unique — a reinstall or a permission reset produces a *new* endpoint rather
    than reusing the old one, which is why dead rows accumulate and have to be
    reaped (see `failure_count`).

    Nothing here is a secret of ours: `p256dh` and `auth` are the browser's own
    public key material, used to encrypt payloads *to* that browser.

    Push is strictly a delivery layer over `Notification` rows. If every
    subscription here is dead, the feed is unaffected.
    """
    MAX_FAILURES = 3

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=200, help_text="Browser's public key")
    auth = models.CharField(max_length=100, help_text="Browser's auth secret")
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_success = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user'])]
        verbose_name = "Push Subscription"

    def __str__(self):
        return f"{self.user.username} — {UserSession.get_device_name(self.user_agent)}"

    @property
    def device_name(self):
        return UserSession.get_device_name(self.user_agent)

    def as_dict(self):
        """The shape pywebpush expects."""
        return {
            'endpoint': self.endpoint,
            'keys': {'p256dh': self.p256dh, 'auth': self.auth},
        }


class AccountLockout(models.Model):
    """
    Failed sign-in attempts counted **per account**.

    `FailedAttempt` counts by IP, which is the wrong unit for this workshop: the
    laptop, the tablet and both owners' phones all leave through one connection.
    Five fumbled attempts on the Floor tablet therefore locked the owners out of
    their own phones for fifteen minutes — the attack and the collateral damage
    were indistinguishable. Counting per account locks only the account being
    guessed at and leaves everyone else working.

    The IP gate is kept as a backstop against someone spraying *many* accounts
    from one place, but at a much higher threshold (`IP_FAILURE_LIMIT` in
    `auth_views`) so ordinary shared-connection use never trips it.
    """
    MAX_FAILURES = 5
    LOCKOUT_MINUTES = 15

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='lockout')
    failures = models.PositiveIntegerField(default=0)
    last_attempt = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Account Lockout"

    def __str__(self):
        return f"{self.user.username}: {self.failures} failed attempt(s)"

    @property
    def locked_until(self):
        return self.last_attempt + timedelta(minutes=self.LOCKOUT_MINUTES)

    @classmethod
    def minutes_remaining(cls, user):
        """Minutes this account stays locked, or 0 if it is not locked."""
        row = cls.objects.filter(user=user).first()
        if row is None or row.failures < cls.MAX_FAILURES:
            return 0

        remaining = (row.locked_until - timezone.now()).total_seconds()
        if remaining <= 0:
            # Window elapsed — the count resets rather than lingering, so an old
            # bad day never shortens the budget on a good one.
            row.failures = 0
            row.save(update_fields=['failures'])
            return 0
        return int(remaining // 60) + 1

    @classmethod
    def record_failure(cls, user):
        row, _ = cls.objects.get_or_create(user=user)
        # F() would avoid a read, but auto_now on last_attempt needs a save()
        # anyway and this table sees a handful of rows.
        row.failures += 1
        row.save(update_fields=['failures', 'last_attempt'])
        return row.failures

    @classmethod
    def clear(cls, user):
        cls.objects.filter(user=user).update(failures=0)


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


class PasswordResetOTP(models.Model):
    """
    A single-use 6-digit code emailed to an owner who has forgotten their password.

    **Why a code and not a reset link.** On iOS an installed PWA has its own
    cookie jar, separate from the browser — so a link tapped in the mail app
    opens in Safari/Chrome and completes the reset in a *different* session,
    leaving the app itself still signed out. A code has no such dependency: it is
    plain text, so the reset finishes in the same session that requested it, on
    any OS, installed or not. That is worth the extra code in this file.

    **Why the DB and not the session.** The throttle is the reason. Session-held
    counters are defeated by clearing cookies, which would let someone hammer the
    mail provider until the sending quota burns and the domain gets flagged.
    Every limit below is therefore counted per *account*, in the database.

    The code itself is never stored — only its SHA-256 hash, compared in constant
    time. A database dump does not hand over a live reset.
    """
    CODE_LENGTH = 6
    VALIDITY_MINUTES = 10
    MAX_ATTEMPTS = 5
    RESEND_COOLDOWN_SECONDS = 60
    MAX_REQUESTS_PER_HOUR = 3

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reset_codes')
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]
        verbose_name = "Password Reset Code"

    def __str__(self):
        return f"Reset code for {self.user.username} ({self.created_at:%d %b %Y %H:%M})"

    # ------------------------------------------------------------------
    @staticmethod
    def _hash(code):
        import hashlib
        return hashlib.sha256(str(code).encode()).hexdigest()

    @property
    def is_usable(self):
        """Unused, unexpired, and not yet burned through its attempt budget."""
        return (
            self.used_at is None
            and self.attempts < self.MAX_ATTEMPTS
            and timezone.now() < self.expires_at
        )

    # ------------------------------------------------------------------
    #: `throttle_kind` outcomes. The two are treated differently everywhere:
    #: COOLDOWN is somebody tapping the button twice, HOURLY is somebody working
    #: through an account.
    THROTTLE_COOLDOWN = 'COOLDOWN'
    THROTTLE_HOURLY = 'HOURLY'

    @classmethod
    def throttle_kind(cls, user):
        """
        WHICH limit stops this account requesting a code, or None if none does.

        Split out from `throttle_reason` below so there is exactly one lookup
        behind both the message shown to the visitor and the alert raised to the
        owners. Two implementations of "is this account throttled, and why?"
        would be two answers free to disagree — and they would disagree in the
        one place it matters, as an account being worked through with nobody
        told about it. Same reasoning as `merge_preview()` sharing its helpers
        with `rename_*`.

        Counted per account rather than per session or per IP: a session counter
        is cleared with the cookies, and both owners may sit behind the
        workshop's single IP.
        """
        now = timezone.now()

        latest = cls.objects.filter(user=user).first()  # Meta.ordering = -created_at
        if latest:
            elapsed = (now - latest.created_at).total_seconds()
            if elapsed < cls.RESEND_COOLDOWN_SECONDS:
                wait = int(cls.RESEND_COOLDOWN_SECONDS - elapsed) + 1
                return cls.THROTTLE_COOLDOWN, wait

        recent = cls.objects.filter(user=user, created_at__gte=now - timedelta(hours=1)).count()
        if recent >= cls.MAX_REQUESTS_PER_HOUR:
            return cls.THROTTLE_HOURLY, recent

        return None, 0

    @classmethod
    def throttle_reason(cls, user):
        """
        Why this account may not request a code right now, or None if it may.

        Kept as its own method because callers read it as a plain "may this
        proceed?" boolean; the wording is never shown to the visitor — step 1
        replies identically whatever happens, or it becomes an account-existence
        oracle.
        """
        kind, value = cls.throttle_kind(user)

        if kind == cls.THROTTLE_COOLDOWN:
            return (
                f"Please wait {value} more second{'s' if value != 1 else ''} "
                f"before requesting another code."
            )
        if kind == cls.THROTTLE_HOURLY:
            return "Too many reset codes requested in the last hour. Please try again later."

        return None

    @classmethod
    def issue(cls, user, ip=None):
        """
        Create a fresh code, retiring any still outstanding.

        Returns (instance, plain_code). The plain code is returned once, for the
        email, and never persisted.
        """
        from django.utils.crypto import get_random_string

        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())

        code = get_random_string(length=cls.CODE_LENGTH, allowed_chars='0123456789')
        instance = cls.objects.create(
            user=user,
            code_hash=cls._hash(code),
            expires_at=timezone.now() + timedelta(minutes=cls.VALIDITY_MINUTES),
            requested_ip=ip,
        )
        return instance, code

    def verify(self, submitted_code):
        """
        Check a submitted code, spending one attempt.

        Always records the attempt before comparing, so a wrong guess costs
        something even if the response is discarded. Uses a constant-time
        comparison — a timing signal on a 6-digit space is worth closing.
        """
        import hmac

        if not self.is_usable:
            return False

        self.attempts += 1
        matched = hmac.compare_digest(self.code_hash, self._hash(submitted_code))
        if matched:
            self.used_at = timezone.now()
        self.save(update_fields=['attempts', 'used_at'])
        return matched

    @property
    def attempts_remaining(self):
        return max(0, self.MAX_ATTEMPTS - self.attempts)
# -----------------------------------------------------------------------------
# 1. STUDY SECTION MODELS
# These models act as the "Master Lists" for autocomplete suggestions.
# -----------------------------------------------------------------------------

class Mechanic(models.Model):
    """
    Represents a staff member working in the shop (mechanics and non-mechanic
    roles alike). Used for tracking who performed jobs / is on staff, without
    requiring individual logins — model/table name kept as "Mechanic" for
    historical continuity (JobCard.lead_mechanic and years of data point at
    it); the UI calls this "Staff Registration".
    """
    ROLE_MECHANIC = 'MECHANIC'
    ROLE_ASSISTANT_MECHANIC = 'ASSISTANT_MECHANIC'
    ROLE_OFFICE_STAFF = 'OFFICE_STAFF'
    ROLE_GENERAL_HELPER = 'GENERAL_HELPER'
    ROLE_CHOICES = [
        (ROLE_MECHANIC, 'Mechanic'),
        (ROLE_ASSISTANT_MECHANIC, 'Assistant Mechanic'),
        (ROLE_OFFICE_STAFF, 'Office Staff'),
        (ROLE_GENERAL_HELPER, 'General Helper'),
    ]
    # Only these two roles can be assigned as a Job Card's lead mechanic.
    JOBCARD_ELIGIBLE_ROLES = [ROLE_MECHANIC, ROLE_ASSISTANT_MECHANIC]

    name = models.CharField(max_length=100, unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MECHANIC)
    is_active = models.BooleanField(default=True, help_text="Disable if this staff member leaves the workshop")
    current_salary = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Current monthly salary used by Salary & Advance settlement. "
                   "Changing this only affects months settled after the change — "
                   "already-saved months keep the salary that was in effect then."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SalaryAdvance(models.Model):
    """
    A cash advance given to a staff member against their salary, recorded the
    day it happens. Settled at month-end by subtracting the month's advances
    from that staff's salary in a SalaryPaymentLine — this row is never itself
    flagged "used"; a payment line's advance_used is always summed fresh from
    whichever advances fall inside that calendar month, so re-settling a month
    (e.g. after a late-recorded advance) just recomputes cleanly.
    """
    staff = models.ForeignKey(Mechanic, on_delete=models.CASCADE, related_name='salary_advances')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=timezone.now, db_index=True)
    note = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.staff.name} — ₹{self.amount} ({self.date})"


class SalaryPayment(models.Model):
    """
    One row per calendar month once that month's salary settlement has been
    entered — the "box" the office fills in at month-end. A row existing
    means the month is settled; no row means it's still outstanding.
    """
    month = models.DateField(unique=True, db_index=True, help_text="Always the 1st of the month it represents")
    superseded = models.BooleanField(
        default=False, db_index=True,
        help_text="Set once a LATER month has been settled. Never unset — a "
                  "closed month stays closed even if that later settlement is "
                  "deleted, so nobody can walk backwards through history one "
                  "delete at a time.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-month']

    @property
    def total_amount(self):
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        return self.lines.aggregate(
            total=Coalesce(Sum('net_amount'), Decimal('0'), output_field=models.DecimalField())
        )['total']

    def __str__(self):
        return self.month.strftime('%B %Y')


class SalaryPaymentLine(models.Model):
    """
    One staff member's frozen settlement figures for one month — written once
    at save time and never recalculated afterwards, even if Mechanic.current_salary
    changes later. A salary hike next month must never rewrite last month's
    already-paid numbers, so this row is the permanent record of what was
    actually used: salary_used, leave_days and advance_used at that moment.
    """
    payment = models.ForeignKey(SalaryPayment, on_delete=models.CASCADE, related_name='lines')
    staff = models.ForeignKey(Mechanic, on_delete=models.CASCADE, related_name='salary_payment_lines')
    salary_used = models.DecimalField(max_digits=10, decimal_places=2)
    leave_days = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0'))
    overtime_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        help_text="Overtime earned this month, as a single amount. Added to the "
                  "net, so the wage cost the Profit page reads (net + advance) "
                  "includes it without any further arithmetic.",
    )
    advance_used = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    net_amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('payment', 'staff')
        ordering = ['staff__name']

    def __str__(self):
        return f"{self.staff.name} — {self.payment.month.strftime('%b %Y')}: ₹{self.net_amount}"


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

        The cost expression is IMPORTED, never restated. It used to be a
        hand-rolled copy of `analysis_engine.SPARE_COST` kept "identical" by
        comment — one of five such copies — and this is the one that decides
        what the workshop owes a shop, so a copy left behind after a fix would
        show a different debt on the shop's own page than on the Profit page.
        The import is local because `analysis_engine` imports this module.
        """
        from django.db.models import Sum, DecimalField, Value
        from django.db.models.functions import Coalesce
        from decimal import Decimal

        from .analysis_engine import SHOP_LINE_COST

        purchases = self.spare_items.aggregate(
            total=Coalesce(
                Sum(SHOP_LINE_COST, output_field=DecimalField()),
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

# -----------------------------------------------------------------------------
# CAR COLOUR — one palette, shared by every record that describes a vehicle
# -----------------------------------------------------------------------------
# Job Cards and Estimates both name a car's colour, and both draw the same
# stripe from it (dashboard cards, completed list, estimate history). The list
# and the hex map therefore live here rather than on either model: two copies
# would let a Grey job card and a Grey estimate print different greys, which is
# the kind of difference nobody notices until they are side by side.
CAR_COLOR_CHOICES = [
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

CAR_COLOR_HEX = {
    'Black': '#000000',
    'White': '#f8fafc',   # off-white, so it is visible against a white card
    'Silver': '#94a3b8',  # deeper metallic silver
    'Grey': '#64748b',    # slate grey
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

#: Shown when no colour was recorded. Solid slate — the templates additionally
#: hatch the stripe so "not recorded" never looks like a grey car.
CAR_COLOR_UNSET_HEX = '#475569'


def car_color_hex(color, other=None):
    """The CSS colour for a stored choice. 'Other' may carry a literal hex."""
    if color == 'Other' and other and other.startswith('#'):
        return other
    return CAR_COLOR_HEX.get(color, CAR_COLOR_UNSET_HEX)


def car_color_label(color, other=None):
    """What to call the colour in words."""
    if color == 'Other':
        return other or 'Other'
    return color or 'Unknown'


class CarColourMixin:
    """
    `get_car_color_hex` / `get_car_color_display` for anything carrying
    `car_color` + `car_color_other`. Property names are kept exactly as JobCard
    had them — a dozen templates call them, and renaming would be churn for no
    behavioural gain.

    **`get_car_color_display` must be RE-DECLARED in each model's own body**, and
    the line below shows how. Inheriting it silently does not work: a field with
    `choices` generates its own `get_<field>_display`, and Django's
    `Field.contribute_to_class` guards that with `"get_%s_display" not in
    cls.__dict__` — it checks the class's OWN dict, never its bases, expressly so
    a subclass can override inherited choices. So a property arriving through a
    mixin is overwritten every time, and the attribute quietly becomes Django's
    partialmethod: `car_color='Other'` then reads "Other" instead of the colour
    that was picked, and an unset colour reads "" instead of "Unknown". Nothing
    raises. `get_car_color_hex` has no such clash and inherits normally.
    """

    @property
    def get_car_color_hex(self):
        return car_color_hex(self.car_color, self.car_color_other)

    @property
    def get_car_color_display(self):
        return car_color_label(self.car_color, self.car_color_other)


class JobCard(CarColourMixin, models.Model):
    # What counts as a discount worth an owner's attention. Shared by
    # `audit_high_discounts`, the HIGH_DISCOUNT notification and the settlement
    # confirmation on the invoice, so no two of them can disagree about where
    # the line is.
    #
    # A flat RUPEE figure, not a percentage. It was 30% until 2026-08-10, and
    # the owner changed it because a proportion answers the wrong question here:
    # what an owner wants to be told about is money, and 30% means something
    # different on every bill. A ₹5,000 service discounted ₹1,500 tripped the
    # old alert and is an ordinary rounding-down at pickup; a ₹60,000 rebuild
    # discounted ₹15,000 did not trip it and is a quarter of a month's margin.
    # The threshold now says the same thing on every bill: more than ₹3,500 off,
    # and both owners hear about it.
    #
    # Consequence, accepted knowingly: a small bill can now be discounted to
    # almost nothing without an alert (₹3,000 off ₹5,000 is 60% and silent),
    # because the amount at stake is genuinely small. The compensating control
    # for the proportion is still the audit page, which lists every one of them.
    HIGH_DISCOUNT_AMOUNT = Decimal('3500')

    """
    The Industrial Heart of WorkshopOS. Manages the end-to-end lifecycle 
    of a vehicle service, from admission to billing.
    
    Key Features:
    - Auto-Generating Bill Numbers (JB-26-001)
    - Triple-Tier Security States (Active, Completed, Billed)
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
    completed_date = models.DateField(db_index=True, blank=True, null=True, help_text="Auto-filled when job is marked as Completed")

    # Completion Status (separate from planning date)
    completed = models.BooleanField(default=False, db_index=True, help_text="Job is Completed (marked via Completed button)")
    
    # On Hold Status (for jobs waiting for parts or paused)
    on_hold = models.BooleanField(default=False, help_text="Job is on hold (waiting for parts, etc.)")

    # Soft Delete (Trash System)
    is_deleted = models.BooleanField(default=False, db_index=True, help_text="Hide from main list (moved to trash)")


    # Vehicle Details (Text fields with Autocomplete)
    brand_name = models.CharField(max_length=100, db_index=True)
    model_name = models.CharField(max_length=100, db_index=True)
    registration_number = models.CharField(max_length=50, db_index=True)
    mileage = models.CharField(max_length=20, blank=True, null=True, help_text="e.g. 50000 or 50k")

    # Car Colour. The list itself is CAR_COLOR_CHOICES above, shared with
    # Estimate; the alias is kept because `jobcard_form.html` renders the picker
    # from `form.fields.car_color.choices` and other code may reference it.
    COLOR_CHOICES = CAR_COLOR_CHOICES
    car_color = models.CharField(max_length=50, choices=CAR_COLOR_CHOICES, blank=True, null=True)
    car_color_other = models.CharField(max_length=100, blank=True, null=True, help_text="Specific color name if 'Other' is selected")

    # Customer Details
    customer_name = models.CharField(max_length=150, db_index=True, blank=True, null=True)
    customer_contact = models.CharField(max_length=20, blank=True, null=True)

    # A line for the workshop's own eyes — "customer says the noise only happens
    # cold", "do not wash, owner is fussy". Declared exactly like
    # `Estimate.notes`: same length, same `blank=True` with no `null`, so the two
    # internal-note boxes behave identically and neither can be NULL-or-empty
    # depending on which screen wrote it.
    #
    # It is NOT printed, and nothing has to be done to keep it that way:
    # `workshop/invoice.py` builds the customer documents from named fields and
    # the template reads named fields, so a column nobody references cannot leak
    # onto a bill. `test_the_internal_note_never_reaches_the_customer` pins that
    # against the day somebody adds a generic field loop.
    notes = models.CharField(
        max_length=255, blank=True,
        help_text="Internal note — never printed on the customer's bill"
    )

    # Assignment
    lead_mechanic = models.ForeignKey(Mechanic, on_delete=models.SET_NULL, null=True, blank=True, related_name='job_cards', help_text="The main mechanic assigned to this job")
    bulk_payer = models.ForeignKey('BulkPayer', on_delete=models.SET_NULL, null=True, blank=True, related_name='job_cards', help_text="Assigned bulk payer")

    # One charge for ALL the work on this card. The workshop quotes labour as a
    # whole — a customer is told "₹22,300 for the job", never a price per line —
    # so Office types one figure and the Jobs section lists only what was done.
    # `JobCardLabourItem.amount` is the column this replaced; it is dormant now
    # (see that model) and every existing row's lines were summed into here by
    # migration, so no bill changed value.
    # blank=True because plenty of job cards are parts only. Required would make
    # an empty box a validation error and refuse to save a card that genuinely
    # has no labour on it; JobCardForm.clean_labour_amount turns empty into 0.
    labour_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, blank=True,
        help_text="Total labour charge for every job on this card (entered once, not per line)"
    )

    # Financials (NEW - Optimized for 1M+ records)
    total_bill_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Denormalized total for instant dashboard loading")
    received_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Amount actually received from customer")
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Internal discount tracking (calculated on Paid status)")
    paid_date = models.DateTimeField(null=True, blank=True, db_index=True, help_text="Set only when payment_status becomes PAID/BULK_PAID — Paid Bills filters on this, not updated_at, so an unrelated later edit never resurfaces an old bill under 'Today'")
    
    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending (Unpaid)'),
        ('PAID', 'Fully Paid'),
        ('PARTIAL', 'Partially Paid'),
        ('BULK_PAID', 'Fleet Paid'),
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
        # Covered: (is_deleted=False, completed=False) sorted by updated_at DESC.
        indexes = [
            models.Index(fields=['is_deleted', 'completed', '-updated_at']),
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
            # Strip and title-case: 'toyota  ' → 'Toyota'.
            # ⚠ Title-casing is WRONG for an acronym marque — 'BMW' becomes
            # 'Bmw' — so it is only the fallback. The master list gets the final
            # say immediately below, exactly as it already does for the model.
            self.brand_name = ' '.join(self.brand_name.split()).title()
            canonical_brand = (
                CarBrand.objects
                .filter(name__iexact=self.brand_name)
                .values_list('name', flat=True)
                .first()
            )
            if canonical_brand:
                self.brand_name = canonical_brand
        if self.model_name:
            # Whitespace only — deliberately NOT .title() like brand_name.
            # Model names are alphanumeric in ways title-casing destroys:
            # 'i20' → 'I20', 'CR-V' → 'Cr-V', 'GLE 350d' → 'Gle 350D'.
            self.model_name = ' '.join(self.model_name.split())

        # Snap the model to the master list's spelling when that brand already
        # has this model recorded, case-insensitively. Reports group by
        # `model_name` (it is free text on the card, by deliberate design), so
        # 'corolla' and 'COROLLA' were two different models everywhere they were
        # counted. Rather than invent a capitalisation rule that would mangle
        # 'i20', let the curated list be the authority on how its own entries
        # are spelled; a model that is genuinely new stays exactly as typed.
        # One indexed lookup, and only when both fields are present.
        if self.model_name and self.brand_name:
            canonical = (
                CarModel.objects
                .filter(brand__name__iexact=self.brand_name, name__iexact=self.model_name)
                .values_list('name', flat=True)
                .first()
            )
            if canonical:
                self.model_name = canonical

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
                prefix = f'JB-{year}-'

                # Find the highest existing bill number for this year — computed
                # NUMERICALLY, not by text ordering.
                #
                # A previous version used order_by('-bill_number').first(), but
                # bill_number is a CharField, so that is a LEXICOGRAPHIC sort:
                # "JB-26-999" sorts *higher* than "JB-26-1000" (because '9' > '1'
                # at the first differing character). Past 999 bills/year that made
                # the sequence loop back to 1000 and collide on the unique
                # constraint — crashing job-card creation for the rest of the year.
                #
                # select_for_update() locks the year's rows so two job cards created
                # concurrently can't be assigned the same number (effective on
                # PostgreSQL; a harmless no-op on SQLite).
                max_num = 0
                for existing_bill in (
                    JobCard.objects.select_for_update()
                    .filter(bill_number__startswith=prefix)
                    .only('bill_number')
                ):
                    try:
                        n = int(existing_bill.bill_number.rsplit('-', 1)[-1])
                    except (ValueError, IndexError):
                        # Skip any bill whose suffix isn't a plain integer.
                        continue
                    if n > max_num:
                        max_num = n

                next_num = max_num + 1

                # zfill(3) keeps the familiar JB-26-001 look for 1–999 and grows
                # naturally beyond that (JB-26-1000, JB-26-10000, …) without breaking
                # the ordering, since ordering is now numeric.
                self.bill_number = f'{prefix}{str(next_num).zfill(3)}'
        
        super().save(*args, **kwargs)
    
    def update_totals(self):
        """
        Calculates and saves the denormalized total_bill_amount.
        This eliminates expensive on-the-fly calculations for 1M+ records.
        """
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        
        spare_total = self.spares.aggregate(total=Coalesce(Sum('total_price'), 0, output_field=models.DecimalField()))['total']

        # Labour is ONE figure on this row, not a sum over the job lines. It used
        # to be Sum(labours.amount); the lines no longer carry money.
        new_total = spare_total + (self.labour_amount or Decimal('0'))
        if self.total_bill_amount != new_total:
            self.total_bill_amount = new_total
            # Use update to avoid triggering save() recursion if called from save()
            JobCard.objects.filter(pk=self.pk).update(total_bill_amount=new_total)
            if self.bulk_payer_id:
                self.bulk_payer.update_totals()

    def mark_completed(self):
        """
        Move this card off the workshop floor. Returns True if it moved.

        One implementation because there are now two doors into it — the
        Completed button on the board, and "Complete & settle" in the settle
        dialog, which exists because taking a customer's money for a car the
        system still shows as being worked on is a contradiction the person at
        the counter should be able to resolve without leaving the page.

        Idempotent on purpose: the second door can be reached on a card that is
        already completed (a re-settlement, a corrected amount), and re-stamping
        `completed_date` there would move the day the car was finished to the
        day somebody edited a figure — and that date is what the Completed list
        filters and sorts on.

        `localdate()`, never `date.today()`: the server can run in UTC while the
        workshop works in IST, and near midnight the two disagree about which
        day it is.

        A PLAIN `save()`, deliberately, not `update_fields=[...]`. This model's
        `save()` calls `clean()` to normalise the registration, brand and model,
        and assigns `bill_number` when there is none — with `update_fields` all
        three would still be computed and then silently not written, because
        they are not in the list. Narrowing the write here would save nothing
        and would make this the one path that quietly skips them.
        """
        if self.completed:
            return False
        self.completed = True
        self.completed_date = timezone.localdate()
        self.save()
        return True

    @classmethod
    def get_active_conflict(cls, registration_number, exclude_pk=None):
        """
        Returns the OTHER active job card (not completed, not trashed) for this
        registration number, if one exists — or None.

        Single source of truth for the "one active job card per vehicle" rule.
        Used by jobcard_create, jobcard_edit, and undo_completed so all three
        entry points that can put a car "on the floor" agree on what counts as
        a conflict, instead of each re-implementing (or skipping) the check.
        """
        if not registration_number:
            return None
        qs = cls.objects.filter(
            registration_number__iexact=registration_number.strip(),
            completed=False,
            is_deleted=False,
        )
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return qs.first()

    def __str__(self):
        return f"{self.bill_number or f'#{self.id}'}"

    # From CarColourMixin, so the Job Card and the Estimate cannot print two
    # different greys. `get_car_color_display` has to be named again HERE rather
    # than inherited — see the mixin for why, and do not "tidy" this line away.
    get_car_color_display = CarColourMixin.get_car_color_display

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
    A part fitted to a car — reaching it by one of exactly two routes, recorded
    in `source`:

      SHOP      ordered from a SpareShop for this job. The ordering workflow
                (status / ordered_date / received_date / shop) applies, and the
                money leaves via the spare-shop ledger.
      INVENTORY taken off the warehouse shelf. `item` points at the stock
                product, the ordering fields are meaningless, and the money
                already left earlier via a supplier restock bill.

    Before 2026-07-30 there was no `source`: the route was *guessed* — from a
    NULL shop plus a case-insensitive match of `spare_part_name` against
    `Item.name` — and the guess was made differently in the stock signals than
    in the analysis engine. A shop-bought part that happened to share a name
    with a stock product was wrongly deducted from the warehouse by one rule
    while correctly billed as a shop purchase by the other. `source` is the
    single answer both now read; do not reintroduce name matching.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ORDERED', 'Ordered'),
        ('RECEIVED', 'Received'),
    ]

    SOURCE_SHOP = 'SHOP'
    SOURCE_INVENTORY = 'INVENTORY'
    SOURCE_CHOICES = [
        (SOURCE_SHOP, 'Spare Shop'),
        (SOURCE_INVENTORY, 'Inventory'),
    ]

    job_card = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name='spares', null=True, blank=True)
    spare_part_name = models.CharField(max_length=100, blank=True, null=True)
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default=SOURCE_SHOP, db_index=True,
        help_text="Which route this part reached the car by — never inferred, always stored"
    )
    item = models.ForeignKey(
        'inventory.Item', on_delete=models.PROTECT, null=True, blank=True,
        related_name='job_card_uses',
        help_text="The stock product drawn (INVENTORY rows only). PROTECT: a product "
                  "used on a job card must not be deletable out from under that history."
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    quantity = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)

    # Pricing. `unit_price` is the workshop's COST, and its shape DIFFERS by
    # route — changed 2026-08-17 on the owner's instruction:
    #
    #   SHOP      the LINE TOTAL the shop billed for this row, typed by Office
    #             straight off the shop's own bill. Never multiplied.
    #   INVENTORY the weighted-average cost of ONE unit, snapshotted from
    #             Item.avg_cost here in save() and rewritten by the replay in
    #             inventory/costing.py. Per unit by construction — it is derived
    #             from the shelf, not typed — so a draw's cost IS × quantity.
    #
    # `analysis_engine.SPARE_COST` is the one expression that knows which is
    # which; nothing else may re-derive it. See SHOP_LINE_COST there for why the
    # shop side stopped multiplying.
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Workshop cost: the shop's line total (SHOP), or the warehouse average cost per unit (INVENTORY)")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Customer price — the figure that bills, shown in the UI as 'Customer Price'")
    # The optional "Unit Price" box on an INVENTORY row: what the customer is
    # charged per unit. INPUT ONLY — never back-filled from total_price ÷ quantity,
    # so a null here honestly means "nobody entered a rate" and the two figures
    # can never quietly disagree. Staff usually skip it and type the total.
    customer_rate = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Customer price per unit (optional; drives total_price when set)")

    # Order tracking
    shop_name = models.CharField(max_length=100, blank=True, null=True, help_text="Shop where part was ordered (text copy for display)")
    shop = models.ForeignKey('SpareShop', on_delete=models.SET_NULL, null=True, blank=True, related_name='spare_items', help_text="Linked SpareShop profile")
    ordered_date = models.DateField(blank=True, null=True, db_index=True, help_text="Auto-filled when status → ORDERED")
    received_date = models.DateField(blank=True, null=True, db_index=True, help_text="Auto-filled when status → RECEIVED")
    original_vehicle_info = models.CharField(max_length=255, blank=True, null=True, help_text="Stores car details if unassigned from a job card")

    def save(self, *args, **kwargs):
        if self.spare_part_name:
            self.spare_part_name = self.spare_part_name.strip()

        if self.source == self.SOURCE_INVENTORY and self.item_id:
            # Read the product straight from the database rather than through
            # `self.item`. `recompute_average_cost` writes avg_cost with
            # .update(), which leaves any Item instance a caller happens to be
            # holding stale — and snapshotting a stale cost would silently
            # misprice the draw.
            from inventory.models import Item
            product = Item.objects.filter(pk=self.item_id).values('name', 'avg_cost').first()
            if product:
                # Keep the display name aligned with the product actually drawn, so
                # renaming a stock product can never leave a job card describing a
                # part by a name the warehouse no longer knows.
                if not self.spare_part_name:
                    self.spare_part_name = product['name']
                # Snapshot the warehouse cost once, at draw time. Never recomputed:
                # a price change next month must not rewrite last month's margin.
                #
                # A zero average means the cost is genuinely UNKNOWN, not free —
                # opening stock counted onto the shelf before any supplier bill
                # exists, or a product whose only restock bill was deleted. Storing
                # 0 there would report those parts as pure profit; leaving
                # `unit_price` NULL keeps "nobody knows" distinguishable from
                # "it cost nothing", and analysis_engine counts such draws so they
                # can be seen instead of silently understating cost.
                if self.pk is None and self.unit_price is None:
                    avg = product['avg_cost']
                    if avg and avg > 0:
                        self.unit_price = avg

        # A rate that was deliberately entered is authoritative over the typed
        # total, so editing 7 L down to 4 L recomputes the bill instead of
        # leaving a stale one.
        if self.customer_rate is not None and self.quantity is not None:
            self.total_price = (self.customer_rate * self.quantity).quantize(Decimal('0.01'))

        # Which shop this row was billed to BEFORE this save. Moving a spare from
        # one shop to another has to refresh both ledgers: only refreshing the new
        # one left the old shop's cached total still counting a row it no longer
        # owns, so ₹1,000 spent showed as ₹1,000 owed to A *and* ₹1,000 owed to B.
        # It never self-corrected, and clearing the dropdown stranded the debt on a
        # shop with no matching row at all.
        previous_shop_id = None
        if self.pk:
            previous_shop_id = (JobCardSpareItem.objects
                                .filter(pk=self.pk)
                                .values_list('shop_id', flat=True).first())

        super().save(*args, **kwargs)
        if self.job_card:
            self.job_card.update_totals()

        for shop in SpareShop.objects.filter(
                pk__in={previous_shop_id, self.shop_id} - {None}):
            shop.update_totals()

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
    One job that was done. A DESCRIPTION, not a price.

    The workshop does not cost work line by line — it quotes a job as a whole and
    Office enters that single figure on `JobCard.labour_amount`. So this model
    answers "what was done?" and nothing else; the invoice prints these lines
    with an empty AMOUNT column and one SUBTOTAL underneath, which is exactly how
    the workshop's own printed bill has always read.

    `amount` is DORMANT, in the same sense as `JobCard.is_deleted`: still on the
    table, no longer written by any form, and no longer read by any total. Every
    existing row's value was summed into `JobCard.labour_amount` by migration
    0066 so not one bill changed, and it is kept rather than dropped so that
    history remains inspectable. Do not reintroduce it as a money source — two
    places holding the labour charge is exactly one more than can be reconciled.
    """
    job_card = models.ForeignKey(JobCard, on_delete=models.CASCADE, related_name='labours')
    job_description = models.CharField(max_length=150)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text="DORMANT — pre-2026-08-04 per-line charge. Superseded by JobCard.labour_amount."
    )

    def __str__(self):
        return self.job_description


def _filename_safe(text, limit=40):
    """
    Collapse a free-text value into something safe to hand a filesystem.

    Registration numbers carry spaces, part names carry brackets and slashes,
    and a slash in particular would read as a directory separator on the way
    into somebody's phone. Anything that is not a letter, digit, dash or dot
    becomes a single dash.
    """
    import re as _re
    cleaned = _re.sub(r'[^A-Za-z0-9.-]+', '-', (text or '').strip())
    return _re.sub(r'-{2,}', '-', cleaned).strip('-')[:limit]


class JobCardPhoto(models.Model):
    """
    A photograph taken on the shop floor — of the car, or of one spare part.

    ONE TABLE, TWO SUBJECTS, told apart by which FK is set. That is the same
    shape `JobCardSpareItem` uses to hold both part routes: one table, one set
    of rules, no chance of two implementations drifting. `job_card` set means a
    car photo; `spare` set means a part photo.

    **Exactly one of them is ever populated, and the DATABASE enforces it.**
    `clean()` alone would not: Django does not call it on `save()`, so a model
    check is advisory and only forms honour it — and this model has no form, it
    is written by an endpoint. The `CheckConstraint` is what makes the rule
    true. A row with both set would count against two different limits and show
    in two galleries; a row with neither is reachable from no screen at all and
    invisible to the sweep.

    `spare` alone is enough for a part photo because an unassigned spare has no
    job card at all, and those rows are photographed like any other.

    WHY THIS SECTION IS COMPLETELY OPTIONAL
    ---------------------------------------
    Nothing points AT a photo. There is no column on `JobCard` or
    `JobCardSpareItem`, no money, no stock, no ledger line, nothing in
    `analysis_engine.py` and nothing in `invoice.py` — so a photo can never
    reach a customer's bill, exactly as the internal note cannot. Photos also
    upload independently of the form POST, so R2 being slow, down, or entirely
    unconfigured cannot block a job card from saving. And `settlement.py` must
    never chase a missing photo: turning "no photos" into a settlement gap would
    paint every ordinary card red on the Live Report, which is the opposite of
    optional.

    The primary key is a UUID because it doubles as the storage key (see
    `photos.object_key`) — derived, never stored, so the database and the bucket
    cannot disagree about where an image lives. It also means a key cannot be
    guessed from a sequence.

    `taken_at` is set by the SERVER. The gallery prints it, and a tablet whose
    clock is wrong would otherwise put a confident, false time on evidence.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    job_card = models.ForeignKey(
        JobCard, on_delete=models.CASCADE, related_name='photos',
        null=True, blank=True,
        help_text="Set for a CAR photo. Null for a spare photo.",
    )
    spare = models.ForeignKey(
        JobCardSpareItem, on_delete=models.CASCADE, related_name='photos',
        null=True, blank=True,
        help_text="Set for a SPARE photo. Null for a car photo.",
    )

    taken_at = models.DateTimeField(auto_now_add=True, db_index=True)
    taken_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='photos_taken',
    )
    byte_size = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-taken_at']
        indexes = [
            models.Index(fields=['job_card', '-taken_at']),
            models.Index(fields=['spare', '-taken_at']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(job_card__isnull=False, spare__isnull=True)
                    | models.Q(job_card__isnull=True, spare__isnull=False)
                ),
                name='photo_belongs_to_exactly_one_subject',
            ),
        ]

    def __str__(self):
        return f"Photo {self.id}"

    @property
    def is_car_photo(self):
        return self.job_card_id is not None

    def clean(self):
        from django.core.exceptions import ValidationError
        if bool(self.job_card_id) == bool(self.spare_id):
            raise ValidationError(
                "A photo belongs to exactly one subject — a job card or a spare, never both and never neither."
            )

    @property
    def storage_key(self):
        from . import photos as photo_storage
        return photo_storage.object_key(self.id)

    def download_name(self):
        """
        The filename a long-press "Save image" offers — the car, its plate, the
        job card and the date.

        THIS IS THE READABLE NAME, AND THE STORAGE KEY IS DELIBERATELY NOT.
        The object in the bucket stays `<uuid>.jpg` for three reasons, and each
        one is a defect avoided rather than a preference:

          * The key is DERIVED from the primary key, so nothing has to be kept
            in step with it. Build it from the registration instead and
            correcting a typo in a plate silently orphans every photo of that
            car — the row would compute a key that no longer exists.
          * Two photos of one car on one job card would collide.
          * A readable key is a GUESSABLE key. A bucket left public would then
            be enumerable by anyone who knows a registration number, which is
            most of a workshop's customers.

        None of that applies to the download name: it is a label on a copy
        somebody already has, carried by `Content-Disposition` on the signed
        URL. Verified against Supabase, which honours the S3 override.
        """
        stamp = timezone.localtime(self.taken_at).strftime('%Y-%m-%d') if self.taken_at else 'undated'

        card = self.job_card if self.job_card_id else (
            self.spare.job_card if self.spare_id and self.spare else None
        )

        parts = []
        if self.spare_id and self.spare:
            parts.append(self.spare.spare_part_name or 'part')
        if card:
            parts.append(f"{card.brand_name or ''} {card.model_name or ''}")
            parts.append(card.registration_number or '')
            parts.append(card.bill_number or f"JB{card.pk}")

        name = '_'.join(_filename_safe(p) for p in parts if _filename_safe(p))
        return f"{name}_{stamp}.jpg" if name else f"photo_{stamp}.jpg"


class OrphanedPhotoBlob(models.Model):
    """
    A storage key whose row is gone but whose object is still in the bucket.

    Deleting the row and deleting the blob are deliberately separated. A DELETE
    to R2 is a network call, and this codebase does not put those on the request
    path — the same reasoning that sends Web Push off to a background thread. If
    the bucket is slow or unreachable, a photo must still disappear from the app
    the moment somebody deletes it; the object is collected afterwards by
    `sweep_photo_blobs`.

    The row is written in the same transaction as the delete, so a key can never
    be lost by a crash between the two. A re-run is harmless: R2 answers a
    DELETE for a missing key with a success, and this row is only removed once
    that has happened.
    """
    storage_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.storage_key


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
    # THE LAST OF THE THREE LEDGERS TO GET A NOTE, and the one that carries the
    # workshop's LARGEST single receipts. `SpareShopPayment.note` and
    # `inventory.SupplierPayment.note` have had one since they were written, so
    # the shared "Record a Payment" control drew a Note box on two of the three
    # screens an owner settles from — and a fleet collector handing over six
    # figures against several months of cars is exactly the payment somebody
    # needs to write a cheque number or "Aug + Sep" against.
    #
    # Same column as its two siblings, character for character, so the three
    # cannot disagree about how much may be written: CharField(255), blank and
    # null. Blank means nobody wrote one — never an empty string standing in for
    # a note that was typed and lost.
    note = models.CharField(max_length=255, blank=True, null=True, help_text="Optional description or reference")
    jobs_affected = models.PositiveIntegerField(default=0)
    details = models.TextField(blank=True, help_text="JSON snapshot of which jobs got how much")
    is_trashed = models.BooleanField(default=False)
    # THE DAY THE MONEY MOVED — the third and last ledger in this app to get
    # one, and the one where it matters most. `inventory.SupplierPayment` has
    # had this column since day one and `SpareShopPayment` gained it in 0071;
    # a fleet payment was still stamped with `created_at`, the keystroke.
    #
    # A fleet settles in lump sums and hands over cash on its own rhythm, so
    # the gap between the day the money arrives and the day somebody keys it
    # is routine — and these are the LARGEST single receipts the workshop
    # takes, which is why the same defect is worse here than it was on either
    # shop. `created_at` stays: it is the audit trail, it breaks ties inside a
    # day, and the two answer different questions.
    date = models.DateField(default=timezone.now, db_index=True,
                            help_text="The day the money actually moved.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # `-date` leads and `created_at` breaks ties, so two payments
        # back-dated to one day still read in the order they were entered.
        ordering = ['-date', '-created_at']

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
    # THE DAY THE MONEY MOVED, which is not the day it was typed. A shop is
    # settled on the 30th and the payment is often keyed the following week, so
    # `created_at` — the keystroke — filed it under the wrong month on every
    # date window the shop page and its print sheet offer, with no way to
    # correct it. The same defect `CashbookEntry.date` exists to stop, and the
    # same column its sibling `inventory.SupplierPayment` already had.
    # `created_at` stays: it is the audit trail, and the two answer different
    # questions. Nothing here reaches the Profit page — a payment settles a
    # debt that was expensed when the part reached a car — which is why the
    # defect survived this long.
    date = models.DateField(default=timezone.now, db_index=True,
                            help_text="The day the money actually moved.")
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
        # Newest payment first BY THE DAY IT WAS MADE, with `created_at` only
        # breaking ties within a day — two payments back-dated to the same date
        # still read in the order they were entered.
        ordering = ['-date', '-created_at']
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
            # Two readers walk this column: the Profit page groups the whole
            # cashbook by it, and the add form offers the spellings already in
            # use so a new entry snaps to one rather than inventing a variant.
            # Both are DISTINCT/GROUP BY over every row, which without an index
            # is a full sort of the table each time the page opens.
            models.Index(fields=['category']),
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


# -----------------------------------------------------------------------------
# ESTIMATES — a quote, and nothing more
# -----------------------------------------------------------------------------
# An estimate is a piece of paper handed to a customer BEFORE any work is agreed.
# It is deliberately connected to nothing: no job card, no spare shop, no
# warehouse stock, no ledger, no line on the Profit page. Money on an estimate is
# a proposal, and a proposal that moved stock or entered a report would be the
# workshop counting work it has not done.
#
# That isolation is the design, not an unfinished edge. Three consequences worth
# knowing before "connecting it up":
#
#   * These three models are read by exactly two views and one printing
#     function. Nothing in `analysis_engine.py`, `inventory/signals.py` or any
#     ledger touches them, and nothing should start to.
#   * The lines are free text — even the part name — for the same reason a job
#     card's is (see the taxonomy decision in CLAUDE.md). An estimate is typed
#     fastest of all, often with the customer waiting.
#   * Deleting one moves no money, so it does NOT write a DeletionLog row. See
#     `estimate_delete` for why that is the deliberate answer and not an
#     oversight.
# -----------------------------------------------------------------------------

class Estimate(CarColourMixin, models.Model):
    """
    One quotation. Mirrors the shape of a JobCard's printable half — vehicle,
    customer, jobs, parts — and carries none of its machinery.
    """

    # EST-, never JB-. An estimate and a bill must not be confusable in the
    # workshop's own books: the same three digits under two prefixes is the whole
    # point, so a number read out over the phone says which document it is.
    NUMBER_PREFIX = 'EST'

    estimate_number = models.CharField(
        max_length=20, unique=True, blank=True, null=True,
        help_text="Auto-generated (e.g. EST-26-001)"
    )
    date = models.DateField(db_index=True, default=timezone.localdate)

    # Vehicle + customer. Free text with autocomplete, exactly as on a job card —
    # an estimate is often written for a car that has never been here.
    customer_name = models.CharField(max_length=150, blank=True, null=True, db_index=True)
    customer_contact = models.CharField(max_length=20, blank=True, null=True)
    brand_name = models.CharField(max_length=100, blank=True, null=True)
    model_name = models.CharField(max_length=100, blank=True, null=True)
    registration_number = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    mileage = models.CharField(max_length=20, blank=True, null=True)

    # Same palette and the same picker as a Job Card, and it is NOT printed on
    # the sheet: the colour is how staff recognise a car in the history list
    # (the stripe down each row, exactly as on the dashboard), not something a
    # customer needs on their quotation.
    car_color = models.CharField(max_length=50, choices=CAR_COLOR_CHOICES, blank=True, null=True)
    car_color_other = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Specific colour name if 'Other' is selected"
    )

    # One figure for all the work quoted — the same rule as JobCard.labour_amount,
    # and for the same reason: this workshop quotes a job whole ("₹22,300 for the
    # job"), so EstimateJobLine carries no money at all. An estimate that split
    # the work into five prices would invite a line-by-line negotiation over work
    # the bill will then present as one number.
    labour_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, blank=True,
        help_text="Total quoted labour for every job line (entered once, not per line)"
    )

    # Denormalized, exactly like JobCard.total_bill_amount: the history list reads
    # one column instead of aggregating each row's lines. Written only by
    # update_totals(), which the create/edit views call once after the formsets
    # save. There are no signals on these models — deliberately, since nothing
    # else in the app has any reason to react to an estimate changing.
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    notes = models.CharField(max_length=255, blank=True, help_text="Internal note — never printed")

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='estimates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-id']
        indexes = [
            models.Index(fields=['-date', '-id']),
        ]
        verbose_name = "Estimate"
        verbose_name_plural = "Estimates"

    def clean(self):
        """Same normalisation a job card applies, so the two agree about what a
        registration number and a brand look like."""
        if self.registration_number:
            self.registration_number = self.registration_number.strip().upper()
        if self.brand_name:
            # Same rule as JobCard.clean(): title-case, then let the master list
            # decide, so an estimate and the job card that follows it cannot
            # spell the same marque two different ways.
            self.brand_name = ' '.join(self.brand_name.split()).title()
            canonical_brand = (
                CarBrand.objects
                .filter(name__iexact=self.brand_name)
                .values_list('name', flat=True)
                .first()
            )
            if canonical_brand:
                self.brand_name = canonical_brand
        if self.model_name:
            # Whitespace only — NOT title-cased. 'i20' → 'I20' and 'CR-V' →
            # 'Cr-V' is why JobCard.clean does the same.
            self.model_name = ' '.join(self.model_name.split())

    def save(self, *args, **kwargs):
        self.clean()
        if not self.estimate_number:
            with transaction.atomic():
                year = str((self.date or timezone.localdate()).year)[2:]
                prefix = f'{self.NUMBER_PREFIX}-{year}-'

                # NUMERIC, not lexicographic. A CharField sorts "EST-26-999"
                # above "EST-26-1000", which is how JobCard's numbering once
                # looped back and collided on its unique constraint past 999.
                # select_for_update locks the year's rows so two estimates saved
                # at once cannot take the same number (real on PostgreSQL, a
                # harmless no-op on SQLite).
                max_num = 0
                for existing in (
                    Estimate.objects.select_for_update()
                    .filter(estimate_number__startswith=prefix)
                    .only('estimate_number')
                ):
                    try:
                        n = int(existing.estimate_number.rsplit('-', 1)[-1])
                    except (ValueError, IndexError):
                        continue
                    if n > max_num:
                        max_num = n

                self.estimate_number = f'{prefix}{str(max_num + 1).zfill(3)}'

        super().save(*args, **kwargs)

    def update_totals(self):
        """
        Recompute the denormalized total: quoted parts + the one labour figure.

        Called explicitly by the views after the formsets save — there is no
        signal doing it. The lines carry no side effects, so a save-time hook
        would be machinery with nothing to protect.
        """
        from django.db.models import Sum
        from django.db.models.functions import Coalesce

        part_total = self.parts.aggregate(
            total=Coalesce(Sum('amount'), Decimal('0'), output_field=models.DecimalField())
        )['total']
        new_total = part_total + (self.labour_amount or Decimal('0'))
        if self.total_amount != new_total:
            self.total_amount = new_total
            Estimate.objects.filter(pk=self.pk).update(total_amount=new_total)

    # Named again here rather than inherited — see CarColourMixin. Without this
    # line Django's generated display method wins and an 'Other' colour reads
    # back as the literal word "Other".
    get_car_color_display = CarColourMixin.get_car_color_display

    @property
    def vehicle_label(self):
        """'Toyota Corolla' — for list rows, never for the printed sheet."""
        return ' '.join(p for p in (self.brand_name, self.model_name) if p)

    def __str__(self):
        return self.estimate_number or f'Estimate #{self.pk}'


class EstimateJobLine(models.Model):
    """
    One line of work being quoted. A DESCRIPTION, not a price.

    Same shape as JobCardLabourItem after the 2026-08-04 change, and for the
    identical reason — the charge lives once on `Estimate.labour_amount`. This
    model deliberately has no `amount` column at all: JobCardLabourItem kept a
    dormant one only because it had history to preserve, and there is no reason
    to create a second place that could hold the quoted labour figure.
    """
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name='job_lines')
    description = models.CharField(max_length=150)

    def __str__(self):
        return self.description


class EstimatePartLine(models.Model):
    """
    One part being quoted.

    NOTE the naming, because it is the opposite of `JobCardSpareItem`: there,
    `unit_price` means the workshop's COST per unit and `total_price` is what the
    customer pays. An estimate has no cost side — every figure on it is what the
    customer is being quoted — so the per-unit field is called `customer_rate`,
    matching the one field on JobCardSpareItem that already means exactly that.
    Nothing here may be read as a cost, by the analysis engine or anyone else.
    """
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name='parts')
    name = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    customer_rate = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text="Quoted price per unit (optional; drives amount when set)"
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text="Quoted price for this line — the figure that prints"
    )

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip()
        # A rate that was deliberately entered wins over a stale total, so
        # editing 7 L down to 4 L requotes the line instead of leaving the old
        # figure. Identical rule to JobCardSpareItem.customer_rate.
        if self.customer_rate is not None and self.quantity is not None:
            self.amount = (self.customer_rate * self.quantity).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.quantity})"


# -----------------------------------------------------------------------------
# DELETION HISTORY (read-only audit of permanent deletions)
# -----------------------------------------------------------------------------
class DeletionLog(models.Model):
    """
    Immutable, Owner-only record of every permanent deletion in the system.

    The deletion model is: *accounts are deactivated* (reversible — see `is_active`
    on SpareShop/BulkPayer/SupplierShop/Mechanic), while *transactions and records
    are permanently deleted* — but never silently. Every permanent delete first
    writes one row here with a full readable snapshot of what was removed, who
    removed it, and (optionally) why.

    This is a **read-only history**. There is deliberately no restore path: reviving
    stale financial data long after the fact would corrupt running balances. Owners
    read it purely for oversight of what Office cleared.
    """
    ENTITY_JOBCARD = 'JOBCARD'
    ENTITY_BULK_PAYMENT = 'BULK_PAYMENT'
    ENTITY_SHOP_PAYMENT = 'SHOP_PAYMENT'
    ENTITY_SUPPLIER_PAYMENT = 'SUPPLIER_PAYMENT'
    ENTITY_RESTOCK_BILL = 'RESTOCK_BILL'
    ENTITY_CASHBOOK = 'CASHBOOK'
    ENTITY_INVENTORY_ITEM = 'INVENTORY_ITEM'
    ENTITY_SALARY_ADVANCE = 'SALARY_ADVANCE'
    ENTITY_SALARY_PAYMENT = 'SALARY_PAYMENT'
    ENTITY_UNASSIGNED_SPARE = 'UNASSIGNED_SPARE'
    # Master-list rows (spare-part names, concerns). Not financial — job cards
    # store these as free text, so removing one cannot alter a bill, a ledger or
    # a report (proven; see MasterDataDeleteTouchesNoHistoryTests). Logged
    # anyway, because it was the one permanent delete in the system that left no
    # trace at all: an entry someone removed by accident simply stopped existing,
    # with nothing to say it ever had. The snapshot carries the exact wording, so
    # a mistake is retypeable straight from Deletion History.
    ENTITY_MASTER_DATA = 'MASTER_DATA'
    ENTITY_CHOICES = [
        (ENTITY_JOBCARD, 'Job Card'),
        (ENTITY_BULK_PAYMENT, 'Fleet Account Payment'),
        (ENTITY_SHOP_PAYMENT, 'Spare-Shop Payment'),
        (ENTITY_SUPPLIER_PAYMENT, 'Supplier Payment'),
        (ENTITY_RESTOCK_BILL, 'Restock Bill'),
        (ENTITY_CASHBOOK, 'Cashbook Entry'),
        (ENTITY_INVENTORY_ITEM, 'Inventory Product'),
        (ENTITY_SALARY_ADVANCE, 'Salary Advance'),
        (ENTITY_SALARY_PAYMENT, 'Salary Payment'),
        (ENTITY_UNASSIGNED_SPARE, 'Unassigned Spare'),
        (ENTITY_MASTER_DATA, 'Master List Entry'),
    ]

    entity_type = models.CharField(max_length=20, choices=ENTITY_CHOICES, db_index=True)
    entity_label = models.CharField(max_length=255, help_text="Human-readable identity of the deleted record")
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Financial magnitude, if any")
    snapshot = models.JSONField(default=dict, help_text="Full readable copy of the deleted record for later reference")
    reason = models.CharField(max_length=255, blank=True, help_text="Optional note entered at delete time")
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deletions')
    deleted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-deleted_at']
        indexes = [
            models.Index(fields=['entity_type', '-deleted_at']),
        ]
        verbose_name = "Deletion Log"
        verbose_name_plural = "Deletion History"

    def __str__(self):
        return f"{self.get_entity_type_display()}: {self.entity_label} ({self.deleted_at:%d %b %Y})"

    @staticmethod
    def _snapshot(instance, extra_children=None):
        """Build a JSON-safe dict of an instance's fields (+ optional child data)."""
        from django.forms.models import model_to_dict
        import datetime

        def _safe(v):
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, (datetime.date, datetime.datetime)):
                return v.isoformat()
            try:
                json.dumps(v)
                return v
            except (TypeError, ValueError):
                return str(v)

        data = {k: _safe(v) for k, v in model_to_dict(instance).items()}
        if extra_children:
            data.update(extra_children)
        return data

    @classmethod
    def record(cls, entity_type, instance, user=None, reason="", amount=None, label=None, extra=None):
        """
        Write one deletion-history row. Call this immediately BEFORE the hard delete,
        inside the same atomic block, so the snapshot is captured even if the delete
        cascades children.

        This is also where every deletion notification originates. Because every
        permanent delete in the codebase already funnels through here, one call
        covers all nine entity types — and any type added later is covered for
        free. Do not scatter equivalent `notify()` calls into the individual
        delete views; this choke point is the reason they stay correct.
        """
        entry = cls.objects.create(
            entity_type=entity_type,
            entity_label=(label or str(instance))[:255],
            amount=amount,
            snapshot=cls._snapshot(instance, extra),
            reason=(reason or "")[:255],
            deleted_by=user if (user is not None and getattr(user, 'is_authenticated', False)) else None,
        )

        # Imported here, not at module scope: `notifications` imports this module,
        # so a top-level import would be circular.
        from django.urls import reverse
        from .notifications import notify

        # THE HEADLINE IS THE LABEL PLUS THE VERB; THE KIND DROPS BELOW IT.
        #
        # It used to be `"{kind} deleted: {label} (₹{amount})"` — one string,
        # printing two facts twice each. The kind opens most labels already
        # ("Restock Bill #669 · …"), and SEVEN of the eighteen `record()` call
        # sites put the amount inside their own label, formatted the app's way,
        # so a restock bill arrived as
        #     "Restock Bill deleted: Restock Bill #669 · Fluid manjeri ·
        #      ₹31,500 (₹31500.00)"
        # — the type twice, the amount twice, in two spellings of one number.
        #
        # Both guards read what the LABEL already carries rather than a list of
        # which call sites do what, so a nineteenth cannot reintroduce either.
        kind = entry.get_entity_type_display()
        headline = entry.entity_label
        if amount is not None and '₹' not in headline:
            headline = f"{headline} · ₹{amount:,.0f}"

        notify(
            'RECORD_DELETED',
            f"{headline} deleted",
            # Omitted when the label already opens with it, or the row would
            # read "Restock Bill #669 … deleted" over "Record deleted ·
            # Restock Bill".
            detail=('' if headline.lower().startswith(kind.lower()) else kind),
            actor=entry.deleted_by,
            # reverse(), not an f-string path. A notification's whole value is
            # that tapping it lands on the record; a hardcoded URL survives a
            # route change silently and starts sending owners to a 404.
            url=reverse('deletion_history_detail', args=[entry.pk]),
            object_type=entity_type,
            object_id=entry.pk,
        )

        return entry


@receiver(user_logged_out)
def on_user_logout(sender, request, user, **kwargs):
    """
    When an owner logs out manually, immediately delete their UserSession record
    so the 'Active Now' dashboard stays 100% accurate.
    """
    if user:
        UserSession.objects.filter(session_key=request.session.session_key).delete()


@receiver(models.signals.post_delete, sender=JobCardPhoto)
def queue_photo_blob_for_collection(sender, instance, **kwargs):
    """
    Whenever a photo row disappears, remember the object it left behind.

    ON THE SIGNAL, NOT IN THE VIEW, AND THAT IS THE WHOLE POINT. The delete
    endpoint used to queue the key itself, which covered exactly one of the ways
    a photo row can vanish. Every other way is a CASCADE — deleting a spare
    row, deleting a job card, `purge_business_data`, the retention purge — and a
    CASCADE fires no view, so those objects were orphaned in the bucket for
    ever with nothing left pointing at them and no record that they existed.

    Django disables its fast-delete path for a model that has a post_delete
    receiver, so this fires for querysets and cascades too, not just
    `instance.delete()`. It runs inside the same transaction as the delete, so
    a key can never be lost between the two, and `get_or_create` keeps it
    idempotent.

    The object itself is removed later by `sweep_photo_blobs`. Deleting a row
    and deleting a blob stay separated: a DELETE to storage is a network call,
    and a slow or unreachable bucket must never be able to stop a photo
    disappearing from the app.
    """
    OrphanedPhotoBlob.objects.get_or_create(storage_key=instance.storage_key)
