"""
The phone tab bar (2026-09-24): one lit tab, a capsule that glides, labels
that can be read, and a tap that is answered before the server is.

Nothing in the Django suite executes CSS or JavaScript, so most of this reads
the declarations — the level the drift happens at. The one thing it CAN check
by rendering is also the one the glide depends on: exactly one tab is lit on
every page, for every role, because two elements carrying the view transition's
name make the browser skip the transition outright. It found Floor's Inventory
pages lighting two tabs at once — Inventory, and Menu beside it.
"""
import re

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

BASE = 'workshop/templates/workshop/base.html'
SCRIPT = 'workshop/static/js/script.js'


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def css_only(source):
    """base.html with template comments, template tags and CSS comments taken
    out, so braces inside prose cannot be counted as CSS blocks."""
    source = re.sub(r'{% comment %}.*?{% endcomment %}', '', source, flags=re.S)
    source = re.sub(r'{%.*?%}|{#.*?#}|{{.*?}}', '', source, flags=re.S)
    return re.sub(r'/\*.*?\*/', '', source, flags=re.S)


def block_after(source, opener):
    """The body of the CSS block whose header matches the regex `opener`
    (ending at its `{`), braces balanced."""
    start = re.search(opener, source).end()
    depth, i = 1, start
    while depth:
        depth += {'{': 1, '}': -1}.get(source[i], 0)
        i += 1
    return source[start:i - 1]


def lit_tabs(html):
    """Labels of the tabs lit in the rendered bar."""
    nav = html.split('<nav class="navbar-top', 1)[1].split('</nav>', 1)[0]
    lit = []
    for match in re.finditer(r'class="(nav-btn[^"]*)"(.*?)</(?:a|button)>', nav, re.S):
        classes = match.group(1).split()
        if 'active' in classes or 'is-open' in classes:
            label = re.search(r'class="nav-label">([^<]*)<', match.group(2))
            lit.append(label.group(1).strip() if label else match.group(1))
    return lit


def contrast(fg, bg):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def lum(rgb):
        r, g, b = (lin(v) for v in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def hex_rgb(value):
    value = value.lstrip('#')
    return [int(value[i:i + 2], 16) for i in (0, 2, 4)]


class ExactlyOneTabIsLitTests(TestCase):
    """
    Every page each role can reach from its own bar and its own drawer, read
    as that role: never more than one lit tab. Scraped rather than listed, so
    a drawer entry added later is covered with nothing to remember.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.users = {}
        for role in ('Owner', 'Office', 'Floor'):
            user = User.objects.create_user('tabs_' + role.lower(), password='pw',
                                            is_superuser=(role == 'Owner'))
            user.groups.add(Group.objects.get(name=role))
            self.users[role] = user

    def destinations(self, html):
        nav = html.split('<nav class="navbar-top', 1)[1].split('</nav>', 1)[0]
        drawer = html.split('id="appDrawer"', 1)[-1]
        hrefs = set(re.findall(r'<a class="nav-btn[^"]*"\s+href="(/[^"]*)"', nav))
        hrefs |= set(re.findall(r'<a class="drawer-link[^"]*"\s+href="(/[^"]*)"', drawer))
        return hrefs

    def test_no_page_lights_two_tabs_for_any_role(self):
        for role, user in self.users.items():
            self.client.force_login(user)
            home = self.client.get(reverse('home')).content.decode()
            hrefs = self.destinations(home)
            self.assertGreater(len(hrefs), 3, 'no destinations found for %s — has base.html changed shape?' % role)
            for href in sorted(hrefs):
                response = self.client.get(href)
                if response.status_code != 200 or b'<nav class="navbar-top' not in response.content:
                    continue
                with self.subTest(role=role, href=href):
                    self.assertLessEqual(len(lit_tabs(response.content.decode())), 1,
                                         '%s lights %s' % (href, lit_tabs(response.content.decode())))

    def test_floors_inventory_pages_light_inventory_and_not_menu(self):
        """The defect this found: `/inventory/` is a drawer section (for Owner
        and Office, who have no Inventory tab), so Floor's Menu lit beside
        the Inventory tab that already owns the page."""
        self.client.force_login(self.users['Floor'])
        for url in (reverse('inventory_list'), '/inventory/low-stock/'):
            with self.subTest(url=url):
                self.assertEqual(lit_tabs(self.client.get(url).content.decode()), ['Inventory'])

    def test_for_owner_and_office_inventory_still_lights_manage(self):
        """They reach it through the drawer, so Manage is the right answer."""
        for role in ('Owner', 'Office'):
            self.client.force_login(self.users[role])
            with self.subTest(role=role):
                self.assertEqual(lit_tabs(self.client.get(reverse('inventory_list')).content.decode()),
                                 ['Manage'])

    def test_the_lit_inventory_glyph_is_the_filled_one(self):
        """Every other lit tab swaps to its `-fill` glyph; Inventory did not."""
        self.client.force_login(self.users['Floor'])
        self.assertIn('bi-box-seam-fill', self.client.get(reverse('inventory_list')).content.decode())
        self.assertNotIn('bi-box-seam-fill', self.client.get(reverse('home')).content.decode())


class TheGlideIsTheCapsuleAndNothingElseTests(TestCase):
    """The cross-document view transition must move the capsule and leave
    every other thing about how a page arrives exactly as it was."""

    def setUp(self):
        self.css = css_only(read(BASE))
        self.block = block_after(
            self.css, r'@media \(max-width: 640px\) and \(prefers-reduced-motion: no-preference\) \{')

    def test_it_is_phone_only_and_never_under_reduced_motion(self):
        self.assertIn('@view-transition { navigation: auto; }', self.block)
        self.assertEqual(self.css.count('@view-transition'), 1,
                         'a second opt-in outside the phone/motion block')

    def test_the_root_is_not_captured(self):
        """Without this every navigation in the app cross-fades the page."""
        self.assertIn(':root { view-transition-name: none; }', self.block)

    def test_the_overlay_never_swallows_a_tap(self):
        self.assertIn('::view-transition { pointer-events: none; }', self.block)

    def test_only_the_lit_glyph_carries_the_name(self):
        names = re.findall(r'view-transition-name:\s*([\w-]+)', self.css)
        self.assertEqual(sorted(names), ['nav-current', 'none'])
        rule = re.search(r'([^{}]+)\{\s*view-transition-name:\s*nav-current', self.block).group(1)
        selectors = sorted(s.strip() for s in rule.split(','))
        self.assertEqual(selectors, ['.nav-btn--menu.is-open i', '.nav-btn.active i'])


class HoverOnlyWhereThereIsAPointerTests(TestCase):

    def test_every_nav_hover_rule_is_behind_a_hover_query(self):
        """On a touch screen `:hover` sticks to the last tap — the Manage tab
        kept its wash after the drawer closed."""
        css = css_only(read(BASE))
        hovers = [m.start() for m in re.finditer(r'\.nav-btn:hover', css)]
        self.assertGreaterEqual(len(hovers), 2, 'hover rules not found — has base.html changed shape?')
        for at in hovers:
            opener = css.rfind('@media (hover: hover)', 0, at)
            self.assertNotEqual(opener, -1, 'a .nav-btn:hover rule outside any hover query')
            between = css[opener:at]
            self.assertGreater(between.count('{') - between.count('}'), 0,
                               'a .nav-btn:hover rule outside its hover query')


class ThePhoneLabelsCanBeReadTests(TestCase):
    """Labels are ~10.5px — under the large-text threshold, so 4.5:1."""

    def setUp(self):
        self.css = css_only(read(BASE))
        # The phone block is the one that opens by redefining :root.
        self.phone = block_after(self.css, r'@media \(max-width: 640px\) \{(?=\s*:root \{)')
        pill = block_after(self.phone, r'\.navbar-container \{')
        self.pill = hex_rgb(re.search(r'background:\s*(#[0-9a-fA-F]{6});', pill).group(1))

    def test_an_unlit_label_clears_4_5_on_the_pill(self):
        nav_btn = block_after(self.phone, r'(?<![\w-])\.nav-btn \{')
        label = hex_rgb(re.search(r'color:\s*(#[0-9a-fA-F]{6});', nav_btn).group(1))
        self.assertGreaterEqual(contrast(label, self.pill), 4.5)

    def test_the_lit_label_clears_4_5_on_the_pill(self):
        lit = block_after(self.phone, r'\.nav-btn--menu\.is-open \.nav-label \{')
        self.assertGreaterEqual(contrast(hex_rgb(re.search(r'color:\s*(#[0-9a-fA-F]{6});', lit).group(1)),
                                         self.pill), 4.5)

    def test_a_label_has_room_for_its_descenders(self):
        """The label clips its overflow for the ellipsis, so at the bar's own
        `line-height: 1` its box was exactly one em tall and cut the g of
        "Manage" and the p of "Completed" off at the baseline."""
        label = block_after(self.phone, r'\.nav-btn \.nav-label \{')
        self.assertIn('overflow: hidden', label)
        line_height = float(re.search(r'line-height:\s*([0-9.]+);', label).group(1))
        self.assertGreaterEqual(line_height, 1.2)

    def test_the_lit_glyph_clears_4_5_on_its_capsule_and_the_capsule_stands_off_the_pill(self):
        """The capsule is a state indicator, so it needs 3:1 against the pill
        it sits on (WCAG 1.4.11) as well as 4.5:1 for the glyph inside it."""
        lit = re.search(r'\.nav-btn--menu\.is-open i \{\s*background-color:\s*(#[0-9a-fA-F]{6});\s*'
                        r'color:\s*(#[0-9a-fA-F]{6});', self.phone)
        self.assertIsNotNone(lit, 'the capsule rule changed shape')
        capsule, glyph = hex_rgb(lit.group(1)), hex_rgb(lit.group(2))
        self.assertGreaterEqual(contrast(glyph, capsule), 4.5)
        self.assertGreaterEqual(contrast(capsule, self.pill), 3)

    def test_no_state_turns_a_label_white_on_the_white_pill(self):
        """The laptop rules set the label WHITE for hover, focus and a pending
        tap — right on the blue bar, invisible on this one. Each of the three
        must restate its colour inside the phone block."""
        for opener in (r'(?<![\w-])\.nav-btn\.is-pending \{',
                       r'(?<![\w-])\.nav-btn:hover \{',
                       r'(?<![\w-])\.nav-btn:focus-visible \{'):
            with self.subTest(state=opener):
                rule = block_after(self.phone, opener)
                colour = re.search(r'(?<![\w-])color:\s*(#[0-9a-fA-F]{6});', rule)
                self.assertIsNotNone(colour, 'this state keeps the laptop bar\'s white label')
                self.assertGreaterEqual(contrast(hex_rgb(colour.group(1)), self.pill), 4.5)


class ThePhoneBarSpeaksTheAppsOwnSelectedStateTests(TestCase):
    """The owner's call (2026-09-24): a white bar, with the current tab in the
    black the dashboard's mechanic filter already uses for "selected"."""

    def setUp(self):
        self.phone = block_after(css_only(read(BASE)), r'@media \(max-width: 640px\) \{(?=\s*:root \{)')
        self.pill = block_after(self.phone, r'\.navbar-container \{')

    def test_the_capsule_is_the_dashboard_filters_own_black(self):
        dashboard = read('workshop/templates/workshop/dashboard/dashboard_home.html')
        chip = re.search(r'--pit-track:\s*(#[0-9a-fA-F]{6})', dashboard).group(1)
        self.assertIn('.pit-crew-chip.is-active', dashboard)
        self.assertIn('var(--tint, var(--pit-track))', dashboard,
                      'the dashboard chip no longer fills with --pit-track')
        capsule = re.search(r'\.nav-btn\.active i,\s*\.nav-btn--menu\.is-open i \{\s*'
                            r'background-color:\s*(#[0-9a-fA-F]{6});', self.phone).group(1)
        self.assertEqual(capsule.lower(), chip.lower())

    def test_the_pill_is_one_solid_fill(self):
        """A gradient repeats under its border unless told otherwise — the
        black line and light blue line the owner spotted on the blue pill. A
        solid fill has no seam to show, and the pill is one."""
        self.assertNotIn('gradient', self.pill)
        self.assertRegex(self.pill, r'background:\s*#ffffff;')

    def test_the_badge_is_ringed_in_the_pills_own_colour(self):
        """So the count reads as cut out of the bar rather than stuck on it."""
        fill = re.search(r'background:\s*(#[0-9a-fA-F]{6});', self.pill).group(1)
        badge = block_after(self.phone, r'\.nav-btn--bell \.nav-badge \{')
        self.assertIn('box-shadow: 0 0 0 2px %s;' % fill, badge)

    def test_the_laptop_bar_keeps_its_gradient(self):
        """Phone only: the change must not reach the bar above 640px."""
        css = css_only(read(BASE))
        top = block_after(css, r'(?<![\w-])\.navbar-top \{')
        self.assertIn('linear-gradient(90deg, var(--nav-blue-1)', top)


class TheTapIsAnsweredTests(TestCase):
    """The script and the stylesheet must agree on the one class they share,
    and the tab must be marked by the same click the progress bar trusts."""

    def test_the_script_marks_a_tapped_tab_that_the_stylesheet_draws(self):
        script = read(SCRIPT)
        self.assertIn("link.classList.add('is-pending')", script)
        self.assertIn('.nav-btn.is-pending', read(BASE))

    def test_it_rides_on_the_progress_bars_own_guards(self):
        handler = read(SCRIPT).split("document.addEventListener('click', function (e) {", 1)[1]
        handler = handler.split('}, false);', 1)[0]
        self.assertIn('startNavigation();\n        followTab(link);', handler)

    def test_a_leave_prompt_answered_stay_takes_the_mark_back(self):
        script = read(SCRIPT)
        watch = script.split('function watchForStay() {', 1)[1].split('\n    }\n', 1)[0]
        self.assertIn("addEventListener('beforeunload'", watch)
        self.assertIn('e.defaultPrevented', watch)
        self.assertIn('unfollowTab();', watch)
