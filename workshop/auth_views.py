from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from .decorators import owner_required, is_owner
from django.contrib.auth.models import User
from datetime import timedelta
from django.utils import timezone
from .models import UserSession, FailedAttempt, UserProfile, PasswordResetOTP, AccountLockout
from .notifications import notify, recently_raised
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
def _role_name(user):
    """
    "Owner" / "Office" / "Floor" for a sign-in alert, or '' for an account in no
    group at all.

    The alert lands on a phone, and "amal signed in" says nothing about whether
    that account can see money — so the role travels with it, leading the
    detail line. An account in no group returns '' rather than "Staff":
    inventing a role would be worse than omitting one, and an account with none
    is itself the anomaly worth noticing.

    ⚠ This USED to return " (Office)" and, when the username already equalled
    the role, ''. That suppression existed for one reason and the reason is
    gone: the body was a single string, `"{username}{role} signed in"`, which
    rendered "Floor (Floor) signed in" for the accounts this workshop actually
    has — both staff logins are named after their role. The role now sits in
    its own field, printed nowhere near the username, so there is no
    duplication to avoid and suppressing it reported the real Office and Floor
    accounts as having no role at all.
    """
    names = set(user.groups.values_list('name', flat=True))
    for role in ('Owner', 'Office', 'Floor'):
        if role in names:
            return role
    return ""


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

    This is the **recovery-side** resolver and stays deliberately permissive. The
    sign-in form goes through `resolve_login_identifier`, which wraps this and
    narrows owner accounts to their email address.
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


def is_owner_account(user):
    """Owner by group or by superuser flag — the same pair every RBAC check uses."""
    return user.is_superuser or user.groups.filter(name='Owner').exists()


def resolve_login_identifier(identifier):
    """
    Resolve an identifier for SIGNING IN — deliberately stricter than resolving
    one for password recovery.

    **An owner account may be named only by its email address here.** Username
    and mobile still resolve for Office and Floor, and still resolve everywhere
    in the reset flow.

    *Why owners are narrowed.* `resolve_user_by_identifier` accepts the last ten
    digits of a mobile number, so the workshop's own published phone number was a
    valid owner identifier — it is on the website, on business cards and on
    Google Maps — and a first-name username is barely better. An email is no more
    secret than either, but a personal address is far less *published*, and this
    is the one form where being named carries two costs: it is where guessing
    happens, and it is where five wrong tries lock the account. Anyone who could
    name an owner could lock that owner out on demand, repeatedly, for free.

    *Why the reset flow is NOT narrowed.* It answers identically whether or not an
    account exists, carries its own two throttles, and delivers only to the
    address already on the account — so accepting a username there hands an
    attacker nothing, while refusing it would strand an owner who remembers their
    username but not which address is on file. Recovery paths should be generous
    about identifying you; authentication paths should not. The first fix in this
    same round of work was a recovery path that dead-ended, and narrowing a second
    one would be that same mistake wearing a security hat.

    *An owner with no email is deliberately exempt.* Nothing here may produce an
    account nobody can reach: with no address there is no email to sign in with
    **and** no `can_reset_password`, so the rule would be a permanent lockout with
    no self-service way back. Only an owner can clear an owner's email (`/admin/`
    is unreachable by design), so the exemption is not a lever an attacker can
    pull.
    """
    user = resolve_user_by_identifier(identifier)
    if user is None:
        return None

    email = (user.email or '').strip()
    if email and is_owner_account(user):
        if (identifier or '').strip().lower() != email.lower():
            return None

    return user


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

    brand = getattr(django_settings, 'BUSINESS_NAME', 'Formula D')

    subject = f"{code} is your {brand} password reset code"
    # Two clauses in the fourth paragraph are load-bearing, not padding.
    # "your password has not changed" is the reassurance: most of these arrive
    # because the other owner was testing or someone mistyped, and a mail that
    # only says "someone may be attempting to access your account" reads as
    # "you are already compromised". "Tell the other owner" is the action —
    # without it the reader is warned and given nothing to do, and that
    # escalation is the detection path for the one attack this flow cannot stop
    # by itself (an intruder who already has the owner's mailbox).
    body = (
        f"Hello {user.username},\n\n"
        f"Your password reset code is: {code}\n\n"
        f"It expires in {PasswordResetOTP.VALIDITY_MINUTES} minutes and works once on the requested device/browser.\n\n"
        f"Please make sure to choose a strong password.\n\n"
        f"This mailbox is unmonitored. Please do not reply."
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
# Login — one door
# ============================================================
#
# There used to be two full views, and the cost showed: the staff view rejected
# a valid owner password with a fake "Invalid credentials" (a lie that bought
# nothing, since the owner door was one link away), and the two drifted on which
# lockout they applied. They were collapsed into one view behind two *faces* —
# `/login/` and `/admin-login/`, differing only in heading and accent.
#
# The faces are gone too, as of 2026-08-12. They protected nothing, because
# either one accepted any role; what they did was publish the org chart to
# anyone who typed the address. "Admin Sign In" at a fixed URL announces that
# privileged accounts exist and where their door is, and the staff face named
# the tiers outright in its placeholder ("Office/Floor username"). Neither is a
# secret worth having, but neither is worth handing over either.
#
# Two smaller things went with them. `Forgot?` used to appear only on the owner
# face while the nav bar links to `/login/`, so an owner who arrived the ordinary
# way had no recovery route on screen; it is now on the one door, and leaks
# nothing, because step 1 of the reset already answers identically whether or not
# an account exists. And the two faces had already drifted — different field
# labels, different placeholders — which is the same lesson as the nav rebuild in
# CLAUDE.md: two copies of one thing diverge.
#
# Obscurity is not a control here and must not be treated as one. The controls
# are the password, the two lockouts, HTTPS and the RBAC decorators. This just
# stops the front door drawing a map.
LOGIN_TEMPLATE = 'workshop/auth/login.html'


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


def login_view(request):
    """
    Sign in. Office and Floor by username, email or mobile; **owners by their
    email address only** — see `resolve_login_identifier`.

    Failures are counted twice over: against the account (5 tries, the precise
    instrument) and against the IP (20, a backstop). See `AccountLockout` for
    why the account is the unit that matters in a workshop behind one connection.
    """
    template = LOGIN_TEMPLATE

    if request.user.is_authenticated:
        return redirect('home')

    if check_ip_lockout(request):
        messages.error(request, "Too many failed attempts from this network. Please wait 15 minutes.")
        return render(request, template, AUTH_PAGE)

    if request.method == 'POST':
        identifier = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        account = resolve_login_identifier(identifier)

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

        # Authenticate against the RESOLVED username, so email and mobile work.
        #
        # When nothing resolved, pass an empty username — never the raw input.
        # This is load-bearing, not tidying: Django's ModelBackend looks accounts
        # up **by username**, so falling back to the typed text would hand an
        # owner's username straight to the backend and sign them in on it,
        # silently undoing the whole of `resolve_login_identifier`. The refusal
        # has to be enforced here as well as there, because these two lines are
        # the only thing standing between a refused identifier and a valid login.
        #
        # Timing is unchanged either way: `''` matches no account, and ModelBackend
        # hashes a dummy password whenever the lookup misses, so the response time
        # still does not reveal which identifiers exist.
        user = authenticate(
            request,
            username=account.username if account else '',
            password=password,
        )

        if user is not None:
            auth_login(request, user)
            reset_login_failures(request)
            AccountLockout.clear(user)

            device = UserSession.get_device_name(request.META.get('HTTP_USER_AGENT', ''))
            # Which of the two sign-in events this is. An owner signing in is
            # routine and stays in the bell (INFO); Office or Floor signing in
            # pushes to the owners' phones, because a staff account lives on
            # shared shop-floor devices and its use is the thing the owners
            # cannot otherwise see. `notify()` already excludes the actor, so an
            # owner never buzzes themselves either way.
            #
            # Read from `is_owner` rather than a fresh group query so this can
            # never disagree with what the RBAC decorators consider an owner.
            role = _role_name(user)
            owner_signing_in = is_owner(user)
            # The title carries the category ("Owner signed in" / "Staff signed
            # in"), so the body says only what DIFFERS: who, and on what. It
            # used to repeat "signed in" from the title and then spend its last
            # third on an IP address.
            #
            # The IP is deliberately gone from BOTH sign-in events, and it is
            # not gone from the four security ones. Every device in this
            # workshop leaves through one connection — the laptop, the tablet
            # and both owners' phones — so on a routine sign-in the address is
            # near-constant and carries almost no information, while the
            # DEVICE is the thing that would look wrong. Control Hub →
            # Security, which this links to, still lists both per session.
            #
            # The role rides along for staff, where it is what decides whether
            # that account can see money; for an owner the title has said it.
            notify(
                'LOGIN' if owner_signing_in else 'STAFF_LOGIN',
                f"{user.username} signed in",
                # The role leads the detail for staff, because it is what
                # decides whether that account can see money; for an owner the
                # category beside it has already said so.
                detail=(device if owner_signing_in
                        else f"{role or 'No role'} · {device}"),
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
                # Route by whether the locked account can actually be acted on
                # where we are sending them. Control Hub → Accounts lists Office
                # and Floor only, and `manage_unlock_account` refuses owners by
                # design — so a locked *owner* used to open a page that did not
                # contain the account, did not mention a lockout, and offered
                # nothing to press. Security at least answers the question an
                # owner lockout actually raises: whose devices are signed in?
                locked_owner = (
                    account.is_superuser
                    or account.groups.filter(name='Owner').exists()
                )
                if locked_owner:
                    target = reverse('manage_dashboard') + '?section=security'
                    remedy = (
                        f"It clears itself in {AccountLockout.LOCKOUT_MINUTES} minutes; "
                        f"owner accounts cannot be unlocked from Control Hub."
                    )
                else:
                    target = reverse('manage_dashboard') + '?section=accounts'
                    # The window is stated, and stated FIRST. The unlock button
                    # in Control Hub renders only while the account is actually
                    # locked (`lock_minutes`), which is correct — a permanent
                    # button invites being clicked as a fix for something else.
                    # But this notification is permanent and the condition it
                    # describes is not, so an owner reading it an hour later
                    # followed the instruction, found an ordinary account list,
                    # and reasonably concluded the alert was lying. The owner
                    # branch above already said the duration; this one did not.
                    remedy = (
                        f"It clears itself in {AccountLockout.LOCKOUT_MINUTES} minutes; "
                        f"unlock it sooner from Control Hub → Accounts."
                    )

                notify(
                    'ACCOUNT_LOCKED',
                    f"{account.username}'s account locked",
                    # The remedy stays in full, in the detail line: it EXPIRES,
                    # and a permanent notification describing a temporary
                    # button has to say so.
                    detail=(
                        f"{failures} wrong passwords from "
                        f"{get_client_ip(request)}. {remedy}"
                    ),
                    url=target,
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


# ------------------------------------------------------------------
# Per-browser request log — what makes the throttle *visible*
# ------------------------------------------------------------------
# `PasswordResetOTP.throttle_reason` is the enforcement and stays silent,
# because it is keyed to the *account*: reporting "wait 45 seconds" would answer
# "does this account exist and can it reset?" for anyone who asked, which is the
# whole reason step 1 has a single generic reply.
#
# This log answers a different and harmless question — how many times has *this
# browser* submitted the form — so it can be reported in full. It runs on the
# same two numbers as the DB throttle, so for the ordinary case (one owner, one
# phone) the message shown is exactly the rule that will be applied.
#
# It is deliberately **not** a security control: clearing cookies resets it, and
# the account-keyed throttle is still underneath. Its only job is to stop the
# silence that made a rate limit look like a broken app.
SESSION_REQUEST_LOG = 'pwd_reset_request_times'


def _recent_own_requests(request):
    """This browser's reset submissions within the last hour, oldest first."""
    from django.utils.dateparse import parse_datetime

    now = timezone.now()
    times = []
    for value in request.session.get(SESSION_REQUEST_LOG, []):
        parsed = parse_datetime(value) if isinstance(value, str) else None
        if parsed is not None and (now - parsed).total_seconds() < 3600:
            times.append(parsed)
    times.sort()
    return times


def _own_request_throttle(request):
    """What to tell this browser about its own request rate, or None to proceed."""
    now = timezone.now()
    times = _recent_own_requests(request)

    if times:
        elapsed = (now - times[-1]).total_seconds()
        if elapsed < PasswordResetOTP.RESEND_COOLDOWN_SECONDS:
            wait = int(PasswordResetOTP.RESEND_COOLDOWN_SECONDS - elapsed) + 1
            return (
                f"A code was requested from this device moments ago. You can request "
                f"another in {wait} second{'s' if wait != 1 else ''}."
            )

    if len(times) >= PasswordResetOTP.MAX_REQUESTS_PER_HOUR:
        ready = timezone.localtime(times[0] + timedelta(hours=1))
        return (
            f"That is {PasswordResetOTP.MAX_REQUESTS_PER_HOUR} codes requested from this "
            f"device within an hour, which is the limit. You can request another at "
            f"{ready:%I:%M %p}. If none of them arrived, check the spam folder — and if "
            f"they are not there either, the address on the account may be wrong. "
            f"Please enter the registered Identifier."
        )

    return None


def _record_own_request(request):
    """Append this submission to the browser's log, dropping anything over an hour old."""
    times = _recent_own_requests(request)
    times.append(timezone.now())
    request.session[SESSION_REQUEST_LOG] = [t.isoformat() for t in times]


def owner_forgot_password_view(request):
    """
    Step 1 of the emailed-code reset. Owners only — see `can_reset_password`.
    """
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        identifier = request.POST.get('username', '').strip()

        # Checked before anything else, and reported plainly. This is the one
        # limit that can be disclosed without leaking whether the account exists,
        # because it describes what this browser did, not what the account is.
        own_limit = _own_request_throttle(request)
        if own_limit:
            messages.warning(request, own_limit)
            return redirect('owner_forgot_password')

        user = resolve_user_by_identifier(identifier)
        _record_own_request(request)

        # Marks the flow as started for *every* submission, real or not, so that
        # step 2 renders identically either way. Without a real user id behind it
        # no submitted code can ever verify.
        request.session['pwd_reset_pending'] = True
        request.session.pop('pwd_reset_user_id', None)

        throttle_kind = (
            PasswordResetOTP.throttle_kind(user)[0] if can_reset_password(user) else None
        )

        # Somebody has worked through this account's whole hourly budget. Tell
        # the owners — this and the burned-attempts alert below were the only
        # security events the system stayed silent about, while it announced
        # every routine sign-in.
        #
        # Only the HOURLY limit. The 60-second cooldown is a double-tapped
        # button, and an alert for that would be noise within a week.
        #
        # Worth understanding why this fires so rarely for an innocent owner,
        # because it is the ordering above doing the work rather than luck.
        # `_own_request_throttle` is checked FIRST and returns early, and it
        # runs on the same two numbers — so an owner fumbling their own reset in
        # one browser is stopped by their own session log and never reaches
        # here. Getting this far means the requests arrived with no session log
        # behind them: a cleared cookie jar, a private window, or another
        # machine. Which is precisely the shape of the thing worth a phone
        # buzzing about.
        if throttle_kind == PasswordResetOTP.THROTTLE_HOURLY and not recently_raised(
            'RESET_CODE_LIMIT', user.pk
        ):
            notify(
                'RESET_CODE_LIMIT',
                f"{user.username} — {PasswordResetOTP.MAX_REQUESTS_PER_HOUR} reset codes "
                f"requested in an hour",
                # "Change the password" was the wrong instruction as well as
                # an alarming one. This attack goes through the RESET flow, so
                # the password was never exposed and changing it stops nothing.
                # What is true: the throttle held, and nothing on the account
                # moved. The password advice that IS worth giving is about
                # strength, not rotation.
                detail=(
                    f"From {get_client_ip(request)}. No more codes for an hour, and "
                    f"nothing on the account changed. Worth making sure the password "
                    f"is a strong one."
                ),
                url=reverse('manage_dashboard') + '?section=security',
                object_type='USER',
                object_id=user.pk,
            )

        if can_reset_password(user) and throttle_kind is None:
            otp, code = PasswordResetOTP.issue(user, ip=get_client_ip(request))
            request.session['pwd_reset_user_id'] = user.id

            if not send_reset_code_email(user, code):
                # Delivery genuinely failed. Delete the code rather than retiring
                # it: a retired row still counts toward `throttle_reason`, which
                # counts by `created_at`, so three failed sends used to spend the
                # whole hourly budget and turn this honest error into the generic
                # "code sent" reply — the app then looked broken in two different
                # ways within a minute. An undelivered code is worth nothing to
                # anyone; `issue()` has already retired whatever preceded it.
                otp.delete()
                request.session.pop('pwd_reset_user_id', None)
                messages.error(
                    request,
                    "Could not send the code right now — the email did not go out. "
                    "This is a mail delivery problem, not a wrong username. Please try "
                    "again in a moment, and tell your developer if it keeps happening."
                )
                return redirect('owner_forgot_password')

        messages.success(request, GENERIC_RESET_REPLY)
        return redirect('owner_reset_password')

    return render(request, 'workshop/auth/forgot_password.html', AUTH_PAGE)


# ============================================================
# Forgot Password — Step 2: verify the code, set the password
# ============================================================
def _reset_page(request, submitted_code=''):
    """
    Re-render step 2, carrying the typed code back into the field.

    Every rejection on this page except a spent code is about the *password* —
    too short, mismatched, too common. Dropping the six digits along with it made
    the owner re-read them from the email for a mistake they had already fixed,
    on a phone, with the mail app one task-switch away. The code is not a
    password: it is single-use, expiring, and already in the visitor's inbox, so
    echoing it back reveals nothing they do not have. The two password fields are
    deliberately *not* echoed.
    """
    return render(request, 'workshop/auth/reset_password.html',
                  {**AUTH_PAGE, 'submitted_code': submitted_code})


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
            messages.error(request, "Password must be at least 8 characters. Your code is still valid.")
            return _reset_page(request, submitted_code)

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match. Your code is still valid.")
            return _reset_page(request, submitted_code)

        user_id = request.session.get('pwd_reset_user_id')
        user = User.objects.filter(pk=user_id).first() if user_id else None

        if user is not None:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError
            try:
                validate_password(new_password, user=user)
            except ValidationError as exc:
                messages.error(
                    request,
                    f"Password not strong enough: {' '.join(exc.messages)} Your code is still valid."
                )
                return _reset_page(request, submitted_code)

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
                # Five wrong codes against a live one. Raised HERE and not
                # inside `_dead_end()`, which is also reached by a code that
                # merely expired or was already spent — an owner who came back
                # to a stale email is not an attack and must not read like one.
                #
                # No actor, so both owners hear it: the account holder is who
                # can act, and the other owner is the corroboration.
                if not recently_raised('RESET_CODE_ATTEMPTS_SPENT', user.pk):
                    notify(
                        'RESET_CODE_ATTEMPTS_SPENT',
                        f"{user.username} — reset code guessed wrong "
                        f"{PasswordResetOTP.MAX_ATTEMPTS} times",
                        # Same rule as RESET_CODE_LIMIT above: the guesses
                        # were against a one-time CODE, not the password, and
                        # they all failed. Saying "change the password" reads
                        # as "you have been broken into" for an event whose
                        # whole content is that the defences worked.
                        detail=(
                            f"From {get_client_ip(request)}. The code is dead and "
                            f"nothing on the account changed. Worth making sure the "
                            f"password is a strong one."
                        ),
                        url=reverse('manage_dashboard') + '?section=security',
                        object_type='USER',
                        object_id=user.pk,
                    )
                return _dead_end()
            messages.error(
                request,
                f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} remaining."
            )
            # Echoed back so a single mistyped digit can be corrected in place
            # rather than re-entered from scratch against a shrinking budget.
            return _reset_page(request, submitted_code)

        # --- Verified. Apply the new password. ---
        user.set_password(new_password)
        user.save(update_fields=['password'])

        # Retire anything still outstanding so a second code cannot be replayed.
        PasswordResetOTP.objects.filter(user=user, used_at__isnull=True).update(
            used_at=timezone.now()
        )

        # Lift the sign-in lockout. This is the whole reason the reset exists:
        # owners cannot be unlocked from Control Hub (`manage_unlock_account`
        # refuses them by design), so the emailed code is a locked-out owner's
        # only self-service route back — and it used to dead-end. The lock is
        # keyed to the account, not the password, so it survived the reset: the
        # owner read "Password changed. Please sign in with your new password",
        # typed it, and was told "This account is locked after too many failed
        # attempts."
        #
        # That reads as the reset having failed, and the natural next move makes
        # it worse — request another code, against a budget of three an hour,
        # until `RESET_CODE_LIMIT` fires a CRITICAL alert at both owners over
        # somebody recovering their own account correctly.
        #
        # Safe to clear here: the lock exists to stop guessing, and holding the
        # registered mailbox plus setting a new password answers that far more
        # strongly than waiting out fifteen minutes does.
        #
        # The IP backstop (`FailedAttempt`) is deliberately NOT cleared. Its
        # message names the network rather than the account, so it does not
        # contradict the reset the way this one did, it clears itself on the same
        # timer, and wiping it would erase the record of a spray against every
        # other account behind that connection.
        AccountLockout.clear(user)

        # A reset is how a locked-out owner recovers, so every existing session
        # has to die — a stolen one must not survive the recovery.
        _terminate_all_sessions(user)

        # Tell the *other* owner. `actor=user` excludes the person who just did
        # it, which is the right audience in both readings: if this was the real
        # owner they need no telling, and if it was not, the account they just
        # took over is the last place a warning should land. The victim's own
        # signal is the reset email itself, which says to raise it here.
        notify(
            'PASSWORD_RESET',
            f"{user.username}'s password was reset",
            # The one event of the three where something actually changed, so
            # it says so plainly and does not soften it. No instruction: this
            # reaches the OTHER owner (the actor is excluded), whose useful next
            # move is to look at the signed-in devices — which is where the link
            # already goes.
            detail=(
                f"Emailed code · "
                f"{UserSession.get_device_name(request.META.get('HTTP_USER_AGENT', ''))} "
                f"({get_client_ip(request)}). Every device was signed out."
            ),
            actor=user,
            url=reverse('manage_dashboard') + '?section=security',
            object_type='USER',
            object_id=user.pk,
        )

        for key in ('pwd_reset_pending', 'pwd_reset_user_id'):
            request.session.pop(key, None)
        request.session.cycle_key()

        messages.success(request, "Password changed. Please sign in with your new password.")
        return redirect('login')

    return _reset_page(request)


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
