"""
Template comments must not leak onto the page.

**Django's `{# ... #}` comment is single-line only.** Spread one across two
lines and Django does not treat it as a comment at all — it renders the whole
thing as visible text. On 2026-07-29 ten of them shipped this way and paragraphs
of developer commentary appeared inside the nav bar, the login forms and the
notifications page. Every functional test still passed, because they assert on
specific strings and status codes; nothing was looking at what the page actually
said.

So this file checks the source, not a rendered page: a static scan catches the
mistake in any template, including ones with no view test of their own, and does
it without needing a request, a user, or a fixture.

Use `{% comment %} ... {% endcomment %}` for anything spanning lines.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

# `{#` that is not closed by `#}` before the end of its own line.
UNCLOSED_COMMENT = re.compile(r'\{#(?![^\n]*#\})')


#: Not just `*.html`. `sw.js` and `robots.txt` are Django templates too — they
#: are rendered by views so they can carry `{% static %}` URLs — and a leaked
#: `{#` in the service worker is worse than one on a page: it is a syntax error
#: in JavaScript, so the worker fails to install and push stops working
#: silently. Scanning every file under a templates/ root costs nothing and
#: removes the question of which extensions are "real" templates.
TEMPLATE_SUFFIXES = ('.html', '.js', '.txt', '.svg', '.xml')


def _template_files():
    roots = [Path(settings.BASE_DIR) / 'templates']
    for app in ('workshop', 'inventory'):
        roots.append(Path(settings.BASE_DIR) / app / 'templates')
    for root in roots:
        if root.exists():
            yield from sorted(
                path for path in root.rglob('*')
                if path.is_file() and path.suffix in TEMPLATE_SUFFIXES
            )


#: A template that renders its own messages block. `{% if messages %}` is the
#: only way to open one, so matching it finds every case.
RENDERS_MESSAGES = re.compile(r'\{%\s*if\s+messages\s*%\}')

#: A template that inherits `base.html`'s chrome — and therefore its single
#: messages banner.
EXTENDS_BASE = re.compile(r"\{%\s*extends\s+['\"]workshop/base\.html['\"]\s*%\}")


class MessagesAreRenderedOnceTests(TestCase):
    """
    `base.html` renders Django messages once, for every page that extends it. A
    child template that renders them *again* prints every message twice — and in
    practice does it with its own ad-hoc styling, which is the part that bites:
    both offenders found on 2026-08-10 mapped every tag that was not `warning`
    (or not `error`) onto **success**, so a failure to save a job card appeared
    as a green tick, and the rename/merge warnings on Data Cleanup appeared as
    confirmations. Neither carried `data-sound-tag`, so the duplicate was also
    silent while the real banner played the tone.

    The rule was documented in `CLAUDE.md` from the beginning and nothing
    enforced it, which is exactly how two templates drifted out of it. A static
    scan covers all ~102 templates, including the many with no view test.

    The two printed documents are the deliberate exception: `invoice_template.html`
    and `estimate_print.html` are standalone (they do not extend `base.html`), so
    rendering their own messages is the only way they can show one at all — which
    matters, because the invoice is the screen where money is actually settled.
    They are excluded by the `extends` check rather than by name, so a third
    standalone template needs no change here.
    """

    def test_no_template_extending_base_renders_its_own_messages(self):
        offenders = []
        for path in _template_files():
            if path.suffix != '.html':
                continue
            source = path.read_text(encoding='utf-8', errors='replace')
            if EXTENDS_BASE.search(source) and RENDERS_MESSAGES.search(source):
                line = source[:RENDERS_MESSAGES.search(source).start()].count('\n') + 1
                offenders.append(f'{path.name}:{line}')

        self.assertEqual(
            offenders, [],
            "These templates extend base.html and also render {% if messages %}, "
            "so every message prints twice and the duplicate loses its "
            "error/success styling and its sound tag. Delete the block — "
            "base.html already renders it: " + ', '.join(offenders)
        )


class TemplateCommentSyntaxTests(TestCase):
    def test_no_multiline_hash_comments(self):
        """
        The bug this exists to prevent: `{#` opened on one line and `#}` closed
        on another renders as visible page text instead of disappearing.
        """
        offenders = []

        for path in _template_files():
            text = path.read_text(encoding='utf-8')
            for lineno, line in enumerate(text.splitlines(), start=1):
                if UNCLOSED_COMMENT.search(line):
                    rel = path.relative_to(settings.BASE_DIR)
                    offenders.append(f"{rel}:{lineno}: {line.strip()[:70]}")

        self.assertEqual(
            offenders, [],
            "Multi-line {# #} comments render as visible text — Django's hash "
            "comment is single-line only. Use {% comment %}...{% endcomment %} "
            "instead:\n  " + "\n  ".join(offenders),
        )

    def test_no_stray_comment_markers_in_rendered_pages(self):
        """
        A second net, from the reader's side: fetch the pages an unauthenticated
        visitor can reach and confirm no comment syntax survives into the HTML.
        """
        from django.urls import reverse

        for name in ('login', 'owner_forgot_password'):
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                for marker in ('{#', '#}', '{% comment %}', '{% endcomment %}'):
                    self.assertNotIn(
                        marker, html,
                        f"{marker!r} leaked into the rendered {name} page",
                    )
