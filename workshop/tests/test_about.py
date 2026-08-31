"""
The About page — an owner-only tour of what is in the system.

Four rules worth pinning, because each was a decision rather than a default:
the page is Owner-only, it carries no links at all, its map is the GENERATED
partial rather than a pasted copy, and its drawer entry is last and appears
for nobody but an owner.
"""

import re

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse


class AboutPageTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user('owner', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))

        self.office = User.objects.create_user('office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))

        self.floor = User.objects.create_user('floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))

        self.url = reverse('about')

    # -- who may open it --------------------------------------------------

    def test_an_owner_can_read_it(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_office_and_floor_are_refused(self):
        """
        403, not a redirect. A signed-in user who simply lacks the role gets
        PermissionDenied — being bounced to a sign-in form while already
        signed in is the defect the RBAC decorators were fixed to stop.
        """
        for user in (self.office, self.floor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_a_signed_out_visitor_is_sent_to_the_one_door(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/login/'))

    # -- what is on it ----------------------------------------------------

    def test_the_map_is_the_generated_partial_not_a_pasted_copy(self):
        """
        The header draws scratchpad/build_system_map.py's own output, so the
        page and the printed A4 sheet cannot disagree. Asserted on the
        generated file's banner, which a hand-pasted <svg> would not carry.
        """
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()

        self.assertIn('<svg viewBox="0 0 1414 1000"', html)
        # Cards only the generator writes — proof this is the real drawing.
        for title in ('SETTLEMENT CHECK', 'AVERAGE COST', 'DELETE WINDOW'):
            self.assertIn(title, html)

    def test_it_carries_no_links_at_all(self):
        """
        "Scroll and read all" — the brief. A page of shortcuts into other
        sections is a second menu, and the drawer this page is opened from
        is already the menu. Scoped to the page's own <section> blocks so
        base.html's nav and drawer are not counted.
        """
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()

        body = ''.join(re.findall(r'<section class="ab-fam".*?</section>',
                                  html, re.S))
        self.assertTrue(body, 'the About sections did not render')
        self.assertNotIn('<a ', body)
        self.assertNotIn('<button', body)
        self.assertNotIn('<form', body)

    def test_every_family_on_the_page_is_drawn(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()
        for heading in ('The car&rsquo;s day', 'Parts &amp; stock',
                        'Money coming in', 'Money going out',
                        'What you read', 'Names &amp; tidying up',
                        'Getting in', 'What keeps it honest',
                        'The app itself'):
            with self.subTest(heading=heading):
                self.assertIn(heading, html)

    def test_it_covers_every_area_the_map_draws(self):
        """
        The page is the map in words, so a card on the sheet with nothing said
        about it is a gap. Checked on the things easiest to forget — the ones
        added last and the ones that are rules rather than screens.
        """
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()

        for topic in (
            'Estimates',            # the estimate list
            'Job Cards',            # the job card list
            'Owner Withdrawals',    # the newest card on the sheet
            'Categories',           # the generic part name
            'Shop catalogue',       # what each shop sells
            'staff roster',         # the Mechanic model
            'Master lists',
            'Devices',              # sessions
            'Control Hub',
            'Forgotten password',
            'seven-day window',
            'Stock History',
            'Average cost',
            'Low Stock',
            'Backups',
            'Email',
        ):
            with self.subTest(topic=topic):
                self.assertIn(topic, html)

    def test_it_does_not_use_the_owner_s_own_names_for_the_system(self):
        """
        "WorkshopOS" and "Titan" are the owner's personal names for it and are
        deliberately kept out of the page's own prose. The nav wordmark in
        base.html is separate and untouched, so this is scoped to the body.
        """
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()
        body = html.split('<div class="about-wrap">', 1)[-1]
        # The map's own title block reads SYSTEM MAP, not the product name.
        self.assertNotIn('Titan', body)
        self.assertNotIn('WORKSHOP<tspan', body)

    # -- claims that were wrong once and could drift back -----------------
    #
    # The page is prose, so nothing else fails when a sentence stops being
    # true. These four were each corrected from a statement the code does not
    # support, and each is the kind that comes back.

    def _card(self, html, title):
        """
        One .ab-card, from its <h3> to the end of its list.

        Scoped rather than searched whole-page, because this page carries a
        <style> block and the map's own text, and a bare `assertNotIn` over
        the response would answer about those too.
        """
        marker = '<h3>%s</h3>' % title
        self.assertIn(marker, html, 'no card titled %r on the page' % title)
        return marker + html.split(marker, 1)[1].split('</ul>', 1)[0]

    def test_it_does_not_offer_staff_a_way_in_their_login_does_not_hold(self):
        """
        `resolve_user_by_identifier` tries username, then email, then mobile —
        but `manage_create_user` stores ONLY a username, a password and a role,
        so no staff login in this workshop has either of the other two. The
        page said staff could use "a username, an email or a mobile number",
        which is a capability rather than a fact, and two thirds of it can
        never work here.
        """
        self.client.force_login(self.owner)
        card = self._card(self.client.get(self.url).content.decode(),
                          'Signing in')

        self.assertIn('username', card)
        for absent in ('mobile number', 'an email or'):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, card)

    def test_the_data_cleanup_card_stays_inside_what_that_screen_does(self):
        """
        `cleanup_views` renames and merges SPARE NAMES and CONCERNS, and
        nothing else — brands and models are handled on the Master List, by
        `rename_brand` / `rename_model`. The card illustrated itself with
        "Toyta" to "Toyota", which is a job that screen cannot do.
        """
        self.client.force_login(self.owner)
        card = self._card(self.client.get(self.url).content.decode(),
                          'Data Cleanup')

        self.assertNotIn('Toyta', card)
        self.assertNotIn('Toyota', card)

    def test_the_spelling_rule_is_claimed_only_for_a_brand_and_a_model(self):
        """
        `JobCard.clean()` snaps `brand_name` and `model_name` to the master
        list's own spelling. `spare_part_name` is only `.strip()`ed, and a
        concern is not touched at all — which is precisely why Data Cleanup
        exists. Master lists claimed the rule for all four of its lists.
        """
        self.client.force_login(self.owner)
        card = self._card(self.client.get(self.url).content.decode(),
                          'Master lists')

        self.assertIn('brand and a model, the list decides the spelling', card)
        self.assertIn('saved exactly as typed', card)

    def test_the_map_says_what_a_staff_login_actually_holds(self):
        """
        The drawing sits at the top of the page explaining the same things, so
        a stale label on it contradicts the prose underneath. It read
        `user - email - mobile`. Generated by scratchpad/build_system_map.py.
        """
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()

        self.assertIn('username - owner email', html)
        self.assertNotIn('user - email - mobile', html)

    def test_the_map_agrees_with_the_catalogue_about_how_many_are_critical(self):
        """
        `notifications.EVENTS` is the one catalogue. The sheet read
        "10 critical" for a day after LOGIN was raised to CRITICAL.
        """
        from workshop.notifications import EVENTS, CRITICAL

        critical = sum(1 for e in EVENTS.values() if e.severity == CRITICAL)
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()

        self.assertIn('%d events - %d critical' % (len(EVENTS), critical), html)

    # -- how it is reached ------------------------------------------------

    def test_the_drawer_offers_it_to_an_owner_and_to_nobody_else(self):
        self.client.force_login(self.owner)
        self.assertIn('href="/about/"',
                      self.client.get(reverse('home')).content.decode())

        for user in (self.office, self.floor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                html = self.client.get(reverse('home')).content.decode()
                self.assertNotIn('href="/about/"', html)

    def test_the_drawer_calls_it_the_guide_and_wears_the_information_glyph(self):
        """
        "Help" promised somewhere to get unstuck; the page is a tour of what
        exists. `bi-compass` promised navigation from the one page in the app
        that deliberately carries no links. Both on the owner's instruction.
        """
        self.client.force_login(self.owner)
        html = self.client.get(reverse('home')).content.decode()

        self.assertIn('<div class="drawer-label">Guide</div>', html)
        self.assertIn('bi bi-info-circle', html)

    def test_it_is_the_last_thing_in_the_drawer(self):
        """
        The owner asked for it as the last button. Asserted by position, so
        a section added later above it still passes and one added below it
        fails — which is the conversation worth having.
        """
        self.client.force_login(self.owner)
        html = self.client.get(reverse('home')).content.decode()

        drawer = re.search(r'<div class="drawer-body">(.*?)</div>\s*</div>',
                           html, re.S)
        self.assertIsNotNone(drawer, 'drawer body did not render')
        hrefs = re.findall(r'class="drawer-link[^"]*"\s+href="([^"]+)"',
                           drawer.group(1))
        self.assertTrue(hrefs, 'no drawer links found')
        self.assertEqual(hrefs[-1], '/about/')

    def test_opening_it_lights_the_manage_button(self):
        """
        /about/ is in DRAWER_SECTION_PREFIXES, so the pill the page is
        reached from stays lit while the page is open.
        """
        from workshop.templatetags.custom_filters import is_drawer_section
        self.assertTrue(is_drawer_section('/about/'))
