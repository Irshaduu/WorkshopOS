"""
Management command: set_owner_email
-----------------------------------
Sets the address an account's password-reset code is delivered to.

This exists instead of editing the field in /admin/ for two reasons. Owners
deliberately hold no admin access (`sync_owner_identity` clears `is_staff`), so
the admin form is not reachable by the people who own these accounts. And the
admin form performs none of the checks that matter for this particular field:
`User.email` carries no uniqueness constraint in Django, so it will happily
accept an address already in use — which would make login-by-email ambiguous and
send one person's reset code toward another's account.

Changing this address redirects the password-reset path, so a typo here hands a
reset route to a stranger. That is why the command previews the change and does
nothing until `--yes`.

Usage:
    python manage.py set_owner_email Sahad owner@example.com          # preview
    python manage.py set_owner_email Sahad owner@example.com --yes    # apply
"""

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

OWNER_GROUP = 'Owner'


class Command(BaseCommand):
    help = ("Set the password-reset email for an account. "
            "Use --yes to apply; without it the command only previews.")

    def add_arguments(self, parser):
        parser.add_argument('username', help="Account whose email is being set.")
        parser.add_argument('email', help="New delivery address for reset codes.")
        parser.add_argument(
            '--yes', action='store_true',
            help="Actually write the change. Without this flag nothing is modified.",
        )

    def handle(self, *args, **options):
        username = options['username'].strip()
        # Addresses are stored lower-cased so that login-by-email can match on a
        # plain lookup and the duplicate check below cannot be defeated by case.
        new_email = options['email'].strip().lower()

        user = User.objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No account with username '{username}'.")

        try:
            validate_email(new_email)
        except ValidationError:
            raise CommandError(f"'{new_email}' is not a valid email address.")

        clash = User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).first()
        if clash:
            raise CommandError(
                f"'{new_email}' is already the address for '{clash.username}'. "
                f"Two accounts sharing an address would make login-by-email ambiguous."
            )

        old_email = user.email or '(not set)'

        if not user.groups.filter(name=OWNER_GROUP).exists() and not user.is_superuser:
            self.stdout.write(self.style.WARNING(
                f"Note: '{username}' is not an Owner. Only owner accounts use the "
                f"password-reset flow, so this address will not be used."
            ))

        self.stdout.write("")
        self.stdout.write(f"  account : {username}")
        self.stdout.write(f"  from    : {old_email}")
        self.stdout.write(f"  to      : {new_email}")
        self.stdout.write("")

        if old_email == new_email:
            self.stdout.write(self.style.SUCCESS("Already set to this address - nothing to do."))
            return

        if not options['yes']:
            self.stdout.write(self.style.WARNING(
                "DRY RUN - nothing written. Re-run with --yes to apply."
            ))
            return

        user.email = new_email
        user.save(update_fields=['email'])

        self.stdout.write(self.style.SUCCESS(f"Updated. Reset codes for '{username}' now go to {new_email}."))
        self.stdout.write(
            "Send one real reset to confirm delivery - check the spam folder on the "
            "first send, a new sender has no reputation with the inbox."
        )
