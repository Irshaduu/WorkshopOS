"""
Copy the local SQLite database up into PostgreSQL.

The intended workflow, and the reason SQLite is still around at all:

    $env:USE_SQLITE = "true"
    python manage.py seed_dummy_data --start 2021-01-01 --end 2026-07-27
    Remove-Item Env:\\USE_SQLITE
    python manage.py copy_sqlite_to_postgres          # dry run
    python manage.py copy_sqlite_to_postgres --yes    # actually copy

seed_dummy_data writes every row through the ORM so signals fire. Against a
hosted Postgres that is tens of thousands of network round-trips and takes
hours; against a local file it takes minutes. So: seed locally, then move the
finished result up in bulk.

WHAT IT DOES NOT DO
  • It does not migrate. Both databases must already be on the same migration
    state — the command checks and refuses otherwise, because copying rows into
    a stale schema is how you get a half-broken database.
  • It does not fire signals. bulk_create() deliberately skips them, which is
    what we want: the SQLite rows are already internally consistent, and
    replaying the stock-sync signals would deduct every spare a second time.
  • It does not copy content types, permissions, sessions or admin log entries.
    Those are created by `migrate` on the target and their ids need not match;
    copying them over the top is a reliable way to corrupt admin access.

SEQUENCES
Rows are inserted with their original primary keys so foreign keys still line
up. Postgres sequences do not advance when an id is supplied explicitly, so
every sequence is reset afterwards — without that the next insert collides with
an existing id. SQLite has no equivalent, which is exactly why this bites only
after switching to Postgres.
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, transaction

# Owned by `migrate` on the target, or meaningless to copy. Ids are allowed to
# differ between the two databases, so copying these would break more than it
# fixes.
EXCLUDED = {
    'contenttypes.ContentType',
    'auth.Permission',
    'sessions.Session',
    'admin.LogEntry',
    # Permission M2Ms reference auth_permission ids, which are assigned by
    # migrate and can differ per database. RBAC here runs on group *names*
    # (Owner / Office / Floor), so nothing is lost by skipping them.
    'auth.Group_permissions',
    'auth.User_user_permissions',
}

APP_LABELS = ('workshop', 'inventory', 'auth')
SOURCE = 'sqlite'
TARGET = 'default'
BATCH = 500


def _label(model):
    return f"{model._meta.app_label}.{model.__name__}"


def _ordered_models():
    """
    Every concrete model to copy, parents before children.

    Derived by topological sort over each model's FK/O2O targets rather than a
    hand-written list, so adding a model later does not silently leave it out
    or copy it in the wrong order.
    """
    models = []
    for label in APP_LABELS:
        for model in apps.get_app_config(label).get_models(include_auto_created=True):
            if _label(model) not in EXCLUDED and not model._meta.proxy:
                models.append(model)

    deps = {}
    for model in models:
        required = set()
        for field in model._meta.get_fields():
            # `concrete` is what separates a real FK column on THIS table from
            # the reverse accessor Django also reports. Without it a reverse
            # one-to-one (User <- UserProfile) reads as "User depends on
            # UserProfile" and inverts the order — which put auth_user_groups
            # ahead of auth_user and would have failed on an FK violation.
            if not getattr(field, 'concrete', False):
                continue
            if field.many_to_one or field.one_to_one:
                target = field.related_model
                # Self-references resolve within one table, so they never
                # constrain ordering between tables.
                if target in models and target is not model:
                    required.add(target)
        deps[model] = required

    ordered, seen = [], set()
    while len(ordered) < len(models):
        progressed = False
        for model in models:
            if model in seen:
                continue
            if deps[model] <= seen:
                ordered.append(model)
                seen.add(model)
                progressed = True
        if not progressed:
            # A genuine FK cycle. Emit the rest in declaration order and let the
            # DB complain loudly rather than pretending the order is safe.
            ordered.extend(m for m in models if m not in seen)
            break
    return ordered


class Command(BaseCommand):
    help = "Copy all business data from the local SQLite file into PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true',
                            help="Actually copy. Without this it is a dry run.")
        parser.add_argument('--keep-users', action='store_true',
                            help="Leave auth users/groups on the target untouched (copy business data only).")

    def handle(self, *args, **options):
        commit = options['yes']
        keep_users = options['keep_users']

        for alias in (SOURCE, TARGET):
            if alias not in connections:
                raise CommandError(
                    f"No '{alias}' database configured. This command is meant to run under "
                    f"DJANGO_ENV=development, where both aliases exist."
                )

        target_vendor = connections[TARGET].vendor
        if target_vendor != 'postgresql':
            raise CommandError(
                f"Target database is '{target_vendor}', not postgresql. "
                f"USE_SQLITE is probably still set — unset it so 'default' points at Postgres."
            )

        self._check_migrations_match()

        models = _ordered_models()
        if keep_users:
            models = [m for m in models if m._meta.app_label != 'auth']

        # ---- survey both sides before touching anything -------------------
        self.stdout.write(self.style.MIGRATE_HEADING("\nPlan (source SQLite -> target PostgreSQL)"))
        plan, total = [], 0
        for model in models:
            src = model.objects.using(SOURCE).count()
            dst = model.objects.using(TARGET).count()
            if src or dst:
                plan.append((model, src, dst))
                total += src
                flag = '' if not dst else '  <- will be REPLACED'
                self.stdout.write(f"  {_label(model):<38} {src:>8} rows   (target has {dst}){flag}")
        self.stdout.write(f"\n  {total:,} rows to copy across {len(plan)} tables.")

        if not commit:
            self.stdout.write(self.style.WARNING(
                "\nDRY RUN — nothing written. Re-run with --yes to perform the copy.\n"
                "Everything currently in those PostgreSQL tables will be deleted first."
            ))
            return

        # ---- copy ---------------------------------------------------------
        self.stdout.write(self.style.MIGRATE_HEADING("\nCopying"))
        with transaction.atomic(using=TARGET):
            self._clear_target([m for m, _s, d in plan if d])

            for model, src, _dst in plan:
                if not src:
                    continue
                written = 0
                buf = []
                for obj in model.objects.using(SOURCE).all().iterator(chunk_size=BATCH):
                    buf.append(obj)
                    if len(buf) >= BATCH:
                        model.objects.using(TARGET).bulk_create(buf, batch_size=BATCH)
                        written += len(buf)
                        buf = []
                if buf:
                    model.objects.using(TARGET).bulk_create(buf, batch_size=BATCH)
                    written += len(buf)
                self.stdout.write(f"  {_label(model):<38} {written:>8} rows")

            self._reset_sequences([m for m, s, _ in plan if s])

        self._verify(plan)

    # ------------------------------------------------------------------ #
    def _clear_target(self, models):
        """
        Empty the target tables with one raw DELETE each, children first.

        NOT `QuerySet.delete()`. Django's version runs a collector that pulls
        every cascading child row across the wire to decide what to remove —
        against a database one network hop away that turned emptying ~35k rows
        into a transaction that sat idle for a quarter of an hour. We already
        know the safe order, so a plain DELETE per table is one round trip and
        needs no cascade.

        django_admin_log is cleared as well even though it is never copied: it
        carries an FK to auth_user, so leaving rows there would block the user
        table from emptying.
        """
        conn = connections[TARGET]
        tables = [m._meta.db_table for m in reversed(models)]

        admin_log = apps.get_model('admin', 'LogEntry')._meta.db_table
        if admin_log not in tables:
            tables.insert(0, admin_log)

        with conn.cursor() as cursor:
            for table in tables:
                cursor.execute(f'DELETE FROM "{table}"')
        self.stdout.write(f"  cleared {len(tables)} target tables")

    def _check_migrations_match(self):
        """Both databases must be on the same migration state before copying."""
        from django.db.migrations.executor import MigrationExecutor

        pending = {}
        for alias in (SOURCE, TARGET):
            executor = MigrationExecutor(connections[alias])
            targets = executor.loader.graph.leaf_nodes()
            plan = executor.migration_plan(targets)
            if plan:
                pending[alias] = len(plan)
        if pending:
            details = ', '.join(f"{a}: {n} unapplied" for a, n in pending.items())
            raise CommandError(
                f"Migration state differs between the databases ({details}). "
                f"Run migrate on both before copying — rows into a stale schema will not fit."
            )

    def _reset_sequences(self, models):
        """
        Realign Postgres sequences with the ids just inserted.

        Rows carry their original primary keys, and an explicit id does not
        advance the sequence — so without this the very next insert through the
        app would fail on a duplicate key.
        """
        conn = connections[TARGET]
        statements = conn.ops.sequence_reset_sql(no_style(), models)
        if not statements:
            return
        with conn.cursor() as cursor:
            for sql in statements:
                cursor.execute(sql)
        self.stdout.write(self.style.SUCCESS(f"  sequences realigned ({len(statements)} statements)"))

    def _verify(self, plan):
        """Re-count both sides; a copy that silently lost rows is worse than one that failed."""
        self.stdout.write(self.style.MIGRATE_HEADING("\nVerifying"))
        bad = []
        for model, src, _dst in plan:
            if not src:
                continue
            now = model.objects.using(TARGET).count()
            if now != src:
                bad.append((_label(model), src, now))
        if bad:
            for label, src, now in bad:
                self.stdout.write(self.style.ERROR(f"  MISMATCH {label}: expected {src}, found {now}"))
            raise CommandError("Row counts do not match after copy — inspect before using this database.")
        self.stdout.write(self.style.SUCCESS("  every table matches the source row-for-row."))
        self.stdout.write(self.style.SUCCESS("\nDone. PostgreSQL now mirrors the local SQLite file.\n"))
