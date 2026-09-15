"""
Demo seeders run on a development machine and nowhere else.

These commands write made-up job cards, wages and rent, and some of them delete
what is already there. On the live Railway database one mistyped command in the
console would put fake money into the real books, and several of them have no
dry run. So each calls `refuse_outside_development()` before it touches a row.

The test is DJANGO_ENV, not DEBUG: it is the value that picks the settings file,
it has no default, and `manage.py test` runs with it set to development — so the
suite still exercises every seeder.

The leading underscore is load-bearing: Django does not offer a module in
`management/commands/` whose name starts with one as a command.
"""
import os

from django.core.management.base import CommandError


def refuse_outside_development(command):
    env = os.environ.get('DJANGO_ENV')
    if env != 'development':
        raise CommandError(
            f"{command} writes demo data, so it only runs with DJANGO_ENV=development "
            f"(this environment is {env!r}). Nothing was written."
        )
