"""
Photo endpoints: sign, commit, list, delete.

The signing itself lives in `workshop/photos.py` (top-level, no HTTP); this
module is only the HTTP surface — the same split as `analysis_engine.py` /
`analysis_views.py` and `push.py` / `views/push.py`.

WHY FOUR ENDPOINTS AND NOT THREE
--------------------------------
Sign and commit are separate on purpose. The obvious design creates the row
first and lets the browser upload afterwards — and then a browser that closes
mid-upload leaves a row pointing at an object that does not exist, which the
gallery renders as a broken image nobody can explain or remove.

Signing first and committing after inverts the failure: a row is written only
once the bytes are actually in the bucket, so **a row always means a real
photo**. The other side of that trade is an orphaned object when a commit never
arrives, which is invisible to everyone and is collected by `sweep_photo_blobs`.
An invisible orphan is a far better failure than a visible ghost.

The UUID is minted by the server at sign time and handed back, because the
storage key is derived from it — the browser has to know where it is PUTting
before the row exists.

WHAT IS ENFORCED HERE, AND WHY IT IS HERE
-----------------------------------------
Every rule is server-side: the per-subject limit, the settled-card freeze, and
RBAC. The freeze in particular is keyed on **the card's own payment status, not
on which page the request came from** — Purchase History has no Financial Lock
on it, so a page-based check would leave that door open.
"""

import json
import os
import uuid

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.encoding import escape_uri_path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .. import photos as photo_storage
from ..decorators import staff_required
from ..models import JobCard, JobCardPhoto, JobCardSpareItem

SUBJECT_CARD = 'card'
SUBJECT_SPARE = 'spare'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload(request):
    try:
        return json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return {}


def _card_is_frozen(card):
    """
    A settled card's photos are frozen — no additions, no deletions.

    This deliberately reuses the Financial Lock's own boundary rather than
    inventing a second one. Money and evidence stop moving at the same moment,
    which means there is never an evidence-destroying delete to reason about,
    and therefore nothing here needs a `DeletionLog` row.
    """
    return bool(card) and card.payment_status in ('PAID', 'BULK_PAID')


def _resolve_subject(subject, subject_id):
    """
    Return `(obj, card, limit, filter_kwargs)` for the thing being photographed.

    A spare with no job card is an unassigned purchase — legitimate, and never
    frozen, because there is no bill to settle.

    The id is parsed here rather than handed to the ORM, because a crafted
    `?id=abc` reaches the field's `get_prep_value` and raises — a 500 from a
    hand-edited URL. The same reasoning as `_apply_date_filter` parsing a custom
    range before it reaches a `__date__gte` lookup.
    """
    try:
        pk = int(subject_id)
    except (TypeError, ValueError):
        raise Http404('Bad photo subject id')

    if subject == SUBJECT_CARD:
        card = get_object_or_404(JobCard, pk=pk, is_deleted=False)
        return card, card, settings.PHOTO_LIMIT_CAR, {'job_card': card}

    if subject == SUBJECT_SPARE:
        spare = get_object_or_404(
            JobCardSpareItem.objects.select_related('job_card'), pk=pk
        )
        return spare, spare.job_card, settings.PHOTO_LIMIT_SPARE, {'spare': spare}

    raise Http404('Unknown photo subject')


def _not_configured():
    return JsonResponse(
        {'ok': False, 'error': 'Photo storage is not set up on this server.'},
        status=503,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@staff_required
@require_POST
def photo_sign(request):
    """
    Mint a photo id and hand back a short-lived URL the browser may PUT to.

    Writes nothing. The limit is checked here so a full subject is refused
    before the tablet spends bandwidth on an upload nobody will keep — but it is
    checked again at commit, which is the check that actually counts.

    The byte figure is what the browser *declares*. It is a guard against an
    accident, not a hard bound: what really bounds this bucket is that only
    signed-in staff can obtain a URL at all, each one is good for a single key
    for five minutes, and no subject may hold more than its limit.
    """
    if not photo_storage.photos_are_configured():
        return _not_configured()

    body = _payload(request)
    obj, card, limit, filters = _resolve_subject(body.get('subject'), body.get('id'))

    if _card_is_frozen(card):
        return JsonResponse(
            {'ok': False, 'error': 'This bill is settled — its photos are locked.'},
            status=403,
        )

    if JobCardPhoto.objects.filter(**filters).count() >= limit:
        return JsonResponse(
            {'ok': False, 'error': f'That is the maximum of {limit} photos.', 'limit_hit': True},
            status=409,
        )

    declared = body.get('bytes') or 0
    if not isinstance(declared, int) or declared <= 0 or declared > settings.PHOTO_MAX_BYTES:
        return JsonResponse({'ok': False, 'error': 'That image is not a usable size.'}, status=400)

    photo_id = uuid.uuid4()
    return JsonResponse({
        'ok': True,
        'photo_id': str(photo_id),
        'upload_url': photo_storage.upload_url(photo_storage.object_key(photo_id)),
    })


@staff_required
@require_POST
def photo_commit(request):
    """
    Record a photo whose bytes are already in the bucket.

    The limit is re-checked inside the transaction because sign and commit are
    two requests, and a mechanic tapping the shutter quickly has several in
    flight at once.
    """
    if not photo_storage.photos_are_configured():
        return _not_configured()

    body = _payload(request)
    obj, card, limit, filters = _resolve_subject(body.get('subject'), body.get('id'))

    if _card_is_frozen(card):
        return JsonResponse(
            {'ok': False, 'error': 'This bill is settled — its photos are locked.'},
            status=403,
        )

    try:
        photo_id = uuid.UUID(str(body.get('photo_id')))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Malformed photo id.'}, status=400)

    with transaction.atomic():
        # Idempotent on the id AND the subject together. Keyed on the id alone,
        # a commit naming a photo that already exists under a different subject
        # would return that other photo and report success — describing a
        # different car's picture back to the caller. Retrying a commit after a
        # timeout is the case this has to stay quiet for, and that always names
        # the same subject.
        existing = JobCardPhoto.objects.filter(pk=photo_id).first()
        if existing is not None:
            same_subject = all(
                getattr(existing, f'{field}_id') == subject.pk
                for field, subject in filters.items()
            )
            if not same_subject:
                return JsonResponse(
                    {'ok': False, 'error': 'That photo belongs to something else.'},
                    status=409,
                )
            photo = existing
        else:
            if JobCardPhoto.objects.select_for_update().filter(**filters).count() >= limit:
                return JsonResponse(
                    {
                        'ok': False,
                        'error': f'That is the maximum of {limit} photos.',
                        'limit_hit': True,
                    },
                    status=409,
                )
            photo = JobCardPhoto.objects.create(
                id=photo_id,
                taken_by=request.user,
                byte_size=body.get('bytes') or 0,
                **filters,
            )

    return JsonResponse({
        'ok': True,
        'photo': _serialise(photo),
        'count': JobCardPhoto.objects.filter(**filters).count(),
    })


@staff_required
@require_GET
def photo_list(request):
    """
    Every photo for one subject, newest first, with fresh signed view URLs.

    Newest first is not a preference — it is what makes the no-review capture
    safe. Opening the gallery straight after a burst shows what was just taken,
    which is the verification step that replaces reviewing each shot.

    `can_edit` travels with the list so one gallery component serves both a
    working job card and a read-only Purchase History row without a second
    implementation. The client hides Add and Delete on it; this server decides
    it.
    """
    if not photo_storage.photos_are_configured():
        return JsonResponse({'ok': True, 'photos': [], 'can_edit': False, 'configured': False})

    obj, card, limit, filters = _resolve_subject(
        request.GET.get('subject'), request.GET.get('id')
    )

    photos = (
        JobCardPhoto.objects
        .filter(**filters)
        .select_related('taken_by', 'job_card', 'spare')
    )
    return JsonResponse({
        'ok': True,
        'configured': True,
        'can_edit': not _card_is_frozen(card),
        'limit': limit,
        'photos': [_serialise(p) for p in photos],
    })


@staff_required
@require_POST
def photo_delete(request):
    """
    Remove one photo.

    The row goes now and the object is queued for collection, rather than the
    two happening together — by a post_delete signal on the model, so that every
    route a photo row can vanish by is covered and not just this one. It runs in
    the same transaction as the delete, so a key can never be lost between them.

    No `DeletionLog` row: the freeze above means a settled card's photos cannot
    be deleted at all, so this only ever removes a mis-shot on an open card.
    That is housekeeping, and `DeletionLog.record()` is what raises a CRITICAL
    push to both owners — buzzing two phones over a blurry photo is precisely
    how a critical alert stops being read.
    """
    if not photo_storage.photos_are_configured():
        return _not_configured()

    body = _payload(request)
    try:
        photo_id = uuid.UUID(str(body.get('photo_id')))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Malformed photo id.'}, status=400)

    photo = get_object_or_404(
        JobCardPhoto.objects.select_related('job_card', 'spare__job_card'), pk=photo_id
    )
    card = photo.job_card or (photo.spare.job_card if photo.spare_id else None)
    if _card_is_frozen(card):
        return JsonResponse(
            {'ok': False, 'error': 'This bill is settled — its photos are locked.'},
            status=403,
        )

    # The object left behind is queued by a post_delete signal on the model, not
    # here — that way every route a photo row can vanish by is covered, including
    # the cascades this view knows nothing about.
    photo.delete()
    return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# LOCAL BACKEND — the two endpoints that stand in for a bucket
# ---------------------------------------------------------------------------
# Only reachable when `storage_backend()` is 'local', i.e. a DEBUG server with
# no S3 credentials. They exist so the whole feature can be shown on a laptop
# with no account and no payment card; in production these refuse and the bytes
# go straight to the bucket without passing through Django at all.
#
# `csrf_exempt` is safe here and is the point: the URL carries its own HMAC
# permission (see `photos.local_token`), which is the local equivalent of a
# presigned URL. The S3 path deliberately sends no custom headers — adding one
# would force the bucket's CORS policy to allow it — so the two backends have to
# accept the same request shape.

@csrf_exempt
@require_http_methods(['PUT'])
def photo_blob_put(request):
    if photo_storage.storage_backend() != photo_storage.BACKEND_LOCAL:
        raise Http404
    key = request.GET.get('k', '')
    if not photo_storage.local_token_is_valid(key, request.GET.get('e'), request.GET.get('t')):
        return JsonResponse({'ok': False, 'error': 'That upload link has expired.'}, status=403)

    path = photo_storage.local_path(key)
    if not path:
        raise Http404

    body = request.body
    if not body or len(body) > settings.PHOTO_MAX_BYTES:
        return JsonResponse({'ok': False, 'error': 'That image is not a usable size.'}, status=400)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(body)
    return HttpResponse(status=200)


@require_GET
def photo_blob_get(request):
    """
    Serve one stored photo.

    Deliberately NOT `@staff_required`: the signed link is the permission, the
    same way it is on the S3 path, and an `<img>` inside the gallery must work
    without depending on how the browser treats cookies on a subresource.
    """
    if photo_storage.storage_backend() != photo_storage.BACKEND_LOCAL:
        raise Http404
    key = request.GET.get('k', '')
    if not photo_storage.local_token_is_valid(key, request.GET.get('e'), request.GET.get('t')):
        raise Http404

    path = photo_storage.local_path(key)
    if not path or not os.path.exists(path):
        raise Http404

    response = FileResponse(open(path, 'rb'), content_type='image/jpeg')
    name = request.GET.get('n') or 'photo.jpg'
    response['Content-Disposition'] = f'inline; filename="{escape_uri_path(name)}"'
    return response


def _serialise(photo):
    taken = timezone.localtime(photo.taken_at) if photo.taken_at else None
    return {
        'id': str(photo.id),
        'url': photo_storage.view_url(photo.storage_key, photo.download_name()),
        'taken_at': taken.strftime('%d %b %Y, %I:%M %p') if taken else '',
        'taken_by': photo.taken_by.username if photo.taken_by_id else '',
    }
