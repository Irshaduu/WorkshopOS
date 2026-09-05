"""
One back-navigation system.

The installed app declares `"display": "standalone"` (`static/manifest.json`),
so it carries no address bar and no browser Back button. A phone still has a
system back gesture; a laptop has nothing at all. Two consequences, and this
file guards both.

**Every page carries its own way out, and they all look the same.** Seventeen
back controls had drifted into two placements and seven treatments — nine
copies of a 40px round icon button (six byte-identical `.btn-round` blocks, three
more rebuilding the same geometry out of Bootstrap classes and inline styles) and
eight text links across four near-identical bespoke declarations. They are one
`.pg-back` component in `static/css/style.css` now. Nothing in the Django suite
executes CSS, so the shared declaration is asserted as a string; what these tests
really protect is that no page goes back to rolling its own.

**`/spare-shops/<pk>/print/` had no way out at all.** It rendered zero anchors —
the only true dead end in the app — so in the installed app the page was a trap.
It is one of three standalone templates (with the printed invoice and the printed
estimate) that extend no base and therefore carry no nav bar, and all three now
answer the same way: an optional `?back=` for the screen you came from, and a
named fallback that is always correct when there is none.

⚠ The rule these tests exist for is that a back control here is a NAMED
DESTINATION, never `history.back()`. `start_url` is "/", so on the first tap of
a session `history.length` is 1 and a history button does nothing — and a
control that sometimes does nothing is worse than no control. A named
destination also survives arriving from a notification or a bookmark, which is
how these pages are actually reached.
"""

import re
from html import unescape
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from workshop.models import SpareShop


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


#: Every treatment the `.pg-back` component replaced. Each was a real control on
#: a real page; any of them reappearing means the app has started keeping two
#: answers to one question again.
RETIRED = {
    'btn-round': 'the 40px round icon button — now `.pg-back` in style.css',
    'ua-back': 'the Unassigned Hub\'s own copy',
    'si-back': 'the item-suppliers copy',
    'sa-back': 'the salary staff-detail copy',
    'javascript:history.back': 'a history jump — see the module docstring',
}


class ThereIsOneBackControlTests(TestCase):
    """The look is shared, and the retired treatments stay retired."""

    def test_no_template_rolls_its_own_back_control(self):
        offenders = []
        for path in _template_files():
            text = path.read_text(encoding='utf-8', errors='replace')
            for needle, why in RETIRED.items():
                if needle in text:
                    offenders.append(f'{path.name}: {needle!r} — {why}')
        self.assertEqual(
            offenders, [],
            'A retired back treatment came back. Use `.pg-back` (declared once '
            'in static/css/style.css) instead:\n  ' + '\n  '.join(offenders))

    def test_the_component_is_declared_exactly_once_and_in_the_shared_sheet(self):
        """
        `base.html` links `static/css/style.css` on every page, which is what
        makes one declaration reach ~23 templates. A second copy in a page's own
        `<style>` is how `.btn-round` came to exist six times.
        """
        shared = (Path(settings.BASE_DIR) / 'static' / 'css' / 'style.css').read_text(
            encoding='utf-8', errors='replace')
        self.assertIn('.pg-back {', shared)

        elsewhere = [
            path.name for path in _template_files()
            if '.pg-back {' in path.read_text(encoding='utf-8', errors='replace')
        ]
        self.assertEqual(
            elsewhere, [],
            '`.pg-back` is declared in a template as well as in style.css — '
            'two copies of one control is what this component replaced: '
            + ', '.join(elsewhere))

    def test_it_is_a_real_target_on_a_finger_and_never_animates_its_own_colour(self):
        """
        38px on a pointer and 44px under `(hover: none)` — keyed on input method
        rather than a width breakpoint, because the Floor tablet is wider than
        plenty of laptops. Asserted here because nothing in this suite paints a
        frame.
        """
        shared = (Path(settings.BASE_DIR) / 'static' / 'css' / 'style.css').read_text(
            encoding='utf-8', errors='replace')
        block = shared[shared.index('.pg-back {'):]
        self.assertIn('min-height: 38px', block[:block.index('}')])
        touch = re.search(
            r'@media \(hover: none\) \{\s*\.pg-back \{ min-height: 44px; \}', shared)
        self.assertIsNotNone(
            touch, 'the touch target must grow to 44px under `(hover: none)`')


class EveryStandaloneSheetHasAWayOutTests(TestCase):
    """
    The three templates that extend no base — so no nav bar, no drawer, and in
    the installed app no browser chrome either.
    """

    STANDALONE = [
        'workshop/templates/workshop/invoice/invoice_template.html',
        'workshop/templates/workshop/estimate/estimate_print.html',
        'workshop/templates/workshop/spare_shops/shop_print.html',
    ]

    def test_each_one_offers_an_exit_whether_or_not_it_was_given_a_back(self):
        """
        The `{% else %}` is the point. `?back=` is absent on a cold arrival —
        a bookmark, a notification, a redirect after settling — and that is
        exactly when the page must not be a trap.
        """
        for rel in self.STANDALONE:
            with self.subTest(sheet=rel):
                text = (Path(settings.BASE_DIR) / rel).read_text(
                    encoding='utf-8', errors='replace')
                self.assertIn('{% if back_url %}', text)
                self.assertIn('{% else %}', text[text.index('{% if back_url %}'):])

    def test_the_print_sheet_uses_no_icon_font(self):
        """
        These templates load nothing from anywhere — not even from our own
        origin — so a `<i class="bi ...">` renders an empty box. The arrow has
        to be an inline SVG.
        """
        text = (Path(settings.BASE_DIR) / self.STANDALONE[2]).read_text(
            encoding='utf-8', errors='replace')
        bar = text[text.index('<div class="bar no-print">'):]
        bar = bar[:bar.index('</div>')]
        self.assertNotIn('class="bi ', bar)
        self.assertIn('<svg', bar)


class TheSpareShopReportIsNoLongerADeadEndTests(TestCase):
    """
    It rendered ZERO anchors — measured, not inferred. In a browser tab the
    address bar rescued it; in the installed app there was nothing at all.
    """

    def setUp(self):
        self.owner = User.objects.create_superuser(
            'owner-back', 'owner-back@example.com', 'pw')
        self.shop = SpareShop.objects.create(name='Backtest Spares')
        self.client.force_login(self.owner)
        self.url = reverse('spare_shop_print', args=[self.shop.pk])

    def _anchors(self, response):
        """
        Unescaped, because a real href renders `&` as `&amp;` and this is
        comparing destinations, not markup.
        """
        body = response.content.decode()
        return [unescape(href)
                for href in re.findall(r'<a [^>]*href="([^"#][^"]*)"', body)]

    def test_it_carries_an_exit_with_no_back_supplied(self):
        anchors = self._anchors(self.client.get(self.url))
        self.assertEqual(
            anchors, [reverse('spare_shop_detail', args=[self.shop.pk])],
            'a cold arrival must still land on the shop this report is about')

    def test_a_supplied_back_carries_the_filter_state_home(self):
        """
        The shop's page links here with its sort, its window and its custom
        dates attached. Returning to a bare unfiltered ledger after reading a
        filtered report is its own small defeat, so `?back=` is not carrying the
        destination — it is carrying the FILTER.
        """
        came_from = '/spare-shops/%d/?filter=this_month&sort_by=ordered' % self.shop.pk
        anchors = self._anchors(self.client.get(self.url, {'back': came_from}))
        self.assertEqual(anchors, [came_from])

    def test_a_crafted_back_is_refused_and_the_page_still_has_a_way_out(self):
        """
        The value lands in an `href`, so an unchecked one puts another origin —
        or `javascript:` — behind a button wearing this app's own styling.
        `return_to.safe_return` refuses it, and the fallback still renders: a
        refused parameter must never cost the page its only exit.
        """
        for hostile in ('https://evil.example.com/harvest',
                        'javascript:alert(1)',
                        '//evil.example.com'):
            with self.subTest(back=hostile):
                response = self.client.get(self.url, {'back': hostile})
                self.assertNotContains(response, 'evil.example.com')
                self.assertNotContains(response, 'javascript:alert')
                self.assertEqual(
                    self._anchors(response),
                    [reverse('spare_shop_detail', args=[self.shop.pk])])

    def test_the_link_that_opens_it_hands_over_the_filter(self):
        """The other half — a `?back=` nothing sends is a column nothing reads."""
        detail = self.client.get(
            reverse('spare_shop_detail', args=[self.shop.pk]),
            {'filter': 'this_month', 'sort_by': 'ordered'})
        self.assertContains(detail, 'back=')


class CancelIsANamedDestinationTests(TestCase):
    """
    The four master-list forms cancelled with `javascript:history.back()`. Each
    has exactly one caller, so a named URL was always available — and it is
    strictly better: it survives an empty history, and it is not the one thing
    on these pages a CSP would break.

    ⚠ They keep the word **Cancel** and do NOT take `.pg-back`. Cancel-beside-Save
    in a form footer is a different control from a page's back affordance, and
    collapsing the two would put a "back" pill inside a button group.
    """

    FORMS = [
        ('brand_add', 'brand_list'),
        ('spare_add', 'spare_list'),
        ('concern_add', 'concern_list'),
    ]

    def setUp(self):
        self.owner = User.objects.create_superuser(
            'owner-cancel', 'owner-cancel@example.com', 'pw')
        self.client.force_login(self.owner)

    def test_cancel_names_the_list_it_came_from(self):
        for form_name, list_name in self.FORMS:
            with self.subTest(form=form_name):
                html = self.client.get(reverse(form_name)).content.decode()
                self.assertIn(
                    '<a href="%s" class="btn btn-outline-secondary py-2">Cancel</a>'
                    % reverse(list_name), html)

    def test_a_model_form_cancels_to_its_own_brand(self):
        """
        The one that needed a view change: `model_edit` rendered without the
        brand in context, so it had nothing to build a URL from. Toyota's models
        and another make's are different lists.
        """
        from workshop.models import CarBrand, CarModel
        brand = CarBrand.objects.create(name='Backtest Marque')
        model = CarModel.objects.create(brand=brand, name='Backtest Model')

        expected = reverse('brand_model_list', args=[brand.pk])
        for url in (reverse('model_add', args=[brand.pk]),
                    reverse('model_edit', args=[model.pk])):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn(
                    '<a href="%s" class="btn btn-outline-secondary py-2">Cancel</a>'
                    % expected, html)
