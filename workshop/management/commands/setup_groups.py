"""
Create the three auth groups this app's RBAC is built on.

WHY THIS EXISTS AT ALL
----------------------
`Owner`, `Office` and `Floor` are ordinary `auth.Group` rows and **no migration
creates them**, so a freshly migrated database has none. `sync_owner_identity`
does `get_or_create(name='Owner')` as a side effect of its own job, which is why
Owner tends to appear on its own — but **Office and Floor are created by nothing
else**, and Control Hub cannot create an Office or Floor login without them.

WHAT THIS COMMAND USED TO DO, AND WHY IT IS RECORDED HERE
---------------------------------------------------------
It created `Workers` and `Admins`: two groups nothing in this codebase reads.
That was left over from an RBAC model this app has not used for a long time, and
it failed in the worst possible direction — GO_LIVE_RUNBOOK.md carried the
checklist line "`setup_groups` created Owner / Office / Floor", and
`manage_create_user` tells anyone who hits a missing role to *"Run
`manage.py setup_groups` to restore the Owner/Office/Floor roles"*.

So the documented remedy and the on-screen remedy both pointed here, this ran,
reported success in green ticks, and changed nothing that mattered. Found on a
rehearsal deployment on 2026-09-06, on an empty database — which is the only
place it can be found, because every database that already has the groups hides
it.

Safe to run repeatedly: `get_or_create` touches nothing that already exists, and
this never removes a group or moves anybody between them.
"""
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

# The one list. `decorators.py` gates every view on these three names, and
# `manage_create_user` resolves a new login's role against them.
REQUIRED_GROUPS = ('Owner', 'Office', 'Floor')


class Command(BaseCommand):
    help = "Create the Owner / Office / Floor auth groups (safe to re-run)."

    def handle(self, *args, **kwargs):
        created_any = False

        for name in REQUIRED_GROUPS:
            _, created = Group.objects.get_or_create(name=name)
            if created:
                created_any = True
                self.stdout.write(self.style.SUCCESS('  created  %s' % name))
            else:
                self.stdout.write('  exists   %s' % name)

        if created_any:
            self.stdout.write(self.style.SUCCESS('\nRoles are in place.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                '\nNothing to do — all three roles already existed.'))

        # Deliberately NOT "go to /admin/". Owner accounts are is_staff=False by
        # design, so the Django admin is unenterable by anybody — see CLAUDE.md.
        # Logins are created in the app, by an owner.
        self.stdout.write(
            '\nNext: sign in as an owner and create staff logins at '
            'Manage -> Control Hub -> Accounts.'
        )
