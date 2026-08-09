"""
Email over Resend's HTTPS API instead of SMTP.

WHY THIS EXISTS
---------------
Railway blocks outbound SMTP on every plan below Pro — ports 25, 465, 587 and
2525 are all unreachable, by design, to stop the platform being used for spam.
Render's free tier behaves the same way (reset mail timed out at the 10s
`EMAIL_TIMEOUT`). So the password-reset code, which is the one thing this app
sends, could not leave the host at all.

The fix is the transport, not the flow. `PasswordResetOTP`, the two-step form,
the per-account and per-session throttles, and the deliberate choice to put the
code in the *subject line* are all unchanged and still tested — Django routes
every `send_mail()` through `EMAIL_BACKEND`, so swapping that one setting moves
the mail onto HTTPS, which no host blocks.

Deliberately written against `urllib.request` from the standard library rather
than `requests` or the `resend` SDK. `requests` was removed from this project
when Twilio went; re-adding a dependency to send five emails a year is a poor
trade, and this is the whole of the API surface we use.

CONFIGURE (production only)
---------------------------
    RESEND_API_KEY=re_xxxxxxxx
    DEFAULT_FROM_EMAIL=Formula D <noreply@mail.formuladservice.in>

The sending domain must be verified in Resend first. Verify a SUBDOMAIN
(`mail.formuladservice.in`), never the root — the root carries the public
WordPress site, and SPF/DKIM records added there can disturb mail for the
business domain itself.

Development is untouched: it uses the console backend unless EMAIL_REAL=true.
Tests are untouched: `manage.py test` forces Django's locmem backend.
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = 'https://api.resend.com/emails'


class ResendEmailBackend(BaseEmailBackend):
    """
    Send each message as one POST to Resend.

    Returns the number of messages accepted, which is what `send_mail()`
    reports back to `_send_reset_email` — it checks `delivered > 0` before
    telling the visitor a code is on its way, so a wrong return value here
    would make the app claim it sent something it did not.
    """

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = getattr(settings, 'RESEND_API_KEY', '')
        # EMAIL_TIMEOUT is shared with the SMTP backend on purpose: whatever
        # the transport, a password-reset page must not hang on a dead
        # provider. urllib blocks forever without an explicit timeout.
        self.timeout = getattr(settings, 'EMAIL_TIMEOUT', 10)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        if not self.api_key:
            # A missing key is a deployment mistake, not a transient failure,
            # and silently returning 0 would surface as "we could not send a
            # code" with nothing anywhere saying why. Honour fail_silently so
            # the contract matches every other Django backend.
            if self.fail_silently:
                return 0
            raise ImproperlyConfigured(
                'RESEND_API_KEY is not set — cannot send mail over the Resend API.'
            )

        sent = 0
        for message in email_messages:
            if self._send(message):
                sent += 1
        return sent

    def _send(self, message):
        recipients = message.recipients()
        if not recipients:
            return False

        payload = json.dumps({
            'from': message.from_email or settings.DEFAULT_FROM_EMAIL,
            'to': list(recipients),
            'subject': message.subject,
            # Every mail this app sends is plain text. `message.body` is the
            # text part for both EmailMessage and EmailMultiAlternatives, so
            # there is no HTML branch to get wrong.
            'text': message.body,
        }).encode('utf-8')

        request = urllib.request.Request(
            RESEND_ENDPOINT,
            data=payload,
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as exc:
            # Read the body: Resend explains refusals ("domain not verified",
            # "invalid from address") here, and without it the log says only
            # "400" on the day this matters most. Recipients are NOT logged —
            # an owner's address should not end up in a hosting provider's
            # log viewer.
            detail = ''
            try:
                detail = exc.read().decode('utf-8', 'replace')[:400]
            except Exception:  # pragma: no cover - diagnostics only
                pass
            logger.error('Resend rejected a message: HTTP %s %s', exc.code, detail)
            if not self.fail_silently:
                raise
            return False
        except Exception as exc:
            logger.error('Resend delivery failed: %s', exc)
            if not self.fail_silently:
                raise
            return False
