"""
Management command: purge_old_photos
-------------------------------------
Delete photographs older than a retention window.

WHY A YEAR
----------
The owner's rule, from the workshop's own experience: complaints about a job
stop after about twelve months. The arithmetic works out — roughly 1.8 GB of
photos go in each year, so once this runs, roughly 1.8 GB goes out each year and
the bucket settles at about 2 GB. That sits inside Cloudflare R2's free 10 GB
indefinitely, which is what makes the storage cost stable for the life of the
business rather than a bill that grows.

THE ONE EXCEPTION, AND IT MATTERS
---------------------------------
Cards still `PENDING` or `PARTIAL` are SKIPPED, whatever their age. A year-old
bill that has not been paid is the single case where "no complaints after a
year" is false by construction — an unpaid bill *is* an open argument, and the
photos of that car are the evidence in it. Deleting those on a timer would
destroy exactly the ones worth keeping.

Age is measured from `taken_at`, not from the job card's date, so a photo added
late to an old card still gets its own full year.

DELIBERATELY NOT SCHEDULED BY DEFAULT
-------------------------------------
Run it from a Railway cron once the workshop is settled, or by hand. It is
time-based and idempotent, so it is safe to run twice and safe to skip for
months. It writes no `DeletionLog` rows: this is housekeeping, and
`DeletionLog.record()` raises a CRITICAL push to both owners — several hundred
of those a month is precisely how a critical alert stops being read.

The blob is not deleted here either. Rows go now, objects are queued for
`sweep_photo_blobs`, for the same reason the interactive delete does it: a
network call has no business in a loop that is deleting database rows.

Usage:
    python manage.py purge_old_photos                        # dry run, 365 days
    python manage.py purge_old_photos --yes                  # actually delete
    python manage.py purge_old_photos --older-than 730 --yes # a two-year window
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from workshop.models import JobCardPhoto

# Payment states that mean money is still owed, and therefore that the job may
# still be argued about. Mirrors the states `pending_payments_list` shows.
UNSETTLED = ('PENDING', 'PARTIAL')


class Command(BaseCommand):
    help = "Delete photos past the retention window. Use --yes to confirm."

    def add_arguments(self, parser):
        parser.add_argument(
            '--older-than', type=int, default=365, metavar='DAYS',
            help='Retention window in days (default: 365).',
        )
        parser.add_argument(
            '--yes', action='store_true',
            help='Actually delete. Without this the command only reports.',
        )

    def handle(self, *args, **options):
        days = options['older_than']
        if days < 1:
            self.stderr.write(self.style.ERROR('--older-than must be at least 1 day.'))
            return

        cutoff = timezone.now() - timedelta(days=days)
        old = JobCardPhoto.objects.filter(taken_at__lt=cutoff)

        # A photo is protected when the card it belongs to still owes money —
        # whether it is a car photo (job_card set) or a part photo, which reaches
        # its card through the spare.
        protected = old.filter(job_card__payment_status__in=UNSETTLED) \
            | old.filter(spare__job_card__payment_status__in=UNSETTLED)
        protected_ids = set(protected.values_list('pk', flat=True))

        doomed = old.exclude(pk__in=protected_ids)
        count = doomed.count()

        self.stdout.write(
            f"Retention window: {days} days (photos taken before "
            f"{timezone.localtime(cutoff).strftime('%d %b %Y')})."
        )
        self.stdout.write(f"  {count:>7,}  photo(s) past the window")
        if protected_ids:
            self.stdout.write(self.style.WARNING(
                f"  {len(protected_ids):>7,}  KEPT — their bill is still unpaid, so the job "
                f"may still be in dispute"
            ))

        if not count:
            self.stdout.write(self.style.SUCCESS('\nNothing to delete.'))
            return

        if not options['yes']:
            self.stdout.write(self.style.WARNING(
                '\nDry run — nothing deleted. Re-run with --yes to actually delete.'
            ))
            return

        with transaction.atomic():
            # The objects are queued by the post_delete signal on JobCardPhoto,
            # in this same transaction. Nothing to do here but delete.
            doomed.delete()

        self.stdout.write(self.style.SUCCESS(f'\n[DONE] {count:,} photo(s) deleted.'))
        self.stdout.write(
            'Their objects are queued for removal from storage — '
            'run `manage.py sweep_photo_blobs --yes` to collect them.'
        )
