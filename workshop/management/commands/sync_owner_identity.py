"""
Management command: sync_owner_identity
---------------------------------------
Moves owner identity out of .env and into the database.

Historically the owners were identified by OWNER_1_USERNAME / OWNER_1_MOBILE
(and OWNER_2_*) read from .env on every request. That capped the system at
exactly two owners, needed a redeploy to change, and — the part that actually
bites — left the `Owner` auth group **empty**: both accounts cleared RBAC through
`is_superuser` instead, so anything asking "who are the owners?" by group got
back nothing. Notifications addressed to Owners would have reached no one.

This command reconciles the .env values into the DB so the database becomes the
single source of truth:
  - each OWNER_n_USERNAME is added to the `Owner` group
  - `is_staff` is cleared, closing Django admin (see below)
  - a UserProfile is created/updated carrying OWNER_n_MOBILE
  - email state is audited (reported, never written — see below)

**Owners get no Django admin access.** `is_staff` is what gates /admin/, and the
admin site bypasses every protection the app enforces: deletions there write no
DeletionLog, the Financial Lock does not apply, and archive-don't-delete is not
honoured. Clearing `is_staff` removes exactly nothing from an owner's in-app
authority — `is_superuser` stays True, so every decorator and `has_group` check
still passes. If you genuinely need admin, create a separate account with
`createsuperuser` and delete it afterwards; because it is not an OWNER_n entry,
this command will leave it alone.

Emails are deliberately NOT sourced from .env. They are the delivery address for
password-reset codes and are set per account in Django admin
(/admin/auth/user/), which is why changing one later needs no deploy. This
command only reports whether each owner has a usable, unambiguous address.

Superuser status is left alone. Adding the group is purely additive: these
accounts already passed every check via is_superuser, so nothing about today's
access changes — the group simply makes owners *findable* by query.

Usage:
    python manage.py sync_owner_identity          # dry run — prints the plan
    python manage.py sync_owner_identity --yes    # apply it
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.core.validators import validate_email
from django.contrib.auth.models import User, Group
from django.db import transaction
from decouple import config

from workshop.models import UserProfile

OWNER_GROUP = 'Owner'
MAX_OWNER_SLOTS = 10


class Command(BaseCommand):
    help = ("Migrate owner identity (group membership + mobile) from .env into the database. "
            "Use --yes to apply; without it the command only prints the plan.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', action='store_true',
            help="Actually write the changes. Without this flag nothing is modified.",
        )

    # ------------------------------------------------------------------
    # .env reading
    # ------------------------------------------------------------------
    def _env_owners(self):
        """Read OWNER_n_USERNAME / OWNER_n_MOBILE pairs until a slot is empty."""
        owners = []
        for i in range(1, MAX_OWNER_SLOTS + 1):
            username = config(f'OWNER_{i}_USERNAME', default='').strip(' =')
            if not username:
                continue
            mobile = config(f'OWNER_{i}_MOBILE', default='').strip(' =')
            owners.append((i, username, mobile))
        return owners

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        apply_changes = options['yes']
        env_owners = self._env_owners()

        if not env_owners:
            self.stdout.write(self.style.ERROR(
                "No OWNER_n_USERNAME entries found in .env — nothing to migrate."
            ))
            return

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Owner identity: .env -> database"))
        self.stdout.write("")

        actions = []      # callables applied only under --yes
        problems = []     # blocking issues
        warnings = []     # non-blocking, human judgement needed

        group, group_created = Group.objects.get_or_create(name=OWNER_GROUP)
        if group_created:
            # get_or_create already wrote it; harmless either way since the group
            # carries no permissions of its own — the decorators check by name.
            self.stdout.write(f"  created missing '{OWNER_GROUP}' group")

        resolved_users = []

        for slot, username, mobile in env_owners:
            self.stdout.write(self.style.HTTP_INFO(f"OWNER_{slot}: {username}"))

            user = User.objects.filter(username=username).first()
            if user is None:
                problems.append(f"OWNER_{slot}: no User with username '{username}'")
                self.stdout.write(self.style.ERROR("    account not found - skipped"))
                self.stdout.write("")
                continue

            resolved_users.append(user)

            # --- group membership -------------------------------------
            if user.groups.filter(name=OWNER_GROUP).exists():
                self.stdout.write(f"    group      : already in '{OWNER_GROUP}'")
            else:
                self.stdout.write(self.style.WARNING(
                    f"    group      : NOT in '{OWNER_GROUP}' -> will add"
                ))
                actions.append(lambda u=user: u.groups.add(group))

            # --- admin site access ------------------------------------
            if user.is_staff:
                self.stdout.write(self.style.WARNING(
                    "    admin site : is_staff=True -> will revoke /admin/ access"
                ))
                actions.append(
                    lambda u=user: User.objects.filter(pk=u.pk).update(is_staff=False)
                )
            else:
                self.stdout.write("    admin site : closed (is_staff=False)")

            # --- mobile number ----------------------------------------
            profile = UserProfile.objects.filter(user=user).first()
            target_mobile = mobile or None

            if target_mobile is None:
                self.stdout.write("    mobile     : none set in .env - skipped")
            else:
                clash = UserProfile.objects.filter(
                    mobile_number=target_mobile
                ).exclude(user=user).first()
                if clash:
                    problems.append(
                        f"OWNER_{slot}: mobile {target_mobile} already belongs to "
                        f"'{clash.user.username}'"
                    )
                    self.stdout.write(self.style.ERROR(
                        f"    mobile     : {target_mobile} CLASHES with "
                        f"'{clash.user.username}' - skipped"
                    ))
                elif profile is None:
                    self.stdout.write(self.style.WARNING(
                        f"    mobile     : no profile -> will create with {target_mobile}"
                    ))
                    actions.append(
                        lambda u=user, m=target_mobile: UserProfile.objects.create(
                            user=u, mobile_number=m
                        )
                    )
                elif profile.mobile_number != target_mobile:
                    self.stdout.write(self.style.WARNING(
                        f"    mobile     : {profile.mobile_number!r} -> {target_mobile!r}"
                    ))
                    actions.append(
                        lambda p=profile, m=target_mobile: UserProfile.objects.filter(
                            pk=p.pk
                        ).update(mobile_number=m)
                    )
                else:
                    self.stdout.write(f"    mobile     : {target_mobile} (already correct)")

            # --- email audit (report only) ----------------------------
            self._audit_email(user, warnings)
            self.stdout.write("")

        self._report_extra_owners(resolved_users, warnings)
        self._summarise(actions, problems, warnings, apply_changes)

    # ------------------------------------------------------------------
    def _audit_email(self, user, warnings):
        """Report the account's reset-code destination. Never writes."""
        email = (user.email or '').strip()
        if not email:
            warnings.append(f"{user.username}: no email set - cannot receive a reset code")
            self.stdout.write(self.style.WARNING("    email      : NOT SET - set it in /admin/"))
            return

        try:
            validate_email(email)
        except ValidationError:
            warnings.append(f"{user.username}: email {email!r} is not a valid address")
            self.stdout.write(self.style.ERROR(f"    email      : {email} - INVALID FORMAT"))
            return

        duplicate = User.objects.filter(email__iexact=email).exclude(pk=user.pk).first()
        if duplicate:
            warnings.append(
                f"{user.username}: email {email} is shared with '{duplicate.username}'"
            )
            self.stdout.write(self.style.ERROR(
                f"    email      : {email} - SHARED with '{duplicate.username}'"
            ))
            return

        self.stdout.write(f"    email      : {email}")

    # ------------------------------------------------------------------
    def _report_extra_owners(self, resolved_users, warnings):
        """Flag accounts holding owner-level access that .env doesn't know about."""
        resolved_ids = {u.pk for u in resolved_users}
        extras = User.objects.filter(
            is_superuser=True
        ).exclude(pk__in=resolved_ids).order_by('username')

        if not extras:
            return

        self.stdout.write(self.style.WARNING("Superusers not listed in .env:"))
        for u in extras:
            in_group = u.groups.filter(name=OWNER_GROUP).exists()
            self.stdout.write(
                f"    {u.username} (in '{OWNER_GROUP}' group: {'yes' if in_group else 'no'})"
            )
            warnings.append(
                f"{u.username}: superuser but not an OWNER_n entry - review whether "
                f"this account should exist"
            )
        self.stdout.write("")

    # ------------------------------------------------------------------
    def _summarise(self, actions, problems, warnings, apply_changes):
        if problems:
            self.stdout.write(self.style.ERROR("Problems (must be fixed by hand):"))
            for p in problems:
                self.stdout.write(self.style.ERROR(f"    - {p}"))
            self.stdout.write("")

        if warnings:
            self.stdout.write(self.style.WARNING("Needs a human decision:"))
            for w in warnings:
                self.stdout.write(self.style.WARNING(f"    - {w}"))
            self.stdout.write("")

        if not actions:
            self.stdout.write(self.style.SUCCESS("Nothing to change - database already matches .env."))
            return

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN - {len(actions)} change(s) planned, nothing written.\n"
                f"Re-run with --yes to apply."
            ))
            return

        with transaction.atomic():
            for action in actions:
                action()

        self.stdout.write(self.style.SUCCESS(f"Applied {len(actions)} change(s)."))
