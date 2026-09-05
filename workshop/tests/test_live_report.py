"""
The Live Report — the screen an owner opens on a phone to see the workshop.

Office and Owner only, whole page. It answers three questions in the order they
get asked: what has already been billed with boxes nobody filled in, who is
holding which car and what is still open on it, and which parts are travelling
or unordered. The tests here pin the role gate, the rule that decides which
parts get chased, the rule that decides which bills get chased, the work list
an owner gives the next instruction from, and the counts above each box.
"""

import re
from datetime import date, timedelta

from django.contrib.auth.models import User, Group
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Category, Item
from workshop.models import (JobCard, JobCardConcern, JobCardSpareItem,
                             Mechanic, SpareShop)


def _text(html):
    """The page with its tags stripped — for asking 'does this word appear'."""
    return re.sub(r'<[^>]+>', ' ', html)


class LiveReportTestCase(TestCase):
    """Roles, one live car, and the helpers the subclasses share."""

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='owner', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.office = User.objects.create_user(username='office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.floor = User.objects.create_user(username='floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))

        self.url = reverse('live_report')
        self.client = Client()

    def _page(self, user):
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _car(self, reg, mechanic=None, **kwargs):
        return JobCard.objects.create(
            admitted_date=kwargs.pop('admitted_date', date(2026, 8, 1)),
            brand_name=kwargs.pop('brand_name', 'Toyota'),
            model_name=kwargs.pop('model_name', 'Corolla'),
            registration_number=reg,
            lead_mechanic=mechanic,
            **kwargs,
        )


class ThePageIsOfficeAndOwnerOnlyTests(LiveReportTestCase):
    """
    The WHOLE page, not just the board inside it.

    It used to be `@staff_required` with the board gated internally, because
    "Live Jobs" underneath was for all three roles. That list has gone — the
    home page's car cards do the same job better, and are where Floor already
    works — so everything left on this page is supplier names, ordering state
    and money-side gaps, none of which Floor is shown anywhere else in the app.

    The gate is the decorator, so a mechanic gets 403 rather than a page with
    the shop names sitting in HTML they can read. The nav pill has always been
    gated `is_owner or is_office`; the two now agree, which is the rule
    `InvoiceLinkVisibilityTests` exists to enforce.
    """

    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='Kochi Auto Spares')
        self.mech = Mechanic.objects.create(name='Rafeeq')
        self.car = self._car('KL01AA1111', mechanic=self.mech)
        JobCardSpareItem.objects.create(
            job_card=self.car, spare_part_name='Brake Pad Set',
            source=JobCardSpareItem.SOURCE_SHOP, status='ORDERED', shop=self.shop,
        )

    def test_office_and_owner_see_the_board(self):
        for user in (self.office, self.owner):
            with self.subTest(user=user.username):
                page = self._page(user)
                self.assertIn('On the floor', page)
                self.assertIn('On the way', page)
                self.assertIn('Not ordered yet', page)
                self.assertIn('Kochi Auto Spares', page)

    def test_floor_is_refused_the_page_outright(self):
        self.client.force_login(self.floor)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        """403 is for a signed-in user with the wrong role; anonymous gets the
        door, carrying ?next= so the page is reachable after signing in."""
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])


class TheFloorIsGroupedByWhoIsHoldingTheCarTests(LiveReportTestCase):

    def setUp(self):
        super().setUp()
        self.rafeeq = Mechanic.objects.create(name='Rafeeq')
        self.anas = Mechanic.objects.create(name='Anas')
        self.idle = Mechanic.objects.create(name='Zubair')

    def _groups(self):
        self.client.force_login(self.owner)
        return self.client.get(self.url).context['mechanic_groups']

    def test_each_car_sits_under_the_mechanic_holding_it(self):
        self._car('KL01AA1111', mechanic=self.rafeeq)
        self._car('KL01BB2222', mechanic=self.rafeeq)
        self._car('KL01CC3333', mechanic=self.anas)

        groups = {g['name']: [j.registration_number for j in g['jobs']] for g in self._groups()}

        self.assertEqual(sorted(groups['Rafeeq']), ['KL01AA1111', 'KL01BB2222'])
        self.assertEqual(groups['Anas'], ['KL01CC3333'])

    def test_a_mechanic_holding_nothing_is_not_listed(self):
        """
        Every name on this board has work under it — that is what keeps it
        short enough to read at a glance on a phone.
        """
        self._car('KL01AA1111', mechanic=self.rafeeq)

        self.assertNotIn('Zubair', [g['name'] for g in self._groups()])
        self.assertNotIn('Zubair', self._page(self.owner))

    def test_unassigned_cars_get_their_own_group_at_the_end(self):
        """
        The position is decided in Python, not by `order_by`. PostgreSQL sorts
        NULL last on an ascending sort and SQLite sorts it first, so a database
        ordering would put "Not assigned" at a different end of the page in the
        tests than in production.
        """
        self._car('KL01ZZ9999', mechanic=None)
        self._car('KL01AA1111', mechanic=self.rafeeq)
        self._car('KL01CC3333', mechanic=self.anas)

        groups = self._groups()

        self.assertEqual([g['name'] for g in groups], ['Anas', 'Rafeeq', 'Not assigned'])
        self.assertTrue(groups[-1]['unassigned'])
        self.assertEqual([j.registration_number for j in groups[-1]['jobs']], ['KL01ZZ9999'])

    def test_only_cars_still_in_the_workshop_appear(self):
        self._car('KL01AA1111', mechanic=self.rafeeq)
        self._car('KL01BB2222', mechanic=self.rafeeq, completed=True, completed_date=date(2026, 8, 2))
        self._car('KL01CC3333', mechanic=self.rafeeq, is_deleted=True)

        page = self._page(self.owner)

        self.assertIn('KL01AA1111', page)
        self.assertNotIn('KL01BB2222', page)
        self.assertNotIn('KL01CC3333', page)

    def test_nothing_on_the_page_is_narrowed_by_a_query_string(self):
        """
        There is no search box here and a crafted `?q=` must not invent one.
        The page answers "what is the state of the workshop right now", and a
        half-filtered answer to that question is worse than no answer.
        """
        self._car('KL01AA1111', mechanic=self.rafeeq, brand_name='Toyota')
        self._car('KL01CC3333', mechanic=self.anas, brand_name='Honda')

        self.client.force_login(self.owner)
        context = self.client.get(self.url, {'q': 'Toyota', 'status': 'PAID'}).context

        self.assertEqual(context['floor_count'], 2)
        self.assertEqual(sorted(g['name'] for g in context['mechanic_groups']), ['Anas', 'Rafeeq'])


class OnlyAPartWithAnOrderingWorkflowIsChasedTests(LiveReportTestCase):
    """
    A part reaches a car by one of two routes. A SHOP purchase is ordered, waited
    for and received; a warehouse draw came off the shelf already fitted, so its
    `status` column means nothing. Listing a draw as "waiting" would send someone
    chasing a part that is already on the car.
    """

    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='Kochi Auto Spares')
        self.car = self._car('KL01AA1111')
        self.stock = Item.objects.create(
            category=Category.objects.create(name='Engine Oil'),
            name='Castrol Edge 5W-30', current_stock=50,
        )

    def _boxes(self):
        self.client.force_login(self.owner)
        context = self.client.get(self.url).context
        return (
            [s.spare_part_name for s in context['ordered_spares']],
            [s.spare_part_name for s in context['pending_spares']],
        )

    def _shop_part(self, name, status):
        return JobCardSpareItem.objects.create(
            job_card=self.car, spare_part_name=name, shop=self.shop,
            source=JobCardSpareItem.SOURCE_SHOP, status=status,
        )

    def test_ordered_goes_amber_and_pending_goes_red(self):
        self._shop_part('Brake Pad Set', 'ORDERED')
        self._shop_part('Clutch Plate', 'PENDING')

        on_the_way, not_ordered = self._boxes()

        self.assertEqual(on_the_way, ['Brake Pad Set'])
        self.assertEqual(not_ordered, ['Clutch Plate'])

    def test_a_received_part_is_in_neither_box(self):
        self._shop_part('Air Filter', 'RECEIVED')

        self.assertEqual(self._boxes(), ([], []))

    def test_a_warehouse_draw_is_never_chased_whatever_its_status(self):
        for status in ('PENDING', 'ORDERED', 'RECEIVED'):
            with self.subTest(status=status):
                JobCardSpareItem.objects.all().delete()
                JobCardSpareItem.objects.create(
                    job_card=self.car, source=JobCardSpareItem.SOURCE_INVENTORY,
                    item=self.stock, quantity=1, status=status,
                )
                self.assertEqual(self._boxes(), ([], []))

    def test_a_part_on_a_car_that_has_left_is_not_chased(self):
        """A stale PENDING row on a delivered car would fill the red box with
        work nobody is going to do."""
        gone = self._car('KL01BB2222', completed=True, completed_date=date(2026, 8, 2))
        JobCardSpareItem.objects.create(
            job_card=gone, spare_part_name='Wiper Blade', shop=self.shop,
            source=JobCardSpareItem.SOURCE_SHOP, status='PENDING',
        )

        self.assertEqual(self._boxes(), ([], []))

    def test_a_spare_belonging_to_no_car_is_not_chased(self):
        """Every row in these boxes opens a job card; one with no card has none
        to open, and lives in the Unassigned Hub instead."""
        JobCardSpareItem.objects.create(
            job_card=None, spare_part_name='Loose Bolt', shop=self.shop,
            source=JobCardSpareItem.SOURCE_SHOP, status='PENDING',
        )

        self.assertEqual(self._boxes(), ([], []))

    def test_every_row_names_the_part_the_car_and_the_shop(self):
        self._shop_part('Brake Pad Set', 'ORDERED')

        page = _text(self._page(self.owner))

        self.assertIn('Brake Pad Set', page)
        self.assertIn('KL01AA1111', page)
        self.assertIn('Kochi Auto Spares', page)

    def test_every_row_opens_its_job_card(self):
        self._shop_part('Brake Pad Set', 'ORDERED')
        self._shop_part('Clutch Plate', 'PENDING')

        page = self._page(self.owner)
        target = reverse('jobcard_edit', args=[self.car.pk])

        # One link per box, plus the car on the floor board.
        self.assertGreaterEqual(page.count(f'href="{target}?next=mini"'), 3)


class TheCountAboveABoxIsTheRowsBeneathItTests(LiveReportTestCase):
    """
    A figure that cannot be added up from what is on screen is worse than no
    figure, on a page whose whole purpose is one confident look.
    """

    def _count(self, page, title):
        """The number in the pill beside `title`."""
        match = re.search(
            re.escape(title) + r'</span>\s*<span class="lr-box-count">(\d+)</span>',
            page,
        )
        self.assertIsNotNone(match, f'could not find the count beside "{title}"')
        return int(match.group(1))

    def test_the_counts_match_what_is_rendered(self):
        shop = SpareShop.objects.create(name='Kochi Auto Spares')
        mech = Mechanic.objects.create(name='Rafeeq')
        for n in range(3):
            car = self._car(f'KL01A{n:04d}', mechanic=mech)
            JobCardSpareItem.objects.create(
                job_card=car, spare_part_name=f'Part {n}', shop=shop,
                source=JobCardSpareItem.SOURCE_SHOP,
                status='ORDERED' if n < 2 else 'PENDING',
            )

        page = self._page(self.owner)

        self.assertEqual(self._count(page, 'On the floor'), 3)
        self.assertEqual(self._count(page, 'On the way'), 2)
        self.assertEqual(self._count(page, 'Not ordered yet'), 1)
        # One rail per car chip, one row per waited-on part.
        self.assertEqual(page.count('<span class="lr-car-rail">'), 3)
        self.assertEqual(page.count('class="lr-spare"'), 3)

    def test_an_empty_workshop_prints_zero_not_a_blank(self):
        """
        The header used to read `{{ active_jobs.count }}`, and `active_jobs` is
        a Page — its `.count` is `Sequence.count(value)`, a method wanting an
        argument. Django swallows that and substitutes `string_if_invalid`, so
        the count was ALWAYS blank, on every load, however many cars were in.
        """
        self.assertFalse(JobCard.objects.exists())

        page = self._page(self.owner)

        self.assertIn('>0 in workshop<', page)
        self.assertEqual(self._count(page, 'On the floor'), 0)
        self.assertIn('No cars on the floor right now.', page)
        self.assertIn('Nothing on the way.', page)
        self.assertIn('Every part is ordered.', page)

    def test_the_count_is_right_when_there_are_cars(self):
        for n in range(4):
            self._car(f'KL01A{n:04d}')

        self.assertIn('>4 in workshop<', self._page(self.owner))

    def test_the_heading_counts_the_workshop_whatever_is_in_the_url(self):
        """
        The heading counts the WORKSHOP. Nothing on this page is filtered, and a
        crafted query string must not make it read "1 in workshop" with three
        more cars on the ramp — that would be the one number here that is simply
        untrue.
        """
        self._car('KL01AA1111', brand_name='Toyota')
        self._car('KL01BB2222', brand_name='Honda')
        self._car('KL01CC3333', brand_name='Honda')

        self.client.force_login(self.owner)
        page = self.client.get(self.url, {'q': 'Toyota'}).content.decode()

        self.assertIn('>3 in workshop<', page)


class ACarOnTheBoardLeadsWithItsNameTests(LiveReportTestCase):
    """
    The board's card is the car's NAME in large type, with the registration and
    the age sharing one very small line beneath it.

    The age is worded ONE way across the whole page — `New`, `9d`, `213d`. It
    briefly had a long form too, for the roomier Live Jobs card, and the owner
    collapsed the two: the same fact worded differently in two places on one
    screen invites being read as two different facts.
    """

    def setUp(self):
        super().setUp()
        self.mech = Mechanic.objects.create(name='Hijaz')

    def test_the_card_carries_the_name_the_plate_and_the_age(self):
        from django.utils import timezone
        self._car(
            'KL09HA5933', mechanic=self.mech,
            brand_name='Mini Cooper', model_name='Clubman',
            admitted_date=timezone.localdate() - timedelta(days=213),
        )

        page = self._page(self.owner)

        self.assertIn('>Mini Cooper Clubman</span>', page)
        self.assertIn('>KL09HA5933</span>', page)
        self.assertIn('>213d</span>', page)

    def test_the_age_reads_the_way_it_is_said(self):
        from workshop.views.dashboard import _age_label

        # A car admitted today is NEW, not "Today" — the question the line
        # answers is how long it has been here, not what today's date is.
        self.assertEqual(_age_label(0), 'New')
        self.assertEqual(_age_label(1), '1d')
        self.assertEqual(_age_label(213), '213d')
        # A card with no admission date has nothing to say rather than "None".
        self.assertEqual(_age_label(None), '')

    def test_a_stopped_car_says_so_instead_of_its_age(self):
        """On hold is why the car has not moved — it replaces the day count
        rather than crowding a line that is already two facts long."""
        self._car('KL09HA5933', mechanic=self.mech, on_hold=True)

        page = self._page(self.owner)

        # The rendered span, not the bare class name — that also appears in the
        # page's own stylesheet, where it proves nothing either way.
        self.assertIn('<span class="lr-car-hold">', page)
        self.assertNotIn('<span class="lr-car-age">', page)

    def test_every_mechanic_is_a_panel_of_one_container(self):
        """
        One panel per mechanic, their cars inside it — two panels to a row
        from 800px up, one below. ONE container holds them all, so the layout
        is the stylesheet's job and not a count baked into the template.

        They used to read four across on a laptop. That went when each car
        grew its own work list underneath it: four columns left 135px of text
        width per car, which is not a column anybody can print a customer's
        own sentence in. Two leaves 301px against a 252px worst case, and
        three wraps 13 rows in 25. See the CSS comment on `.lr-crews`.
        """
        names = ('Amlah', 'Shafee', 'Sabith', 'Rafeeq', 'Zubair')
        for n, name in enumerate(names):
            self._car(f'KL01A{n:04d}', mechanic=Mechanic.objects.create(name=name))

        page = self._page(self.owner)
        crews = page.split('<div class="lr-crews">', 1)[1].split('</section>', 1)[0]

        self.assertEqual(crews.count('class="lr-crew '), 5)
        for name in names:
            self.assertIn(f'>{name}</span>', crews)
class TheBoardSaysWhatIsLeftToBeCommandedTests(LiveReportTestCase):
    """
    Each car on the floor carries the concerns that are still open on it.

    The owner's own workflow, in their words: finish this car's vibration, then
    tell him to do the periodic service because those parts are here, then move
    him on to his second car. Only Office and the owners decide what a mechanic
    does next — they are the ones tracking which parts have arrived — and until
    this box carried the work list they were holding the whole floor's in their
    heads, one job card at a time.

    So the CONCERN is the row and the car is only its heading. What is listed
    is exactly what is still to be decided; a fixed concern is counted instead,
    because a finished job is not a decision anybody has left to make.
    """

    def setUp(self):
        super().setUp()
        self.mech = Mechanic.objects.create(name='Amlah')

    def _floor(self, user=None):
        """The floor box alone. A concern's text can also appear in the
        billed-but-not-filled box above it, which proves nothing here."""
        page = self._page(user or self.owner)
        return page.split('<div class="lr-crews">', 1)[1].split('</section>', 1)[0]

    def _concerned(self, reg='KL07AA1111', **statuses):
        car = self._car(reg, mechanic=self.mech)
        for text, status in sorted(statuses.items()):
            JobCardConcern.objects.create(
                job_card=car, concern_text=text.replace('_', ' '), status=status)
        return car

    def test_a_still_open_concern_is_listed_under_its_car(self):
        self._concerned(Vibration_at_high_speed='PENDING')

        self.assertIn('Vibration at high speed', self._floor())

    def test_a_fixed_concern_is_not_listed(self):
        self._concerned(Wheel_alignment_done='FIXED')

        self.assertNotIn('Wheel alignment done', self._floor())

    def test_the_fixed_ones_are_counted_rather_than_dropped(self):
        """How close the car is to finished is a fact the owner acts on: it is
        the difference between commanding the next job and closing the card."""
        self._concerned(A_first='FIXED', B_second='FIXED', C_still_open='PENDING')

        self.assertIn('2 done', self._floor())

    def test_what_he_is_on_now_sorts_above_what_is_queued(self):
        """WORKING then PENDING, the order the instruction is spoken in."""
        car = self._car('KL07AA1111', mechanic=self.mech)
        JobCardConcern.objects.create(job_card=car, concern_text='Queued job',
                                      status='PENDING')
        JobCardConcern.objects.create(job_card=car, concern_text='Running job',
                                      status='WORKING')

        floor = self._floor()

        self.assertLess(floor.index('Running job'), floor.index('Queued job'))

    def test_the_running_job_is_marked_apart_from_the_queued_one(self):
        self._concerned(Running_job='WORKING')

        self.assertIn('lr-concern--working', self._floor())

    def test_a_car_with_every_concern_fixed_says_so(self):
        """It is itself an action — nobody has closed the card. An empty block
        under the car would read as something that failed to load."""
        self._concerned(All_done='FIXED')

        self.assertIn('All concerns fixed', self._floor())

    def test_a_car_with_no_concerns_at_all_says_nothing(self):
        """Nobody wrote one down is a different fact from every one being
        fixed, so the line claiming the second is not printed for the first."""
        self._car('KL07AA1111', mechanic=self.mech)

        floor = self._floor()

        self.assertNotIn('All concerns fixed', floor)
        self.assertNotIn('lr-concerns', floor)

    def test_a_long_list_stops_and_names_how_many_are_left(self):
        from workshop.views.dashboard import FLOOR_CONCERN_ROW_CAP

        car = self._car('KL07AA1111', mechanic=self.mech)
        for n in range(FLOOR_CONCERN_ROW_CAP + 3):
            JobCardConcern.objects.create(job_card=car, status='PENDING',
                                          concern_text='Concern %02d' % n)

        floor = self._floor()

        self.assertIn('Concern %02d' % (FLOOR_CONCERN_ROW_CAP - 1), floor)
        self.assertNotIn('Concern %02d' % FLOOR_CONCERN_ROW_CAP, floor)
        self.assertIn('+3 more', floor)

    def test_the_concerns_cost_no_query_per_car(self):
        """They ride the floor queryset's own prefetch.

        Asserted as the INVARIANT — the page costs the same whether one car is
        on the floor or five — rather than as a magic number, which would have
        to be re-tuned every time an unrelated query on this page moved.

        The first request is thrown away deliberately: signing in writes the
        session row, so measuring it would compare a cold request against a
        warm one and report a DROP in queries as five cars were added, which is
        exactly what happened when this was written the obvious way round.
        """
        self._concerned('KL07AA0001', First_job='PENDING')
        self.client.force_login(self.owner)
        self._query_count()
        one_car = self._query_count()

        for n in range(2, 6):
            self._concerned('KL07AA000%d' % n, Some_job='PENDING',
                            Another_job='WORKING')

        self.assertEqual(self._query_count(), one_car)

    def _query_count(self):
        """Queries for one already-signed-in GET of the page."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        return len(ctx.captured_queries)
class WhatLandedRecentlyIsListedApartTests(LiveReportTestCase):
    """
    "Just arrived" — shop parts received in the last `RECEIVED_WINDOW_DAYS`.

    The only box on this page that is not a list of work. Arrivals are tracked
    physically or the mechanic says so; this is for looking one up again
    afterwards, which is also why it is windowed. Nearly every shop spare on a
    live card is already RECEIVED — 43 of 45 on the development data — so with
    no window it would be longer than the rest of the page put together, and
    most of those parts are already on the car.

    It is built EXACTLY like the two parts boxes below it — same head, same
    row, no subtitle — on the owner's instruction. The window is said once, in
    the heading.
    """

    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='Spare club')
        self.today = date.today()

    def _arrival(self, days_ago, name='Brake Pads', reg='KL07AA1111', **kw):
        card = kw.pop('card', None) or self._car(reg)
        return JobCardSpareItem.objects.create(
            job_card=card,
            spare_part_name=name,
            source=JobCardSpareItem.SOURCE_SHOP,
            shop=self.shop,
            status='RECEIVED',
            ordered_date=self.today - timedelta(days=days_ago + 2),
            received_date=self.today - timedelta(days=days_ago),
            **kw,
        )

    def _box(self, user=None):
        """The green section alone. `lr-box--green` is also a stylesheet rule,
        so a whole-page search finds it on every render and proves nothing."""
        page = self._page(user or self.owner)
        return (page.split('<section class="lr-box lr-box--green">', 1)[1]
                    .split('</section>', 1)[0])

    def test_a_part_that_landed_today_is_listed(self):
        self._arrival(0, name='Brake Pads')

        self.assertIn('Brake Pads', self._box())

    def test_a_part_that_landed_inside_the_window_is_listed(self):
        from workshop.views.dashboard import RECEIVED_WINDOW_DAYS

        self._arrival(RECEIVED_WINDOW_DAYS - 1, name='Wiper Blades')

        self.assertIn('Wiper Blades', self._box())

    def test_a_part_that_landed_before_the_window_is_not(self):
        from workshop.views.dashboard import RECEIVED_WINDOW_DAYS

        self._arrival(RECEIVED_WINDOW_DAYS, name='Drive Belt')

        self.assertNotIn('Drive Belt', self._box())

    def test_a_part_still_travelling_is_not_in_it(self):
        """ORDERED belongs to the amber box. The three parts boxes partition
        the rows by status and must never show one twice."""
        card = self._car('KL07AA2222')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Fuel Filter',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            status='ORDERED', ordered_date=self.today,
        )

        self.assertNotIn('Fuel Filter', self._box())

    def test_a_warehouse_draw_is_never_listed(self):
        """Same rule the other two boxes follow: a draw came off the shelf
        already fitted, so it never travelled and never arrived."""
        card = self._car('KL07AA3333')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Engine Oil',
            source=JobCardSpareItem.SOURCE_INVENTORY,
            status='RECEIVED', received_date=self.today,
        )

        self.assertNotIn('Engine Oil', self._box())

    def test_a_part_on_a_car_that_has_left_is_not_listed(self):
        card = self._car('KL07AA4444', completed=True,
                         completed_date=self.today)
        self._arrival(0, name='Cabin Filter', card=card)

        self.assertNotIn('Cabin Filter', self._box())

    def test_a_received_row_with_no_date_cannot_be_listed(self):
        """Nothing can say when it arrived, so nothing here can honestly
        report it — it falls outside the window rather than being guessed at."""
        card = self._car('KL07AA5555')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Air Filter',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            status='RECEIVED', received_date=None,
        )

        self.assertNotIn('Air Filter', self._box())

    def test_the_newest_arrival_is_first(self):
        card = self._car('KL07AA6666')
        self._arrival(3, name='Older Part', card=card)
        self._arrival(0, name='Newer Part', card=card)

        box = self._box()

        self.assertLess(box.index('Newer Part'), box.index('Older Part'))

    def test_its_rows_are_built_exactly_like_the_two_boxes_below_it(self):
        """The owner's instruction, asserted as the invariant rather than as a
        list of classes: whatever a row in "On the way" is made of, a row here
        is made of the same things. It carried an arrival-age chip for one
        revision and that is what this stops coming back."""
        card = self._car('KL07AA8888')
        self._arrival(0, name='Landed Part', card=card)
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Travelling Part',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            status='ORDERED', ordered_date=self.today,
        )
        page = self._page(self.owner)

        def row_classes(section_class):
            block = (page.split('<section class="lr-box %s">' % section_class, 1)[1]
                         .split('</section>', 1)[0])
            return set(re.findall(r'class="(lr-spare[a-z-]*)"', block))

        self.assertEqual(row_classes('lr-box--green'),
                         row_classes('lr-box--amber'))

    def test_it_is_drawn_as_the_same_kind_of_box_as_its_neighbours(self):
        """Every rule that makes a parts box a parts box names all three.

        Nothing in the Django suite executes CSS, so this reads the
        declarations — the same way `TheHistoryListCanAlwaysBeActedOnTests`
        does on Owner Withdrawals. It shipped for a revision carrying only the
        background and border, which left it ROUNDED where its neighbours are
        square, its rows unruled, and its heading and count in the default
        slate while theirs were coloured: a different kind of object on a page
        whose whole point is that the colour is read first.
        """
        # The whole response, not the first <style> block — base.html renders
        # its own stylesheet first, so splitting on `</style>` reads THAT one
        # and finds none of this page's rules.
        #
        # Runs of spaces are collapsed because these selectors are COLUMN
        # ALIGNED in the stylesheet (`.lr-box--red   .lr-box-title`), so an
        # exact-string match reports a false miss on whichever variant happens
        # to have the shorter name.
        sheet = re.sub(r'[ 	]+', ' ', self._page(self.owner))

        # Square corners: one rule, and green has to be named in it.
        square = [ln for ln in sheet.splitlines() if 'border-radius: 0;' in ln
                  and 'lr-box--' in ln]
        self.assertEqual(len(square), 1, 'expected one square-corners rule')
        for variant in ('amber', 'red', 'green'):
            self.assertIn('lr-box--%s' % variant, square[0])

        # The four rules that exist once per variant.
        for rule in ('.lr-box--%s .lr-box-title',
                     '.lr-box--%s .lr-box-count',
                     '.lr-box--%s .lr-spare + .lr-spare',
                     '.lr-box--%s .lr-spare:hover'):
            for variant in ('amber', 'red', 'green'):
                self.assertIn(rule % variant, sheet,
                              'missing %s' % (rule % variant))

    def test_the_three_parts_boxes_sit_under_one_Spares_heading(self):
        """It groups the three and nothing else: what landed, what is coming,
        what nobody has ordered.

        The heading is unchanged; what moved underneath it is. This used to
        assert the floor board sat ABOVE the heading, because it did — the
        board went last on 2026-09-02 on the owner's instruction. So the thing
        that now ends this group is the Floor heading rather than the end of
        the page, and that is asserted here: an `<h6>` opens a group nothing
        else closes, so without one the board would read as a fourth spare.
        """
        page = self._page(self.owner)
        heading = '<h6 class="lr-group">Spares</h6>'
        closes = '<h6 class="lr-group">Still to do</h6>'

        # The rendered tag, never the bare class name -- `.lr-group` is also a
        # rule in this page's own stylesheet, which comes FIRST in the response
        # and makes every position comparison meaningless.
        self.assertIn(heading, page)
        self.assertLess(page.index('Billed but not filled'), page.index(heading))
        self.assertLess(page.index(heading), page.index('Received (last'))
        self.assertLess(page.index('Not ordered yet'), page.index(closes))

    def test_it_carries_no_subtitle(self):
        """Removed on the owner's instruction — the headline says the window,
        and the box is the same shape as its neighbours."""
        self._arrival(0)

        self.assertNotIn('lr-box-note', self._page(self.owner))

    def test_it_sits_above_the_parts_still_coming(self):
        """Green, amber, red down the page — most finished first, which is the
        order the two boxes that were here already established."""
        page = self._page(self.owner)

        self.assertLess(page.index('Received (last'), page.index('On the way'))
        self.assertLess(page.index('On the way'), page.index('Not ordered yet'))

    def test_the_heading_names_the_window_so_nothing_else_has_to(self):
        from workshop.views.dashboard import RECEIVED_WINDOW_DAYS

        self.assertIn('Received (last %d days)' % RECEIVED_WINDOW_DAYS,
                      self._page(self.owner))

    def test_an_empty_window_says_so_rather_than_nothing(self):
        page = self._page(self.owner)

        self.assertIn('Nothing received.', page)

    def test_floor_is_still_refused_the_whole_page(self):
        """It carries supplier names and which car got what, like the rest."""
        self.client.force_login(self.floor)

        self.assertEqual(self.client.get(self.url).status_code, 403)


class TheFloorBoardSitsLastTests(LiveReportTestCase):
    """Moved from second to last on the owner's instruction (2026-09-02).

    It is by far the longest block on the page — one panel per mechanic, with
    every open concern under every car; measured at 814px against 214px for the
    next biggest box. Sitting second it pushed all three parts boxes off the
    first screen, so the two lists that are SCANNED sat below the one that is
    READ. Nothing about the board itself changed.
    """

    FLOOR = '</i> On the floor</span>'
    SPARES_HEAD = '<h6 class="lr-group">Spares</h6>'
    FLOOR_HEAD = '<h6 class="lr-group">Still to do</h6>'

    def test_the_board_comes_after_every_parts_box(self):
        page = self._page(self.owner)
        floor = page.index(self.FLOOR)

        for box in ('Received (last', 'On the way', 'Not ordered yet'):
            with self.subTest(box=box):
                self.assertLess(page.index(box), floor)

    def test_billed_but_not_filled_still_leads(self):
        """The move did not disturb the one box that must come first.

        It is the only container here describing money that has already moved;
        everything below it is work in progress.
        """
        page = self._page(self.owner)

        self.assertLess(page.index('Billed but not filled'),
                        page.index('Received (last'))
        self.assertLess(page.index('Billed but not filled'), page.index(self.FLOOR))

    def test_the_board_carries_its_own_group_heading(self):
        """It needed a HEADING, not just a move.

        `<h6 class="lr-group">Spares</h6>` opens a group that nothing closes —
        no wrapper, no second heading — so a box dropped after the three parts
        boxes with no heading of its own reads as a fourth kind of spare, to the
        eye and to a screen reader alike. The heading is what ends that group.
        """
        page = self._page(self.owner)

        self.assertIn(self.FLOOR_HEAD, page)
        self.assertLess(page.index(self.SPARES_HEAD), page.index(self.FLOOR_HEAD))
        self.assertLess(page.index(self.FLOOR_HEAD), page.index(self.FLOOR))

    def test_the_three_parts_boxes_keep_their_own_order(self):
        """Green, amber, red — the lifecycle backwards, most finished first."""
        page = self._page(self.owner)

        self.assertLess(page.index('Received (last'), page.index('On the way'))
        self.assertLess(page.index('On the way'), page.index('Not ordered yet'))
