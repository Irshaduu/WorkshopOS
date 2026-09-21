"""
AUD-0107 — ONE answer to "what is the visitor's IP?", and it is the right one
behind Railway's proxy. See `workshop/client_ip.py` for the measurement.

The shape measured on the Railway test host (2026-09-21):
REMOTE_ADDR = 100.64.0.13 (Railway's internal proxy), X-Forwarded-For = the
real visitor, and a faked X-Forwarded-For did not survive.
"""
import io
import ipaddress
import re

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from workshop.auth_views import check_ip_lockout, get_client_ip
from workshop.client_ip import client_ip
from workshop.models import FailedAttempt, UserSession

PROXY = '100.64.0.13'          # what Railway hands the app as REMOTE_ADDR
VISITOR = '157.51.207.147'     # the visitor, in the header Railway sets
OTHER_VISITOR = '157.51.213.197'


def request_from(remote, forwarded=None):
    extra = {'REMOTE_ADDR': remote}
    if forwarded is not None:
        extra['HTTP_X_FORWARDED_FOR'] = forwarded
    return RequestFactory().get('/', **extra)


class OneRuleForTheVisitorsIPTests(TestCase):

    def test_behind_railways_proxy_it_is_the_visitor_not_the_proxy(self):
        self.assertEqual(client_ip(request_from(PROXY, VISITOR)), VISITOR)

    def test_it_takes_the_first_value_of_a_chain(self):
        self.assertEqual(client_ip(request_from(PROXY, '%s, 100.64.0.2' % VISITOR)), VISITOR)

    def test_a_DIRECT_connection_cannot_choose_its_own_address(self):
        """A public REMOTE_ADDR has no proxy in front of it, so its header is
        whatever the visitor typed — the old spoof-protection, kept here."""
        self.assertEqual(client_ip(request_from(VISITOR, '1.2.3.4')), VISITOR)

    def test_no_header_means_the_connection_itself(self):
        self.assertEqual(client_ip(request_from('127.0.0.1')), '127.0.0.1')

    def test_an_unreadable_header_never_reaches_the_database(self):
        """`unknown` and `host:port` are refused by Postgres's inet column, and
        the raise is inside the session middleware — every page would 500."""
        for junk in ('unknown', '1.2.3.4:5678', '', ' , ', '<script>'):
            with self.subTest(header=junk):
                self.assertEqual(client_ip(request_from(PROXY, junk)), PROXY)

    def test_nothing_usable_at_all_is_a_valid_placeholder(self):
        value = client_ip(request_from('not-an-ip'))
        self.assertEqual(value, '0.0.0.0')
        ipaddress.ip_address(value)

    def test_sign_in_reads_the_same_rule(self):
        request = request_from(PROXY, VISITOR)
        self.assertEqual(get_client_ip(request), client_ip(request))


class TheLockoutCountsVisitorsNotTheProxyTests(TestCase):
    """The defect this exists for: with REMOTE_ADDR as the key, every visitor
    arriving through Railway's proxy shared ONE failure counter, so 20 wrong
    passwords from any of them would lock out all the others for 15 minutes."""

    def test_one_visitors_failures_do_not_lock_out_another(self):
        FailedAttempt.objects.create(ip_address=VISITOR, failures=20)
        self.assertTrue(check_ip_lockout(request_from(PROXY, VISITOR)))
        self.assertFalse(check_ip_lockout(request_from(PROXY, OTHER_VISITOR)))

    def test_a_wrong_password_is_counted_against_the_visitor(self):
        self.client.post(reverse('login'), {'username': 'nobody-here', 'password': 'x'},
                         REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR=VISITOR)
        self.assertEqual(FailedAttempt.objects.get(ip_address=VISITOR).failures, 1)
        self.assertFalse(FailedAttempt.objects.filter(ip_address=PROXY).exists())


class TheSessionListRecordsTheVisitorTests(TestCase):

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user('ip-office', password='pw')
        user.groups.add(office)
        self.client.force_login(user)

    def test_it_records_the_visitor(self):
        self.client.get(reverse('home'), REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR=VISITOR)
        self.assertEqual(UserSession.objects.get().ip_address, VISITOR)

    def test_a_junk_header_still_stores_a_real_address(self):
        response = self.client.get(reverse('home'), REMOTE_ADDR=PROXY,
                                   HTTP_X_FORWARDED_FOR='unknown')
        self.assertEqual(response.status_code, 200)
        stored = UserSession.objects.get().ip_address
        ipaddress.ip_address(stored)          # SQLite would accept anything; Postgres would not
        self.assertEqual(stored, PROXY)


class NobodyReadsTheHeadersButTheOneRuleTests(TestCase):
    """Two answers to one question is how this went wrong. A third copy is
    invisible to every other kind of test, so this scans for it."""

    ALLOWED = {'workshop/client_ip.py'}
    READS = re.compile(r"""['"](REMOTE_ADDR|HTTP_X_FORWARDED_FOR|HTTP_X_REAL_IP)['"]""")

    def _app_files(self):
        base = settings.BASE_DIR
        for app in ('workshop', 'inventory', 'formulad_workshop'):
            for path in (base / app).rglob('*.py'):
                rel = path.relative_to(base).as_posix()
                if '/tests' in rel or rel.rsplit('/', 1)[-1].startswith('test') or '/migrations/' in rel:
                    continue
                yield rel, io.open(path, encoding='utf-8').read()

    def test_the_scan_actually_reads_the_codebase(self):
        files = dict(self._app_files())
        self.assertGreater(len(files), 40, 'the scan read almost nothing - check BASE_DIR')
        self.assertIn('workshop/client_ip.py', files)

    def test_only_client_ip_reads_the_address_headers(self):
        offenders = ['%s:%d' % (rel, text.count('\n', 0, m.start()) + 1)
                     for rel, text in self._app_files() if rel not in self.ALLOWED
                     for m in self.READS.finditer(text)]
        self.assertEqual(offenders, [], 'read the IP through workshop.client_ip.client_ip()')
