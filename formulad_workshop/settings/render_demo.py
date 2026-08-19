"""
Render demo settings — SQLite, committed to git, for a one-off client-meeting
deploy. NOT the real production path (that's production.py: Postgres,
Resend, HSTS). This exists only because Railway's free-tier Postgres was too
slow for a live demo; SQLite as a plain file removes the network round trip
entirely.

This whole branch (render-demo) is throwaway — see CLAUDE.md's "Deliberate
decisions" section, which documents Postgres as the deliberate choice for
both dev and production. Delete this branch after the meeting; don't merge
it into main.
"""
from .base import *  # noqa: F401,F403
from .base import BASE_DIR, config

DEBUG = False

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='.onrender.com').split(',')
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default='https://*.onrender.com'
).split(',')

# The committed file, checked into this branch — see db.sqlite3 in git.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# No SMTP account on Render's free tier and no real recipients for a demo —
# password-reset codes just print to the server log instead of sending.
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Render terminates TLS at its edge and forwards the original scheme, same
# shape as Railway in production.py.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Deliberately no VAPID / PHOTO_S3_* here — both features degrade quietly
# with no keys set (see base.py), which is exactly right for a throwaway demo.
