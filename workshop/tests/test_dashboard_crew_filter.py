"""The dashboard's crew filter — "All 10 · Amlah 3 · Hijaz 3 · Unassigned 1".

The Live Report has grouped the floor by mechanic for months, but that page is
`@office_required`, so the people actually holding the cars had no way to see
which ones were theirs. This is that view on the board Floor works from.

The rules pinned here are the ones that would break silently: a heading that
starts describing the filtered board rather than the workshop, counts that stop
adding up to All, and a stale filter that renders an empty page instead of
falling back.
"""

from datetime import date

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from workshop.models import JobCard, Mechanic


class CrewFilterTests(TestCase):
    def setUp(self):
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')
        self.user = User.objects.create_user(username='floor', password='pw')
        self.user.groups.add(self.floor_group)
        self.client = Client()
        self.client.login(username='floor', password='pw')

        self.amlah = Mechanic.objects.create(name='Amlah')
        self.hijaz = Mechanic.objects.create(name='Hijaz')
        # Holds nothing, on purpose — see the no-chip test below.
        self.shafeeq = Mechanic.objects.create(name='Shafeeq')

        self._n = 0
        for _ in range(3):
            self.card(self.amlah)
        for _ in range(2):
            self.card(self.hijaz)
        self.orphan = self.card(None)

    def card(self, mechanic, completed=False):
        self._n += 1
        return JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Toyota', model_name='Corolla',
            registration_number=f'KL01AA{self._n:04d}',
            lead_mechanic=mechanic, completed=completed,
        )

    def chips(self, response):
        return response.context['mechanic_chips']

    def by_name(self, response):
        return {c['name']: c['count'] for c in self.chips(response)}

    # ── The one that would break silently ────────────────────────────────

    def test_the_heading_counts_the_whole_floor_not_the_filtered_board(self):
        """"6 IN WORKSHOP" stays 6 while the board shows one mechanic's 3.

        The heading read `page_obj.paginator.count`, which was the same number
        only for as long as nothing could narrow this board. Filtered, that
        prints "3 IN WORKSHOP" while six cars are in the workshop — the one
        figure on the page that would then be flatly untrue.

        It is also what makes a filter safe to leave switched on: a filter
        somebody else left is contradicted out loud by the page itself.
        """
        response = self.client.get(reverse('home'), {'mechanic': self.amlah.pk})

        self.assertEqual(response.context['floor_count'], 6)
        self.assertEqual(len(response.context['active_jobcards']), 3)
        self.assertNotEqual(
            response.context['floor_count'],
            response.context['page_obj'].paginator.count,
            'the heading has started following the filter',
        )

    def test_the_counts_sum_to_all(self):
        """No car may fall out of the row — the unassigned one included.

        Every chip comes from one aggregate over the floor, so this holds by
        construction. It is asserted because the failure is invisible: the row
        still looks correct, it just quietly stops accounting for a car.
        """
        chips = self.chips(self.client.get(reverse('home')))
        all_chip = chips[0]
        groups = chips[1:]

        self.assertEqual(all_chip['name'], 'All')
        self.assertEqual(all_chip['count'], 6)
        self.assertEqual(sum(c['count'] for c in groups), all_chip['count'])

    # ── What is on the row ───────────────────────────────────────────────

    def test_a_mechanic_holding_no_car_gets_no_chip(self):
        """`_floor_by_mechanic`'s own rule, one screen over.

        Every name here has work under it, which keeps the row short enough to
        read at a glance — and a `Shafeeq 0` chip is a door that opens onto an
        empty board.
        """
        names = self.by_name(self.client.get(reverse('home')))

        self.assertNotIn('Shafeeq', names)
        self.assertEqual(names['Amlah'], 3)
        self.assertEqual(names['Hijaz'], 2)

    def test_a_chip_appears_the_moment_that_mechanic_holds_a_car(self):
        """The row is a picture of the floor, not a staff list."""
        self.card(self.shafeeq)
        names = self.by_name(self.client.get(reverse('home')))

        self.assertEqual(names['Shafeeq'], 1)
        self.assertEqual(names['All'], 7)

    def test_unassigned_is_listed_only_while_a_car_has_nobody(self):
        """It asks for a decision, so it must not be furniture when there is none."""
        names = self.by_name(self.client.get(reverse('home')))
        self.assertEqual(names['Unassigned'], 1)

        self.orphan.lead_mechanic = self.hijaz
        self.orphan.save()

        names = self.by_name(self.client.get(reverse('home')))
        self.assertNotIn('Unassigned', names)
        self.assertEqual(names['All'], 6, 'the car left the row entirely')
        self.assertEqual(names['Hijaz'], 3)

    def test_the_chips_are_ordered_by_name_not_by_count(self):
        """Alphabetical is stable; by count a chip moves under the reaching thumb.

        `Zack` holds four cars and `Aaron` one, so the two orderings disagree.
        All leads and Unassigned trails, whatever the names.
        """
        aaron = Mechanic.objects.create(name='Aaron')
        zack = Mechanic.objects.create(name='Zack')
        self.card(aaron)
        for _ in range(4):
            self.card(zack)

        order = [c['name'] for c in self.chips(self.client.get(reverse('home')))]

        self.assertEqual(order[0], 'All')
        self.assertEqual(order[-1], 'Unassigned')
        self.assertEqual(order[1:-1], ['Aaron', 'Amlah', 'Hijaz', 'Zack'])

    def test_a_completed_car_is_off_the_row_and_off_the_board(self):
        """The row counts the FLOOR, which is what the board shows."""
        self.card(self.amlah, completed=True)
        names = self.by_name(self.client.get(reverse('home')))

        self.assertEqual(names['Amlah'], 3)
        self.assertEqual(names['All'], 6)

    # ── What the chips do ────────────────────────────────────────────────

    def test_a_chip_narrows_the_board_to_that_mechanic(self):
        response = self.client.get(reverse('home'), {'mechanic': self.amlah.pk})
        shown = list(response.context['active_jobcards'])

        self.assertEqual(len(shown), 3)
        self.assertTrue(all(c.lead_mechanic_id == self.amlah.pk for c in shown))

    def test_unassigned_narrows_to_the_cars_nobody_is_holding(self):
        response = self.client.get(reverse('home'), {'mechanic': 'none'})
        shown = list(response.context['active_jobcards'])

        self.assertEqual([c.pk for c in shown], [self.orphan.pk])

    def test_the_filter_survives_a_refresh(self):
        """It rides in the URL like every other filter in this app.

        Two identical requests, because that is what a refresh is — nothing is
        held in a session that could quietly expire or leak between devices.
        """
        for _ in range(2):
            response = self.client.get(reverse('home'), {'mechanic': self.hijaz.pk})
            self.assertEqual(len(response.context['active_jobcards']), 2)
            self.assertEqual(response.context['mechanic_key'], str(self.hijaz.pk))

    # ── When the filter no longer names anything ─────────────────────────

    def test_a_stale_or_crafted_key_falls_back_to_all(self):
        """Filter to Amlah, let his last car be completed, come back to the URL.

        There is no Amlah chip any more, so the board must fall back to All
        rather than render empty under a filter that no longer exists. Validated
        against the CHIPS rather than the staff roster, which is what makes the
        stale case and the crafted case one rule — the same fallback the
        Estimates list gives an unrecognised `?filter=`.
        """
        gone = Mechanic.objects.create(name='Gone')  # active staff, holds nothing
        for raw in ('999999', 'abc', "' OR 1=1--", '', '   ', str(gone.pk), '-1'):
            with self.subTest(mechanic=raw):
                response = self.client.get(reverse('home'), {'mechanic': raw})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['mechanic_key'], '')
                self.assertEqual(len(response.context['active_jobcards']), 6)

    def test_the_active_chip_is_the_one_that_was_asked_for(self):
        response = self.client.get(reverse('home'), {'mechanic': self.amlah.pk})
        active = [c for c in self.chips(response) if c['active']]

        self.assertEqual(len(active), 1, 'exactly one chip is ever lit')
        self.assertEqual(active[0]['name'], 'Amlah')

    def test_all_is_lit_when_nothing_is_asked_for(self):
        active = [c for c in self.chips(self.client.get(reverse('home'))) if c['active']]

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['name'], 'All')

    # ── The row on the page ──────────────────────────────────────────────

    def test_the_row_is_not_drawn_on_an_empty_workshop(self):
        """"ALL 0" over the empty state is a control with nothing to control.

        Asserted on the `<nav>` that opens the row, never on `pit-crew-chip`:
        this page declares its CSS inline, so the class name is on every render
        whether or not a single chip was drawn — the whole-page-search trap.
        """
        self.assertContains(self.client.get(reverse('home')), '<nav class="pit-crew"')

        JobCard.objects.update(completed=True)
        response = self.client.get(reverse('home'))

        self.assertEqual(response.context['floor_count'], 0)
        self.assertNotContains(response, '<nav class="pit-crew"')

    def test_every_chip_is_a_link_carrying_its_own_key(self):
        """A chip is a navigation, so the filter is bookmarkable and Back works."""
        response = self.client.get(reverse('home'))
        html = response.content.decode()

        self.assertContains(response, f'?mechanic={self.amlah.pk}"')
        self.assertContains(response, '?mechanic=none"')
        # All is a bare `?` — a clean URL, and it drops `page` like every other
        # chip, because a different filter is a different list.
        self.assertIn('href="?"', html)

    def test_the_pager_carries_the_filter(self):
        """Page 2 of one mechanic's cars must not be page 2 of everybody's.

        The shared pagination include names the query parameters it forwards,
        so a filter it has never heard of is dropped silently — which is the
        defect Car Profiles records, where page 2 of a search returned page 2
        of every car in the workshop.
        """
        for _ in range(46):
            self.card(self.amlah)

        response = self.client.get(reverse('home'), {'mechanic': self.amlah.pk})

        self.assertTrue(response.context['page_obj'].has_next())
        self.assertContains(response, f'&mechanic={self.amlah.pk}')

    def test_the_selected_unassigned_chip_keeps_its_white_type(self):
        """The red marking is scoped `:not(.is-active)`, and it has to stay so.

        `.pit-crew-chip.is-unassigned` and `.pit-crew-chip.is-active` are both
        two classes — equal specificity, so the winner is DOCUMENT ORDER, and
        the unassigned block sits after the active one. Unscoped, selecting the
        chip took `color: #dc2626` back over the white it had just been given
        and rendered dark red on the red fill: **1.28:1**, measured in a
        browser, which is the word disappearing.

        Nothing in this suite executes CSS, so the declaration is asserted
        directly — the rule `.wd-list`'s overflow already follows.
        """
        css = self.client.get(reverse('home')).content.decode()

        for selector in (
            '.pit-crew-chip.is-unassigned:not(.is-active)',
            '.pit-crew-chip.is-unassigned:not(.is-active) .n',
        ):
            self.assertIn(selector, css)

        # The bare form would win over `.is-active` on document order again.
        self.assertNotIn('.pit-crew-chip.is-unassigned {\n        color:', css)

    def test_the_selected_red_fill_is_the_measured_one(self):
        """`#dc2626`, not the page's decorative `--pit-red` (#ef4444).

        This fill carries white text at 13.6px bold, where `--pit-red` measures
        **3.77:1** — under the 4.5:1 that size needs. `#dc2626` measures
        **4.83:1**. `--pit-red` stays what it is for the header hairline, which
        carries no text.
        """
        css = self.client.get(reverse('home')).content.decode()

        self.assertIn('.pit-crew-chip.is-unassigned { --tint: #dc2626; }', css)

    def test_the_completed_today_figure_ignores_the_chip(self):
        """A mechanic filter is a way of reading the floor, not another workshop.

        That figure counts a different population — cars that left today — so
        narrowing it would answer a question nobody asked.
        """
        done = self.card(self.hijaz, completed=True)
        JobCard.objects.filter(pk=done.pk).update(completed_date=date.today())

        plain = self.client.get(reverse('home'))
        filtered = self.client.get(reverse('home'), {'mechanic': self.amlah.pk})

        self.assertEqual(plain.context['completed_count'], 1)
        self.assertEqual(filtered.context['completed_count'], 1)
