"""
Presigned URLs for photo storage on Cloudflare R2.

WHY THIS EXISTS
---------------
Railway sells no object storage — only Volumes, a persistent disk bolted to one
service. A Volume would have worked, and it lost on one point: `backup_db` does
not see it. Photos of a car are evidence in a pre-existing-damage dispute, and
on a Volume they would be the only data in this system with no backup at all.
R2 is free at this workshop's volume (~1.8 GB/year against a 10 GB tier), has
zero egress fees, and survives a change of host.

The other half of the choice is that **the bytes never touch Django**. The
browser PUTs straight to R2 with a presigned URL and GETs the same way, so a
300 KB upload on bad shop wifi never occupies a gunicorn worker, and the
`no-store` middleware never forces a re-fetch of an image the browser could
have cached.

Written against `hmac` and `hashlib` from the standard library rather than
`boto3`, which is ~10 MB and pulls botocore, to sign a handful of URLs. Same
trade as `ResendEmailBackend`, and unlike an HTTP client this is *offline
testable*: `presign()` is a pure function, and AWS publishes a known-answer
vector for it (see `test_photos_signing.py`).

OPTIONAL EVERYWHERE
-------------------
With no credentials configured `photos_are_configured()` is False, the endpoints
refuse politely, and the template renders no box. A job card still saves, an
invoice still prints, and settlement never chases a photo — the same degradation
Web Push has with no VAPID keys. Nothing in this module is ever called from the
job-card save path.

CONFIGURE
---------
    PHOTO_S3_ACCOUNT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    PHOTO_S3_ACCESS_KEY_ID=xxxxxxxxxxxxxxxxxxxx
    PHOTO_S3_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxx
    PHOTO_S3_BUCKET=formulad-photos

Supabase Storage instead (no payment card required, same protocol):

    PHOTO_S3_ENDPOINT=<project-ref>.supabase.co
    PHOTO_S3_PATH_PREFIX=storage/v1/s3
    PHOTO_S3_REGION=ap-south-1
    PHOTO_S3_ACCESS_KEY_ID=...        # Project Settings -> Storage -> S3 keys
    PHOTO_S3_SECRET_ACCESS_KEY=...
    PHOTO_S3_BUCKET=photos

With neither, a DEBUG server stores photographs on local disk so the section can
be demonstrated with no account at all.

Use SEPARATE buckets for development and production. They are free, and one
bucket shared between them means a purge run against dev can reach real photos.

The bucket also needs a CORS policy, or every upload fails with an opaque
browser error that reads exactly like a signing bug:

    AllowedOrigins: ["https://your-app-domain"]
    AllowedMethods: ["PUT", "GET"]
    AllowedHeaders: ["content-type"]
"""

import datetime as dt
import hashlib
import hmac
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

ALGORITHM = 'AWS4-HMAC-SHA256'
UNSIGNED_PAYLOAD = 'UNSIGNED-PAYLOAD'
SERVICE = 's3'

# An upload URL is handed out and used within seconds; a view URL has to outlive
# somebody browsing a gallery. Both are far below SigV4's 7-day ceiling, so a
# leaked URL stops working quickly.
UPLOAD_TTL = 300
VIEW_TTL = 3600

BACKEND_S3 = 's3'
BACKEND_LOCAL = 'local'
BACKEND_OFF = 'off'


def _s3_is_configured():
    return all([
        settings.PHOTO_S3_ACCESS_KEY_ID,
        settings.PHOTO_S3_SECRET_ACCESS_KEY,
        settings.PHOTO_S3_BUCKET,
        s3_host(),
    ])


def storage_backend():
    """
    Where the bytes go: an S3-compatible bucket, this machine's disk, or nowhere.

    WHY THERE IS A LOCAL BACKEND AT ALL
    -----------------------------------
    Cloudflare R2 requires a payment method on file even for its free tier, and
    the workshop's own card is not available until the owners' accounts are set
    up. Without a second option the entire photo section would be undemonstrable
    until then — including at the meeting where the owners decide whether they
    want it.

    Local storage costs almost nothing to support because of a decision already
    made: the browser is handed a URL and PUTs to it, so **it does not care
    whether that URL points at a bucket or at this Django process.** Swapping
    the backend swaps two functions, not the camera, not the queue, not the
    gallery.

    It is deliberately gated on DEBUG. Local disk is the right answer on a
    laptop and the wrong one in production, where the container filesystem is
    wiped on every deploy — a deployment that lost its credentials must fall
    back to OFF, which is honest, rather than to a disk that silently eats
    photographs.
    """
    if _s3_is_configured():
        return BACKEND_S3
    if settings.DEBUG and settings.PHOTO_LOCAL_FALLBACK:
        return BACKEND_LOCAL
    return BACKEND_OFF


def photos_are_configured():
    """True when photographs can actually be stored somewhere."""
    return storage_backend() != BACKEND_OFF


# ---------------------------------------------------------------------------
# SigV4 — pure functions, no settings, no network
# ---------------------------------------------------------------------------

def _quote(value):
    """
    Percent-encode one query-string name or value the way SigV4 requires.

    The unreserved set is exactly `A-Za-z0-9-_.~`; everything else is encoded,
    **including `/`**. That last part matters: the credential scope contains
    slashes and they must arrive as `%2F` in both the canonical string and the
    URL, or the signature will not match.
    """
    return urllib.parse.quote(str(value), safe='-_.~')


def _canonical_uri(path):
    """Encode each path segment while keeping the separators."""
    return '/'.join(urllib.parse.quote(seg, safe='-_.~') for seg in path.split('/'))


def _signing_key(secret_key, datestamp, region, service):
    key = ('AWS4' + secret_key).encode('utf-8')
    for part in (datestamp, region, service, 'aws4_request'):
        key = hmac.new(key, part.encode('utf-8'), hashlib.sha256).digest()
    return key


def presign(method, host, path, access_key, secret_key, region, service,
            expires, extra_query=None, now=None):
    """
    Build one presigned (query-string authenticated) URL.

    Deliberately takes every input as an argument and reads no settings, so it
    can be checked against AWS's published example vector — which uses
    virtual-host addressing on `s3.amazonaws.com` and would be unreachable if
    the R2 host were baked in here. The R2 wrappers below supply the rest.

    `now` is injectable for the same reason: a signature is a function of the
    timestamp, so a known-answer test needs to pin it.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime('%Y%m%dT%H%M%SZ')
    datestamp = now.strftime('%Y%m%d')
    scope = f'{datestamp}/{region}/{service}/aws4_request'

    query = dict(extra_query or {})
    query['X-Amz-Algorithm'] = ALGORITHM
    query['X-Amz-Credential'] = f'{access_key}/{scope}'
    query['X-Amz-Date'] = amz_date
    query['X-Amz-Expires'] = str(expires)
    query['X-Amz-SignedHeaders'] = 'host'

    # Sorted by the ENCODED name, which is what the specification says. Our
    # names are all ASCII so it makes no difference today, and would the moment
    # anything unusual is added.
    pairs = sorted((_quote(k), _quote(v)) for k, v in query.items())
    canonical_query = '&'.join(f'{k}={v}' for k, v in pairs)
    canonical_uri = _canonical_uri(path)

    canonical_request = '\n'.join([
        method,
        canonical_uri,
        canonical_query,
        f'host:{host}\n',
        'host',
        UNSIGNED_PAYLOAD,
    ])

    string_to_sign = '\n'.join([
        ALGORITHM,
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode('utf-8')).hexdigest(),
    ])

    signature = hmac.new(
        _signing_key(secret_key, datestamp, region, service),
        string_to_sign.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()

    return f'https://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}'


# ---------------------------------------------------------------------------
# S3-compatible bucket (Cloudflare R2, Supabase Storage, or any other)
# ---------------------------------------------------------------------------

def s3_host():
    """
    The bucket's hostname.

    Given `PHOTO_S3_ENDPOINT` it is used verbatim, which is what makes any
    S3-compatible provider work with no code change — Supabase Storage in
    particular, whose free tier needs no payment card and which speaks the same
    protocol. Otherwise it is built from the Cloudflare account id, because R2
    is the intended production home and deriving it is one less thing to mistype.
    """
    endpoint = (settings.PHOTO_S3_ENDPOINT or '').strip()
    if endpoint:
        return endpoint.replace('https://', '').replace('http://', '').rstrip('/')
    if settings.PHOTO_S3_ACCOUNT_ID:
        return f'{settings.PHOTO_S3_ACCOUNT_ID}.r2.cloudflarestorage.com'
    return ''


def object_key(photo_id):
    """
    The storage key for a photo, derived from its UUID primary key.

    Derived rather than stored, so there is exactly one answer to "where does
    this photo live" and no column that can drift out of step with the bucket.
    """
    prefix = (settings.PHOTO_S3_PREFIX or '').strip('/')
    name = f'{photo_id}.jpg'
    return f'{prefix}/{name}' if prefix else name


def _s3_presign(method, key, expires, extra_query=None):
    # Supabase serves its S3 API under /storage/v1/s3; R2 serves it at the root.
    # A configurable path prefix is the whole difference between the two.
    base = (settings.PHOTO_S3_PATH_PREFIX or '').strip('/')
    path = f'/{base}/{settings.PHOTO_S3_BUCKET}/{key}' if base else f'/{settings.PHOTO_S3_BUCKET}/{key}'
    return presign(
        method,
        s3_host(),
        path,
        settings.PHOTO_S3_ACCESS_KEY_ID,
        settings.PHOTO_S3_SECRET_ACCESS_KEY,
        settings.PHOTO_S3_REGION,
        SERVICE,
        expires,
        extra_query,
    )


# ---------------------------------------------------------------------------
# Local disk — development and demonstration only
# ---------------------------------------------------------------------------

def local_path(key):
    """
    Where one photo sits on this machine, or None if the key is not one of ours.

    Every key this app mints is `<uuid4>.jpg` under an optional prefix, so
    anything else is a crafted request and is refused rather than sanitised —
    there is no legitimate caller that needs `..` to work.
    """
    name = key.rsplit('/', 1)[-1]
    if not re.fullmatch(r'[0-9a-fA-F-]{36}\.jpg', name):
        return None
    return os.path.join(settings.MEDIA_ROOT, 'photos', name)


def local_token(key, expires_at):
    """
    The local backend's answer to a presigned URL.

    Same shape as the S3 path — a URL that carries its own permission and stops
    working shortly — so the two backends behave alike and the browser code does
    not branch. It also means the PUT endpoint needs no CSRF token, which
    matters because the S3 path deliberately sends no custom headers.
    """
    message = f'{key}:{expires_at}'.encode('utf-8')
    return hmac.new(
        settings.SECRET_KEY.encode('utf-8'), message, hashlib.sha256
    ).hexdigest()[:32]


def _local_url(view_name, key, ttl, **extra):
    from django.urls import reverse

    expires_at = int(dt.datetime.now(dt.timezone.utc).timestamp()) + ttl
    params = {'k': key, 'e': expires_at, 't': local_token(key, expires_at), **extra}
    return f'{reverse(view_name)}?{urllib.parse.urlencode(params)}'


def local_token_is_valid(key, expires_at, token):
    try:
        expiry = int(expires_at)
    except (TypeError, ValueError):
        return False
    if expiry < int(dt.datetime.now(dt.timezone.utc).timestamp()):
        return False
    return hmac.compare_digest(local_token(key, expiry), token or '')


# ---------------------------------------------------------------------------
# The two calls the rest of the app makes
# ---------------------------------------------------------------------------

def upload_url(key):
    """A short-lived URL the browser may PUT one JPEG to."""
    if storage_backend() == BACKEND_LOCAL:
        return _local_url('photo_blob_put', key, UPLOAD_TTL)
    return _s3_presign('PUT', key, UPLOAD_TTL)


def view_url(key, filename=None):
    """
    A URL the browser may GET the image from.

    `response-content-type` forces `image/jpeg` regardless of what the PUT
    happened to send, so the gallery can never be handed something the browser
    decides to download instead of display. `filename` sets the name a
    long-press "Save image" offers — without it the phone suggests a raw UUID.
    `inline` keeps it displayable; only the saved copy takes the name.
    """
    if storage_backend() == BACKEND_LOCAL:
        return _local_url('photo_blob_get', key, VIEW_TTL, n=filename or '')
    extra = {'response-content-type': 'image/jpeg'}
    if filename:
        extra['response-content-disposition'] = f'inline; filename="{filename}"'
    return _s3_presign('GET', key, VIEW_TTL, extra)


def delete_object(key):
    """
    Remove one object from the bucket. Returns True when it is gone.

    Called ONLY from the sweep command, never from a request — deleting a row
    and deleting a blob are separated on purpose so that a slow or unreachable
    bucket can never make a photo appear undeletable in the app. R2 answers a
    DELETE for a key that does not exist with 204, so a re-run of the sweep is
    harmless.
    """
    backend = storage_backend()
    if backend == BACKEND_OFF:
        return False

    if backend == BACKEND_LOCAL:
        path = local_path(key)
        if not path:
            return False
        try:
            os.remove(path)
        except FileNotFoundError:
            pass                    # already gone is the outcome we wanted
        except OSError as exc:
            logger.warning('Local photo delete failed for %s: %s', key, exc)
            return False
        return True

    request = urllib.request.Request(_s3_presign('DELETE', key, UPLOAD_TTL), method='DELETE')
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        # 404 means somebody already removed it; that is the outcome we wanted.
        if exc.code == 404:
            return True
        logger.warning('R2 delete failed for %s: HTTP %s', key, exc.code)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('R2 delete failed for %s: %s', key, exc)
    return False
