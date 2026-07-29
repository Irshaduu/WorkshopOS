from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from .decorators import owner_required
from django.contrib.auth.models import User
from datetime import timedelta
from django.utils import timezone
from .models import UserSession, FailedAttempt, UserProfile, PasswordResetOTP, AccountLockout
from .notifications import notify
from django.urls import reverse
from django.db.models import F
import logging

logger = logging.getLogger(__name__)


# ============================================================
# Phone Number Normalization (Last 10 Digits Matching)
# Lets a typed number match a stored one whatever the formatting:
# "+91 95674 94933", "+919567494933" and "9567494933" all reduce to the same
# 10 digits. Used by resolve_user_by_identifier.
# ============================================================
def normalize_phone(phone_str):
    """
    Normalizes a phone number to its last 10 digits for consistent lookup.
    
    Args:
        phone_str (str): Raw phone input (e.g., '+91 98765 43210').
        
    Returns:
        str: Sanitized 10-digit numeric string.
    """
    if not phone_str:
        return ""
    digits = "".join(filter(str.isdigit, phone_str))
    return digits[-10:] if len(digits) >= 10 else digits


# ============================================================
# IP-Based Lockout Infrastructure (Steel Gate)
# ============================================================
# Raised from 5 to 20 on 2026-07-28. The whole workshop — laptop, tablet, both
# owners' phones — leaves through one connection, so at 5 a single person
# fumbling their password on the Floor tablet locked the owners out of their own
# devices for fifteen minutes. Per-account lockout (`AccountLockout`, 5 failures)
# is now the precise instrument; this gate stays only as a backstop against
# someone spraying *many* accounts from one place, which 20 still catches while
# ordinary shared-connection use never reaches it.
IP_FAILURE_LIMIT = 20
IP_LOCKOUT_MINUTES = 15


# Signed-out pages own the whole viewport: `base.html` skips the nav bar when
# this is set. A bar offering "Floor" and "Login" above a login form is noise on
# a page whose only job is signing in.
AUTH_PAGE = {'hide_chrome': True}


def get_client_ip(request):
    """
    Returns the direct client IP.
    Only use REMOTE_ADDR — never trust client-supplied headers without a
    verified trusted proxy configuration.
    """
    return request.META.get('REMOTE_ADDR', '0.0.0.0')

def check_ip_lockout(request):
    """
    Evaluates if the visitor's IP is currently under a 'Steel Gate' block.
    
    Args:
        request (HttpRequest): Current login attempt request.
        
    Returns:
        bool: True if this IP is blocked (failures >= IP_FAILURE_LIMIT in window).
    """
    ip = get_client_ip(request)
    attempt = FailedAttempt.objects.filter(ip_address=ip).first()
    if attempt and attempt.failures >= IP_FAILURE_LIMIT:
        lockout_expiry = attempt.last_attempt + timedelta(minutes=IP_LOCKOUT_MINUTES)
        if timezone.now() < lockout_expiry:
            return True
        else:
            # Lockout expired — reset
            attempt.failures = 0
            attempt.save()
    return False

def record_login_failure(request):
    ip = get_client_ip(request)
    FailedAttempt.objects.get_or_create(ip_address=ip)
    FailedAttempt.objects.filter(ip_address=ip).update(failures=F('failures') + 1)

def reset_login_failures(request):
    ip = get_client_ip(request)
    FailedAttempt.objects.filter(ip_address=ip).update(failures=0)


# ============================================================
# Account lookup — username, email, or mobile
# ============================================================
def resolve_user_by_identifier(identifier):
    """
    Find the one account matching a username, email address, or mobile number.

    **Fails closed.** If an identifier somehow matches more than one account the
    answer is None, never a guess — an ambiguous "who is this?" must not resolve
    to an arbitrary user. The uniqueness constraint on
    `UserProfile.mobile_number` and the duplicate check in `set_owner_email`
    exist to keep that case from arising; this is the backstop if one slips
    through.

    Tried in order: exact username, then email (case-insensitive), then the last
    10 digits of a mobile number so stored and typed formats need not agree.
    """
    identifier = (identifier or '').strip()
    if not identifier:
        return None

    exact = User.objects.filter(username=identifier)
    if exact.count() == 1:
        return exact.first()

    by_email = User.objects.filter(email__iexact=identifier)
    if by_email.count() == 1:
        return by_email.first()

    digits = normalize_phone(identifier)
    if len(digits) == 10:
        profiles = UserProfile.objects.filter(mobile_number__endswith=digits).select_related('user')
        if profiles.count() == 1:
            return profiles.first().user

    return None


def can_reset_password(user):
    """
    Only owners with a deliverable address can use the emailed-code flow.

    Office and Floor accounts deliberately carry no email: owners create those
    logins and manage their passwords from Control Hub, so there is no
    self-service path for them to reach.
    """
    if user is None or not user.is_active:
        return False
    if not (user.email or '').strip():
        return False
    return user.is_superuser or user.groups.filter(name='Owner').exists()


# ============================================================
# Reset code delivery
# ============================================================
def send_reset_code_email(user, code):
    """
    Email a reset code. Returns True only if the provider accepted it.

    The code goes in the **subject line** on purpose: both iOS and Android show
    the subject in the notification banner, so the owner can read it without
    opening the mail app at all. That removes the app switch that is otherwise
    the slowest part of the flow. The trade — the code is briefly visible on a
    locked screen — is worth it for two owners on personal phones.
    """
    from django.conf import settings as django_settings
    from django.core.mail import send_mail

    subject = f"{code} is your WorkshopOS password reset code"
    body = (
        f"Hello {user.username},\n\n"
        f"Your password reset code is: {code}\n\n"
        f"Enter it in the app to set a new password. It expires in "
        f"{PasswordResetOTP.VALIDITY_MINUTES} minutes and can be used once.\n\n"
        f"If you did not ask for this, you can ignore this email - your password "
        f"has not changed.\n\n"
        f"This mailbox is not monitored. Please do not reply."
    )

    try:
        delivered = send_mail(
            subject,
            body,
            django_settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
        return delivered > 0
    except Exception as exc:
        # Never surface the provider's error to the browser — it can leak the
        # sending account and its configuration.
        logger.error(f"Reset code delivery failed for {user.username}: {exc}")
        return False


# ============================================================
# Login — one engine, two faces
# ============================================================
#
# `/login/` (staff) and `/admin-login/` (owner) are the same view with a
# different heading. The separation is presentational: owners think of these as
# two doors, and the owner face is the only one that offers Forgot Password
# because only owners carry an email.
#
# What is emphatically *not* duplicated is the authentication itself — one
# identifier resolver, one lockout, one code path. There used to be two full
# views, and the cost showed: the staff view rejected a valid owner password
# with a fake "Invalid credentials" (a lie that bought nothing, since the owner
# door was one link away), and the two drifted on which lockout they applied.
# Same lesson as the nav rebuild in CLAUDE.md — two copies of one thing diverge.
#
# Either face accepts any role. Whoever signs in lands on the dashboard their
# role renders.
FACE_TEMPLATES = {
    'owner': 'workshop/auth/admin_login.html',
    'staff': 'workshop/auth/login.html',
}


def _safe_next(request):
    """
    Honour ?next= only when it points back into this site.

    An unchecked next parameter turns the login page into an open redirect: a
    crafted link signs someone in and then bounces them to an attacker's page,
    which is a convincing way to harvest a password on the "session expired"
    screen that follows.
    """
    from django.utils.http import url_has_allowed_host_and_scheme

    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return None


def login_view(request, face='staff'):
    """
    Sign in with a username, email address, or mobile number.

    Failures are counted twice over: against the account (5 tries, the precise
    instrument) and against the IP (20, a backstop). See `AccountLockout` for
    why the account is the unit that matters in a workshop behind one connection.
    """
    template = FACE_TEMPLATES.get(face, FACE_TEMPLATES['staff'])

    if request.user.is_authenticated:
        return redirect('home')

    if check_ip_lockout(request):
        messages.error(request, "Too many failed attempts from this network. Please wait 15 minutes.")
        return render(request, template, AUTH_PAGE)

    if request.method == 'POST':
        identifier = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        account = resolve_user_by_identifier(identifier)

        if account is not None:
            locked_for = AccountLockout.minutes_remaining(account)
            if locked_for:
                # Naming the lockout does confirm the account exists. That is a
                # deliberate trade: the usernames here are not secrets, an
                # attacker must already have burned five failures to see this,
                # and an owner who cannot get in needs to know *why* — otherwise
                # the next step is a phone call, or more attempts that extend
                # the lock.
                messages.error(
                    request,
                    f"This account is locked after too many failed attempts. "
                    f"Try again in {locked_for} minute{'s' if locked_for != 1 else ''}."
                )
                return render(request, template, AUTH_PAGE)

        # Authenticate against the resolved username so email and mobile work.
        # When nothing resolved we still call authenticate() with the raw input:
        # Django's ModelBackend hashes a dummy password for unknown users, so
        # the response time does not reveal which identifiers exist.
        user = authenticate(
            request,
            username=account.username if account else identifier,
            password=password,
        )

        if user is not None:
            auth_login(request, user)
            reset_login_failures(request)
            AccountLockout.clear(user)

            device = UserSession.get_device_name(request.META.get('HTTP_USER_AGENT', ''))
            notify(
                'LOGIN',
                f"{user.username} signed in — {device} · {get_client_ip(request)}",
                actor=user,
                url=reverse('manage_dashboard') + '?section=security',
            )

            return redirect(_safe_next(request) or 'home')

        record_login_failure(request)
        if account is not None:
            failures = AccountLockout.record_failure(account)
            # Fires on the crossing only. Without the equality check every
            # further attempt against an already-locked account would raise
            # another notification — an attacker could fill the owners' feed at
            # will, which is both noise and a way to bury something real.
            if failures == AccountLockout.MAX_FAILURES:
                notify(
                    'ACCOUNT_LOCKED',
                    f"{account.username} was locked after {failures} failed sign-in "
                    f"attempts from {get_client_ip(request)}.",
                    url=reverse('manage_dashboard') + '?section=accounts',
                    object_type='USER',
                    object_id=account.pk,
                )
        messages.error(request, "Invalid credentials.")

    return render(request, template, AUTH_PAGE)


# ============================================================
# Change Password — signed-in owners, no email involved
# ============================================================
def _terminate_other_sessions(request, user):
    """
    Sign every *other* device out and clear its tracking row.

    Django already invalidates the other sessions on a password change — it
    compares each session's stored auth hash against the current password — but
    the rows survive until the 40-day sweep, so Control Hub's security list
    would keep advertising devices that are actually dead. Deleting both makes
    the dashboard tell the truth immediately, and matches what
    `manage_terminate_session` does for a single device.

    Returns the number of devices signed out.
    """
    from django.contrib.sessions.models import Session

    current_key = request.session.session_key
    stale = UserSession.objects.filter(user=user).exclude(session_key=current_key)
    stale_keys = list(stale.values_list('session_key', flat=True))

    if stale_keys:
        Session.objects.filter(session_key__in=stale_keys).delete()
        stale.delete()

    return len(stale_keys)


@owner_required
def change_password_view(request):
    """
    Lets a signed-in owner replace their own password. No email, no OTP.

    This is the handover path: an owner is given a temporary password verbally,
    signs in, and sets their own here. Keeping it independent of email is the
    point — go-live does not wait on the mail provider being configured, and the
    OTP flow stays what it should be, a rarely-used backstop for a genuinely
    forgotten password.

    Owner-only by design. Office and Floor passwords are managed entirely by
    owners from Control Hub (/manage/?section=accounts); those roles never
    change their own credentials, so there is deliberately no self-service path
    for them.
    """
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()

            signed_out = _terminate_other_sessions(request, user)

            # Without this the owner is logged out by their own change: Django
            # rotates the session auth hash, and the current session would fail
            # the very next request's check.
            update_session_auth_hash(request, user)

            if signed_out:
                messages.success(
                    request,
                    f"Password changed. {signed_out} other device"
                    f"{'s were' if signed_out > 1 else ' was'} signed out."
                )
            else:
                messages.success(request, "Password changed.")
            return redirect('home')
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'workshop/auth/change_password.html', {'form': form})


# ============================================================
# Forgot Password — Step 1: identify the account, email a code
# ============================================================
#
# Every reply on this page is identical whether or not the account exists, is an
# owner, has an email, or is currently throttled. Differing responses — even a
# different redirect target — would turn this form into an account-existence
# oracle. The one message therefore also states the once-a-minute rule, so a real
# owner who re-requests too quickly understands why no second email arrived
# instead of assuming the system is broken.
GENERIC_RESET_REPLY = (
    "If that account exists, a 6-digit code has been sent to its registered email. "
    "A new code can be requested once a minute."
)


def owner_forgot_password_view(request):
    """
    Step 1 of the emailed-code reset. Owners only — see `can_reset_password`.
    """
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        identifier = request.POST.get('username', '').strip()
        user = resolve_user_by_identifier(identifier)

        # Marks the flow as started for *every* submission, real or not, so that
        # step 2 renders identically either way. Without a real user id behind it
        # no submitted code can ever verify.
        request.session['pwd_reset_pending'] = True
        request.session.pop('pwd_reset_user_id', None)

        if can_reset_password(user) and PasswordResetOTP.throttle_reason(user) is None:
            otp, code = PasswordResetOTP.issue(user, ip=get_client_ip(request))
            request.session['pwd_reset_user_id'] = user.id

            if not send_reset_code_email(user, code):
                # Delivery genuinely failed. Retire the code rather than leaving a
                # live one nobody received, and say so — the old behaviour reported
                # success regardless, so a failed reset looked like a working one.
                otp.used_at = timezone.now()
                otp.save(update_fields=['used_at'])
                request.session.pop('pwd_reset_user_id', None)
                messages.error(
                    request,
                    "Could not send the code right now. Please try again in a moment."
                )
                return redirect('owner_forgot_password')

        messages.success(request, GENERIC_RESET_REPLY)
        return redirect('owner_reset_password')

    return render(request, 'workshop/auth/forgot_password.html', AUTH_PAGE)


# ============================================================
# Forgot Password — Step 2: verify the code, set the password
# ============================================================
def owner_reset_password_view(request):
    """
    Step 2. The account is held in the session, the code lives in the database.

    Session-bound on purpose: the reset can only be completed in the browser that
    asked for it. On an installed PWA that session survives switching to the mail
    app and back, which is the whole reason this is a code and not a link.
    """
    if not request.session.get('pwd_reset_pending'):
        messages.error(request, "Please start from the Forgot Password page.")
        return redirect('owner_forgot_password')

    if request.method == 'POST':
        submitted_code = request.POST.get('otp', '').strip()
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        # Password rules are checked BEFORE the code is spent. A valid code paired
        # with a too-short password must not burn the code — the owner would have
        # to request another one for a typo they can simply fix.
        if len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
            return render(request, 'workshop/auth/reset_password.html', AUTH_PAGE)

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, 'workshop/auth/reset_password.html', AUTH_PAGE)

        user_id = request.session.get('pwd_reset_user_id')
        user = User.objects.filter(pk=user_id).first() if user_id else None

        if user is not None:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError
            try:
                validate_password(new_password, user=user)
            except ValidationError as exc:
                messages.error(request, f"Password not strong enough: {', '.join(exc.messages)}")
                return render(request, 'workshop/auth/reset_password.html', AUTH_PAGE)

        otp = PasswordResetOTP.objects.filter(user=user).first() if user else None

        def _dead_end():
            """Expired, already used, budget spent, or no real account behind the
            session — one identical outcome for all four, same non-disclosure rule
            as step 1."""
            for key in ('pwd_reset_pending', 'pwd_reset_user_id'):
                request.session.pop(key, None)
            messages.error(request, "That code is no longer valid. Please request a new one.")
            return redirect('owner_forgot_password')

        if otp is None or not otp.is_usable:
            return _dead_end()

        if not otp.verify(submitted_code):
            remaining = otp.attempts_remaining
            if remaining == 0:
                return _dead_end()
            messages.error(
                request,
                f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} remaining."
            )
            return render(request, 'workshop/auth/reset_password.html', AUTH_PAGE)

        # --- Verified. Apply the new password. ---
        user.set_password(new_password)
        user.save(update_fields=['password'])

        # Retire anything still outstanding so a second code cannot be replayed.
        PasswordResetOTP.objects.filter(user=user, used_at__isnull=True).update(
            used_at=timezone.now()
        )

        # A reset is how a locked-out owner recovers, so every existing session
        # has to die — a stolen one must not survive the recovery.
        _terminate_all_sessions(user)

        for key in ('pwd_reset_pending', 'pwd_reset_user_id'):
            request.session.pop(key, None)
        request.session.cycle_key()

        messages.success(request, "Password changed. Please sign in with your new password.")
        return redirect('admin_login')

    return render(request, 'workshop/auth/reset_password.html', AUTH_PAGE)


def _terminate_all_sessions(user):
    """
    Sign a user out everywhere and clear the tracking rows.

    Used after a reset rather than `_terminate_other_sessions`: the person
    completing the reset is not signed in yet, so there is no current session to
    preserve, and anyone who *was* signed in as this account should not stay
    that way.
    """
    from django.contrib.sessions.models import Session

    keys = list(UserSession.objects.filter(user=user).values_list('session_key', flat=True))
    if keys:
        Session.objects.filter(session_key__in=keys).delete()
    UserSession.objects.filter(user=user).delete()
    return len(keys)
