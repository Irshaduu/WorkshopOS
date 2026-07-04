"""
Settings Package — Auto-selects environment based on DJANGO_ENV variable.
Fails safely if DJANGO_ENV is not set.
"""
import os
from django.core.exceptions import ImproperlyConfigured

env = os.environ.get('DJANGO_ENV')

if env == 'production':
    from .production import *  # noqa: F401,F403
elif env == 'development':
    from .development import *  # noqa: F401,F403
else:
    raise ImproperlyConfigured(
        "DJANGO_ENV environment variable must be set to 'development' or 'production'."
    )
