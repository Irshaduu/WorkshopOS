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


def _template_files():
    roots = [Path(settings.BASE_DIR) / 'templates']
    for app in ('workshop', 'inventory'):
        roots.append(Path(settings.BASE_DIR) / app / 'templates')
    for root in roots:
        if root.exists():
            yield from sorted(root.rglob('*.html'))


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

        for name in ('login', 'admin_login', 'owner_forgot_password'):
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                for marker in ('{#', '#}', '{% comment %}', '{% endcomment %}'):
                    self.assertNotIn(
                        marker, html,
                        f"{marker!r} leaked into the rendered {name} page",
                    )
