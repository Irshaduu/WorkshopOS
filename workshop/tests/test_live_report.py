"""
The Live Report — the screen an owner opens on a phone to see the workshop.

Office and Owner only, whole page. It answers three questions in the order they
get asked: what has already been billed with boxes nobody filled in, who is
holding which car, and which parts are travelling or unordered. The tests here
pin the role gate, the rule that decides which parts get chased, the rule that
decides which bills get chased, and the counts that sit above each box.
"""

import re
from datetime import date, timedelta

from django.contrib.auth.models import User, Group
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Category, Item
from workshop.models import JobCard, JobCardSpareItem, Mechanic, SpareShop


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

    def test_every_mechanic_is_a_column_of_one_grid(self):
        """
        Mechanics read across, their cars down — four names to a row on a
        laptop, wrapping to a second row for the fifth. One grid holds them,
        so the wrap is the browser's job and not a count baked into the
        template.
        """
        names = ('Amlah', 'Shafee', 'Sabith', 'Rafeeq', 'Zubair')
        for n, name in enumerate(names):
            self._car(f'KL01A{n:04d}', mechanic=Mechanic.objects.create(name=name))

        page = self._page(self.owner)
        crews = page.split('<div class="lr-crews">', 1)[1].split('</section>', 1)[0]

        self.assertEqual(crews.count('class="lr-crew '), 5)
        for name in names:
            self.assertIn(f'>{name}</span>', crews)
