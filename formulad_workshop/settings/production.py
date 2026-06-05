"""
Production settings — PostgreSQL, SSL enforced, full security hardening.
Resolves all 6 Django deploy warnings.
"""
from .base import *  # noqa: F401,F403

DEBUG = False

# Production database (configure in .env)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='titan_db'),
        'USER': config('DB_USER', default='titan_user'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
    }
}

# SSL & HSTS (Resolves security.W004, W008, W012, W016)
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
