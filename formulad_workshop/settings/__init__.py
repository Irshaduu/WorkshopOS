"""
Settings Package — Auto-selects environment based on DJANGO_ENV variable.
Default: development
"""
import os

env = os.environ.get('DJANGO_ENV', 'development')

if env == 'production':
    from .production import *  # noqa: F401,F403
else:
    from .development import *  # noqa: F401,F403
