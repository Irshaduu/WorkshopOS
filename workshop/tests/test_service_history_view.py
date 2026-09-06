"""
The service history as PAGES — the options step, the sheet, and the door in.

`test_service_history.py` covers the arithmetic. This file covers everything
between that module and a customer's hands: who may open it, what the tick
boxes do, what happens to a reading that cannot be true, that there is always a
way out, and — the class that matters most — that nothing the workshop keeps to
itself can reach a document it hands over.
"""

import re
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Category, Item
from workshop.models import (
    JobCard, JobCardConcern, JobCardLabourItem, JobCardSpareItem, SpareShop,
)


REG = 'KL 10 AA 1000'
EVERYTHING = {'amount': '1', 'work': '1', 'concerns': '1'}


class ServiceHistoryPageTestCase(TestCase):

    def setUp(self):
        Group.objects.get_or_create(name='Floor')
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='office', password='pw')

        self.shop = SpareShop.objects.create(name='Pullara Spares')
        self.category = Category.objects.create(name='Engine Oil')
        self.product = Item.objects.create(
            category=self.category, name='Castrol Edge 5W-30',
            average_stock=Decimal('40'), current_stock=Decimal('500'),
            avg_cost=Decimal('420'),
        )

    def _visit(self, admitted, mileage='60000', parts=(), completed=True, **kwargs):
        defaults = dict(
            admitted_date=admitted, brand_name='Audi', model_name='A4',
            registration_number=REG, mileage=mileage, completed=completed,
            completed_date=admitted if completed else None,
        )
        defaults.update(kwargs)
        card = JobCard.objects.create(**defaults)
        for name in parts:
            JobCardSpareItem.objects.create(
                job_card=card, spare_part_name=name,
                source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
                quantity=Decimal('1'), total_price=Decimal('1000'),
            )
        return card

    def _options_url(self, registration=REG):
        return reverse('car_service_history', args=[registration])

    def _sheet_url(self, registration=REG):
        return reverse('car_service_history_sheet', args=[registration])

    def _render(self, params=None, registration=REG):
        response = self.client.get(
            self._sheet_url(registration),
            EVERYTHING if params is None else params,
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _sheet(self, params=None, registration=REG):
        """
        Just the printed sheet, with the inlined letterhead removed.

        Both halves matter for a "this must not appear" assertion. Searching
        the whole PAGE finds the toolbar and the stylesheet, so a class name in
        CSS reads as a value on the paper. And the letterhead is a base64 data
        URI tens of thousands of characters long, so a bare figure like '900'
        matches inside it and the test fails for a reason nothing to do with
        the document — which is how a real leak gets dismissed as a flaky
        assertion.
        """
        html = self._render(params, registration)
        sheet = html[html.index('<div class="sheet"'):]
        return re.sub(r'base64,[A-Za-z0-9+/=]+', 'base64,LETTERHEAD', sheet)


class WhoMayOpenItTests(ServiceHistoryPageTestCase):
    """
    `@office_required` on both routes, matching the car profile they are opened
    from. Floor is shown no money anywhere in this app and this sheet is money
    end to end.
    """

    def test_office_may_open_both(self):
        self._visit(date(2026, 1, 1))
        self.assertEqual(self.client.get(self._options_url()).status_code, 200)
        self.assertEqual(self.client.get(self._sheet_url()).status_code, 200)

    def test_floor_is_refused_on_both_with_a_403(self):
        """
        A signed-in user who simply lacks the role gets PermissionDenied. A
        redirect to the sign-in form would show a login screen to somebody
        already signed in — the app-wide rule for these decorators.
        """
        self._visit(date(2026, 1, 1))
        floor = User.objects.create_user(username='floor', password='pw')
        floor.groups.add(Group.objects.get(name='Floor'))
        client = Client()
        client.login(username='floor', password='pw')
        self.assertEqual(client.get(self._options_url()).status_code, 403)
        self.assertEqual(client.get(self._sheet_url()).status_code, 403)

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        self._visit(date(2026, 1, 1))
        response = Client().get(self._sheet_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_a_registration_with_no_cards_is_a_404_on_both(self):
        """The same answer the profile page gives — not a car this workshop knows."""
        for url in (self._options_url('KL 99 ZZ 9999'),
                    self._sheet_url('KL 99 ZZ 9999')):
            self.assertEqual(self.client.get(url).status_code, 404)


class ChoosingWhatGoesOnTheCopyTests(ServiceHistoryPageTestCase):
    """
    The page between the button and the document. It exists for the one
    question nothing in the database can answer — what is the car reading now —
    and asks the other three while it has the person's attention.
    """

    def test_every_box_starts_ticked(self):
        self._visit(date(2026, 1, 1))
        html = self.client.get(self._options_url()).content.decode()
        self.assertEqual(html.count('type="checkbox"'), 3)
        self.assertEqual(html.count('value="1" checked'), 3)

    def test_submitting_opens_the_sheet_carrying_the_choices(self):
        self._visit(date(2026, 1, 1))
        response = self.client.get(
            self._options_url(), dict(EVERYTHING, go='1', km='70000'))
        self.assertEqual(response.status_code, 302)
        for expected in ('amount=1', 'work=1', 'concerns=1', 'km=70000'):
            self.assertIn(expected, response['Location'])

    def test_an_unticked_box_simply_does_not_travel(self):
        """
        ⚠ THE REASON `go` EXISTS. An unticked checkbox sends nothing at all, so
        without a marker "the user unticked Amount" and "the page has just
        opened" are the identical empty payload — and the boxes could never
        default to ticked.
        """
        self._visit(date(2026, 1, 1))
        response = self.client.get(self._options_url(), {'go': '1', 'work': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('work=1', response['Location'])
        self.assertNotIn('amount', response['Location'])
        self.assertNotIn('concerns', response['Location'])

    def test_a_reading_below_the_last_visit_is_refused_on_the_page(self):
        """
        Not redirected and not silently dropped: the person is on the phone
        with the customer and can ask again.
        """
        self._visit(date(2026, 1, 1), '120000')
        response = self.client.get(
            self._options_url(), dict(EVERYTHING, go='1', km='90000'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cannot')
        # Indian grouping, like every other figure in this app.
        self.assertContains(response, '1,20,000')

    def test_a_reading_that_is_not_a_number_is_refused(self):
        self._visit(date(2026, 1, 1), '120000')
        response = self.client.get(
            self._options_url(), dict(EVERYTHING, go='1', km='about 130k'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not a reading this can use')

    def test_what_was_typed_survives_the_refusal(self):
        """Retyping a six-digit number you just read out is the friction."""
        self._visit(date(2026, 1, 1), '120000')
        response = self.client.get(
            self._options_url(), dict(EVERYTHING, go='1', km='90000'))
        self.assertContains(response, 'value="90000"')

    def test_no_reading_at_all_is_fine(self):
        self._visit(date(2026, 1, 1))
        response = self.client.get(self._options_url(), dict(EVERYTHING, go='1'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('km=', response['Location'])


class WhatTheTicksDoTests(ServiceHistoryPageTestCase):

    def _loaded(self):
        card = self._visit(date(2026, 1, 1), '60000', ['Oil Filter'],
                           labour_amount=Decimal('4000'))
        JobCardConcern.objects.create(
            job_card=card, concern_text='Noise from the front left')
        JobCardLabourItem.objects.create(
            job_card=card, job_description='Wheel bearing replaced')
        card.update_totals()
        return card

    def test_all_three_on(self):
        self._loaded()
        html = self._sheet(EVERYTHING)
        self.assertIn('Noise from the front left', html)
        self.assertIn('Wheel bearing replaced', html)
        self.assertIn('AMOUNT', html)

    def test_all_three_off(self):
        self._loaded()
        html = self._sheet({})
        self.assertNotIn('Noise from the front left', html)
        self.assertNotIn('Wheel bearing replaced', html)
        self.assertNotIn('AMOUNT', html)

    def test_the_parts_are_never_optional(self):
        """
        They are the document. Everything else is a choice about who this copy
        is for.
        """
        self._loaded()
        self.assertIn('Oil Filter', self._sheet({}))

    def test_the_reading_reaches_the_running_figures(self):
        self._visit(date(2024, 1, 1), '60000', ['Oil Filter'])
        self._visit(date(2026, 1, 1), '80000', ['Oil Filter'])

        without = self._sheet({})
        with_km = self._sheet({'km': '95000'})
        self.assertNotIn('15,000 km', without)
        self.assertIn('15,000 km', with_km)
        self.assertIn('as told by the customer', with_km)


class NothingInternalReachesTheCustomerTests(ServiceHistoryPageTestCase):
    """
    ⚠ THE MOST IMPORTANT CLASS IN THIS FILE.

    This document is handed to a customer and, when the car is sold, to a
    stranger. Everything the workshop keeps to itself has to stay off it — and
    each item below is on the job card the sheet is built from, so each is one
    careless template line away from being printed.
    """

    def _loaded_card(self):
        card = self._visit(date(2026, 1, 1), '60000', labour_amount=Decimal('4000'),
                           notes='Owner is fussy — do not wash')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Pads - Front',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=Decimal('1'),
            unit_price=Decimal('5500'),       # the workshop's COST
            total_price=Decimal('8000'),      # what the customer pays
        )
        JobCardSpareItem.objects.create(
            job_card=card, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.product, quantity=Decimal('5'),
            customer_rate=Decimal('1200'),
        )
        card.update_totals()
        card.discount_amount = Decimal('900')
        card.received_amount = Decimal('12100')
        card.payment_status = 'PAID'
        card.save()
        return card

    def test_the_workshops_own_COST_is_nowhere_on_the_sheet(self):
        """
        `unit_price` is what the shop charged the workshop. Printing it hands
        the customer the margin on every part.
        """
        self._loaded_card()
        self.assertNotIn('5,500', self._sheet())

    def test_the_supply_chain_is_nowhere_on_the_sheet(self):
        """
        Not the spare shop's name, and not the branded SKU behind a warehouse
        draw. A customer document names the CATEGORY — 'Engine Oil', never
        'Castrol Edge 5W-30'.
        """
        self._loaded_card()
        html = self._sheet()
        self.assertNotIn('Pullara Spares', html)
        self.assertNotIn('Castrol', html)
        self.assertIn('Engine Oil', html)

    def test_the_internal_note_never_reaches_the_customer(self):
        self._loaded_card()
        self.assertNotIn('do not wash', self._sheet())

    def test_the_discount_is_not_printed(self):
        """
        The workshop's own write-off, agreed verbally at the counter. Printing
        it invites renegotiating a figure nobody was quoted — the reason
        `settlement()` keeps it off the invoice too.
        """
        self._loaded_card()
        self.assertNotIn('900.00', self._sheet())

    def test_no_payment_state_appears_anywhere(self):
        """
        A record of WORK, not of debt. A customer handing this to a buyer
        should not be handing over their own payment history with it.
        """
        self._loaded_card()
        html = self._sheet().lower()
        for word in ('fully paid', 'pending', 'unpaid', 'outstanding', 'balance due'):
            self.assertNotIn(word, html)

    def test_the_bill_TOTAL_is_printed_because_that_is_what_the_invoice_said(self):
        """
        ₹4,000 labour + ₹8,000 pads + ₹6,000 of oil = ₹18,000, which is what
        the customer's own invoice totals — not the ₹17,100 of revenue left
        after the discount.
        """
        card = self._loaded_card()
        self.assertEqual(card.total_bill_amount, Decimal('18000'))
        self.assertIn('18,000.00', self._sheet())


class ThereIsAlwaysAWayOutTests(ServiceHistoryPageTestCase):
    """
    A standalone template carries no nav bar and no drawer, and in the
    installed app there is no browser Back button either.
    """

    def test_it_falls_back_to_the_car_it_describes_never_to_home(self):
        self._visit(date(2026, 1, 1))
        profile = reverse('car_profile_detail', args=[REG])
        self.assertIn(f'href="{profile}"', self._render())

    def test_a_same_site_back_is_honoured(self):
        self._visit(date(2026, 1, 1))
        html = self._render(dict(EVERYTHING, back='/completed/?filter=today'))
        self.assertIn('href="/completed/?filter=today"', html)

    def test_an_off_site_back_is_refused_and_the_car_stands_in(self):
        """
        The value ends up in an href on a page about to be handed to a
        customer, so it is checked rather than trusted.
        """
        self._visit(date(2026, 1, 1))
        html = self._render(dict(EVERYTHING, back='https://evil.example/x'))
        self.assertNotIn('evil.example', html)
        self.assertIn(f'href="{reverse("car_profile_detail", args=[REG])}"', html)

    def test_the_sheet_offers_a_way_back_to_the_choices(self):
        """
        Changing one tick must not mean setting all four again — and without
        this the only route back is the browser's Back button, which the
        installed app does not have.
        """
        self._visit(date(2026, 1, 1))
        html = self._render()
        self.assertIn(self._options_url(), html)
        self.assertIn('edit=1', html)


class TheChangeLinkActuallyOpensTheChoicesTests(ServiceHistoryPageTestCase):
    """
    ⚠ IT DID NOT, AND IT LOOKED LIKE A DEAD BUTTON.

    The link has to carry the current ticks or changing one would mean setting
    them all again — so it carried `go`, the FORM's own marker. The options
    view reads `go` as "this was submitted, open the sheet", so following the
    link fired one 302 straight back to the sheet the person was standing on:
    nothing on screen, nothing in the console, a button that did nothing.

    Two markers now, because these are two questions: `edit` says read the
    ticks literally, `go` says read them AND leave.
    """

    def _change_link(self, params=None):
        html = self._render(params)
        match = re.search(r'href="([^"]*service-history/\?[^"]*)"', html)
        self.assertIsNotNone(match, 'the sheet carries no link back to the choices')
        return match.group(1).replace('&amp;', '&')

    def test_following_it_renders_the_choices_instead_of_bouncing_back(self):
        self._visit(date(2026, 1, 1))
        response = self.client.get(self._change_link())
        self.assertEqual(
            response.status_code, 200,
            'the Change link redirected instead of opening the options page',
        )
        self.assertContains(response, 'What to include')

    def test_it_arrives_carrying_exactly_what_was_chosen(self):
        """
        The whole reason it cannot simply drop the marker: with nothing said,
        the page defaults every box to ticked, so an unticked one would come
        back ticked and the next print would carry a column somebody had
        deliberately removed.
        """
        self._visit(date(2026, 1, 1))
        html = self.client.get(
            self._change_link({'amount': '1'})).content.decode()

        checked = re.findall(r'name="(\w+)" value="1"( checked)?', html)
        self.assertIn(('amount', ' checked'), checked)
        self.assertIn(('work', ''), checked)
        self.assertIn(('concerns', ''), checked)

    def test_a_fresh_arrival_still_defaults_to_everything(self):
        """The marker must not leak into the ordinary door in from the car."""
        self._visit(date(2026, 1, 1))
        html = self.client.get(self._options_url()).content.decode()
        for key in ('amount', 'work', 'concerns'):
            self.assertIn(f'name="{key}" value="1" checked', html)

    def test_the_form_itself_still_leaves_for_the_sheet(self):
        """`go` is untouched — it is what the submit button posts."""
        self._visit(date(2026, 1, 1))
        response = self.client.get(self._options_url(), {'go': '1', 'amount': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(self._sheet_url(), response['Location'])


class TheSheetItselfTests(ServiceHistoryPageTestCase):

    def test_the_newest_visit_is_at_the_top(self):
        self._visit(date(2024, 1, 1), '60000')
        self._visit(date(2026, 1, 1), '92000')

        html = self._sheet()
        chain = html[html.index('sh-chain'):]
        self.assertLess(chain.index('92,000'), chain.index('60,000'))

    def test_the_join_between_two_visits_carries_both_figures(self):
        self._visit(date(2026, 1, 1), '60000')
        self._visit(date(2026, 4, 11), '69800')

        html = self._sheet()
        self.assertIn('9,800 km', html)
        self.assertIn('100 days', html)

    def test_the_chain_ends_rather_than_stopping(self):
        """
        Without this the oldest card reads as though the record were cut off
        there, which on a document a buyer is checking is the worst possible
        ambiguity.
        """
        self._visit(date(2022, 2, 3), '94200')
        self._visit(date(2026, 1, 1), '120000')
        html = self._sheet()
        self.assertIn('FIRST VISIT', html)
        self.assertIn('94,200 km', html)

    def test_a_car_in_the_workshop_is_named_rather_than_silently_omitted(self):
        self._visit(date(2026, 1, 1))
        self._visit(date(2026, 6, 1), completed=False)
        self.assertIn('in the workshop now', self._sheet())

    def test_a_car_with_no_completed_visit_still_renders(self):
        """A first visit, still on the floor. Nothing here may 500."""
        self._visit(date(2026, 6, 1), completed=False)
        self.assertIn('No completed visits', self._sheet())

    def test_the_record_closes_with_what_the_car_has_cost_here(self):
        """
        The sheet printed an AMOUNT on every card and never added them up, so
        the one figure a customer asks for out loud was the one thing they had
        to work out themselves.

        ⚠ AND IT ADDS UP FROM THE ROWS ABOVE IT. `summary.total_billed` is the
        sum of those very AMOUNT figures, so a reader can check the closing
        line against the page it closes — the Cashbook's own rule.
        """
        first = self._visit(date(2026, 1, 1), '60000')
        second = self._visit(date(2026, 4, 1), '69800')
        for card, amount in ((first, '12000'), (second, '10500')):
            card.labour_amount = Decimal(amount)
            card.update_totals()

        html = self._sheet()
        self.assertIn('TOTAL BILLED', html)
        self.assertIn('22,500.00', html)

    def test_the_closing_total_goes_when_the_amounts_do(self):
        """
        A lone figure under a list carrying none would be the sheet answering
        a question it had just refused to ask.
        """
        card = self._visit(date(2026, 1, 1), '60000')
        card.labour_amount = Decimal('12000')
        card.update_totals()
        self.assertNotIn('TOTAL BILLED', self._sheet({}))

    def test_the_closing_total_wears_the_bills_own_total_treatment(self):
        """
        14pt on #bdd7ee is the invoice's TOTAL and nothing else on this sheet
        may wear it — that is what makes this line read as the end. Asserted on
        the declaration because nothing in the Django suite executes CSS.
        """
        self._visit(date(2026, 1, 1), '60000')
        html = self._render()
        self.assertIn('grand-amount', html)
        self.assertIn('#bdd7ee', html)
        self.assertIn('font-size: 14pt', html)

    def test_one_figure_one_heading_across_both_tables(self):
        """
        The visit cards and PART LIFE print the same number — how far this
        fitting has run — and it was unheaded on one and called LASTED on the
        other. LASTED is also untrue of the part still on the car, which is the
        row a reader cares most about.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        self._visit(date(2026, 4, 1), '69800', parts=['Wheel bearing left'])

        html = self._sheet()
        self.assertEqual(html.count('DISTANCE RUN'), 3)   # two cards, one table
        self.assertNotIn('LASTED', html)

    def test_the_asterisk_legend_appears_only_when_something_carries_one(self):
        """
        A legend explaining a mark that is nowhere on the page is the same
        defect as a door somebody can see and cannot open.
        """
        self._visit(date(2026, 1, 1), '60000')
        self._visit(date(2026, 3, 1), '70000')
        self.assertNotIn('unusually large', self._sheet())

        JobCard.objects.filter(admitted_date=date(2026, 3, 1)).update(mileage='600000')
        self.assertIn('unusually large', self._sheet())

    def test_the_title_is_the_saved_pdf_name(self):
        self._visit(date(2026, 1, 1))
        self.assertIn(
            '<title>Audi A4 KL 10 AA 1000 (Service History)</title>',
            self._render(),
        )

    def test_the_page_loads_nothing_from_a_third_party(self):
        """
        The invoice's rule, and this sheet is held to it for the same reason: a
        document that arrives unstyled because a CDN is slow is not a document.

        Asserted on what causes a REQUEST, never on the string "http" — every
        SVG declares `xmlns="http://www.w3.org/2000/svg"`, a namespace NAME
        that no browser resolves. The blunt check fails on it and pushes
        somebody towards deleting the namespace or the test.
        """
        self._visit(date(2026, 1, 1))
        html = self._render()

        self.assertNotIn('cdn.', html)
        self.assertNotIn('<link', html)
        self.assertNotIn('@import', html)
        self.assertNotIn('url(http', html)

        for attribute, value in re.findall(r'\b(src|href)="([^"]*)"', html):
            self.assertFalse(
                value.startswith(('http://', 'https://', '//')),
                f'{attribute}="{value}" points off this origin',
            )

        for match in re.findall(r'https?://[^\s"\'<>)]+', html):
            self.assertIn(
                match,
                ('http://www.w3.org/2000/svg', 'http://www.w3.org/1999/xlink'),
                f'unexpected third-party reference on the sheet: {match}',
            )

    def test_nothing_on_the_sheet_posts(self):
        """
        A record of what already happened. The only controls are in the
        toolbar, which is not on the paper.
        """
        self._visit(date(2026, 1, 1))
        sheet = self._sheet()
        self.assertNotIn('<form', sheet)
        self.assertNotIn('<input', sheet)


class PartLifeCanBeLeftOffThisCopyTests(ServiceHistoryPageTestCase):
    """
    A tick beside Print, not a fourth box on the options page.

    It is a decision about THIS copy taken at the moment of printing, and the
    answer is visible the instant it is tapped — the table leaves the sheet on
    screen exactly as it leaves the paper, so nobody has to take the result on
    trust. On the options page it would have cost a round trip to see.
    """

    def _with_a_repeated_part(self):
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        self._visit(date(2026, 4, 1), '69800', parts=['Wheel bearing left'])

    def test_the_tick_is_offered_and_starts_on(self):
        self._with_a_repeated_part()
        self.assertIn('id="lifeTick" checked', self._render())
        self.assertIn('PART LIFE', self._sheet())

    def test_it_is_not_offered_on_a_car_with_no_part_life_to_hide(self):
        """
        A switch for a table that is not there is the same defect as a door
        somebody can see and cannot open — the rule the audit menu and the
        asterisk legend already follow.

        ⚠ THE HEADING IS CHECKED ON THE SHEET, NEVER ON THE PAGE. "PART LIFE"
        is also the banner over that table's own block in the stylesheet, and
        a `<style>` element is served — the trap CLAUDE.md records for retired
        copy left in a CSS comment, hit here by a comment that is not retired
        at all.
        """
        self._visit(date(2026, 1, 1), '60000')
        self.assertNotIn('id="lifeTick"', self._render())
        self.assertNotIn('PART LIFE', self._sheet())

    def test_the_tick_never_reaches_the_paper(self):
        """
        `.no-print` is the boundary, and it is absolute — the toolbar is the
        workshop's, the sheet is the customer's.

        The ELEMENT, not the name: `_sheet()` runs to the end of the document,
        so it also holds the script that reads this box by id.
        """
        self._with_a_repeated_part()
        self.assertNotIn('id="lifeTick"', self._sheet())

    def test_nothing_is_remembered_between_prints(self):
        """
        Not stored and not in the URL, so a re-print starts from the full
        record. A default that quietly dropped a section from a document being
        handed to a customer is a worse failure than one extra tap.
        """
        self._with_a_repeated_part()
        for _ in range(2):
            self.assertIn('PART LIFE', self._sheet())


class TheToolbarIsOneRowOnAPhoneTests(ServiceHistoryPageTestCase):
    """
    Four controls, one row, 375px — and the two captions that were spent to
    buy it (the owner's call, 2026-09-06).

    Nothing in the Django suite executes CSS, so the declarations are asserted
    directly. The alternative is a layout rule nothing protects, on the screen
    where a wrapped toolbar costs 60px of a document.
    """

    def test_the_way_back_says_back_rather_than_naming_the_car(self):
        """
        ⚠ AND IT IS MORE HONEST, NOT ONLY SHORTER. `back_url` is `?back=` when
        one was carried and the car's profile otherwise, so the plate was a
        named destination that named the wrong thing on every sheet opened
        from anywhere else. The invoice and the spare shop's printed report
        both say plain "Back" in exactly this case.
        """
        self._visit(date(2026, 1, 1))
        html = self._render()
        bar = html[html.index('class="bar'):html.index('<div class="sheet"')]
        self.assertIn('>\n            Back\n', bar)
        self.assertNotIn(REG, bar)

    def test_the_options_link_is_the_cog_alone_and_still_says_what_it_is(self):
        """
        Every pill that can go icon-only carries an `aria-label` — the app's
        own rule. `title` too, because the hover word is the one thing dropping
        the caption actually costs.
        """
        self._visit(date(2026, 1, 1))
        html = self._render()
        bar = html[html.index('class="bar'):html.index('<div class="sheet"')]
        self.assertIn('btn-icon', bar)
        self.assertIn('aria-label="Change what is on this copy"', bar)
        self.assertIn('title="Change what is on this copy"', bar)
        self.assertNotIn('>\n            Change\n', bar)

    def test_the_phone_block_does_not_break_the_row(self):
        """
        The spacer stays a spacer. Turning it into a full-width line break is
        the invoice's answer to a row of FIVE things; this row holds four and
        they fit, so the break would spend a whole row on a gap and strand
        Print at the start of the second one.
        """
        self._visit(date(2026, 1, 1))
        html = self._render()
        self.assertIn('.bar .btn {\n                flex: 0 0 auto;\n            }', html)
        self.assertNotIn('flex: 0 0 100%', html)

    def test_the_icon_button_is_a_full_target_on_both_axes(self):
        """A target is only as big as its smaller side."""
        self._visit(date(2026, 1, 1))
        self.assertIn('.btn-icon {\n            padding: 0;\n            min-width: 44px;\n        }',
                      self._render())


class TheDoorOnTheCarProfileTests(ServiceHistoryPageTestCase):
    """A feature nobody can find is a feature nobody has."""

    def test_the_profile_links_to_the_options_page(self):
        self._visit(date(2026, 1, 1))
        response = self.client.get(reverse('car_profile_detail', args=[REG]))
        self.assertContains(response, self._options_url())
        self.assertContains(response, 'Service History')

    def test_the_link_is_offered_even_on_a_car_with_no_completed_visit(self):
        """
        A button that disappears on some cars is one nobody learns is there,
        and the sheet handles that case honestly on its own.
        """
        self._visit(date(2026, 6, 1), completed=False)
        response = self.client.get(reverse('car_profile_detail', args=[REG]))
        self.assertContains(response, self._options_url())
