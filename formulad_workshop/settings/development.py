"""
Development settings — DEBUG=True, no SSL.

DATABASE: PostgreSQL by default (changed 2026-07-27).

Development now runs on the same PostgreSQL the app will ship on, so
Postgres-only behaviour — stricter GROUP BY, real numeric types, case
sensitivity, sequence handling — surfaces while it is cheap to fix rather than
on go-live day. SQLite stays available, but only for the two jobs it is
genuinely better at:

  1. Bulk dummy data (`USE_SQLITE=true`).
     seed_dummy_data writes tens of thousands of rows through the ORM. Against
     a hosted Postgres each one is a network round-trip and the run takes
     hours; against a local file it takes minutes. Seed into SQLite, then push
     it up with `python manage.py copy_sqlite_to_postgres`.

  2. The test suite — automatic, and not negotiable.
     The Django test runner CREATEs and DROPs an entire database. Pointing that
     at a hosted Postgres is slow (291 tests × ~75 ms per round-trip) and a bad
     idea in principle. `manage.py test` therefore always uses SQLite whatever
     USE_SQLITE says, so there is no flag to remember and no way to run the
     suite against live data by accident.

To force SQLite for one ordinary command, set USE_SQLITE=true in .env
(or `$env:USE_SQLITE="true"` for a single PowerShell session).
"""
import sys

from .base import *  # noqa: F401,F403
from .base import postgres_db, sqlite_db, config

DEBUG = True
ALLOWED_HOSTS = ['*']  # Allow access from other devices on the local network

# `manage.py test ...` — argv[1] is the subcommand. Matched exactly rather than
# scanning the whole argv, so an argument that merely contains "test" can never
# silently swap the database out from under a real command.
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == 'test'
USE_SQLITE = config('USE_SQLITE', default=False, cast=bool)

if RUNNING_TESTS or USE_SQLITE:
    DATABASES = {'default': sqlite_db()}
else:
    DATABASES = {'default': postgres_db()}

# The SQLite file is always reachable under its own alias, so
# copy_sqlite_to_postgres can read from it while `default` points at Postgres.
# Django only opens a connection when an alias is actually used, so declaring
# it here costs nothing when it isn't.
DATABASES['sqlite'] = sqlite_db()

# ---------------------------------------------------------------------------
# PHOTOS ARE OFF UNDER THE TEST RUNNER, WHATEVER .env SAYS
# ---------------------------------------------------------------------------
# Not a convenience — a correctness rule, and it was learned the hard way. The
# photo settings are read by `config()` from `.env`, so the moment a developer
# added real storage credentials the suite started rendering the photo box, the
# spare-parts column and the "Customer, Notes & Photos" heading — and four
# unrelated section tests that assert the ordinary shape began to fail on one
# machine and pass on another.
#
# A suite whose result depends on the developer's local configuration is worse
# than a failing one, because it is trusted. Tests that want photos turn them on
# explicitly with `override_settings`, exactly as `workshop/tests/test_photos.py`
# does, so both shapes stay pinned and neither depends on who is running them.
#
# Same reasoning as forcing SQLite above: the test environment is decided here,
# not by whatever happens to be in the environment.
if RUNNING_TESTS:
    PHOTO_S3_ACCESS_KEY_ID = ''
    PHOTO_S3_SECRET_ACCESS_KEY = ''
    PHOTO_S3_BUCKET = ''
    PHOTO_S3_ACCOUNT_ID = ''
    PHOTO_S3_ENDPOINT = ''
    PHOTO_LOCAL_FALLBACK = False

# No SSL in dev
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Mail prints to the runserver console instead of being sent, so the whole reset
# flow is testable with no SMTP account and no risk of mailing a real owner
# while poking at the form. Set EMAIL_REAL=true in .env for a genuine
# delivery test against the configured mailbox.
# (`manage.py test` ignores both: Django swaps in the locmem backend itself.)
EMAIL_REAL = config('EMAIL_REAL', default=False, cast=bool)
if not EMAIL_REAL:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
