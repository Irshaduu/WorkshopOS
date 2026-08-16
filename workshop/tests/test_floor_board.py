"""
The home board: who may move a card, and how much of one card is drawn.

Two owner decisions from 2026-08-16.

  * Floor may put a card on hold and mark it completed. Both buttons had been
    rendered for Floor all along while both views were `@office_required`, so
    pressing either gave a mechanic a 403 on the one screen they use all day.
  * Each drawer section is capped at `HOME_SECTION_ROW_CAP` and names its
    remainder, because a rebuild in the live data carries 91 spares and there
    are 45 cards to a page.
"""
from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from workshop.models import (JobCard, JobCardConcern, JobCardLabourItem,
                             JobCardSpareItem)
from workshop.views.dashboard import HOME_SECTION_ROW_CAP


class BoardBase(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.floor = User.objects.create_user('board_floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))
        self.office = User.objects.create_user('board_office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))

        self.job = JobCard.objects.create(registration_number='KL07BB2222',
                                          admitted_date=date(2026, 8, 1),
                                          brand_name='Audi', model_name='A4')


class FloorCanMoveACardOffTheBoardTests(BoardBase):
    def test_floor_can_put_a_card_on_hold(self):
        self.client.force_login(self.floor)
        self.client.post(reverse('toggle_hold', args=[self.job.pk]))
        self.job.refresh_from_db()
        self.assertTrue(self.job.on_hold)

    def test_floor_can_take_it_off_hold_again(self):
        self.job.on_hold = True
        self.job.save(update_fields=['on_hold'])
        self.client.force_login(self.floor)
        self.client.post(reverse('toggle_hold', args=[self.job.pk]))
        self.job.refresh_from_db()
        self.assertFalse(self.job.on_hold)

    def test_floor_can_mark_a_card_completed(self):
        self.client.force_login(self.floor)
        self.client.post(reverse('mark_completed', args=[self.job.pk]))
        self.job.refresh_from_db()
        self.assertTrue(self.job.completed)

    def test_the_buttons_floor_is_shown_are_the_ones_floor_can_press(self):
        """
        The template gate and the decorator have to agree — a button that 403s
        is the defect this pair of changes closed.
        """
        self.client.force_login(self.floor)
        page = self.client.get(reverse('home')).content.decode()
        self.assertIn(reverse('toggle_hold', args=[self.job.pk]), page)
        self.assertIn(reverse('mark_completed', args=[self.job.pk]), page)

    def test_undoing_a_completion_is_still_office_only(self):
        """
        Deliberately NOT widened: it can put a second active card on the floor
        for one registration, and has to answer that rule when it does.
        """
        self.job.mark_completed()
        self.client.force_login(self.floor)
        resp = self.client.post(reverse('undo_completed', args=[self.job.pk]))
        self.assertEqual(resp.status_code, 403)
        self.job.refresh_from_db()
        self.assertTrue(self.job.completed)

    def test_floor_is_shown_no_invoice_link(self):
        self.client.force_login(self.floor)
        page = self.client.get(reverse('home')).content.decode()
        self.assertNotIn(reverse('invoice_view', args=[self.job.pk]), page)

    def test_office_is_shown_the_invoice_link(self):
        self.client.force_login(self.office)
        page = self.client.get(reverse('home')).content.decode()
        self.assertIn(reverse('invoice_view', args=[self.job.pk]), page)


class ALongCardIsCappedAndSaysSoTests(BoardBase):
    """
    Every hidden row is on the job card this card already opens, the heading
    keeps the TRUE count, and the remainder is printed — so what is on screen
    plus what the tail names always adds back up to the heading.
    """

    def fill(self, n):
        for i in range(n):
            JobCardSpareItem.objects.create(
                job_card=self.job, source=JobCardSpareItem.SOURCE_SHOP,
                spare_part_name='Part %d' % i, quantity=1)

    def card(self):
        self.client.force_login(self.office)
        return self.client.get(reverse('home'))

    def test_a_short_section_is_not_capped_and_says_nothing(self):
        self.fill(3)
        page = self.card().content.decode()
        self.assertIn('Part 2', page)
        self.assertNotIn('more on the job card', page)

    def test_a_long_section_shows_the_cap_and_names_the_rest(self):
        self.fill(HOME_SECTION_ROW_CAP + 7)
        page = self.card().content.decode()
        self.assertIn('+7 more on the job card', page)
        # The last row inside the cap is drawn; the first one past it is not.
        self.assertIn('Part %d<' % (HOME_SECTION_ROW_CAP - 1), page)
        self.assertNotIn('Part %d<' % HOME_SECTION_ROW_CAP, page)

    def test_the_heading_still_reports_the_true_total(self):
        self.fill(HOME_SECTION_ROW_CAP + 7)
        page = self.card().content.decode()
        self.assertIn('Spare Parts (%d)' % (HOME_SECTION_ROW_CAP + 7), page)

    def test_the_cap_applies_to_every_section(self):
        for i in range(HOME_SECTION_ROW_CAP + 2):
            JobCardConcern.objects.create(job_card=self.job,
                                          concern_text='Concern %d' % i)
            JobCardLabourItem.objects.create(job_card=self.job,
                                             job_description='Job %d' % i)
        page = self.card().content.decode()
        self.assertEqual(page.count('+2 more on the job card'), 2)

    def test_the_view_caps_rather_than_the_template(self):
        """
        A cap in the markup and a remainder computed from a constant are two
        versions of one rule, free to disagree — and they would disagree as a
        "+3 more" beside a different number of visible rows.
        """
        with open('workshop/templates/workshop/dashboard/dashboard_home.html',
                  encoding='utf-8') as fh:
            markup = fh.read()
        self.assertNotIn('|slice:', markup)

    def test_the_two_boards_do_not_share_one_cap_by_accident(self):
        """
        `_capped()` is one implementation taking the cap as an argument. It was
        briefly two functions of the same name, and the later one silently
        shadowed the earlier — the home board took the Live Report's 10 while
        every comment on the page said 25, and nothing on screen would have
        shown it because the remainder line stayed arithmetically correct.
        """
        from workshop.views.dashboard import SECTION_ROW_CAP, _capped
        self.assertNotEqual(HOME_SECTION_ROW_CAP, SECTION_ROW_CAP)
        rows = list(range(30))
        self.assertEqual(_capped(rows, HOME_SECTION_ROW_CAP), (rows[:25], 5))
        self.assertEqual(_capped(rows, SECTION_ROW_CAP), (rows[:10], 20))


class FloorsDrawerIsCoveredTooTests(BoardBase):
    """
    `TheManageButtonLightsUpForEverySectionBehindItTests` scrapes the drawer as
    an OWNER, so it only ever sees the owner branch — a Floor-only entry missing
    from `DRAWER_SECTION_PREFIXES` would slip past it, and Manage would read as
    inactive on the one section Floor reaches through it. This is the same
    check run from the other side.
    """

    def drawer_links(self):
        import re
        self.client.force_login(self.floor)
        page = self.client.get(reverse('home')).content.decode()
        drawer = page.split('id="appDrawer"', 1)[-1]
        return set(re.findall(r'<a class="drawer-link[^"]*"\s+href="([^"]+)"', drawer))

    def test_every_floor_drawer_destination_lights_the_manage_button(self):
        from workshop.templatetags.custom_filters import is_drawer_section
        hrefs = self.drawer_links()
        self.assertGreaterEqual(len(hrefs), 3,
                                'drawer links not found — has base.html changed shape?')
        for href in hrefs:
            with self.subTest(href=href):
                self.assertTrue(is_drawer_section(href),
                                '%s is in Floor\'s drawer but missing from '
                                'DRAWER_SECTION_PREFIXES' % href)

    def test_floor_can_open_everything_its_drawer_offers(self):
        """A link to a page the role is 403'd on is worse than no link."""
        self.client.force_login(self.floor)
        for href in self.drawer_links():
            with self.subTest(href=href):
                self.assertEqual(self.client.get(href).status_code, 200)

    def test_the_unassigned_hub_is_in_it(self):
        """Floor's only door into the Spare Shops section."""
        self.assertIn(reverse('unassigned_spares_hub'), self.drawer_links())
