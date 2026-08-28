"""
Job card photos — the rules, not the camera.

Nothing here executes JavaScript, so the camera, the upload queue and the
gallery are out of reach by construction (the queue and the gallery arithmetic
are covered by `photos-core.test.js` under `node --test`; the camera is covered
by a hand session on the tablet, and CLAUDE.md says so).

What IS testable is everything that matters for correctness: the signature, the
limits, the settled-card freeze, who may reach the endpoints, and — the reason
the owner asked for it — that a workshop with this whole section broken or
switched off carries on exactly as before.
"""

import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from workshop import photos as photo_storage
from workshop.models import (
    JobCard, JobCardPhoto, JobCardSpareItem, OrphanedPhotoBlob, SpareShop,
)

R2_SETTINGS = dict(
    PHOTO_S3_ACCOUNT_ID='acct',
    PHOTO_S3_ACCESS_KEY_ID='AKIAIOSFODNN7EXAMPLE',
    PHOTO_S3_SECRET_ACCESS_KEY='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    PHOTO_S3_BUCKET='photos',
    PHOTO_S3_PREFIX='',
    PHOTO_S3_ENDPOINT='',
    PHOTO_S3_REGION='auto',
    PHOTO_S3_PATH_PREFIX='',
)

# "No storage at all". DEBUG is already False under the test runner, but the
# local fallback is pinned off explicitly so this states the intent rather than
# leaning on a setting somebody could flip.
NO_STORAGE = dict(
    PHOTO_S3_ACCOUNT_ID='', PHOTO_S3_ACCESS_KEY_ID='', PHOTO_S3_SECRET_ACCESS_KEY='',
    PHOTO_S3_BUCKET='', PHOTO_S3_ENDPOINT='', PHOTO_LOCAL_FALLBACK=False,
)


class SigningTests(TestCase):
    """
    The signature is arithmetic, so it can be pinned to a published answer.

    This is the whole reason `presign()` takes every input as an argument and
    reads no settings: AWS's own worked example uses virtual-host addressing on
    s3.amazonaws.com, which is unreachable if the R2 host is baked in. Getting
    this wrong produces uploads that 403 with an opaque browser error, and
    there is no other way to catch it without a live bucket.
    """

    def test_it_matches_the_published_aws_example(self):
        # AWS "Signature Calculation: Transfer Payload in a Single Chunk" —
        # presigned GET of examplebucket/test.txt, valid for 24 hours.
        url = photo_storage.presign(
            'GET',
            'examplebucket.s3.amazonaws.com',
            '/test.txt',
            'AKIAIOSFODNN7EXAMPLE',
            'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
            'us-east-1',
            's3',
            86400,
            now=datetime(2013, 5, 24, tzinfo=dt_timezone.utc),
        )
        self.assertIn(
            'X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404',
            url,
        )

    def test_the_credential_scope_is_percent_encoded(self):
        """
        The slashes in the credential must arrive as %2F.

        Left raw, the signature still computes and the URL still looks
        plausible — it simply fails at the bucket, which is the worst kind of
        bug to find on a shop floor.
        """
        url = photo_storage.presign(
            'PUT', 'h', '/b/k', 'AK', 'SK', 'auto', 's3', 300,
            now=datetime(2026, 8, 18, tzinfo=dt_timezone.utc),
        )
        self.assertIn('X-Amz-Credential=AK%2F20260818%2Fauto%2Fs3%2Faws4_request', url)
        self.assertNotIn('X-Amz-Credential=AK/', url)

    def test_query_parameters_are_sorted(self):
        url = photo_storage.presign(
            'GET', 'h', '/b/k', 'AK', 'SK', 'auto', 's3', 300,
            extra_query={'response-content-type': 'image/jpeg'},
            now=datetime(2026, 8, 18, tzinfo=dt_timezone.utc),
        )
        query = url.split('?', 1)[1].rsplit('&X-Amz-Signature=', 1)[0]
        names = [pair.split('=')[0] for pair in query.split('&')]
        self.assertEqual(names, sorted(names))

    @override_settings(**R2_SETTINGS)
    def test_a_view_url_forces_an_image_content_type(self):
        """
        Without this the browser may decide to download rather than display,
        which turns the gallery into a file save on some phones.
        """
        url = photo_storage.view_url('abc.jpg')
        self.assertIn('response-content-type=image%2Fjpeg', url)


class ConfigurationTests(TestCase):
    @override_settings(**NO_STORAGE)
    def test_missing_credentials_means_switched_off(self):
        self.assertFalse(photo_storage.photos_are_configured())

    @override_settings(**R2_SETTINGS)
    def test_full_credentials_means_switched_on(self):
        self.assertTrue(photo_storage.photos_are_configured())

    @override_settings(**dict(R2_SETTINGS, PHOTO_S3_BUCKET='', PHOTO_LOCAL_FALLBACK=False))
    def test_a_partial_configuration_is_no_configuration(self):
        """Half-configured must read as OFF, never as on-and-broken."""
        self.assertFalse(photo_storage.photos_are_configured())


LOCAL_ONLY = dict(
    PHOTO_S3_ACCOUNT_ID='', PHOTO_S3_ACCESS_KEY_ID='', PHOTO_S3_SECRET_ACCESS_KEY='',
    PHOTO_S3_BUCKET='', PHOTO_S3_ENDPOINT='', PHOTO_LOCAL_FALLBACK=True, DEBUG=True,
)


class WhichBackendTests(TestCase):
    """
    Cloudflare R2 wants a payment card even for its free tier, so until the
    workshop's own accounts exist the section has to be demonstrable without
    one. That is what the local backend is for — and the rule that keeps it
    honest is that it can only ever appear on a DEBUG server.
    """

    @override_settings(**R2_SETTINGS)
    def test_credentials_win_whenever_they_are_present(self):
        self.assertEqual(photo_storage.storage_backend(), photo_storage.BACKEND_S3)

    @override_settings(**dict(R2_SETTINGS, DEBUG=True))
    def test_credentials_still_win_on_a_debug_server(self):
        """A developer with real credentials is testing the real path."""
        self.assertEqual(photo_storage.storage_backend(), photo_storage.BACKEND_S3)

    @override_settings(**LOCAL_ONLY)
    def test_a_debug_server_with_no_bucket_uses_its_own_disk(self):
        self.assertEqual(photo_storage.storage_backend(), photo_storage.BACKEND_LOCAL)

    @override_settings(**NO_STORAGE)
    def test_production_with_no_bucket_is_OFF_not_local(self):
        """
        The one that matters. Railway's container filesystem is wiped on every
        deploy, so a production server falling back to local disk would accept
        photographs all week and lose them on the next push — silently, and
        exactly when they are wanted. Off is the honest answer.
        """
        self.assertEqual(photo_storage.storage_backend(), photo_storage.BACKEND_OFF)

    @override_settings(
        PHOTO_S3_ACCOUNT_ID='', PHOTO_S3_ACCESS_KEY_ID='k', PHOTO_S3_SECRET_ACCESS_KEY='s',
        PHOTO_S3_BUCKET='b', PHOTO_S3_ENDPOINT='', PHOTO_LOCAL_FALLBACK=False,
    )
    def test_credentials_with_no_host_are_not_credentials(self):
        """Keys but no endpoint and no account id is half-configured, so OFF."""
        self.assertEqual(photo_storage.storage_backend(), photo_storage.BACKEND_OFF)

    @override_settings(
        PHOTO_S3_ENDPOINT='abcdef.supabase.co',
        PHOTO_S3_PATH_PREFIX='storage/v1/s3',
        PHOTO_S3_REGION='ap-south-1',
        PHOTO_S3_ACCESS_KEY_ID='k', PHOTO_S3_SECRET_ACCESS_KEY='s',
        PHOTO_S3_BUCKET='photos', PHOTO_S3_PREFIX='',
    )
    def test_a_supabase_endpoint_needs_no_code_change(self):
        """
        Supabase Storage speaks S3 and asks for no payment card, which makes it
        the fallback if the card is still unavailable at go-live. It differs
        from R2 in three settings and nothing else — if this breaks, the
        provider has stopped being a configuration choice.
        """
        url = photo_storage.upload_url('abc.jpg')
        self.assertTrue(url.startswith('https://abcdef.supabase.co/storage/v1/s3/photos/abc.jpg?'))
        self.assertIn('%2Fap-south-1%2Fs3%2Faws4_request', url)


@override_settings(**LOCAL_ONLY)
class TheLocalBackendTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.media, True)
        self.key = f'{uuid.uuid4()}.jpg'

    def _signed(self, view, key=None, **extra):
        key = key or self.key
        expiry = int(datetime.now(dt_timezone.utc).timestamp()) + 300
        params = {'k': key, 'e': expiry, 't': photo_storage.local_token(key, expiry), **extra}
        return reverse(view) + '?' + urlencode(params)

    def test_an_upload_url_points_at_this_server(self):
        url = photo_storage.upload_url(self.key)
        self.assertTrue(url.startswith(reverse('photo_blob_put')))
        self.assertNotIn('cloudflarestorage', url)

    def test_a_signed_put_stores_the_bytes_and_a_signed_get_returns_them(self):
        put = self.client.put(self._signed('photo_blob_put'), data=b'JPEGDATA',
                              content_type='image/jpeg')
        self.assertEqual(put.status_code, 200)

        got = self.client.get(self._signed('photo_blob_get', n='car.jpg'))
        self.assertEqual(got.status_code, 200)
        self.assertEqual(b''.join(got.streaming_content), b'JPEGDATA')
        self.assertEqual(got['Content-Type'], 'image/jpeg')
        self.assertIn('car.jpg', got['Content-Disposition'])

    def test_an_unsigned_put_is_refused(self):
        url = reverse('photo_blob_put') + '?' + urlencode({'k': self.key, 'e': 9999999999, 't': 'x' * 32})
        self.assertEqual(
            self.client.put(url, data=b'X', content_type='image/jpeg').status_code, 403
        )

    def test_an_expired_link_is_refused(self):
        expiry = int(datetime.now(dt_timezone.utc).timestamp()) - 5
        url = reverse('photo_blob_get') + '?' + urlencode(
            {'k': self.key, 'e': expiry, 't': photo_storage.local_token(self.key, expiry)}
        )
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_key_that_is_not_one_of_ours_is_refused(self):
        """
        Every key this app mints is `<uuid>.jpg`. Anything else is crafted, and
        is refused rather than sanitised — there is no caller that needs `..`.
        """
        for bad in ('../../settings.py', 'x.jpg', '/etc/passwd', f'{uuid.uuid4()}.png'):
            with self.subTest(key=bad):
                self.assertIsNone(photo_storage.local_path(bad))

    def test_an_oversized_body_is_refused(self):
        big = b'x' * (settings.PHOTO_MAX_BYTES + 1)
        response = self.client.put(self._signed('photo_blob_put'), data=big,
                                   content_type='image/jpeg')
        self.assertEqual(response.status_code, 400)

    def test_deleting_removes_the_file(self):
        self.client.put(self._signed('photo_blob_put'), data=b'JPEGDATA', content_type='image/jpeg')
        path = photo_storage.local_path(self.key)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(photo_storage.delete_object(self.key))
        self.assertFalse(os.path.exists(path))

    def test_deleting_something_already_gone_is_a_success(self):
        """The sweep must be safe to re-run."""
        self.assertTrue(photo_storage.delete_object(self.key))

    @override_settings(**R2_SETTINGS)
    def test_the_blob_endpoints_vanish_when_a_bucket_is_configured(self):
        """
        They are a stand-in for a bucket. With a real one they must not answer
        at all, or production would carry two ways in and only one of them
        checked by the tests that matter.
        """
        self.assertEqual(self.client.get(self._signed('photo_blob_get')).status_code, 404)
        self.assertEqual(
            self.client.put(self._signed('photo_blob_put'), data=b'X',
                            content_type='image/jpeg').status_code,
            404,
        )


class PhotoTestBase(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.floor = User.objects.create_user('floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))
        self.office = User.objects.create_user('office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))

        today = timezone.localdate()
        self.card = JobCard.objects.create(
            registration_number='KL01AA1111',
            brand_name='Audi',
            model_name='A4',
            payment_status='PENDING',
            admitted_date=today,
        )
        self.settled = JobCard.objects.create(
            registration_number='KL01BB2222',
            brand_name='Audi',
            model_name='A6',
            payment_status='PAID',
            total_bill_amount=1000,
            received_amount=1000,
            admitted_date=today,
        )

    def post_json(self, name, payload):
        return self.client.post(
            reverse(name), data=json.dumps(payload), content_type='application/json'
        )

    @staticmethod
    def all_boxes(html):
        """Every photo box on a page, as dicts of their attributes."""
        return [
            dict(re.findall(r'([\w-]+)="([^"]*)"', match.group(1)))
            for match in re.finditer(r'<div class="photo-box[^"]*"(.*?)>', html, re.S)
        ]

    def spare_box(self, html, spare):
        """
        One spare's box, as a dict of its attributes.

        Two things this exists to avoid, both of which produced convincing false
        failures while these tests were being written.

        The attributes render one per LINE, so any assertion putting two of them
        in one string with a single space between never matches — and the
        failure reads as the box being absent when it is right there.

        And it matches subject AND id together, never the id alone: job cards
        and spares are numbered from separate sequences, so on a fresh database
        the car is pk 1 and the first spare is pk 1 too. Searching for
        `data-subject-id="1"` finds the CAR's box, and every assertion after
        that describes the wrong control.
        """
        for attrs in self.all_boxes(html):
            if attrs.get('data-subject') == 'spare' and attrs.get('data-subject-id') == str(spare.pk):
                return attrs
        self.fail(f'no photo box rendered for spare {spare.pk} ({spare.spare_part_name})')


@override_settings(**R2_SETTINGS)
class SignAndCommitTests(PhotoTestBase):
    def test_a_photo_is_only_recorded_once_its_bytes_are_stored(self):
        """
        Sign writes nothing. That is what stops a browser closing mid-upload
        from leaving a row pointing at an object that does not exist — a broken
        image in the gallery that nobody can explain or remove.
        """
        self.client.force_login(self.floor)
        response = self.post_json('photo_sign', {'subject': 'card', 'id': self.card.pk, 'bytes': 1000})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(JobCardPhoto.objects.count(), 0)

        photo_id = response.json()['photo_id']
        self.post_json('photo_commit', {
            'subject': 'card', 'id': self.card.pk, 'photo_id': photo_id, 'bytes': 1000,
        })
        self.assertEqual(JobCardPhoto.objects.count(), 1)
        self.assertEqual(str(JobCardPhoto.objects.get().id), photo_id)

    def test_the_upload_url_points_at_the_photos_own_key(self):
        self.client.force_login(self.floor)
        data = self.post_json(
            'photo_sign', {'subject': 'card', 'id': self.card.pk, 'bytes': 1000}
        ).json()
        self.assertIn(f"/photos/{data['photo_id']}.jpg?", data['upload_url'])

    def test_committing_twice_records_one_photo(self):
        """A retried commit after a timeout must not double the count."""
        self.client.force_login(self.floor)
        photo_id = str(uuid.uuid4())
        body = {'subject': 'card', 'id': self.card.pk, 'photo_id': photo_id, 'bytes': 10}
        self.post_json('photo_commit', body)
        self.post_json('photo_commit', body)
        self.assertEqual(JobCardPhoto.objects.count(), 1)

    def test_committing_a_photo_id_that_belongs_elsewhere_is_refused(self):
        """
        Idempotency is keyed on the id AND the subject. Keyed on the id alone, a
        commit naming an existing photo would hand back a different car's
        picture and call it success.
        """
        self.client.force_login(self.floor)
        photo_id = str(uuid.uuid4())
        self.post_json('photo_commit', {
            'subject': 'card', 'id': self.card.pk, 'photo_id': photo_id, 'bytes': 10,
        })
        other = JobCard.objects.create(
            registration_number='KL01DD4444', brand_name='B', model_name='M',
            admitted_date=timezone.localdate(),
        )
        response = self.post_json('photo_commit', {
            'subject': 'card', 'id': other.pk, 'photo_id': photo_id, 'bytes': 10,
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(JobCardPhoto.objects.filter(job_card=other).count(), 0)

    def test_a_malformed_photo_id_is_refused_not_a_500(self):
        self.client.force_login(self.floor)
        response = self.post_json('photo_commit', {
            'subject': 'card', 'id': self.card.pk, 'photo_id': 'not-a-uuid', 'bytes': 10,
        })
        self.assertEqual(response.status_code, 400)

    def test_a_crafted_subject_id_is_a_404_not_a_500(self):
        """
        A hand-edited `id=abc` reaches the field's get_prep_value and raises.
        Same reasoning as parsing a custom date range before the ORM sees it.
        """
        self.client.force_login(self.floor)
        response = self.post_json('photo_sign', {'subject': 'card', 'id': 'abc', 'bytes': 10})
        self.assertEqual(response.status_code, 404)


@override_settings(**R2_SETTINGS)
class LimitTests(PhotoTestBase):
    def _commit(self, card):
        return self.post_json('photo_commit', {
            'subject': 'card', 'id': card.pk, 'photo_id': str(uuid.uuid4()), 'bytes': 10,
        })

    @override_settings(PHOTO_LIMIT_CAR=3)
    def test_the_limit_is_enforced_at_commit_not_only_at_sign(self):
        """
        Sign and commit are two requests and a burst has several in flight, so
        the check that counts is the one inside the transaction that writes.
        """
        self.client.force_login(self.floor)
        for _ in range(3):
            self.assertEqual(self._commit(self.card).status_code, 200)
        refused = self._commit(self.card)
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(refused.json()['limit_hit'])
        self.assertEqual(JobCardPhoto.objects.count(), 3)

    @override_settings(PHOTO_LIMIT_CAR=1)
    def test_one_cars_limit_does_not_touch_another(self):
        self.client.force_login(self.floor)
        self.assertEqual(self._commit(self.card).status_code, 200)
        self.assertEqual(self._commit(self.settled).status_code, 403)   # settled, not full
        other = JobCard.objects.create(
            registration_number='KL01CC3333', brand_name='B', model_name='M',
            admitted_date=timezone.localdate(),
        )
        self.assertEqual(self._commit(other).status_code, 200)

    def test_an_oversized_image_is_refused(self):
        self.client.force_login(self.floor)
        response = self.post_json('photo_sign', {
            'subject': 'card', 'id': self.card.pk, 'bytes': 99_000_000,
        })
        self.assertEqual(response.status_code, 400)

    def test_a_zero_byte_image_is_refused(self):
        self.client.force_login(self.floor)
        response = self.post_json('photo_sign', {'subject': 'card', 'id': self.card.pk, 'bytes': 0})
        self.assertEqual(response.status_code, 400)


@override_settings(**R2_SETTINGS)
class TheFreezeFollowsTheMoneyTests(PhotoTestBase):
    """
    A settled card's photos may be looked at and not changed.

    The boundary is deliberately the Financial Lock's own — money and evidence
    stop moving together — and it is enforced on the CARD'S PAYMENT STATUS, not
    on which page the request arrived from. Purchase History carries no
    Financial Lock, so a page-based check would leave that door standing open.
    """

    def test_a_settled_card_takes_no_new_photo(self):
        self.client.force_login(self.office)
        response = self.post_json('photo_sign', {
            'subject': 'card', 'id': self.settled.pk, 'bytes': 1000,
        })
        self.assertEqual(response.status_code, 403)

    def test_a_fleet_settled_card_is_frozen_too(self):
        self.settled.payment_status = 'BULK_PAID'
        self.settled.save()
        self.client.force_login(self.office)
        response = self.post_json('photo_sign', {
            'subject': 'card', 'id': self.settled.pk, 'bytes': 1000,
        })
        self.assertEqual(response.status_code, 403)

    def test_a_settled_cards_photos_can_still_be_LOOKED_at(self):
        """
        The half that is easy to break. These are the photos most worth
        keeping, so freezing must never mean hiding.
        """
        JobCardPhoto.objects.create(job_card=self.settled)
        self.client.force_login(self.office)
        response = self.client.get(
            reverse('photo_list'), {'subject': 'card', 'id': self.settled.pk}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body['photos']), 1)
        self.assertFalse(body['can_edit'])

    def test_a_settled_cards_photo_cannot_be_deleted(self):
        photo = JobCardPhoto.objects.create(job_card=self.settled)
        self.client.force_login(self.office)
        response = self.post_json('photo_delete', {'photo_id': str(photo.id)})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(JobCardPhoto.objects.filter(pk=photo.pk).exists())

    def test_an_open_cards_photo_can_be_deleted_by_floor(self):
        """
        Floor takes the photos, so Floor must be able to remove a blurry one
        without going to find Office — otherwise the feature stops being used.
        """
        photo = JobCardPhoto.objects.create(job_card=self.card)
        self.client.force_login(self.floor)
        response = self.post_json('photo_delete', {'photo_id': str(photo.id)})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(JobCardPhoto.objects.filter(pk=photo.pk).exists())

    def test_deleting_queues_the_object_for_collection(self):
        photo = JobCardPhoto.objects.create(job_card=self.card)
        key = photo.storage_key
        self.client.force_login(self.floor)
        self.post_json('photo_delete', {'photo_id': str(photo.id)})
        self.assertTrue(OrphanedPhotoBlob.objects.filter(storage_key=key).exists())

    def test_a_CASCADE_queues_the_object_too(self):
        """
        The leak this closes. Queueing used to happen in the delete endpoint,
        which covers exactly one of the ways a photo row can vanish — every
        other way is a cascade, and a cascade fires no view. Deleting a spare or
        a job card therefore left its photographs in the bucket for ever, with
        nothing left pointing at them and no record they had existed.

        It lives on a post_delete signal now, so it fires however the row goes.
        """
        shop = SpareShop.objects.create(name='S')
        spare = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Brake Disc',
            source=JobCardSpareItem.SOURCE_SHOP, shop=shop,
        )
        car_key = JobCardPhoto.objects.create(job_card=self.card).storage_key
        part_key = JobCardPhoto.objects.create(spare=spare).storage_key

        spare.delete()                                   # cascades the part photo
        self.assertTrue(OrphanedPhotoBlob.objects.filter(storage_key=part_key).exists())

        self.card.delete()                               # cascades the car photo
        self.assertTrue(OrphanedPhotoBlob.objects.filter(storage_key=car_key).exists())
        self.assertEqual(JobCardPhoto.objects.count(), 0)

    def test_a_bulk_queryset_delete_queues_every_object(self):
        """
        Django skips its fast-delete path for a model with a post_delete
        receiver, which is what makes `purge_business_data` and the retention
        purge safe to leave as plain `.delete()` calls.
        """
        keys = [JobCardPhoto.objects.create(job_card=self.card).storage_key
                for _ in range(3)]
        JobCardPhoto.objects.all().delete()
        for key in keys:
            self.assertTrue(OrphanedPhotoBlob.objects.filter(storage_key=key).exists())


@override_settings(**R2_SETTINGS)
class WhoCanReachThemTests(PhotoTestBase):
    def test_every_endpoint_refuses_a_stranger(self):
        for name, method in [
            ('photo_sign', 'post'), ('photo_commit', 'post'),
            ('photo_list', 'get'), ('photo_delete', 'post'),
        ]:
            with self.subTest(endpoint=name):
                response = getattr(self.client, method)(reverse(name))
                self.assertIn(response.status_code, (302, 403), name)

    def test_floor_may_take_and_see_photos(self):
        """Floor is who walks round the car. Photos are not money."""
        self.client.force_login(self.floor)
        self.assertEqual(
            self.post_json('photo_sign', {'subject': 'card', 'id': self.card.pk, 'bytes': 10}).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('photo_list'), {'subject': 'card', 'id': self.card.pk}).status_code,
            200,
        )


class TheSectionIsCompletelyOptionalTests(PhotoTestBase):
    """
    The owner's own question: if this whole section broke, does the workshop
    still work? It has to be yes by construction, not by luck.
    """

    @override_settings(**NO_STORAGE)
    def test_with_no_storage_the_job_card_form_still_opens(self):
        self.client.force_login(self.office)
        response = self.client.get(reverse('jobcard_edit', args=[self.card.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-photo-box')

    @override_settings(**NO_STORAGE)
    def test_with_no_storage_the_invoice_still_prints(self):
        self.client.force_login(self.office)
        response = self.client.get(reverse('invoice_view', args=[self.card.pk]))
        self.assertEqual(response.status_code, 200)

    @override_settings(**NO_STORAGE)
    def test_with_no_storage_the_endpoints_refuse_politely(self):
        self.client.force_login(self.floor)
        response = self.post_json('photo_sign', {'subject': 'card', 'id': self.card.pk, 'bytes': 10})
        self.assertEqual(response.status_code, 503)

    @override_settings(**R2_SETTINGS)
    def test_a_photo_never_reaches_the_customers_bill(self):
        """
        Same guarantee the internal note has: `invoice.py` and the invoice
        template read named fields, so a table nothing references cannot print.
        This is the guard against the day somebody adds a generic loop.
        """
        JobCardPhoto.objects.create(job_card=self.card)
        self.client.force_login(self.office)
        html = self.client.get(reverse('invoice_view', args=[self.card.pk])).content.decode()
        self.assertNotIn('photo', html.lower().split('<style')[0])
        self.assertNotIn('r2.cloudflarestorage.com', html)

    @override_settings(**R2_SETTINGS)
    def test_settlement_never_chases_a_missing_photo(self):
        """
        If "no photos" became a settlement gap, every ordinary card would turn
        red on the Live Report and in the settle dialog. Optional means the
        rest of the app does not know this exists.
        """
        from workshop import settlement

        unfilled = settlement.unfilled(self.card)
        rendered = json.dumps(str(unfilled))
        self.assertNotIn('photo', rendered.lower())

    @override_settings(**NO_STORAGE)
    def test_the_heading_does_not_promise_photos_that_are_switched_off(self):
        """
        A section named "… & Photos" over no photo control is the page
        misdescribing itself — the same reasoning that names Floor's fold
        "Workshop Note" rather than "Customer Details". So the name only
        changes when the thing it names is actually there.
        """
        self.client.force_login(self.office)
        html = self.client.get(reverse('jobcard_edit', args=[self.card.pk])).content.decode()
        self.assertIn('>Customer &amp; Notes</h6>', html)
        self.assertNotIn('Photos</h6>', html)

    @override_settings(**R2_SETTINGS)
    def test_the_heading_names_photos_once_they_are_switched_on(self):
        self.client.force_login(self.office)
        html = self.client.get(reverse('jobcard_edit', args=[self.card.pk])).content.decode()
        self.assertIn('>Customer, Notes &amp; Photos</h6>', html)

    @override_settings(**R2_SETTINGS)
    def test_floor_gets_the_camera_too(self):
        """
        Floor is who walks round the car with the tablet. The box is rendered in
        BOTH branches of the Customer section; leaving it out of Floor's half
        would leave it out of the hands it was built for.
        """
        self.client.force_login(self.floor)
        html = self.client.get(reverse('jobcard_edit', args=[self.card.pk])).content.decode()
        self.assertIn('data-photo-box', html)
        self.assertIn('>Workshop Note &amp; Photos</h6>', html)

    @override_settings(**R2_SETTINGS)
    def test_a_new_card_offers_no_box_but_says_why(self):
        """
        An unsaved card has no primary key to attach a photo to. A box that
        looked live and then refused would be worse than no box.
        """
        self.client.force_login(self.office)
        html = self.client.get(reverse('jobcard_create')).content.decode()
        self.assertNotIn('data-photo-box', html)
        self.assertIn('Save the job card first', html)

    @override_settings(**R2_SETTINGS)
    def test_the_box_is_not_a_button_so_the_financial_lock_cannot_kill_viewing(self):
        """
        The Financial Lock disables everything matching
        `input, select, textarea, button` inside the form. A real <button> here
        would go dead on a settled card — taking the GALLERY with it, on
        exactly the cards whose photos matter most.
        """
        JobCardPhoto.objects.create(job_card=self.settled)
        self.client.force_login(self.office)
        html = self.client.get(reverse('jobcard_edit', args=[self.settled.pk])).content.decode()

        start = html.index('data-photo-box')
        tag_open = html.rindex('<', 0, start)
        self.assertTrue(
            html[tag_open:start].startswith('<div'),
            'the photo box must be a <div role="button">, never a <button>',
        )
        self.assertIn('data-can-edit="0"', html[tag_open:start + 400])

    @override_settings(**R2_SETTINGS)
    def test_a_photo_puts_no_column_on_the_job_card(self):
        """
        Nothing points AT a photo. If somebody adds a FK from JobCard to a
        photo, deleting or failing to load one starts being able to affect a
        card, and this whole entry stops being true.
        """
        field_names = {f.name for f in JobCard._meta.get_fields() if not f.auto_created}
        self.assertNotIn('photos', field_names)


@override_settings(**R2_SETTINGS)
class SparePhotosSharesTheSameTableTests(PhotoTestBase):
    """
    Phase 2 wires a box onto the spare rows; the storage rules are already here
    and are exercised now so the two phases cannot drift apart.
    """

    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='Kochi Auto Spares')
        self.spare = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Brake Disc',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
        )

    def test_a_spare_photo_carries_its_own_limit(self):
        self.client.force_login(self.floor)
        for _ in range(4):
            response = self.post_json('photo_commit', {
                'subject': 'spare', 'id': self.spare.pk,
                'photo_id': str(uuid.uuid4()), 'bytes': 10,
            })
            self.assertEqual(response.status_code, 200)
        refused = self.post_json('photo_commit', {
            'subject': 'spare', 'id': self.spare.pk,
            'photo_id': str(uuid.uuid4()), 'bytes': 10,
        })
        self.assertEqual(refused.status_code, 409)

    def test_a_spare_photo_and_a_car_photo_never_share_a_count(self):
        JobCardPhoto.objects.create(job_card=self.card)
        JobCardPhoto.objects.create(spare=self.spare)
        self.client.force_login(self.floor)

        car = self.client.get(reverse('photo_list'), {'subject': 'card', 'id': self.card.pk}).json()
        part = self.client.get(reverse('photo_list'), {'subject': 'spare', 'id': self.spare.pk}).json()
        self.assertEqual(len(car['photos']), 1)
        self.assertEqual(len(part['photos']), 1)

    def test_an_unassigned_spare_is_never_frozen(self):
        """It has no bill to settle, so there is nothing to freeze it against."""
        loose = JobCardSpareItem.objects.create(
            spare_part_name='Oil Filter', source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
        )
        self.client.force_login(self.floor)
        response = self.post_json('photo_sign', {
            'subject': 'spare', 'id': loose.pk, 'bytes': 100,
        })
        self.assertEqual(response.status_code, 200)

    def test_a_spare_photo_dies_with_its_spare(self):
        JobCardPhoto.objects.create(spare=self.spare)
        self.spare.delete()
        self.assertEqual(JobCardPhoto.objects.count(), 0)


@override_settings(**R2_SETTINGS)
class TheSpareRowCarriesItsOwnBoxTests(PhotoTestBase):
    """Phase 2 — a box in the Spare Parts table, one per row."""

    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='Kochi Auto Spares')
        self.spare = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Brake Disc',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
        )

    def _html(self, user=None):
        self.client.force_login(user or self.office)
        return self.client.get(reverse('jobcard_edit', args=[self.card.pk])).content.decode()

    def test_a_saved_spare_row_gets_a_box_carrying_its_own_subject(self):
        box = self.spare_box(self._html(), self.spare)
        self.assertEqual(box['data-subject'], 'spare')
        self.assertEqual(box['data-subject-id'], str(self.spare.pk))

    def test_the_spare_box_carries_the_spare_limit_not_the_car_limit(self):
        box = self.spare_box(self._html(), self.spare)
        self.assertEqual(box['data-limit'], '4')

    def test_the_clone_template_matches_the_live_row_column_for_column(self):
        """
        A missing cell in `#empty-spare-form` lays every row added by
        "+ Add Spare" one column adrift of its header, and nothing in the
        browser says so.
        """
        html = self._html()
        # Anchored on `col-part`, which only the SPARES header carries. Anchoring
        # on `col-idx` finds the Inventory table, which is rendered first and has
        # five columns — the test then compares two different tables and fails
        # with a number that looks like a real defect.
        header_start = html.index('<th class="ps-3 col-part"')
        header = html[header_start:html.index('</tr>', header_start)]
        header_cells = header.count('<th') + 1        # + the row-number cell above it

        template_start = html.index('id="empty-spare-form"')
        template = html[template_start:html.index('</tr>', template_start)]
        template_cells = template.count('<td')

        self.assertEqual(
            header_cells, template_cells,
            f'header has {header_cells} columns, the added-row template has {template_cells}',
        )

    def test_the_photo_column_is_absent_entirely_when_storage_is_off(self):
        """
        Asserted on the CELL, not on the bare class name: `.col-photo` is also a
        rule in this page's inline stylesheet, which renders on every request.
        The same trap CLAUDE.md records for `.paid-box` and `.pf-critical` —
        searching the whole page finds the stylesheet and proves nothing.
        """
        with override_settings(**NO_STORAGE):
            html = self._html()
        self.assertNotIn('<th class="col-photo', html)
        self.assertNotIn('<td class="col-photo', html)
        self.assertNotIn('data-photo-box', html)

    def test_the_photo_column_is_present_when_storage_is_on(self):
        html = self._html()
        self.assertIn('<th class="col-photo', html)
        self.assertIn('<td class="col-photo', html)

    def test_the_zero_line_height_is_scoped_to_the_BODY_cell(self):
        """
        `line-height: 0` is needed on the `<td>`, because the box inside it is
        inline-flex and would otherwise add the strut of its line box and make
        this the tallest cell in the row.

        Applied to the column as a whole it also hits the `<th>`, whose content
        is real text — and text in a zero-height line box paints on top of its
        neighbours. The header rendered "Photos" as an unreadable smudge over
        "Shop", and that shipped. Nothing in this suite executes CSS, so the
        guard is the selector itself: the rule must name `td`.
        """
        with open(
            'workshop/templates/workshop/jobcard/jobcard_form.html', encoding='utf-8'
        ) as handle:
            css = handle.read()

        for block in re.finditer(r'\.col-photo\s*\{([^}]*)\}', css):
            selector = css[:block.start()].rsplit('\n', 1)[-1] + '.col-photo'
            if 'line-height: 0' in block.group(1):
                self.assertIn(
                    'td.col-photo', selector,
                    'line-height: 0 must be scoped to the body cell — on the <th> it '
                    'paints the header label over its neighbour',
                )

    def test_the_photo_column_costs_a_CONSTANT_number_of_queries(self):
        """
        A rebuild in the live data carries 91 spares. One query per row to
        render a badge would be 91 extra queries on the longest form in the app,
        which is why the count is annotated onto the formset queryset.

        Measured as the difference between photos OFF and photos ON at a fixed
        row count, NOT as growth across row counts — because this page already
        issues about 7 queries per spare row for reasons that have nothing to do
        with photos (role checks in the row markup, chiefly). Measuring total
        growth would fail on somebody else's defect and would keep failing after
        this feature was made perfect. What this owns, and all it owns, is that
        switching photos on does not add a cost that scales with the rows.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for n in range(7):
            JobCardSpareItem.objects.create(
                job_card=self.card, spare_part_name=f'Part {n}',
                source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            )
        self.client.force_login(self.office)
        url = reverse('jobcard_edit', args=[self.card.pk])

        def queries_for_one_render():
            with CaptureQueriesContext(connection) as captured:
                self.client.get(url)
            return len(captured)

        with override_settings(**NO_STORAGE):
            without = queries_for_one_render()
        with_photos = queries_for_one_render()

        self.assertLessEqual(
            with_photos - without, 2,
            f'switching photos on cost {with_photos - without} extra queries across 8 spare '
            f'rows — the per-row count is not being annotated',
        )

    def test_a_spare_photo_count_shows_on_its_own_row_only(self):
        JobCardPhoto.objects.create(spare=self.spare)
        other = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Oil Filter',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
        )
        html = self._html()
        self.assertEqual(self.spare_box(html, self.spare)['data-count'], '1')
        self.assertEqual(self.spare_box(html, other)['data-count'], '0')

    def test_a_settled_cards_spare_rows_are_frozen_too(self):
        frozen_spare = JobCardSpareItem.objects.create(
            job_card=self.settled, spare_part_name='Clutch',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
        )
        self.client.force_login(self.office)
        html = self.client.get(reverse('jobcard_edit', args=[self.settled.pk])).content.decode()
        self.assertEqual(self.spare_box(html, frozen_spare)['data-can-edit'], '0')


@override_settings(**R2_SETTINGS)
class PurchaseHistoryLooksButNeverTouchesTests(PhotoTestBase):
    """Phase 3 — the shop ledger shows photos and offers no way to change them."""

    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='Kochi Auto Spares')
        self.spare = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Brake Disc',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            unit_price=1000, quantity=1,
        )

    def _html(self):
        self.client.force_login(self.office)
        return self.client.get(reverse('spare_shop_detail', args=[self.shop.pk])).content.decode()

    def test_a_row_with_photos_shows_a_view_only_box(self):
        JobCardPhoto.objects.create(spare=self.spare)
        box = self.spare_box(self._html(), self.spare)
        self.assertEqual(box['data-can-edit'], '0')
        self.assertEqual(box['data-count'], '1')

    def test_a_row_with_no_photos_shows_no_box_at_all(self):
        """
        A camera-less box on an empty row opens an empty gallery and answers
        nothing — and there would be one on every row of a 45-row page.
        """
        html = self._html()
        self.assertNotIn(f'data-subject-id="{self.spare.pk}"', html)

    def test_the_date_separator_still_spans_the_whole_table(self):
        """
        A new column means the separator's colspan has to grow with it, or the
        date sits under a short rule with a gap beside it.
        """
        JobCardPhoto.objects.create(spare=self.spare)
        self.assertIn('colspan="9"', self._html())

    def test_with_storage_off_the_column_and_the_old_colspan_both_return(self):
        with override_settings(**NO_STORAGE):
            html = self._html()
        self.assertIn('colspan="8"', html)
        self.assertNotIn('data-photo-box', html)


class TheRetentionPurgeTests(PhotoTestBase):
    """Phase 3 — the one-year window, and the one thing it must never take."""

    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='S')

    def _age(self, photo, days):
        JobCardPhoto.objects.filter(pk=photo.pk).update(
            taken_at=timezone.now() - timedelta(days=days)
        )

    def _run(self, *args):
        out = StringIO()
        call_command('purge_old_photos', *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_a_dry_run_deletes_nothing(self):
        photo = JobCardPhoto.objects.create(job_card=self.settled)
        self._age(photo, 400)
        output = self._run()
        self.assertIn('Dry run', output)
        self.assertEqual(JobCardPhoto.objects.count(), 1)

    def test_a_photo_past_the_window_goes(self):
        photo = JobCardPhoto.objects.create(job_card=self.settled)
        self._age(photo, 400)
        self._run('--yes')
        self.assertEqual(JobCardPhoto.objects.count(), 0)

    def test_a_photo_inside_the_window_stays(self):
        photo = JobCardPhoto.objects.create(job_card=self.settled)
        self._age(photo, 200)
        self._run('--yes')
        self.assertEqual(JobCardPhoto.objects.count(), 1)

    def test_an_unpaid_bill_keeps_its_photos_however_old(self):
        """
        The one exception, and the whole reason it exists: a year-old bill that
        has not been paid IS an open argument, and these are the evidence in it.
        """
        photo = JobCardPhoto.objects.create(job_card=self.card)     # PENDING
        self._age(photo, 900)
        output = self._run('--yes')
        self.assertEqual(JobCardPhoto.objects.count(), 1)
        self.assertIn('still unpaid', output)

    def test_a_part_photo_is_protected_through_its_own_card(self):
        """A spare photo reaches its bill through the spare, not directly."""
        spare = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='X',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
        )
        photo = JobCardPhoto.objects.create(spare=spare)
        self._age(photo, 900)
        self._run('--yes')
        self.assertEqual(JobCardPhoto.objects.count(), 1)

    def test_a_partial_fleet_bill_is_protected_too(self):
        self.settled.payment_status = 'PARTIAL'
        self.settled.save()
        photo = JobCardPhoto.objects.create(job_card=self.settled)
        self._age(photo, 900)
        self._run('--yes')
        self.assertEqual(JobCardPhoto.objects.count(), 1)

    def test_deleting_queues_the_objects_for_collection(self):
        photo = JobCardPhoto.objects.create(job_card=self.settled)
        key = photo.storage_key
        self._age(photo, 400)
        self._run('--yes')
        self.assertTrue(OrphanedPhotoBlob.objects.filter(storage_key=key).exists())

    def test_the_window_is_adjustable(self):
        photo = JobCardPhoto.objects.create(job_card=self.settled)
        self._age(photo, 400)
        self._run('--older-than', '730', '--yes')
        self.assertEqual(JobCardPhoto.objects.count(), 1)

    def test_a_nonsense_window_is_refused_rather_than_deleting_everything(self):
        photo = JobCardPhoto.objects.create(job_card=self.settled)
        self._age(photo, 400)
        output = self._run('--older-than', '0', '--yes')
        self.assertIn('at least 1 day', output)
        self.assertEqual(JobCardPhoto.objects.count(), 1)


@override_settings(**R2_SETTINGS)
class ModelRuleTests(PhotoTestBase):
    def test_a_photo_belongs_to_exactly_one_subject(self):
        from django.core.exceptions import ValidationError

        shop = SpareShop.objects.create(name='S')
        spare = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='X',
            source=JobCardSpareItem.SOURCE_SHOP, shop=shop,
        )
        with self.assertRaises(ValidationError):
            JobCardPhoto(job_card=self.card, spare=spare).clean()
        with self.assertRaises(ValidationError):
            JobCardPhoto().clean()

    def test_the_database_itself_refuses_a_photo_with_two_subjects(self):
        """
        `clean()` is advisory — Django never calls it on `save()`, and this
        model has no form, it is written by an endpoint. The CheckConstraint is
        what makes the rule real. A row with both FKs set would be counted
        against two limits and appear in two galleries.
        """
        from django.db import IntegrityError, transaction

        shop = SpareShop.objects.create(name='S2')
        spare = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Y',
            source=JobCardSpareItem.SOURCE_SHOP, shop=shop,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JobCardPhoto.objects.create(job_card=self.card, spare=spare)

    def test_the_database_itself_refuses_a_photo_with_no_subject(self):
        """Reachable from no screen, and invisible to the sweep."""
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JobCardPhoto.objects.create()

    def test_the_download_name_says_the_car_the_plate_and_the_job_card(self):
        """
        What somebody finds in their phone's gallery months later. The storage
        key stays a UUID — see `download_name`'s docstring for why — so this is
        the only place a photo is named in words.
        """
        self.card.bill_number = 'JB-26-457'
        self.card.save()
        name = JobCardPhoto.objects.create(job_card=self.card).download_name()

        self.assertIn('Audi', name)          # brand
        self.assertIn('A4', name)            # model
        self.assertIn('KL01AA1111', name)    # plate
        self.assertIn('JB-26-457', name)     # job card
        self.assertTrue(name.endswith('.jpg'))

    def test_a_download_name_is_safe_to_hand_a_filesystem(self):
        """
        Registrations carry spaces and part names carry brackets and slashes. A
        slash in particular would read as a directory separator on the way into
        somebody's phone.
        """
        self.card.registration_number = 'KL 07 / CD 7788'
        self.card.brand_name = 'Land Rover'
        self.card.model_name = 'Range Rover Sport (L494)'
        self.card.save()
        name = JobCardPhoto.objects.create(job_card=self.card).download_name()

        for bad in ('/', '\\', '(', ')', ' ', ':'):
            self.assertNotIn(bad, name, f'{bad!r} survived into the filename')
        self.assertTrue(name.endswith('.jpg'))

    def test_a_spare_photo_is_named_for_the_part_as_well(self):
        shop = SpareShop.objects.create(name='S')
        spare = JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Brake Disc',
            source=JobCardSpareItem.SOURCE_SHOP, shop=shop,
        )
        name = JobCardPhoto.objects.create(spare=spare).download_name()
        self.assertIn('Brake-Disc', name)
        self.assertIn('KL01AA1111', name)

    def test_the_storage_key_stays_a_uuid_however_the_car_is_named(self):
        """
        The readable name is a label on the download, never the key. Building
        the key from the registration would orphan every photo of a car the
        moment somebody corrected a typo in its plate.
        """
        photo = JobCardPhoto.objects.create(job_card=self.card)
        self.assertEqual(photo.storage_key, f'{photo.id}.jpg')
        self.assertNotIn('KL01AA1111', photo.storage_key)

    def test_the_storage_key_is_derived_from_the_id(self):
        """Derived, never stored — the bucket and the database cannot disagree."""
        photo = JobCardPhoto.objects.create(job_card=self.card)
        self.assertEqual(photo.storage_key, f'{photo.id}.jpg')

    @override_settings(PHOTO_S3_PREFIX='dev')
    def test_a_prefix_keeps_environments_apart(self):
        photo = JobCardPhoto.objects.create(job_card=self.card)
        self.assertEqual(photo.storage_key, f'dev/{photo.id}.jpg')


class TheLightboxSaysWhichCarTests(PhotoTestBase):
    """
    The caption under a photo reads top-down: where you are in the burst, which
    car, then when and who (2026-08-28).

    "1 of 4" leads and is the boldest of the three because it is the only line
    that CHANGES as you swipe — it used to sit under the date in small grey
    type, which is where the eye arrives last. The car was not there at all.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.office)

    def listing(self, card=None):
        with override_settings(**R2_SETTINGS):
            res = self.client.get(
                reverse('photo_list'),
                {'subject': 'card', 'id': (card or self.card).pk},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res.status_code, 200, res.content[:200])
        return json.loads(res.content)

    def test_the_gallery_is_told_which_car_it_is_about(self):
        self.assertEqual(self.listing()['subject'], 'Audi A4 · KL01AA1111')

    def test_the_label_is_sent_once_for_the_gallery_not_once_per_photo(self):
        """
        Every photo in one gallery belongs to the same card by construction, so
        per-photo it would be the same string ten times over the wire — and ten
        chances to disagree if one were ever built differently.
        """
        data = self.listing()
        self.assertIn('subject', data)
        for photo in data['photos']:
            self.assertNotIn('subject', photo)

    def test_a_car_with_no_brand_falls_back_to_its_plate_alone(self):
        """
        Nothing blank is announced — the rule every screen in this app follows.
        A missing brand leaves no separator behind, because the parts are joined
        rather than concatenated.
        """
        bare = JobCard.objects.create(
            registration_number='KL01CC3333', admitted_date=timezone.localdate())
        label = self.listing(bare)['subject']
        self.assertEqual(label, 'KL01CC3333')
        self.assertNotIn('·', label)

    def test_the_caption_reads_position_then_car_then_when(self):
        """
        DOM order is the reading order, so this is asserted on the markup rather
        than on a stylesheet rule that could be overridden.
        """
        # The overlays are not rendered at all without storage configured —
        # the section is optional end to end, which is its own rule.
        with override_settings(**R2_SETTINGS):
            html = self.client.get(
                reverse('jobcard_edit', args=[self.card.pk])).content.decode()
        bottom = html.split('photo-gal-bottom', 1)[1].split('</div>', 1)[0]
        self.assertLess(bottom.index('photoGalPos'), bottom.index('photoGalCar'))
        self.assertLess(bottom.index('photoGalCar'), bottom.index('photoGalMeta'))

    def test_only_the_position_stands_out_and_the_other_two_are_one_pair(self):
        """
        "1 of 4" is the only line that changes as you swipe, so it is the only
        one given any weight. The car and the date are two halves of one quiet
        caption and share a single declaration — nothing should make one look
        more important than the other, and a rule they both match cannot drift.

        Asserted on the source because nothing in this suite executes CSS.
        """
        import re

        with open('workshop/templates/workshop/includes/_photo_overlays.html',
                  encoding='utf-8') as fh:
            style = fh.read()

        pos = style.split('.photo-gal-pos {', 1)[1].split('}', 1)[0]
        self.assertIn('font-weight: 700', pos)

        # ONE rule for the two quiet lines, not two rules that happen to
        # agree. Matched without the newline between the selectors, because
        # an escape for it inside this string is one more thing to get wrong.
        pair_start = style.index('.photo-gal-car,')
        pair = style[pair_start:].split('{', 1)[1].split('}', 1)[0]
        self.assertIn('.photo-gal-meta', style[pair_start:pair_start + 60])
        self.assertIn('font-weight: 400', pair)
        self.assertNotIn('.photo-gal-car {', style)
