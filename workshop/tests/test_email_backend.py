"""
Mail over Resend's HTTPS API — the transport under the password-reset code.

Railway blocks outbound SMTP on every plan below Pro (Render's free tier does
the same), so the SMTP settings in base.py cannot deliver from the host this
app ships on, however correctly they are filled in. `ResendEmailBackend` moves
the same mail onto HTTPS, which no host blocks.

Only the transport changed. `test_password_reset.py` still owns the flow — the
hashing, the expiry, the throttles, the non-disclosure rules. What these tests
hold is narrower, and all of it is about not lying to the caller:

  - `send_mail()` returns the number actually accepted, because
    `_send_reset_email` checks `delivered > 0` before telling a visitor a code
    is on its way. A backend that returns 1 for a refused message would make
    the app promise mail it never sent.
  - A missing API key is a deployment mistake and says so, rather than looking
    like a delivery failure.
  - `fail_silently` means what it means on every other Django backend.
  - The owner's email address never reaches the logs. Reset mail goes to two
    people, and a hosting provider's log viewer is a wider audience than they
    agreed to.

Nothing here touches the network: `urllib.request.urlopen` is patched in every
test.
"""

import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.core.mail import send_mail
from django.test import SimpleTestCase, override_settings

BACKEND = 'workshop.email_backend.ResendEmailBackend'


def _ok_response(status=200):
    """A context-manager stand-in for the object urlopen returns."""
    response = MagicMock()
    response.status = status
    response.__enter__ = lambda self: self
    response.__exit__ = lambda *args: False
    return response


class _Capture:
    """Records the request urlopen was handed, and reports success."""

    def __init__(self, status=200):
        self.status = status
        self.url = None
        self.headers = {}
        self.payload = None
        self.timeout = None
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        self.url = request.full_url
        self.headers = dict(request.headers)
        self.payload = json.loads(request.data.decode('utf-8'))
        self.timeout = timeout
        return _ok_response(self.status)


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY='re_test_key', EMAIL_TIMEOUT=10)
class SendingOverTheApiTests(SimpleTestCase):

    def test_a_sent_message_is_reported_as_one_delivery(self):
        capture = _Capture()
        with patch('urllib.request.urlopen', capture):
            delivered = send_mail('123456 is your code', 'body', 'D <a@b.c>', ['owner@x.com'])
        self.assertEqual(delivered, 1)
        self.assertEqual(capture.calls, 1)

    def test_the_request_carries_the_key_the_endpoint_and_the_timeout(self):
        capture = _Capture()
        with patch('urllib.request.urlopen', capture):
            send_mail('s', 'b', 'a@b.c', ['owner@x.com'])
        self.assertEqual(capture.url, 'https://api.resend.com/emails')
        # urllib title-cases header names.
        self.assertEqual(capture.headers.get('Authorization'), 'Bearer re_test_key')
        # Without an explicit timeout urllib blocks forever, which would hang
        # the reset page on a dead provider rather than reporting a failure.
        self.assertEqual(capture.timeout, 10)

    def test_the_payload_is_the_message_that_was_asked_for(self):
        capture = _Capture()
        with patch('urllib.request.urlopen', capture):
            send_mail('123456 is your code', 'the body', 'Formula D <no@mail.x>', ['owner@x.com'])
        self.assertEqual(capture.payload, {
            'from': 'Formula D <no@mail.x>',
            'to': ['owner@x.com'],
            'subject': '123456 is your code',
            'text': 'the body',
        })

    def test_the_code_survives_being_put_in_the_subject(self):
        """
        The reset code travels in the *subject* on purpose — iOS and Android
        both show it in the notification banner, so it is read without opening
        the mail app. If a transport ever mangled or dropped the subject that
        design would break silently, and the body alone would still look like
        a successful send.
        """
        capture = _Capture()
        with patch('urllib.request.urlopen', capture):
            send_mail('482913 is your Formula D password reset code', 'b', 'a@b.c', ['o@x.com'])
        self.assertIn('482913', capture.payload['subject'])

    def test_several_messages_are_counted_individually(self):
        capture = _Capture()
        with patch('urllib.request.urlopen', capture):
            delivered = send_mail('s', 'b', 'a@b.c', ['one@x.com', 'two@x.com'])
        # One message with two recipients is still one message.
        self.assertEqual(delivered, 1)
        self.assertEqual(capture.payload['to'], ['one@x.com', 'two@x.com'])


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY='re_test_key')
class WhenTheProviderRefusesTests(SimpleTestCase):

    def _refuse(self, code=422, body=b'{"message":"domain not verified"}'):
        def raiser(request, timeout=None):
            raise urllib.error.HTTPError(
                'https://api.resend.com/emails', code, 'err', {}, None
            )
        return raiser

    def test_a_refusal_is_not_reported_as_a_delivery(self):
        with patch('urllib.request.urlopen', self._refuse()):
            with self.assertRaises(urllib.error.HTTPError):
                send_mail('s', 'b', 'a@b.c', ['o@x.com'], fail_silently=False)

    def test_fail_silently_returns_zero_rather_than_raising(self):
        with patch('urllib.request.urlopen', self._refuse()):
            delivered = send_mail('s', 'b', 'a@b.c', ['o@x.com'], fail_silently=True)
        self.assertEqual(delivered, 0)

    def test_a_network_error_behaves_the_same_way(self):
        def boom(request, timeout=None):
            raise OSError('connection reset')
        with patch('urllib.request.urlopen', boom):
            self.assertEqual(
                send_mail('s', 'b', 'a@b.c', ['o@x.com'], fail_silently=True), 0
            )

    def test_the_recipient_address_is_never_written_to_the_log(self):
        """
        Logs are read in a hosting provider's dashboard. An owner's personal
        address should not be sitting there because a send failed.
        """
        with patch('urllib.request.urlopen', self._refuse()):
            with self.assertLogs('workshop.email_backend', level='ERROR') as captured:
                send_mail('s', 'b', 'a@b.c', ['owner-private@gmail.com'], fail_silently=True)
        self.assertNotIn('owner-private@gmail.com', '\n'.join(captured.output))


class WhenTheKeyIsMissingTests(SimpleTestCase):

    @override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY='')
    def test_an_unset_key_is_a_configuration_error_not_a_delivery_failure(self):
        """
        Returning 0 here would surface to the owner as "we could not send a
        code" — the same message a provider outage produces — with nothing
        anywhere distinguishing a five-second env-var fix from an incident.
        """
        with self.assertRaises(ImproperlyConfigured):
            send_mail('s', 'b', 'a@b.c', ['o@x.com'], fail_silently=False)

    @override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY='')
    def test_fail_silently_still_suppresses_it(self):
        self.assertEqual(send_mail('s', 'b', 'a@b.c', ['o@x.com'], fail_silently=True), 0)

    @override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY='re_k')
    def test_nothing_is_sent_when_there_is_nothing_to_send(self):
        with patch('urllib.request.urlopen', MagicMock()) as opener:
            from django.core.mail import get_connection
            self.assertEqual(get_connection().send_messages([]), 0)
        opener.assert_not_called()
