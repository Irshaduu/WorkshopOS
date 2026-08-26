"""
Base settings shared across all environments.
Extracted from the original settings.py for production readiness.
"""

from pathlib import Path
import os
from decouple import config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1').split(',')

# Allow CSRF for local network access (phone testing via IP)
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default='http://localhost:8000,http://127.0.0.1:8000'
).split(',')


# ---------------------------------------------------------------------------
# DATABASE BUILDERS
# ---------------------------------------------------------------------------
# Defined once here and used by BOTH development.py and production.py. They
# previously each carried their own copy of the Postgres block, which is how a
# connection setting gets fixed in one environment and quietly left broken in
# the other.

def postgres_db():
    """
    The PostgreSQL connection, read from .env — a LOCAL instance in development,
    Railway's own Postgres in production.

    ⚠ `DB_SSLMODE` defaults to `require`, which is wrong for both of those and
    right for neither by accident: it dates from a hosted Neon instance reached
    over the public internet. Local Postgres wants `disable` and Railway's
    private network wants `prefer`, so BOTH environments override it in their
    own `.env`. The default is left alone deliberately — changing it would make
    the insecure case the one you get by forgetting.
    """
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='titan_db'),
        'USER': config('DB_USER', default='titan_user'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
        # Persistent connections matter far more here than on SQLite: the
        # database is a network hop away, so re-handshaking per request would
        # dominate page time.
        'CONN_MAX_AGE': 60,
        'OPTIONS': {
            'sslmode': config('DB_SSLMODE', default='require'),
        },
    }


def sqlite_db():
    """The local SQLite file — bulk seeding and the test suite."""
    return {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'workshop',
    'inventory',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # AUD-0047: Serve static files cleanly
    # ------------------------------------------------------------------
    # COMPRESSION — the pages are large and CANNOT be cached.
    # ------------------------------------------------------------------
    # `NoStoreMiddleware` marks every signed-in response `no-store` so Back
    # cannot un-log-out, which is right and means the whole document is sent
    # again on every navigation. The job card form measures 207,877 bytes and
    # gzips to 54,816 — most of it the ~53 KB of inline JS and ~63 KB of inline
    # CSS that the frontend deliberately keeps in the template. Railway's proxy
    # does not compress, so without this the owners re-download 200 KB per page
    # on a phone.
    #
    # BELOW WhiteNoise on purpose. WhiteNoise short-circuits static requests, so
    # from here they never reach this middleware — which is what we want, since
    # it already serves its own pre-compressed .gz/.br files. Above WhiteNoise
    # this would waste cycles re-compressing assets it had just served.
    #
    # ON BREACH: the preconditions do exist here — `?q=` is reflected on four
    # list pages that also carry a CSRF token. Two Django defences cover it, and
    # both were verified rather than assumed:
    #   1. the CSRF token is re-masked on every render, so there is no stable
    #      secret for a compression-length oracle to walk one byte at a time;
    #   2. `GZipMiddleware.max_random_bytes = 100` pads each response with a
    #      random-length gzip filename field for exactly this reason.
    # No other secret is in a response body — the session id is an HttpOnly
    # cookie. Revisit if a page ever renders a long-lived token into its HTML.
    'django.middleware.gzip.GZipMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'workshop.middleware.SessionTrackingMiddleware',
    'workshop.middleware.NoIndexMiddleware',
    # Must stay AFTER AuthenticationMiddleware — it reads request.user to decide
    # whether the response is worth withholding from the cache.
    'workshop.middleware.NoStoreMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# SESSION & COOKIE SECURITY
# Default is 14 days; Owners requested 40 days (3,456,000 seconds)
SESSION_COOKIE_AGE = 3456000
SESSION_EXPIRE_AT_BROWSER_CLOSE = False 
SESSION_SAVE_EVERY_REQUEST = True 

# Browser Defense (Hardening)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

ROOT_URLCONF = 'formulad_workshop.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'workshop.context_processors.notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'formulad_workshop.wsgi.application'


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Django 5.1 REMOVED `STATICFILES_STORAGE` in favour of `STORAGES`, and does not
# warn when the old name is present — it is simply ignored. This project set
# `STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'`
# and had silently been running on the plain default ever since the Django 5
# upgrade: no content-hashed filenames, so no far-future caching, and none of
# WhiteNoise's gzip/brotli pre-compression. The manual `?v=4` query strings on
# the <script> tags in base.html are the workaround someone reached for when
# cache-busting stopped working; they are what this setting is supposed to make
# unnecessary. Verify with:
#   manage.py shell -c "from django.contrib.staticfiles.storage import staticfiles_storage; print(type(staticfiles_storage))"
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}


MEDIA_URL = 'media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication settings
LOGIN_REDIRECT_URL = 'home'
LOGIN_URL = 'login'

# ---------------------------------------------------------------------------
# EMAIL — password-reset codes only
# ---------------------------------------------------------------------------
# One sending identity (a workshop-owned mailbox, App Password in .env); the
# recipients are per-account `User.email` values in the database, never here.
# Development overrides EMAIL_BACKEND to the console unless EMAIL_REAL=True —
# see development.py — so day-to-day work never sends real mail.
#
# EMAIL_TIMEOUT is not optional: smtplib blocks indefinitely by default, so a
# hung SMTP connection would hold the request thread until the browser gave up.
# The name the *owners* know this workshop by, used in anything they read —
# currently only the reset email. Deliberately not "WorkshopOS", which is the
# project's internal name and appears nowhere in the UI: a reset code whose
# sender says "Formula D Workshop" and whose subject said "WorkshopOS" reads as
# a phishing attempt to exactly the cautious person it should reassure. A
# setting rather than a literal because this codebase is intended to serve other
# workshops later; one env var beats hunting for the hardcoded name.
BUSINESS_NAME = config('BUSINESS_NAME', default='Formula D')

EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='WorkshopOS <noreply@localhost>')

# Resend (HTTPS API) — production only. Railway blocks outbound SMTP below the
# Pro plan, so the SMTP settings above cannot deliver there however correct they
# are. `production.py` points EMAIL_BACKEND at workshop.email_backend; the SMTP
# block stays because development, and any host that does allow SMTP, still use
# it. An empty key is a valid configuration everywhere except production, and
# the backend says so loudly rather than failing quietly.
RESEND_API_KEY = config('RESEND_API_KEY', default='')
EMAIL_TIMEOUT = 10

# ---------------------------------------------------------------------------
# WEB PUSH (VAPID)
# ---------------------------------------------------------------------------
# Optional by design. With no keys configured, `workshop/push.py` skips sending
# and the in-app notification feed carries on unaffected — push is a delivery
# layer over rows that already exist, never the source of truth. That means a
# deploy missing these keys degrades quietly instead of erroring on every event.
VAPID_PUBLIC_KEY = config('VAPID_PUBLIC_KEY', default='')
VAPID_PRIVATE_KEY = config('VAPID_PRIVATE_KEY', default='')
VAPID_ADMIN_EMAIL = config('VAPID_ADMIN_EMAIL', default='')

# ---------------------------------------------------------------------------
# PHOTOS
# ---------------------------------------------------------------------------
# Optional by design, the same way push is. With no storage at all the photo box
# is not rendered, the endpoints refuse politely, and everything else in the app
# is untouched — a job card still saves, an invoice still prints, and settlement
# never chases a missing photo.
#
# THREE BACKENDS, chosen automatically (see `photos.storage_backend`):
#
#   s3     an S3-compatible bucket, whenever credentials are present. Cloudflare
#          R2 is the intended production home; Supabase Storage works with the
#          same settings and needs no payment card, which matters while the
#          workshop's own accounts do not exist yet.
#   local  this machine's disk, on a DEBUG server only. What makes the section
#          demonstrable with no account, no card and no internet.
#   off    neither — the section disappears rather than half-working.
#
# Named PHOTO_S3_* rather than R2_* deliberately: the moment these point at
# Supabase, a setting called R2_BUCKET would be describing something it is not.
PHOTO_S3_ACCESS_KEY_ID = config('PHOTO_S3_ACCESS_KEY_ID', default='')
PHOTO_S3_SECRET_ACCESS_KEY = config('PHOTO_S3_SECRET_ACCESS_KEY', default='')
PHOTO_S3_BUCKET = config('PHOTO_S3_BUCKET', default='')
PHOTO_S3_PREFIX = config('PHOTO_S3_PREFIX', default='')

# R2: set the account id and the host is derived. Anything else: set the full
# endpoint, the region it wants, and the path its S3 API lives under.
#   R2        PHOTO_S3_ACCOUNT_ID=xxxx                       (region auto, no path prefix)
#   Supabase  PHOTO_S3_ENDPOINT=<ref>.supabase.co
#             PHOTO_S3_REGION=ap-south-1
#             PHOTO_S3_PATH_PREFIX=storage/v1/s3
PHOTO_S3_ACCOUNT_ID = config('PHOTO_S3_ACCOUNT_ID', default='')
PHOTO_S3_ENDPOINT = config('PHOTO_S3_ENDPOINT', default='')
PHOTO_S3_REGION = config('PHOTO_S3_REGION', default='auto')
PHOTO_S3_PATH_PREFIX = config('PHOTO_S3_PATH_PREFIX', default='')

# Allow the local-disk fallback when DEBUG is on. Set False to prove the
# switched-off behaviour on a development machine.
PHOTO_LOCAL_FALLBACK = config('PHOTO_LOCAL_FALLBACK', default=True, cast=bool)

# How many photos each subject may carry. Deliberately not shown in the UI —
# a "3/10" badge invites filling it. The number only appears once it is hit.
PHOTO_LIMIT_CAR = 10
PHOTO_LIMIT_SPARE = 4

# Server-side ceiling on one upload. The browser emits ~200 KB at 1600px/q0.75;
# this is the bound on a crafted POST, not on the camera.
PHOTO_MAX_BYTES = 1_500_000

# =============================================================================
# ERROR LOGGING - Save errors to file instead of showing on screen
# =============================================================================
# Both handlers, always. The file is the local convenience; **the console is the
# only one that works on the host.** Render (and any container platform) streams
# stdout/stderr and nothing else — a rotating file inside the container is
# unreadable and is thrown away on the next deploy. With `propagate: False` on
# the app loggers and no console handler, every `logger.error()` in app code was
# invisible in production: the emailed reset code failing to send wrote its
# provider error to a file no one could open, leaving only the browser's
# deliberately vague "Could not send the code right now."
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(BASE_DIR, 'errors.log'),
            'maxBytes': 5 * 1024 * 1024,  # 5 MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'console': {
            'level': 'ERROR',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file', 'console'],
            'level': 'ERROR',
            'propagate': True,
        },
        # App-level loggers (e.g. workshop.auth_views, inventory.*)
        # Without these, logger.error() in app code falls through to stderr, not errors.log
        'workshop': {
            'handlers': ['file', 'console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'inventory': {
            'handlers': ['file', 'console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
