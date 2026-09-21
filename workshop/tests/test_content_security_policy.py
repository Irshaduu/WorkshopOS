"""
AUD-0043 — a Content-Security-Policy, limited to the four directives that cannot
break this app. See `workshop.middleware.ContentSecurityPolicyMiddleware`.

Three things are pinned:
  * EVERY kind of response carries it — signed-in, signed-out, the standalone
    printed invoice, an error page, and the two files served from the root;
  * it names NO script, style, image or fetch directive, because the frontend is
    inline by design and photos come from the bucket's own origin — a policy
    that touched either would break pages with nothing in the Django suite able
    to notice;
  * the app still uses nothing the policy refuses, so the day somebody adds an
    <object>, a <base>, an <iframe> of our own page or an off-site form, THIS
    fails instead of the browser silently blocking it.
"""
import re
from datetime import date

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from workshop.middleware import CONTENT_SECURITY_POLICY
from workshop.models import JobCard

EXPECTED = {
    'object-src': ["'none'"],
    'base-uri': ["'self'"],
    'form-action': ["'self'"],
    'frame-ancestors': ["'none'"],
}


def directives(header):
    out = {}
    for part in header.split(';'):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


class EveryResponseCarriesThePolicyTests(TestCase):

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='off', password='pw')
        user.groups.add(office)
        self.signed_in = Client()
        self.signed_in.login(username='off', password='pw')
        self.card = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota',
            model_name='Corolla', registration_number='KL01A0001')

    def test_the_policy_is_exactly_the_four_directives(self):
        self.assertEqual(directives(CONTENT_SECURITY_POLICY), EXPECTED)

    def test_every_kind_of_response_carries_it(self):
        anonymous = Client()
        responses = {
            'signed-out login page': anonymous.get(reverse('login')),
            'signed-in dashboard': self.signed_in.get(reverse('home')),
            'standalone printed invoice': self.signed_in.get(reverse('invoice_view', args=[self.card.pk])),
            'not found': self.signed_in.get('/no-such-page-here/'),
            'robots.txt': anonymous.get(reverse('robots_txt')),
            'sw.js': anonymous.get(reverse('service_worker')),
        }
        for label, response in responses.items():
            with self.subTest(label):
                self.assertIn(response.status_code, (200, 404), label)
                self.assertEqual(response.get('Content-Security-Policy'),
                                 CONTENT_SECURITY_POLICY, label)

    def test_it_does_not_replace_the_old_frame_header(self):
        """Browsers that predate `frame-ancestors` still get X-Frame-Options."""
        response = self.signed_in.get(reverse('home'))
        self.assertEqual(response.get('X-Frame-Options'), 'DENY')


class ItCannotBreakThePagesTests(TestCase):

    def test_it_names_no_script_style_image_or_fetch_directive(self):
        """A `script-src` without 'unsafe-inline' stops every page (the JS is
        inline by design); an `img-src` / `connect-src` that forgot the photo
        bucket breaks photos with no error; `default-src` would do both. None may
        be added here without first moving the inline JS out."""
        named = set(directives(CONTENT_SECURITY_POLICY))
        forbidden = {'default-src', 'script-src', 'script-src-elem', 'script-src-attr',
                     'style-src', 'style-src-elem', 'style-src-attr', 'img-src',
                     'connect-src', 'font-src', 'media-src', 'worker-src',
                     'manifest-src', 'frame-src', 'child-src'}
        self.assertEqual(named & forbidden, set())

    def _sources(self):
        base = settings.BASE_DIR
        # Our own JS lives in `workshop/static/js/` as well as the top-level
        # `static/` — both, or a script creating an <iframe> goes unseen.
        roots = [base / 'workshop' / 'templates', base / 'inventory' / 'templates',
                 base / 'templates', base / 'static',
                 base / 'workshop' / 'static', base / 'inventory' / 'static']
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob('*'):
                rel = path.relative_to(base).as_posix()
                if path.suffix not in ('.html', '.js') or 'static/vendor/' in rel:
                    continue
                yield rel, path.read_text(encoding='utf-8', errors='replace')

    def test_the_scan_reads_the_templates(self):
        self.assertGreater(len(list(self._sources())), 80)

    def test_nothing_the_policy_refuses_is_used(self):
        refused = [
            # the tag name must END where a real tag's does — `<object-fit>`
            # or a custom `<base-row>` is not one of these
            (re.compile(r'<\s*(object|embed)[\s/>]', re.I), "object-src 'none'"),
            (re.compile(r'<\s*base[\s/>]', re.I), "base-uri 'self'"),
            (re.compile(r'<\s*iframe[\s/>]', re.I), "frame-ancestors 'none' (if it frames our own page)"),
            (re.compile(r'createElement\(\s*[\'"](object|embed|base|iframe)[\'"]', re.I), 'the directive for that element'),
            (re.compile(r'\b(form)?action\s*=\s*["\']https?://', re.I), "form-action 'self'"),
        ]
        offenders = []
        for rel, text in self._sources():
            for pattern, directive in refused:
                for m in pattern.finditer(text):
                    line = text.count('\n', 0, m.start()) + 1
                    offenders.append('%s:%d refused by %s' % (rel, line, directive))
        self.assertEqual(offenders, [], 'change the policy (and its docstring) first')
