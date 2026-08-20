"""
Car Profiles — the list of cars, and one car's history.

The redesign sat on top of three real defects, and each is asserted here rather
than the styling that revealed them:

  * the list template read `search_query`, a name the view has never passed, so
    a search's own pagination links dropped the query and page 2 of a search
    returned page 2 of every car in the workshop;
  * the detail view loaded a car's entire history with no pager;
  * its summary figures would, if totalled from the rows, describe one page
    while being labelled as the whole car.
"""

from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from workshop.models import JobCard, JobCardConcern, JobCardSpareItem, Mechanic
from workshop.views.car_profiles import VISITS_PER_PAGE

AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


class CarProfileBase(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='off', password='pw')
        self.user.groups.add(Group.objects.get(name='Office'))
        self.client = Client()
        self.client.login(username='off', password='pw')
        self.mechanic = Mechanic.objects.create(name='Ramesh')

    def visit(self, reg, day, **overrides):
        fields = dict(
            admitted_date=day, brand_name='Audi', model_name='A4',
            registration_number=reg, customer_name='Rahim',
            customer_contact='9567494933', mileage='51000',
            lead_mechanic=self.mechanic,
        )
        fields.update(overrides)
        return JobCard.objects.create(**fields)


class TheSearchSurvivesItsOwnPaginationTests(CarProfileBase):
    def setUp(self):
        super().setUp()
        for n in range(3):
            self.visit(f'KL01AA{1000 + n}', date(2026, 8, 1) - timedelta(days=n))
        self.visit('KL99ZZ9999', date(2026, 7, 1), customer_name='Somebody Else')

    def test_the_box_comes_back_holding_what_was_searched_for(self):
        """
        It rendered `search_query`, which this view has never passed — so the
        box emptied itself and the pager below it lost the query with it.
        """
        page = self.client.get(reverse('car_profile_list'), {'q': 'KL99'}).content.decode()
        self.assertIn('value="KL99"', page)
        self.assertIn('KL99ZZ9999', page)

    def test_a_bare_visit_still_shows_every_car(self):
        """
        The intent behind the old "clear on full refresh" survives: it is the
        ABSENCE of `?q=` that shows everything, not the request being a full
        page load.
        """
        page = self.client.get(reverse('car_profile_list')).content.decode()
        self.assertIn('KL99ZZ9999', page)
        self.assertIn('KL01AA1000', page)

    def test_pagination_links_carry_the_query(self):
        """
        Page 2 of a search must be page 2 of the SEARCH. With the query dropped
        it silently returned page 2 of every car, which looks like results.
        """
        for n in range(50):
            self.visit(f'KL07BB{2000 + n}', date(2026, 6, 1) - timedelta(days=n))
        body = self.client.get(
            reverse('car_profile_list'), {'q': 'KL07'}, **AJAX
        ).content.decode()
        self.assertIn('page=2', body)
        self.assertIn('q=KL07', body)

    def test_following_that_link_really_does_page_the_search(self):
        """
        The half the markup cannot prove. The pager is an ordinary <a>, so
        following it is a FULL page load — which used to discard `q` and answer
        with page 2 of every car in the workshop.
        """
        for n in range(50):
            self.visit(f'KL07BB{2000 + n}', date(2026, 6, 1) - timedelta(days=n))
        resp = self.client.get(reverse('car_profile_list'), {'q': 'KL07', 'page': '2'})
        regs = [car['registration'] for car in resp.context['car_profiles']]
        self.assertTrue(regs)
        self.assertTrue(all(reg.startswith('KL07') for reg in regs), regs)

    def test_searching_narrows_the_list(self):
        body = self.client.get(
            reverse('car_profile_list'), {'q': 'Somebody'}, **AJAX
        ).content.decode()
        self.assertIn('KL99ZZ9999', body)
        self.assertNotIn('KL01AA1000', body)


class TheListSaysWhichCarsAreHereTests(CarProfileBase):
    # Asserted on the MARKUP (`class="cp-live"`), never the bare class name:
    # `.cp-live` is also a stylesheet rule on this page, so a whole-page search
    # for it matches on every render and the negative test would pass by
    # asserting nothing. Same trap the invoice's `.paid-box` tests document.
    def test_a_car_with_an_open_card_is_flagged(self):
        self.visit('KL01AA1000', date(2026, 8, 1), completed=False)
        body = self.client.get(reverse('car_profile_list')).content.decode()
        self.assertIn('class="cp-live"', body)

    def test_a_car_whose_last_card_is_closed_is_not(self):
        self.visit('KL01AA1000', date(2026, 8, 1),
                   completed=True, completed_date=date(2026, 8, 2))
        body = self.client.get(reverse('car_profile_list')).content.decode()
        self.assertNotIn('class="cp-live"', body)

    def test_a_car_recorded_with_no_make_still_has_a_headline(self):
        """
        There is always exactly one big line on a card. A job card can
        legitimately carry neither make nor model, and a blank where the name
        goes reads as a broken row rather than as missing data.
        """
        self.visit('KL13Q9021', date(2026, 8, 1), brand_name='', model_name='')
        body = self.client.get(reverse('car_profile_list')).content.decode()
        self.assertIn('cp-name', body)
        self.assertIn('KL13Q9021', body)


class TheSearchLooksLikeCompletedsTests(CarProfileBase):
    """
    Car Profiles and Completed are opened one after the other all day, and a
    search box that changes shape between them reads as two different products.
    The owner asked for them to match; this keeps them matching.

    A static scan of the two stylesheets rather than a rendered comparison —
    Django's test client runs no CSS engine, so the only thing a request-level
    test could compare is markup, which is not where the difference would be.
    Every property below was verified equal in a real browser when it was
    written; this catches one of them being edited afterwards.
    """

    SHARED = ('border-radius: 999px', 'font-size: 0.8rem', 'height: 40px',
              'padding: 0 14px 0 32px', 'box-shadow: 0 1px 3px var(--color-shadow)')

    def _css(self, path):
        from django.conf import settings
        import os
        for directory in settings.TEMPLATES[0]['DIRS'] + [
                os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')]:
            candidate = os.path.join(directory, path)
            if os.path.exists(candidate):
                with open(candidate, encoding='utf-8') as handle:
                    return handle.read()
        self.fail(f"template not found: {path}")

    def test_both_search_boxes_declare_the_same_shape(self):
        cars = self._css('workshop/car_profiles/car_profile_list.html')
        completed = self._css('workshop/completed/completed_list.html')
        for declaration in self.SHARED:
            self.assertIn(declaration, completed,
                          f"Completed no longer declares {declaration!r} — if that "
                          f"page was restyled, restyle Car Profiles with it.")
            self.assertIn(declaration, cars,
                          f"Car Profiles must match Completed's search box: {declaration!r}")

    def test_the_car_search_uses_the_same_inset_icon(self):
        cars = self._css('workshop/car_profiles/car_profile_list.html')
        self.assertIn('bi bi-search search-icon', cars)
        self.assertIn('left: 12px', cars)


class GrossProfitTests(CarProfileBase):
    """
    The per-car gross profit — the labour charge plus the margin on both part
    routes, before wages, rent and every other overhead.

    Three properties matter more than the arithmetic: only an owner sees it, it
    is computed over the WHOLE history rather than the page, and it says so
    when its cost side is incomplete — because a missing `unit_price` counts as
    ₹0, which makes an uncosted part read as free and pushes the figure up with
    nothing on screen to say why.
    """

    def setUp(self):
        super().setUp()
        Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='own', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.owner_client = Client()
        self.owner_client.login(username='own', password='pw')

        self.reg = 'KL06XV3863'
        # Saving a spare fires `JobCard.update_totals()`, so the bill is NOT
        # whatever is passed in here — it is recomputed as parts + labour:
        #
        #   parts billed  ₹3,000 + ₹2,000        = ₹5,000
        #   labour                                 ₹4,000
        #   total_bill_amount                      ₹9,000
        #   less discount                        − ₹1,000
        #   revenue                                ₹8,000
        #   parts COST  ₹2,000 (the shop's line total, not a rate)
        #             + (4 x ₹250 warehouse average)  = ₹3,000
        #   gross profit                           ₹5,000  = 62.5%
        self.bill = self.visit(
            self.reg, date(2026, 8, 1),
            discount_amount=D('1000'), received_amount=D('8000'),
            payment_status='PAID', labour_amount=D('4000'))
        JobCardSpareItem.objects.create(
            job_card=self.bill, source=JobCardSpareItem.SOURCE_SHOP,
            # `unit_price` on a SHOP row is what the shop billed for the
            # line — ₹2,000 for the pair — never a per-unit rate. The warehouse
            # row below is the opposite and stays per unit, because there the
            # figure is an average taken off the shelf. See SHOP_LINE_COST.
            spare_part_name='Brake Pad', quantity=D('2'), unit_price=D('2000'),
            total_price=D('3000'))
        JobCardSpareItem.objects.create(
            job_card=self.bill, source=JobCardSpareItem.SOURCE_INVENTORY,
            spare_part_name='Engine Oil', quantity=D('4'), unit_price=D('250'),
            total_price=D('2000'))

    def owner_page(self):
        return self.owner_client.get(
            reverse('car_profile_detail', args=[self.reg]))

    def office_page(self):
        return self.client.get(reverse('car_profile_detail', args=[self.reg]))

    # ---- who sees it -------------------------------------------------

    def test_an_owner_sees_it(self):
        info = self.owner_page().context['car_info']
        self.assertEqual(info['gross_profit'], D('5000'))

    def test_office_does_not_and_it_is_not_even_computed(self):
        """
        Not merely hidden in the template: `None` from the view, so the two
        aggregates never run and there is no second role check to fall out of
        step with the first.

        Asserted on the MARKUP, never the bare phrase — "Gross profit" also
        appears in a CSS comment on this page, so a whole-page string search
        matches on every render and this test would pass by asserting nothing.
        The same trap the invoice's `.paid-box` tests document.
        """
        response = self.office_page()
        self.assertFalse(response.context['show_profit'])
        self.assertIsNone(response.context['car_info']['gross_profit'])
        self.assertNotIn('<dt>Gross profit</dt>', response.content.decode())

    def test_the_owner_page_prints_it(self):
        body = self.owner_page().content.decode()
        self.assertIn('<dt>Gross profit</dt>', body)
        self.assertIn('5,000', body)

    # ---- what it counts ----------------------------------------------

    def test_it_counts_BOTH_part_routes(self):
        """
        The double-count rule governs the workshop-wide Profit page, where a
        warehouse draw must not be charged again because a restock bill already
        paid for it. The question here is different — what did THIS car cost us
        — and a part off the shelf cost what the shelf paid for it.
        """
        info = self.owner_page().context['car_info']
        # ₹8,000 revenue − (₹2,000 shop + ₹1,000 warehouse)
        self.assertEqual(info['gross_profit'], D('5000'))

    def test_a_warehouse_draw_left_out_would_overstate_it(self):
        """The half of the rule that would fail silently: drop the draw's cost
        and the figure rises by exactly that cost, with nothing on screen
        different."""
        self.bill.spares.filter(
            source=JobCardSpareItem.SOURCE_INVENTORY).update(unit_price=None)
        info = self.owner_page().context['car_info']
        self.assertEqual(info['gross_profit'], D('6000'))   # ₹1,000 too high

    def test_the_percentage_is_of_revenue_after_discount(self):
        info = self.owner_page().context['car_info']
        self.assertAlmostEqual(float(info['gross_profit_pct']), 62.5, places=1)

    def test_a_car_with_no_bill_yet_reports_no_percentage(self):
        """Dividing by a ₹0 bill has no answer; it must not be 0% or a crash."""
        JobCard.objects.filter(pk=self.bill.pk).update(
            total_bill_amount=D('0'), discount_amount=D('0'))
        self.bill.spares.all().delete()
        info = self.owner_page().context['car_info']
        self.assertIsNone(info['gross_profit_pct'])

    def test_each_visit_carries_its_own_figure(self):
        bills = list(self.owner_page().context['bills'])
        self.assertEqual(bills[0].gross_profit, D('5000'))

    def test_the_headline_covers_every_visit_not_just_this_page(self):
        """
        Same rule as every other figure in the hero: summed in the database
        over the whole history, so page 2 cannot report a different total.
        """
        for n in range(VISITS_PER_PAGE + 2):
            self.visit(self.reg, date(2025, 1, 1) + timedelta(days=n),
                       total_bill_amount=D('100'), payment_status='PAID',
                       completed=True, completed_date=date(2025, 1, 2))
        info = self.owner_page().context['car_info']
        # Those extra visits carry no parts, so each is ₹100 of pure margin.
        self.assertEqual(info['gross_profit'], D('5000') + D('100') * (VISITS_PER_PAGE + 2))

    # ---- when it cannot be trusted -----------------------------------

    def test_an_uncosted_part_is_counted_and_declared(self):
        """
        `SPARE_COST` treats a missing `unit_price` as ₹0, so the part reads as
        free and the profit goes UP. That is the one way this figure is wrong
        without looking wrong, so the page has to say it.
        """
        self.bill.spares.filter(source=JobCardSpareItem.SOURCE_SHOP).update(unit_price=None)
        response = self.owner_page()
        self.assertEqual(response.context['car_info']['uncosted_parts'], 1)
        self.assertIn('no cost recorded', response.content.decode())

    def test_a_fully_costed_car_says_nothing(self):
        """A caveat printed on every car is a caveat nobody reads."""
        self.assertNotIn('no cost recorded', self.owner_page().content.decode())

    # ---- how it is named ---------------------------------------------

    def test_it_is_never_called_plain_profit(self):
        """
        Measured against live data it runs ~13 points above the workshop's real
        margin, because no wage, rent or power figure can be attributed to one
        car. "Gross" is the warning, and the Profit page is the true number.
        """
        body = self.owner_page().content.decode()
        self.assertIn('<dt>Gross profit</dt>', body)
        self.assertNotIn('<dt>Profit</dt>', body)
        self.assertIn('Before wages, rent and other overheads', body)


class OneCarsHistoryTests(CarProfileBase):
    def setUp(self):
        super().setUp()
        self.reg = 'KL11AJ2266'
        self.old = self.visit(
            self.reg, date(2026, 1, 5), completed=True, completed_date=date(2026, 1, 6),
            total_bill_amount=D('10000'), discount_amount=D('1000'),
            received_amount=D('9000'), payment_status='PAID')
        self.new = self.visit(
            self.reg, date(2026, 8, 1), customer_name='Rahim Kunhi',
            car_color='Red',
            total_bill_amount=D('5000'), payment_status='PENDING')
        JobCardConcern.objects.create(
            job_card=self.new, concern_text='Brake noise', status='WORKING')
        JobCardConcern.objects.create(
            job_card=self.new, concern_text='Aircon', status='FIXED')

    def page(self, **params):
        return self.client.get(
            reverse('car_profile_detail', args=[self.reg]), params)

    def test_a_car_with_no_visits_is_a_404(self):
        resp = self.client.get(reverse('car_profile_detail', args=['NOSUCHCAR']))
        self.assertEqual(resp.status_code, 404)

    def test_billed_to_date_is_the_profit_pages_definition_of_revenue(self):
        """
        `total_bill_amount − discount_amount`, summed. A discount is money never
        earned rather than an expense, and a second definition of "what this
        customer has paid us" is the one an owner ends up quoting at the counter.
        """
        info = self.page().context['car_info']
        self.assertEqual(info['billed'], D('14000'))     # (10000-1000) + 5000

    def test_outstanding_counts_only_what_is_still_owed(self):
        info = self.page().context['car_info']
        self.assertEqual(info['outstanding'], D('5000'))

    def test_the_hero_describes_the_car_as_it_is_now(self):
        info = self.page().context['car_info']
        self.assertEqual(info['customer'], 'Rahim Kunhi')
        self.assertTrue(info['on_floor'])
        self.assertEqual(info['visits'], 2)

    def test_visit_numbers_run_oldest_first(self):
        bills = list(self.page().context['bills'])
        self.assertEqual([b.pk for b in bills], [self.new.pk, self.old.pk])
        self.assertEqual([b.visit_number for b in bills], [2, 1])

    def test_the_row_carries_no_concern_text(self):
        """
        The first concern used to print in each row, truncated, with a "+4
        more". It was the only free-text line in the list, it made every row a
        different height, and a history is scanned for WHEN and HOW MUCH — the
        concerns are one tap away on the card itself.
        """
        body = self.page().content.decode()
        self.assertNotIn('Brake noise', body)
        self.assertNotIn('cd-visit-concern', body)

    def test_the_colour_is_the_rail_and_is_not_also_spelled_out(self):
        """"Red" printed beside a red bar is the same fact twice."""
        body = self.page().content.decode()
        self.assertIn('--cd-accent', body)
        self.assertNotIn('class="cd-chip">Red<', body)

    def test_the_history_is_paginated(self):
        for n in range(VISITS_PER_PAGE + 3):
            self.visit(self.reg, date(2025, 1, 1) + timedelta(days=n),
                       completed=True, completed_date=date(2025, 1, 2))
        resp = self.page()
        self.assertEqual(len(resp.context['bills']), VISITS_PER_PAGE)
        self.assertTrue(resp.context['page_obj'].has_next())

    def test_the_totals_describe_the_car_not_the_page(self):
        """
        With a pager, anything summed from the rows on screen would quietly
        start describing "this page" under a heading that says "this car".
        """
        for n in range(VISITS_PER_PAGE + 3):
            self.visit(self.reg, date(2025, 1, 1) + timedelta(days=n),
                       completed=True, completed_date=date(2025, 1, 2),
                       total_bill_amount=D('100'), received_amount=D('100'),
                       payment_status='PAID')
        info = self.page().context['car_info']
        self.assertEqual(info['visits'], VISITS_PER_PAGE + 5)
        self.assertEqual(info['billed'], D('14000') + D('100') * (VISITS_PER_PAGE + 3))

    def test_visit_numbers_do_not_restart_on_page_two(self):
        """
        They are chronological across the WHOLE history, so they must come from
        the page's offset. Derived from the page's own length instead, page 2
        would start again at 1 and two different visits would carry one number.
        """
        for n in range(VISITS_PER_PAGE + 3):
            self.visit(self.reg, date(2025, 1, 1) + timedelta(days=n),
                       completed=True, completed_date=date(2025, 1, 2))
        total = VISITS_PER_PAGE + 5

        page_one = [b.visit_number for b in self.page().context['bills']]
        page_two = [b.visit_number for b in self.page(page=2).context['bills']]

        self.assertEqual(page_one[0], total)                 # newest is the highest
        self.assertEqual(page_one[-1], total - VISITS_PER_PAGE + 1)
        self.assertEqual(page_two[0], page_one[-1] - 1)      # continues, never restarts
        self.assertEqual(page_two[-1], 1)                    # oldest is 1
        self.assertEqual(len(set(page_one + page_two)), total)

    def test_a_visit_row_has_no_invoice_button_of_its_own(self):
        """
        The job card a row opens carries its own Invoice link, so a button here
        was a second door to the same place — costing a column of width on a
        phone, forcing the row to reflow, and needing its own z-index to stay
        clickable above the row-wide link. The row is one target now.
        """
        body = self.page().content.decode()
        self.assertNotIn(reverse('invoice_view', args=[self.new.pk]), body)
        self.assertIn(reverse('jobcard_detail', args=[self.new.pk]), body)

    def test_the_total_is_labelled_in_words_the_owner_recognises(self):
        """
        "Billed to date" was the first wording and could not be read at a
        glance. Not "Total spent" either — that is the customer's side of the
        same number and it is wrong on exactly the cars that matter, because an
        unpaid bill has been billed and not spent.
        """
        body = self.page().content.decode()
        self.assertIn('Total billed', body)
        self.assertNotIn('Billed to date', body)

    def test_the_car_wears_its_own_colour(self):
        """
        Rail plus an 8% wash, at the same alpha `.lr-car` uses on the Live
        Report — one Red car has to be one red on every screen it appears on.
        """
        body = self.page().content.decode()
        self.assertIn('--cd-shade:', body)
        self.assertIn('14;', body)

    def test_a_car_with_no_colour_recorded_gets_no_wash(self):
        """
        A slate tint would say "this car is grey", which is a different fact
        from "nobody wrote it down".
        """
        JobCard.objects.filter(registration_number=self.reg).update(car_color='')
        body = self.page().content.decode()
        self.assertIn('cd-hero--unset', body)

    def test_a_row_never_puts_a_button_inside_a_link(self):
        """
        An <a> may not contain interactive content; browsers do not forgive it
        quietly — the parser closes the anchor and reopens it, which on the
        Estimates list split each row into four grid containers. The row uses a
        `.stretched-link` inside a <div> instead.
        """
        body = self.page().content.decode()
        row_start = body.index('cd-visit-body')
        row = body[row_start:row_start + 2500]
        self.assertIn('stretched-link', row)
