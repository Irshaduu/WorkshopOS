"""
Management command: unlock_legacy_data
--------------------------------------
Clears the go-live lock on Legacy Data → Opening Stock and Opening Balances, so
a genuine mistake in the starting position can be corrected.

⚠ THIS IS THE ONLY WAY BACK, AND THAT IS THE POINT. The lock is one row
(`workshop.models.LegacyDataLock`), set by an owner from the Legacy Data page
behind three confirmations. Nothing in the app can undo it — no button, no page,
no account — because a lock anybody can lift is not protection from the thing it
exists to stop: a figure quietly corrected months later. Running this command is
a deliberate act by whoever holds the deployment.

Afterwards the two screens are editable again and the page offers Lock once more,
so the correction should be followed by pressing it.

⚠ ONE THING IT CANNOT DO: if `LEGACY_DATA_LOCKED` is also set on the host, the
section stays locked whatever this command says — that switch is unset on the
host itself. The command reports it rather than leaving you guessing.

Usage:
    python manage.py unlock_legacy_data           # dry run — reports only
    python manage.py unlock_legacy_data --yes     # actually unlock
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from workshop.models import LegacyDataLock


class Command(BaseCommand):
    help = ("Clear the go-live lock on Legacy Data (Opening Stock and Opening Balances). "
            "Use --yes to apply.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', action='store_true',
            help="Actually unlock. Without this flag the command only reports.",
        )

    def handle(self, *args, **options):
        row = LegacyDataLock.objects.select_related('locked_by').first()

        if row is None:
            self.stdout.write("Legacy Data is not locked by this system.")
        else:
            who = row.locked_by.get_full_name() or row.locked_by.username if row.locked_by else 'an owner'
            self.stdout.write(f"Locked on {row.locked_at:%d %b %Y %H:%M} by {who}.")

        host_switch = bool(getattr(settings, 'LEGACY_DATA_LOCKED', False))
        if host_switch:
            self.stdout.write(self.style.WARNING(
                "⚠ LEGACY_DATA_LOCKED is also set on this host, so the section stays "
                "locked until that variable is removed there as well."
            ))

        if not options['yes']:
            self.stdout.write(self.style.WARNING(
                "Dry run — nothing changed. Re-run with --yes to unlock."
            ))
            return

        if row is not None:
            LegacyDataLock.objects.all().delete()
            self.stdout.write(self.style.SUCCESS(
                "Unlocked. Correct the figures, then press Lock on the Legacy Data page again."
            ))
