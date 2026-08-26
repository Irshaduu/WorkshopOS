import os
import shutil
import glob
import subprocess
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

# One retention pool across both engines, swept by the `db_backup_*` glob.
KEEP = 14


class Command(BaseCommand):
    help = f'Backs up the active database (PostgreSQL or SQLite) and retains the last {KEEP} backups.'

    def handle(self, *args, **kwargs):
        db_config = settings.DATABASES['default']
        engine = db_config.get('ENGINE', '')
        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        # IST, via localtime() — never a bare datetime.now(), which reads the
        # SERVER's clock. Railway runs its containers in UTC, so a backup taken
        # at 02:00 on a Kerala morning was filed as 20:30 the PREVIOUS day: the
        # one moment the name matters is the day somebody is restoring from it,
        # picking a file by eye out of fourteen and needing "yesterday" to mean
        # yesterday. Nothing else in the command reads the clock, and the
        # retention sweep globs on the `db_backup_*` prefix and sorts by mtime
        # rather than parsing the stamp, so this is the whole of it — existing
        # backups keep their old names and the two spellings rotate together
        # through the changeover.
        timestamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')

        if 'postgresql' in engine:
            if not self._backup_postgres(db_config, backup_dir, timestamp):
                return

        elif 'sqlite' in engine:
            db_path = db_config.get('NAME')
            if not db_path or not os.path.exists(str(db_path)):
                self.stdout.write(self.style.ERROR(f"SQLite database file not found at {db_path}"))
                return

            backup_path = os.path.join(backup_dir, f"db_backup_{timestamp}.sqlite3")
            shutil.copy2(str(db_path), backup_path)
            self.stdout.write(self.style.SUCCESS(f"Successfully backed up SQLite to {backup_path}"))
        else:
            self.stdout.write(self.style.WARNING(f"Unsupported database engine: {engine}"))
            return

        self._prune(backup_dir)

    def _backup_postgres(self, db_config, backup_dir, timestamp):
        """
        Dump to a .part file and only name it a backup once pg_dump has said it
        succeeded. Writing straight to the final name meant a dump that died
        halfway — a dropped connection to Singapore is the ordinary case — left
        a truncated file sitting in backups/ that looked exactly like a good
        one. `_prune` counts by filename, so that half-file then occupied one
        of the retention slots and, once the folder filled, evicted a real
        backup to keep itself.

        Returns True only if a usable dump is now on disk.
        """
        db_name = db_config.get('NAME')
        db_user = db_config.get('USER')
        db_host = db_config.get('HOST') or 'localhost'
        db_port = str(db_config.get('PORT') or '5432')
        db_password = db_config.get('PASSWORD')

        env = os.environ.copy()
        if db_password:
            env['PGPASSWORD'] = str(db_password)

        base = ['pg_dump', '-h', db_host, '-p', db_port, '-U', str(db_user or ''), db_name]
        staging = os.path.join(backup_dir, f"db_backup_{timestamp}.part")

        # The extension states how to restore the file, because the two formats
        # are restored by different tools and there is no telling them apart by
        # eye on the day you need one: a custom-format archive (.dump) needs
        # `pg_restore`, plain SQL (.sql) needs `psql`. Both used to be written
        # as .sql.
        #
        # Custom format is preferred — compressed, and restorable table by
        # table. The plain fallback is not for one specific cause: pg_dump
        # exits non-zero for auth, network and version-mismatch alike, and
        # only the last of those is fixed by changing format. It costs one
        # more attempt to find out, and a plain dump beats no dump.
        attempts = [
            ('custom', base[:-1] + ['-F', 'c', '-b', '-f', staging, db_name], '.dump'),
            ('plain', base[:-1] + ['-f', staging, db_name], '.sql'),
        ]

        first_error = ''
        for label, cmd, suffix in attempts:
            try:
                res = subprocess.run(cmd, env=env, capture_output=True, text=True)
            except FileNotFoundError:
                self._discard(staging)
                self.stdout.write(self.style.ERROR(
                    "pg_dump was not found on PATH. Install the PostgreSQL client tools "
                    "(they ship with the server, and separately as postgresql-client)."
                ))
                return False
            except Exception as e:
                self._discard(staging)
                self.stdout.write(self.style.ERROR(f"PostgreSQL backup failed: {e}"))
                return False

            if res.returncode == 0:
                final = os.path.join(backup_dir, f"db_backup_{timestamp}{suffix}")
                os.replace(staging, final)
                self.stdout.write(self.style.SUCCESS(
                    f"Successfully backed up PostgreSQL ({label}) to {final}"
                ))
                return True

            first_error = first_error or (res.stderr or '').strip()
            self._discard(staging)

        self.stdout.write(self.style.ERROR(f"pg_dump failed: {first_error or 'no output'}"))
        return False

    def _discard(self, path):
        """Remove a half-written dump so it can never be mistaken for a backup."""
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _prune(self, backup_dir):
        backups = glob.glob(os.path.join(backup_dir, 'db_backup_*'))
        backups.sort(key=os.path.getmtime, reverse=True)

        for old_backup in backups[KEEP:]:
            try:
                os.remove(old_backup)
                self.stdout.write(self.style.WARNING(f"Deleted old backup: {os.path.basename(old_backup)}"))
            except OSError as e:
                self.stdout.write(self.style.ERROR(f"Could not delete old backup {old_backup}: {e}"))
