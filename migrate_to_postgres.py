"""
=============================================================================
WorkshopOS (Titan) — Automated SQLite to PostgreSQL Migration Tool
=============================================================================
This tool safely extracts all data from db.sqlite3, applies Django migrations
to your target PostgreSQL database, restores all data, and resets PostgreSQL
primary key auto-increment sequences.

Safety Guarantee:
- Your db.sqlite3 file is NEVER altered or deleted.
- You can revert back to SQLite anytime by setting DJANGO_ENV=development.
=============================================================================
"""

import os
import sys
import subprocess
import django

# Ensure base directory is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Force production mode settings (which use PostgreSQL settings from .env)
os.environ['DJANGO_ENV'] = 'production'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'formulad_workshop.settings')

def run_cmd(command, description, env_vars=None):
    print(f"\n---> {description}...")
    current_env = os.environ.copy()
    if env_vars:
        current_env.update(env_vars)
        
    result = subprocess.run(command, shell=True, capture_output=True, text=True, env=current_env)
    if result.returncode != 0:
        print(f"[ERROR] during: {description}")
        print("STDERR:\n", result.stderr)
        print("STDOUT:\n", result.stdout)
        sys.exit(1)
    else:
        print(f"[SUCCESS]: {description}")
        if result.stdout.strip():
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            for l in lines[-5:]:
                print(f"   {l}")

def main():
    print("="*65)
    print("      WORKSHOP OS -- SAFE POSTGRESQL MIGRATION PIPELINE")
    print("="*65)

    # 1. Initialize Django
    try:
        django.setup()
    except Exception as e:
        print(f"[ERROR] Failed to initialize Django settings: {e}")
        print("\nPlease check your .env file to ensure PostgreSQL credentials are set:")
        print("DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT")
        sys.exit(1)

    from django.conf import settings
    db_engine = settings.DATABASES['default']['ENGINE']
    db_name = settings.DATABASES['default'].get('NAME')

    if 'postgresql' not in db_engine:
        print(f"[ERROR]: Django default database engine is '{db_engine}', not 'django.db.backends.postgresql'.")
        sys.exit(1)

    print(f"\nTarget Database : PostgreSQL [{db_name}]")
    print(f"Host            : {settings.DATABASES['default'].get('HOST')}")
    print(f"User            : {settings.DATABASES['default'].get('USER')}")

    # 2. Dump data from SQLite (pass DJANGO_ENV=development)
    dump_file = os.path.join(BASE_DIR, "sqlite_data_dump.json")
    dump_cmd = (
        f'"{sys.executable}" manage.py dumpdata '
        f'--natural-foreign --natural-primary -e contenttypes -e auth.Permission '
        f'--indent 2 -o "{dump_file}"'
    )
    run_cmd(dump_cmd, "Exporting clean snapshot from db.sqlite3", env_vars={'DJANGO_ENV': 'development'})

    # 3. Apply fresh migrations to PostgreSQL
    migrate_cmd = f'"{sys.executable}" manage.py migrate'
    run_cmd(migrate_cmd, "Applying schema migrations to PostgreSQL")

    # 4. Load snapshot data into PostgreSQL
    load_cmd = f'"{sys.executable}" manage.py loaddata "{dump_file}"'
    run_cmd(load_cmd, "Importing data snapshot into PostgreSQL")

    # 5. Fix PostgreSQL Primary Key Sequences
    print("\n---> Fixing PostgreSQL Primary Key Sequences...")
    from django.db import connection

    apps_to_reset = ['workshop', 'inventory', 'auth', 'sessions']
    commands = []
    for app in apps_to_reset:
        try:
            sql = subprocess.check_output(
                f'"{sys.executable}" manage.py sqlsequencereset {app}',
                shell=True,
                text=True,
                env=os.environ
            )
            if sql.strip():
                commands.append(sql)
        except Exception as ex:
            print(f"   Warning resetting sequence for {app}: {ex}")

    if commands:
        with connection.cursor() as cursor:
            for sql_block in commands:
                cursor.execute(sql_block)
        print("[SUCCESS]: PostgreSQL sequence counters updated.")
    else:
        print("[INFO]: No sequence adjustments required.")

    print("\n" + "="*65)
    print("MIGRATION COMPLETE! YOUR SYSTEM IS NOW RUNNING ON POSTGRESQL.")
    print("="*65)
    print("\nTo start your production server with PostgreSQL:")
    print("  $env:DJANGO_ENV='production'")
    print("  python manage.py runserver\n")

if __name__ == "__main__":
    main()
