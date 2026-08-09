"""
Production settings — PostgreSQL, SSL enforced, full security hardening.
Resolves all 6 Django deploy warnings.
"""
from .base import *  # noqa: F401,F403
from .base import postgres_db

DEBUG = False

# Mail goes over Resend's HTTPS API, not SMTP. Railway blocks outbound SMTP on
# every plan below Pro, so the SMTP settings in base.py cannot deliver from this
# host no matter how they are configured. Only the transport changes: the reset
# flow, its throttles and its tests are untouched. See workshop/email_backend.py.
EMAIL_BACKEND = 'workshop.email_backend.ResendEmailBackend'

# Production database (configure in .env). Built by the shared helper in
# base.py — the same one development.py uses — so the two environments cannot
# drift apart on connection settings.
DATABASES = {'default': postgres_db()}

# SSL & HSTS (Resolves security.W004, W008, W012, W016)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

