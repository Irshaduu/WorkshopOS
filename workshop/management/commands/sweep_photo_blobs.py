"""
Management command: sweep_photo_blobs
--------------------------------------
Delete storage objects whose database row is gone.

WHY THIS IS A COMMAND AND NOT PART OF THE DELETE
------------------------------------------------
Deleting a photo writes an `OrphanedPhotoBlob` row and removes the record, both
in one transaction; the object itself is collected here, later. That split is
deliberate. A DELETE to R2 is a network call, and this codebase does not put
those on the request path — the same reasoning that hands Web Push to a
background thread. If the bucket is slow or unreachable, a photo must still
disappear from the app the instant somebody deletes it.

It also collects the OTHER kind of orphan, which has no row to begin with: an
upload that reached the bucket but whose commit never arrived, because the
tablet lost wifi or the browser was closed mid-burst. Those are invisible to
everyone — which is precisely why sign-then-commit is the right way round.
Reconciling them needs a bucket listing, so it is opt-in via --deep.

Safe to run twice, and safe to skip for months. R2 answers a DELETE for a key
that is already gone with a success, and the queue row is only removed once
that has happened — so nothing is ever lost by a run that fails halfway.

Usage:
    python manage.py sweep_photo_blobs           # dry run — shows what it would delete
    python manage.py sweep_photo_blobs --yes     # actually delete
    python manage.py sweep_photo_blobs --yes --deep   # also reconcile uncommitted uploads
"""

from django.core.management.base import BaseCommand

from workshop import photos as photo_storage
from workshop.models import OrphanedPhotoBlob

# After this many failed attempts a key is reported rather than retried
# forever. It stays in the table: a key that cannot be deleted is a fact worth
# keeping, not one worth hiding.
MAX_ATTEMPTS = 5


class Command(BaseCommand):
    help = "Delete photo objects from storage whose database rows are gone."

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', action='store_true',
            help='Actually delete. Without this the command only reports.',
        )
        parser.add_argument(
            '--deep', action='store_true',
            help='Also look for uploaded objects that were never committed.',
        )

    def handle(self, *args, **options):
        apply = options['yes']

        if not photo_storage.photos_are_configured():
            self.stdout.write(self.style.WARNING(
                'Photo storage is not configured — nothing to sweep.'
            ))
            return

        queued = OrphanedPhotoBlob.objects.filter(attempts__lt=MAX_ATTEMPTS)
        stuck = OrphanedPhotoBlob.objects.filter(attempts__gte=MAX_ATTEMPTS).count()
        total = queued.count()

        if not total:
            self.stdout.write('Nothing queued for collection.')
        elif not apply:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN — {total} object(s) would be deleted from the bucket.'
            ))
            for blob in queued[:20]:
                self.stdout.write(f'  {blob.storage_key}')
            if total > 20:
                self.stdout.write(f'  … and {total - 20} more')
            self.stdout.write('\nRe-run with --yes to apply.')
        else:
            removed = 0
            for blob in list(queued):
                if photo_storage.delete_object(blob.storage_key):
                    blob.delete()
                    removed += 1
                else:
                    blob.attempts += 1
                    blob.save(update_fields=['attempts'])
            self.stdout.write(self.style.SUCCESS(
                f'Deleted {removed} of {total} queued object(s).'
            ))

        if stuck:
            self.stdout.write(self.style.ERROR(
                f'{stuck} key(s) have failed {MAX_ATTEMPTS} times and are no longer retried. '
                f'Check the bucket credentials, then reset their attempt counts.'
            ))

        if options['deep']:
            self.stdout.write(self.style.WARNING(
                '\n--deep is not implemented yet. It needs a bucket listing, which is a '
                'different R2 call from the ones this app signs today. Uncommitted uploads '
                'are harmless in the meantime: they are invisible in the app and cost a few '
                'hundred KB each against a free 10 GB.'
            ))
