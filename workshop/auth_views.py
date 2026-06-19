from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login
from django.contrib import messages
from django.utils.crypto import get_random_string
from decouple import config
import time
from datetime import timedelta
from django.utils import timezone
from .models import UserSession, FailedAttempt
from django.db.models import F
from twilio.rest import Client
import logging
import requests

logger = logging.getLogger(__name__)


# ============================================================
# Phone Number Normalization (Last 10 Digits Matching)
# Matches +91, spaces, and different formats in .env vs input.
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
# Owner Mobile Number Lookup (from .env — developer-registered)
# ============================================================
def get_owner_mobile(identifier):
    """
    Retrieves the registered mobile number for an owner.
    
    Args:
        identifier (str): Username or raw mobile number.
        
    Returns:
        str|None: The mobile number from .env if found, else None.
    """
    owner_map = {
        config('OWNER_1_USERNAME', default='').strip(' ='): config('OWNER_1_MOBILE', default='').strip(' ='),
        config('OWNER_2_USERNAME', default='').strip(' ='): config('OWNER_2_MOBILE', default='').strip(' ='),
    }
    
    # 1. Check if identifier is a registered username
    if identifier in owner_map:
        return owner_map[identifier]
    
    # 2. Check if identifier is a registered mobile number (using normalization)
    target = normalize_phone(identifier)
    for username, mobile in owner_map.items():
        if target == normalize_phone(mobile) and mobile != '':
            return mobile
            
    return None

def get_owner_username_by_mobile(mobile_number):
    """Returns the username associated with a mobile number in .env."""
    target = normalize_phone(mobile_number)
    owner_map = {
        config('OWNER_1_USERNAME', default='').strip(' ='): config('OWNER_1_MOBILE', default='').strip(' ='),
        config('OWNER_2_USERNAME', default='').strip(' ='): config('OWNER_2_MOBILE', default='').strip(' ='),
    }
    for username, mobile in owner_map.items():
        if target == normalize_phone(mobile) and mobile != '':
            return username
    return None


# ============================================================
# Phone Masking (Privacy & Feedback)
# Returns e.g. +91 ******7978
# ============================================================
def mask_phone(phone_str):
    if not phone_str:
        return "****"
    # Keep last 4 digits
    last_four = phone_str[-4:]
    return f"*******{last_four}"


# ============================================================
# IP-Based Lockout Infrastructure (Steel Gate)
# ============================================================
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
        bool: True if IP is blocked (failures >= 5 within 15 min).
    """
    ip = get_client_ip(request)
    attempt = FailedAttempt.objects.filter(ip_address=ip).first()
    if attempt and attempt.failures >= 5:
        # 15 Minute window
        lockout_expiry = attempt.last_attempt + timedelta(minutes=15)
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
# LIVE NOTIFICATION ENGINE (Twilio + Terminal Fallback)
# ============================================================
def send_twilio_sms(to_mobile, message):
    """
    Dispatches a real SMS via Twilio. 
    Falls back to Mock (terminal) if keys are missing.
    """
    sid = config('TWILIO_ACCOUNT_SID', default='your_sid_here').strip()
    token = config('TWILIO_AUTH_TOKEN', default='your_token_here').strip()
    from_num = config('TWILIO_FROM_NUMBER', default='your_twilio_number_here').strip()

    # Safety check: Is the user still using placeholders?
    if 'your_sid_here' in sid or not sid:
        print(f"--- [TITAN MOCK MODE] ---")
        print(f"TO: {to_mobile}\nMESSAGE: {message}")
        print(f"--------------------------")
        return False

    try:
        client = Client(sid, token)
        client.messages.create(
            body=message,
            from_=from_num,
            to=to_mobile
        )
        return True
    except Exception as e:
        logger.error(f"Titan SMS Failure: {str(e)}")
        print(f"!!! SMS FAILED: {str(e)}")
        return False

def send_telegram_msg(chat_id, message):
    """
    Dispatches a message via Telegram Bot API natively.
    """
    token = config('TELEGRAM_BOT_TOKEN', default='').strip()
    if not token or 'your_bot_token_here' in token or not chat_id:
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML'
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram Failure: {str(e)}")
        return False

def send_otp_sms(mobile_number, otp):
    """Sends OTP for Owner 2FA via Twilio & Telegram."""
    msg = f"Your WorkshopOS Login Code: <b>{otp}</b>"
    
    # 1. Twilio SMS
    success_sms = send_twilio_sms(mobile_number, f"Your WorkshopOS Login Code: {otp}")
    if success_sms:
        print(f"[OK] OTP Sent via SMS to {mobile_number}")
    else:
        print(f"[WARN] OTP SMS Fallback to Terminal: {mobile_number} | Code: {otp}")
        
    # 2. Telegram Broadcast
    owner1_mobile = normalize_phone(config('OWNER_1_MOBILE', default=''))
    owner2_mobile = normalize_phone(config('OWNER_2_MOBILE', default=''))
    norm_target = normalize_phone(mobile_number)
    
    if norm_target == owner1_mobile:
        chat_id = config('OWNER_1_CHAT_ID', default='').strip()
        if send_telegram_msg(chat_id, msg):
             print(f"[OK] OTP Sent via Telegram to Owner 1")
    elif norm_target == owner2_mobile:
        chat_id = config('OWNER_2_CHAT_ID', default='').strip()
        if send_telegram_msg(chat_id, msg):
             print(f"[OK] OTP Sent via Telegram to Owner 2")


# ============================================================
# Security Alert — Broadcast to BOTH Owners
# ============================================================
def send_titan_security_alert(user, request):
    """
    Broadcasts a high-priority security alert to BOTH owners (Sahad & Rijas).
    Covers all HQ Portal entry points (Owner & Staff logins).
    """
    owner1_mobile = config('OWNER_1_MOBILE', default='').strip()
    owner2_mobile = config('OWNER_2_MOBILE', default='').strip()
    
    # Get Device & Network Info
    ua = request.META.get('HTTP_USER_AGENT', 'Unknown Device')
    device_name = UserSession.get_device_name(ua)
    ip = request.META.get('REMOTE_ADDR', 'Unknown IP')
    
    # Format exactly as requested by user
    msg = (
        f"[SECURITY ALERT]: {user.username} just logged into HQ Portal.\n"
        f"Device: {device_name}\n"
        f"IP: {ip}\n"
        f"If this wasn't expected, REVOKE access now from your dashboard!"
    )
    
    # Broadcast to both owners via Dual-Channel (SMS + Telegram)
    recipients = [
        (config('OWNER_1_USERNAME', default='Sahad'), owner1_mobile, config('OWNER_1_CHAT_ID', default='').strip()),
        (config('OWNER_2_USERNAME', default='Rijas'), owner2_mobile, config('OWNER_2_CHAT_ID', default='').strip()),
    ]
    
    for name, mobile, chat_id in recipients:
        # Channel 1: Telegram (Primary, Free, Fast)
        if chat_id:
            if send_telegram_msg(chat_id, msg):
                print(f"[OK] Security Broadcast sent via Telegram to {name}")
                
        # Channel 2: Twilio SMS (Secondary/Fallback)
        if mobile:
            success = send_twilio_sms(mobile, msg)
            if success:
                print(f"[OK] Security Broadcast sent via SMS to {name} ({mobile})")
            else:
                print(f"[WARN] Security Broadcast MOCK to {name}: {mobile}")
                print(f"{msg}")



# ============================================================
# Staff Login — Floor & Office only
# ============================================================
def staff_login_view(request):
    """
    Handles standard password-based login for Floor and Office staff.
    Does NOT allow owners (redirects them or shows generic error for privacy).
    
    Algorithm:
    1. Check IP lockout status.
    2. Authenticate username/password.
    3. Block owners/superusers for security partitioning.
    4. Trigger collaborative alerts on success.
    """
    if request.user.is_authenticated:
        return redirect('home')

    # 1. IP Lockout Check
    if check_ip_lockout(request):
        messages.error(request, "[Security Lockout]: Too many failed attempts. Please wait 15 minutes.")
        return render(request, 'workshop/auth/login.html')

    if request.method == 'POST':
        u = request.POST.get('username', '').strip()
        p = request.POST.get('password', '').strip()
        user = authenticate(request, username=u, password=p)

        if user is not None:
            # Block owners from staff portal (Generic Error for security)
            if user.groups.filter(name='Owner').exists() or user.is_superuser:
                # Do NOT record this as a failure — credentials are valid, wrong portal
                messages.error(request, "Invalid credentials.")
                return redirect('login')

            auth_login(request, user)
            reset_login_failures(request)
            
            # --- Titan Security Alert ---
            send_titan_security_alert(user, request)
            return redirect('home')
        else:
            record_login_failure(request)
            messages.error(request, "Invalid credentials.")

    return render(request, 'workshop/auth/login.html')


# ============================================================
# Admin Login — Owner 2FA Step 1 (Password)
# ============================================================
def admin_login_view(request):
    """
    Streamlined HQ Portal Login for Owners (Sahad & Rijas).
    Direct Access + High-Priority Broadcast Alerts.
    
    Algorithm:
    1. Performs 'Steel Gate' IP audit.
    2. Normalizes input (Username or Mobile).
    3. Verifies password against Owner Group membership.
    4. Logs in user immediately.
    5. Dispatches TITAN SECURITY BROADCAST to both owners.
    """
    if request.user.is_authenticated:
        return redirect('home')

    # 1. IP Lockout Check (Steel Gate)
    if check_ip_lockout(request):
        messages.error(request, "[Security Lockout]: Too many failed attempts. Please wait 15 minutes.")
        return render(request, 'workshop/auth/admin_login.html')

    if request.method == 'POST':
        u = request.POST.get('username', '').strip()
        p = request.POST.get('password', '').strip()
        
        login_username = u
        norm_u = normalize_phone(u)
        if len(norm_u) == 10:
            resolved_u = get_owner_username_by_mobile(u)
            if resolved_u:
                login_username = resolved_u
        
        user = authenticate(request, username=login_username, password=p)

        if user is not None:
            # Must be an Owner or superuser
            if not (user.groups.filter(name='Owner').exists() or user.is_superuser):
                record_login_failure(request)
                messages.error(request, "Invalid credentials.")
                return redirect('admin_login')

            # --- DIRECT LOGIN SUCCESS ---
            auth_login(request, user)
            reset_login_failures(request)
            
            # --- Titan Security Alert (Broadcast to BOTH) ---
            send_titan_security_alert(user, request)
            
            messages.success(request, f"Welcome back, {user.username}!")
            return redirect('home')
        else:
            record_login_failure(request)
            messages.error(request, "Invalid credentials.")
            return redirect('admin_login')

    return render(request, 'workshop/auth/admin_login.html')




# ============================================================
# Forgot Password — Step 1: Enter Username → Send OTP
# ============================================================
def owner_forgot_password_view(request):
    """
    Owner enters their username.
    System looks up their mobile from .env and sends a reset OTP.
    """
    if request.user.is_authenticated:
        return redirect('home')

    # Check for active lockout from too many wrong OTP attempts
    blocked_until = request.session.get('pwd_reset_blocked_until')
    if blocked_until:
        remaining_secs = blocked_until - time.time()
        if remaining_secs > 0:
            remaining_mins = int(remaining_secs // 60) + 1
            messages.error(request, f"Too many failed attempts. Please wait {remaining_mins} minute(s) before trying again.")
            return render(request, 'workshop/auth/forgot_password.html')
        else:
            request.session.pop('pwd_reset_blocked_until', None)

    if request.method == 'POST':
        identifier = request.POST.get('username', '').strip()

        # Identification Logic
        target_username = identifier
        norm_id = normalize_phone(identifier)
        if len(norm_id) == 10:
            resolved = get_owner_username_by_mobile(identifier)
            if resolved:
                target_username = resolved

        # Validate existence in .env
        mobile = get_owner_mobile(target_username)
        if not mobile:
            messages.error(request, "No Owner account found with that identifier.")
            return redirect('owner_forgot_password')

        # Check the user actually exists in Django
        from django.contrib.auth.models import User
        try:
            user = User.objects.get(username=target_username)
        except User.DoesNotExist:
            messages.error(request, "No Owner account found with that identifier.")
            return redirect('owner_forgot_password')

        # 60-Second Cooldown Check (Prevent SMS Spam)
        last_send = request.session.get('last_otp_send_time')
        if last_send:
            elapsed = time.time() - last_send
            if elapsed < 60:
                remaining = int(60 - elapsed)
                messages.warning(request, f"Please wait {remaining} seconds before requesting another SMS reset.")
                return render(request, 'workshop/auth/forgot_password.html')

        # Generate OTP
        import hashlib
        otp = get_random_string(length=6, allowed_chars='0123456789')
        otp_hash = hashlib.sha256(otp.encode()).hexdigest()

        # Store in session
        request.session['pwd_reset_user_id'] = user.id
        request.session['pwd_reset_otp'] = otp_hash
        request.session['pwd_reset_expire'] = time.time() + 300  # 5 minutes
        request.session['last_otp_send_time'] = time.time() # Update cooldown

        # Send OTP
        send_otp_sms(mobile, otp)

        messages.success(request, "OTP sent to your registered mobile number.")
        return redirect('owner_reset_password')

    return render(request, 'workshop/auth/forgot_password.html')


# ============================================================
# Forgot Password — Step 2: Enter OTP + New Password
# ============================================================
def owner_reset_password_view(request):
    """
    Owner enters the OTP and their new password.
    """
    user_id = request.session.get('pwd_reset_user_id')
    stored_otp = request.session.get('pwd_reset_otp')
    expire_time = request.session.get('pwd_reset_expire')

    if not all([user_id, stored_otp, expire_time]):
        messages.error(request, "Session expired. Please start again.")
        return redirect('owner_forgot_password')

    if time.time() > expire_time:
        messages.error(request, "OTP expired. Please request a new one.")
        for key in ('pwd_reset_user_id', 'pwd_reset_otp', 'pwd_reset_expire'):
            request.session.pop(key, None)
        return redirect('owner_forgot_password')

    if request.method == 'POST':
        entered_otp = request.POST.get('otp', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        # Track failed OTP attempts
        attempts = request.session.get('pwd_reset_attempts', 0)
        
        import hashlib
        entered_hash = hashlib.sha256(entered_otp.encode()).hexdigest()

        if entered_hash != stored_otp:
            attempts += 1
            request.session['pwd_reset_attempts'] = attempts
            remaining = 3 - attempts

            if attempts >= 3:
                # Lockout — wipe reset session but set a 5-minute block
                for key in ('pwd_reset_user_id', 'pwd_reset_otp', 'pwd_reset_expire', 'pwd_reset_attempts'):
                    request.session.pop(key, None)
                request.session['pwd_reset_blocked_until'] = time.time() + 300  # 5-minute block
                messages.error(request, "Too many wrong attempts. You are blocked for 5 minutes.")
                return redirect('owner_forgot_password')
            else:
                messages.error(request, f"Incorrect OTP. {remaining} attempt(s) remaining.")
            return render(request, 'workshop/auth/reset_password.html')

        if len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
            return render(request, 'workshop/auth/reset_password.html')

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, 'workshop/auth/reset_password.html')

        # All checks passed — update the password
        from django.contrib.auth.models import User
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            messages.error(request, "Account not found. Please restart the reset process.")
            return redirect('owner_forgot_password')
            
        user.set_password(new_password)
        user.save()
        request.session.cycle_key()

        # Clean up session
        for key in ('pwd_reset_user_id', 'pwd_reset_otp', 'pwd_reset_expire', 'pwd_reset_attempts', 'last_otp_send_time'):
            request.session.pop(key, None)

        messages.success(request, "Password changed successfully! Please log in with your new password.")
        return redirect('admin_login')

    return render(request, 'workshop/auth/reset_password.html')

