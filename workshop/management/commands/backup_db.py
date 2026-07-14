import os
import shutil
import datetime
import glob
from django.core.management.base import BaseCommand
from django.conf import settings

class Command(BaseCommand):
    help = 'Backs up the SQLite database and retains the last 7 backups.'

    def handle(self, *args, **kwargs):
        # AUD-0063: Basic automated DB backup strategy
        db_path = settings.DATABASES['default'].get('NAME')
        if not db_path or not str(db_path).endswith('.sqlite3'):
            self.stdout.write(self.style.WARNING("Database is not SQLite. Backup command skipped."))
            return

        db_path = str(db_path)
        if not os.path.exists(db_path):
            self.stdout.write(self.style.ERROR(f"Database not found at {db_path}"))
            return

        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"db_backup_{timestamp}.sqlite3"
        backup_path = os.path.join(backup_dir, backup_filename)

        # Copy the DB file
        shutil.copy2(db_path, backup_path)
        self.stdout.write(self.style.SUCCESS(f"Successfully backed up database to {backup_path}"))

        # Keep only the 7 most recent backups
        backups = glob.glob(os.path.join(backup_dir, 'db_backup_*.sqlite3'))
        backups.sort(key=os.path.getmtime, reverse=True)

        if len(backups) > 7:
            for old_backup in backups[7:]:
                os.remove(old_backup)
                self.stdout.write(self.style.WARNING(f"Deleted old backup: {old_backup}"))
