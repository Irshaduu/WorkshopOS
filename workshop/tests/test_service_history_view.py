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

    def test_each_tick_is_named_for_the_block_it_switches(self):
        """
        The sheet prints AMOUNT, WORK DONE and REPORTED. The ticks read "Job
        Performed" and "Customer Concerns", so a tick named one thing turned on
        a block called another — and each carried a hint line restating its
        own label.
        """
        self._visit(date(2026, 1, 1))
        html = self.client.get(self._options_url()).content.decode()
        form = html[html.index('sh-opt-form'):html.index('sh-opt-go')]
        for label in ('Amount', 'Work done', 'What was reported'):
            self.assertIn(f'<b>{label}</b>', form)
        for gone in ('Job Performed', 'Customer Concerns', '<small>'):
            self.assertNotIn(gone, form)

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
        # ⚠ WHOSE FIGURE IT IS IS SAID ONCE, IN THE NOTES. The TODAY row
        # printed `as told by the customer` beside it as well, and the note is
        # gated on the same reading — so the two always appeared together and
        # the sentence was on the page twice, once inside the record and once
        # in the block of statements about the document. The owner's call was
        # to drop the inline copy (2026-09-08).
        self.assertNotIn('as told by the customer', with_km)
        self.assertEqual(
            with_km.count("Today's reading was supplied by the customer"), 1)

        # And it is not claimed at all when nobody supplied one.
        self.assertNotIn('supplied by the customer', without)


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

    def test_a_discount_is_printed_and_NAMED(self):
        """
        ⚠ THIS TEST ASSERTED THE OPPOSITE UNTIL 2026-09-11. The discount was
        kept off on `settlement()`'s reasoning — a write-off agreed at the
        counter, which printing invites renegotiating. The owners reversed it
        for this document: Formula D discounts every customer on purpose and
        wants it seen. The invoice still prints none.

        ⚠ AND IT IS NAMED, NEVER LEFT AS A SECOND FIGURE. ₹18,000 beside
        ₹17,100 with nothing between them reads to a buyer as ₹900 still owed.
        """
        self._loaded_card()
        sheet = self._sheet()
        self.assertIn('&minus;900.00', sheet)
        card = sheet[sheet.index('sh-chain'):sheet.index('sh-total')]
        self.assertIn('DISCOUNT', card)
        self.assertLess(card.index('AMOUNT'), card.index('DISCOUNT'))

        # The discount is working, drawn plain; only AMOUNT keeps the shading.
        self.assertIn('<tr class="sh-calc">', card)

    def test_a_visit_with_no_discount_prints_no_discount_line(self):
        """Confirming what cannot surprise anyone is how a line stops being read."""
        card = self._visit(date(2026, 1, 1), '60000', labour_amount=Decimal('4000'))
        card.update_totals()
        sheet = self._sheet()
        self.assertNotIn('DISCOUNT', sheet)
        self.assertNotIn('NET TOTAL', sheet)
        self.assertIn('TOTAL BILLED', sheet)

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
        the customer's own invoice totals and what the visit's AMOUNT prints.
        The ₹17,100 left after the ₹900 discount is printed as well — as the
        NET TOTAL that closes the record, never in place of the AMOUNT.
        """
        card = self._loaded_card()
        self.assertEqual(card.total_bill_amount, Decimal('18000'))
        sheet = self._sheet()
        visit = sheet[sheet.index('sh-chain'):sheet.index('sh-total')]
        self.assertIn('18,000.00', visit)
        self.assertNotIn('17,100.00', visit)
        self.assertIn('17,100.00', sheet[sheet.index('sh-total'):])


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

    def test_an_odometer_that_went_backwards_is_said_on_the_card_itself(self):
        """
        The module has always decided this; nothing checked that the sheet
        PRINTS it — and the note moved into the card's own tinted block when
        the card was rebuilt, which is exactly the kind of markup change that
        can drop a line with every module test still green.

        Italic navy, because there is no red on this sheet.
        """
        self._visit(date(2024, 1, 1), '90000')
        self._visit(date(2026, 1, 1), '40000')

        sheet = self._sheet()
        self.assertIn('odometer may have been replaced', sheet)
        self.assertIn('sh-flag', sheet)

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

    def _discounted(self):
        """Two settled visits: ₹95,000 billed, ₹7,000 off, ₹88,000 net."""
        first = self._visit(date(2026, 1, 1), '60000')
        second = self._visit(date(2026, 4, 1), '69800')
        for card, amount, discount in ((first, '25000', '2000'),
                                       (second, '70000', '5000')):
            card.labour_amount = Decimal(amount)
            card.update_totals()
            card.discount_amount = Decimal(discount)
            card.received_amount = card.total_bill_amount - card.discount_amount
            card.payment_status = 'PAID'
            card.save()

    def _closing_block(self, sheet):
        close = sheet[sheet.index('sh-total'):]
        return close[:close.index('</table>')]

    def test_with_a_discount_the_record_closes_on_three_lines_that_add_up(self):
        """
        TOTAL BILLED, DISCOUNT, NET TOTAL — two plain working lines and one
        answer, in that order, with the arithmetic on the page:
        ₹95,000 − ₹7,000 = ₹88,000.
        """
        self._discounted()
        close = self._closing_block(self._sheet())
        self.assertLess(close.index('TOTAL BILLED'), close.index('DISCOUNT'))
        self.assertLess(close.index('DISCOUNT'), close.index('NET TOTAL'))
        self.assertIn('95,000.00', close)
        self.assertIn('&minus;7,000.00', close)
        self.assertIn('88,000.00', close)

        # NET TOTAL, not TOTAL BILLED, wears the bill's 14pt closing treatment.
        self.assertIn('grand-label">NET TOTAL', close)
        self.assertNotIn('grand-label">TOTAL BILLED', close)

        # ⚠ ONLY THE ANSWER IS SHADED. All three in the SUBTOTAL/TOTAL fills
        # read as clutter: three bold bands, nothing for the eye to land on.
        self.assertEqual(close.count('<tr class="sh-calc">'), 2)
        self.assertNotIn('sub-label', close)
        self.assertNotIn('sub-cell', close)

    def test_the_closing_figure_is_NET_never_PAID(self):
        """
        ⚠ A discount exists only on a settled card, but this total also counts
        completed visits nobody has paid for yet — so "TOTAL PAID" would claim
        that money too. Asserted with an unpaid visit on the record for exactly
        that reason.
        """
        self._discounted()
        unpaid = self._visit(date(2026, 8, 1), '80000')
        unpaid.labour_amount = Decimal('22000')
        unpaid.update_totals()

        close = self._closing_block(self._sheet())
        self.assertNotIn('PAID', close.upper())
        self.assertIn('1,10,000.00', close)   # 95,000 + 22,000 − 7,000

    def test_unticking_amount_removes_every_money_line(self):
        self._discounted()
        sheet = self._sheet({})
        paper = sheet[:sheet.index('<script')]
        for gone in ('AMOUNT', 'DISCOUNT', 'TOTAL BILLED', 'NET TOTAL'):
            self.assertNotIn(gone, paper)

    def test_the_working_lines_are_plain(self):
        """Asserted on the declaration: nothing in the suite executes CSS."""
        self._discounted()
        html = self._render()
        start = html.index('.inv-table .sh-calc td {')
        rule = html[start:html.index('}', start)]
        self.assertIn('background: #fff', rule)
        self.assertIn('font-weight: 400', rule)

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

    def test_how_far_a_fitting_ran_is_answered_in_exactly_one_place(self):
        """
        ⚠ THIS REPLACES `test_one_figure_one_heading_across_both_tables`, WHICH
        ASSERTED THE OPPOSITE AND WAS RIGHT AT THE TIME. That test required the
        heading to appear three times — once per visit card, once in PART LIFE
        — because the figure was printed in both and the two had drifted into
        two different words for it.

        The redesign removed the duplication instead of naming it better. Every
        fitting used to print with its distance on the card that fitted it AND
        again in PART LIFE: about thirty rows twice over on a five-visit car.
        The copy on the card was also the confusing one, because the distance a
        fitting RAN is a fact about its future, printed against the visit that
        began it — so a card dated March carried a number covering the two
        years after it.

        The rule now is the one this codebase follows everywhere else: **one
        question, one place.** The card says what happened that day; PART LIFE
        says how long a part lasts. So the heading appears exactly ONCE.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        self._visit(date(2026, 4, 1), '69800', parts=['Wheel bearing left'])

        html = self._sheet()
        self.assertEqual(html.count('DISTANCE RUN'), 1)
        self.assertNotIn('LASTED', html)

        # The card still names what was fitted — it is the distance that left,
        # not the part.
        self.assertIn('PARTS FITTED', html)
        self.assertIn('Wheel bearing left', html)

    def test_the_card_carries_no_figure_that_belongs_to_part_life(self):
        """
        The visit card's own columns went with the distance: a status chip and
        an instance number are both answers to "how long has this been on the
        car", which is the other section's question.

        Asserted on the CARD rather than on the page, because both marks are
        legitimate — and required — inside PART LIFE.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        self._visit(date(2026, 4, 1), '69800', parts=['Wheel bearing left'])

        html = self._sheet()
        chain = html[html.index('sh-chain'):html.index('PART LIFE')]
        self.assertNotIn('ON THE CAR', chain)
        self.assertNotIn('sh-run', chain)
        self.assertNotIn('sh-inst', chain)

    def test_a_fitting_is_numbered_bare_with_no_legend(self):
        """
        `(2)` `(1)` read as unprofessional, and the brackets were only there
        because the number sits in the bill's QTY column drawn like a quantity.
        Navy, it needs neither the brackets nor the note that explained them —
        the date beside it says which came first.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        self._visit(date(2026, 4, 1), '69800', parts=['Wheel bearing left'])

        sheet = self._sheet()
        life = sheet[sheet.index('sh-life'):]
        self.assertIn('class="sh-inst">2<', life)
        self.assertIn('class="sh-inst">1<', life)
        for gone in ('(1)', '(2)', 'first fitting', 'sh-note-life'):
            self.assertNotIn(gone, sheet)

    def test_the_number_is_navy_and_centred(self):
        """Asserted on the declaration: nothing in the suite executes CSS."""
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        html = self._render()
        start = html.index('.inv-table .sh-inst {')
        rule = html[start:html.index('}', start)]
        self.assertIn('#1f4e79', rule)
        self.assertIn('text-align: center', rule)
        self.assertNotIn('font-weight', rule)

    def test_the_average_sits_over_the_figures_it_averages(self):
        """
        "AVG 9,500 km" in the DISTANCE RUN column of the part's own name row,
        right-aligned over the distances it is the mean of — the owner's call.
        It trailed the name as a sentence first ("averages … km between
        changes"), then as "— AVG …".

        With only ONE finished life there is no average to show: the figure
        would be the number printed directly beneath it in row 1, so the note
        is left off rather than said twice.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        self._visit(date(2026, 4, 1), '69800', parts=['Wheel bearing left'])
        once = self._sheet()
        self.assertNotIn('AVG', once)
        self.assertNotIn('First one', once)

        self._visit(date(2026, 7, 1), '79000', parts=['Wheel bearing left'])
        sheet = self._sheet()
        head = sheet[sheet.index('sh-chain-head'):]
        head = head[:head.index('</tr>')]
        # The name alone on the left, across the first three columns...
        self.assertIn('<td colspan="3">', head)
        # ...and the average in the DISTANCE RUN column, right-aligned.
        self.assertIn('<td class="r sh-aside">AVG 9,500 km</td>', head)  # (9,800 + 9,200) / 2
        for gone in ('between changes', 'lasted', 'averages', 'First one', '— AVG'):
            self.assertNotIn(gone, sheet)

    def test_part_life_stands_apart_from_the_visit_record(self):
        """
        A light grey dashed CUT LINE running the full width of the page,
        with 16.8mm of air either side. The gap alone was three times the
        bill's own 5.6mm between its two sections and still read as one run
        under the closing total.

        ⚠ IT BREAKS THE MARGIN ON PURPOSE — the owner's instruction, to
        "create a cutting feel". `margin: 0 -12mm` cancels the sheet's own
        padding, so the line spans 210mm against the tables' 186mm and reaches
        both paper edges. It is the only thing on this document that does, and
        that is what tells a reader PART LIFE is a different question.

        ⚠ LIGHT GREY IS WHAT MAKES THAT SAFE. Solid navy was tried and
        competes with the closing total a centimetre above it; the sheet's own
        two pale blues are FILLS, so a dashed line in either reads as a band
        that failed to render.

        ⚠ SYMMETRICAL — 16.8mm above and below, or the line is a lid on PART
        LIFE rather than a boundary between two sections.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        html = self._render()

        start = html.index('.sh-life {')
        self.assertIn('margin-top: 16.8mm', html[start:html.index('}', start)])

        start = html.index('.sh-life::before {')
        rule = html[start:html.index('}', start)]
        self.assertIn('border-top: 2px dashed #d0d5dd', rule)
        # -12mm is `.sheet`'s padding cancelled: full bleed, and the 16.8mm
        # below matching the 16.8mm above.
        self.assertIn('margin: 0 -12mm 16.8mm', rule)
        # Never solid, and never a bar: it is a cue, not an edge.
        self.assertNotIn('background', rule)
        self.assertNotIn('solid', rule)

        # The sheet's padding is what the negative margin cancels — if that
        # ever changes, the line stops reaching the paper edge.
        start = html.index('.sheet {')
        self.assertIn('padding: 12mm', html[start:html.index('}', start)])

    def test_part_life_is_printed_on_a_page_of_its_own(self):
        """
        The only pagination rule that is right in every scenario, and it was
        measured rather than argued: every car in the development data
        rendered to PDF twice, with the rule and without. 60 of 62 are
        unchanged; 2 gain one page, and both are the largest sheets there.

        It comes out that way because the record already fills a page on
        almost every car — on the SMALLEST sheet, 2 visits and 38 rows, it is
        272mm against 285mm of usable page.

        Left to flow, three things could happen and all three were seen on one
        printout: the cut line alone at the foot of a page with the table
        overleaf, the repeated column heading over a two-row fragment, and a
        page opening on the tail of a chain named on the sheet before.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        html = self._render()

        block = html[html.index('@media print'):]
        start = block.index('.sh-life {')
        rule = block[start:block.index('}', start)]
        self.assertIn('break-before: page', rule)
        # The legacy spelling too — it is what older print engines read.
        self.assertIn('page-break-before: always', rule)

    def test_a_parts_whole_chain_stays_on_one_page(self):
        """
        `.sh-chain-head` binds a part's NAME to its first fitting and nothing
        bound the rest, so a part with six lives could still be cut across the
        fold — the one thing this table is read for.

        Each chain is its own `<tbody>`, and every chain in the development
        data measures 10.4mm to 31.2mm against a 285mm page, so one always
        fits with room to spare.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        html = self._render()

        block = html[html.index('@media print'):]
        start = block.index('.sh-life tbody {')
        rule = block[start:block.index('}', start)]
        self.assertIn('break-inside: avoid', rule)
        self.assertIn('page-break-inside: avoid', rule)

    def test_the_line_leaves_with_the_table_it_separates(self):
        """
        The tick hides one element, and the line is part of it.

        Padding is ignored on a `border-collapse: collapse` table, so the air
        under the line cannot sit on the table — hence the wrapper. Drawing
        the line as a second element beside the table would leave the tick
        with two things to hide and a rule floating over nothing the day it
        only hid one.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        sheet = self._sheet()

        # The wrapper carries the class, and it wraps the table rather than
        # being it — so `hidden` on it takes the line and the table together.
        self.assertIn('<div class="sh-life">', sheet)
        self.assertNotIn('inv-table sh-life', sheet)

        block = sheet[sheet.index('<div class="sh-life">'):]
        self.assertLess(block.index('<table'), block.index('PART LIFE'))

    def test_a_figure_columns_heading_sits_on_its_figures_edge(self):
        """
        MILEAGE and DISTANCE RUN were centred over right-aligned figures — the
        bill's own treatment of UNIT PRICE, which survives there because those
        columns are 14.5 and 20.3% wide. These two are 24.9 and 22.2%, so the
        same rule left most of a column of white between each word and the
        numbers under it, and the owner read the figures as shifted right.

        NOW stays centred, because the chip under it is.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        sheet = self._sheet()

        self.assertIn('<th class="h-right">MILEAGE</th>', sheet)
        self.assertIn('<th class="h-right">DISTANCE RUN</th>', sheet)
        self.assertIn('<th>NOW</th>', sheet)

        # The declaration lives in the stylesheet, which _sheet() crops away.
        html = self._render()
        start = html.index('.inv-table thead th.h-right {')
        self.assertIn('text-align: right',
                      html[start:html.index('}', start)])

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


class ItIsSetLikeTheBillTests(ServiceHistoryPageTestCase):
    """
    ⚠ **THE RULE AT THE HEAD OF THE TEMPLATE WAS BEING OBEYED WHILE THE SHEET
    STILL LOOKED WRONG, WHICH IS WHY THESE EXIST.** Every size and every colour
    on it was legal. What nothing checked was the WEIGHT and the COUNT — the
    bill sets 57% of its text in 10pt regular and 5 elements in 10pt bold,
    while this sheet had 166 bold against 135 regular, and it spent 30 green
    chips where the bill spends three greens on one settled stamp.

    Both were found by measuring the two rendered documents element by element,
    not by reading either stylesheet, and neither can be caught by looking at
    one page on its own. So each is pinned here as the DECLARATION that causes
    it — nothing in the Django suite executes CSS.
    """

    def _css(self, selector, html):
        """The body of one rule, so an assertion cannot match a neighbour."""
        start = html.index(selector + ' {')
        return html[start:html.index('}', start)]

    def test_no_label_in_the_vehicle_block_is_bold(self):
        """
        The bill sets `NAME: Anwar Sadath` in regular 11pt end to end. This
        block bolded every prefix through a `.sh-k` class, which is what left
        the sheet painting eleven-point regular NOT ONCE while the bill paints
        it twice. The colon does the work on both.
        """
        self._visit(date(2026, 1, 1))
        html = self._render()
        self.assertNotIn('sh-k', html)

        sheet = self._sheet()
        block = sheet[sheet.index('VEHICLE'):sheet.index('sh-chain')]
        self.assertIn('MAKE:', block)
        self.assertNotIn('<b>', block)
        self.assertNotIn('font-weight', block)

    def test_nothing_on_the_sheet_is_green(self):
        """
        Green in this system means MONEY — the Profit page's own rule, and the
        settled stamp on the bill. This sheet carries no payment state at all
        by design, so the one warm signal on a Formula D document was being
        spent thirty times on a fact about a wheel bearing.

        Asserted over the WHOLE page, stylesheet included: the chip's fill and
        border live only in CSS, so a sheet-only search would have passed while
        the paper stayed green.
        """
        self._visit(date(2026, 1, 1), parts=['Wheel bearing left'])
        html = self._render()
        for green in ('#eaf5ea', '#7fb37f', '#24632c', '#16a34a'):
            self.assertNotIn(green, html, f'{green} is a money colour')

    def test_the_marker_for_a_part_still_fitted_is_navy(self):
        self._visit(date(2026, 1, 1), parts=['Wheel bearing left'])
        html = self._render()
        self.assertIn('#1f4e79', self._css('.sh-run', html))
        self.assertIn('ON THE CAR', self._sheet())

    def test_the_thank_you_line_is_the_bills_own_twelve_point(self):
        """
        The invoice sets this line two points above its body. This file simply
        did not restate the size, so `.inv-table td` handed it 10pt — the same
        sentence, in the same italic, in the same blue, set smaller on one of
        two documents that are handed over together.

        It has its own class rather than the bill's `.thanks` only because on
        this sheet the sentence is no longer inside a table.
        """
        self._visit(date(2026, 1, 1))
        rule = self._css('.sh-thanks', self._render())
        self.assertIn('font-size: 12pt', rule)
        self.assertIn('#2e74b5', rule)
        self.assertIn('italic', rule)

    def test_the_thank_you_leads_the_foot_rather_than_the_total(self):
        """
        ⚠ READING ORDER, NOT TASTE. On the bill that sentence sits beside TOTAL
        because that row is the LAST thing before the foot. Here it is not:
        PART LIFE follows the total and runs most of a page, so the sentence
        that closes the bill was closing nothing — buried mid-document with a
        table after it.

        ⚠ AND IT IS NO LONGER GATED ON THE AMOUNTS. It rode inside the totals
        table, so a copy printed without amounts lost it altogether, and a
        courtesy to a customer is not a figure.
        """
        card = self._visit(date(2026, 1, 1), parts=['Wheel bearing left'])
        card.labour_amount = Decimal('4000')
        card.update_totals()

        sheet = self._sheet()
        self.assertLess(sheet.index('TOTAL BILLED'),
                        sheet.index('Thank you for your business'))
        foot = sheet[sheet.index('inv-foot'):]
        self.assertIn('Thank you for your business', foot)

        # No amounts on this copy at all — the thank-you still appears.
        bare = self._sheet({})
        self.assertNotIn('TOTAL BILLED', bare)
        self.assertIn('Thank you for your business', bare)

    def test_the_foot_carries_no_rule_above_it(self):
        """The bill's foot is centred 10pt on white with nothing drawn over
        it. A navy hairline here was one more line this sheet had and that one
        did not; the 8.1mm gap separates it on both."""
        self._visit(date(2026, 1, 1))
        rule = self._css('.inv-foot', self._render())
        self.assertIn('margin-top: 8.1mm', rule)
        self.assertNotIn('border-top', rule)

    def test_the_card_splits_on_the_bills_own_gridline(self):
        """
        WORK DONE ends and PARTS FITTED begins at 57.5%, where BILL TO ends and
        VEHICLE INFO begins — so a customer laying the two documents side by
        side finds the rule in the same place. Change a width here and they
        stop agreeing.
        """
        self._visit(date(2026, 1, 1), parts=['Wheel bearing left'])
        sheet = self._sheet()
        card = sheet[sheet.index('sh-vt'):]
        for width in ('57.5%', '7.7%', '14.5%', '20.3%'):
            self.assertIn('width:' + width, card)

    def test_the_record_block_is_the_bills_own_parties_block(self):
        """
        ⚠⚠ THIS RENDERS THE BILL AND COMPARES THE TWO, rather than asserting
        one page against a description of the other — the same form
        `test_both_documents_end_the_same_way` takes, and for the same reason:
        the claim is that these are ONE object drawn twice.

        ⚠ IT REVERSES A FOUR-COLUMN BUILD, AND BOTH SIDES OF THAT ARE WORTH
        KEEPING. The owner's word for the FIRST inline version was "brain
        draining": six facts a side behind labels of six different lengths
        start their values at six different x. Splitting label and value into
        their own columns fixed exactly that — and it stopped being the bill's
        block, because on the bill `NAME: Anwar Sadath` is one run of text.

        The bill is the reference document: the owners supplied its wording and
        its layout. So the ragged left edge of the values is an accepted cost,
        and it is the same cost the bill pays on its own block.
        """
        card = self._visit(date(2026, 1, 1))
        bill = self.client.get(reverse('invoice_view', args=[card.pk]))
        self.assertEqual(bill.status_code, 200)

        def parties(html):
            block = html[html.index('inv-table inv-parties'):]
            return block[:block.index('</table>')]

        theirs = parties(bill.content.decode())
        ours = parties(self._sheet())

        # The same two columns, on the bill's own gridline.
        widths = lambda b: re.findall(r'<col style="width:([\d.]+)%">', b)
        self.assertEqual(widths(ours), ['57.5', '42.5'])
        self.assertEqual(widths(ours), widths(theirs))

        # One row of two cells under the band, on both — never a cell per fact.
        rows = lambda b: b.count('<tr')
        self.assertEqual(rows(ours), rows(theirs))
        self.assertEqual(rows(ours), 2)

        # `LABEL: value` inline, separated by <br>, exactly as the bill sets it.
        self.assertIn('MAKE: ', ours)
        self.assertIn('<br>MODEL: ', ours)
        self.assertNotIn('<td>MAKE:</td>', ours)
        self.assertNotIn('sh-v', ours)

    def test_it_uses_the_bills_own_labels_in_the_bills_own_order(self):
        """
        NAME, MAKE, MODEL — the order the bill sets them in, and its words.

        ⚠ TWO EXCEPTIONS WERE ARGUED FOR AND BOTH WERE OVERRULED (2026-09-09),
        which is worth keeping because each objection was reasonable:

        `OWNER:` was defended on the ground that NAME sits under BILL TO on the
        bill, so under a band reading VEHICLE it would name the car. The value
        settles it in every real case: `NAME: Anwar Sadath` cannot be read as a
        car, and the bill in the customer's hand says NAME for the same person.

        `ODOMETER:` was defended on "one word per fact inside one document",
        against the PART LIFE column of the same name. That rule is right and
        the conclusion was backwards — **MILEAGE is the workshop's own word**
        (`JobCard.mileage`, `workshop/mileage.py`, and the bill has printed it
        for longer than this sheet has existed), and ODOMETER was invented
        here. So PART LIFE's heading moved too, which is what this asserts.
        """
        self._visit(date(2026, 1, 1), customer_name='Anwar Sadath',
                    customer_contact='9847012345',
                    parts=['Wheel bearing left'])
        sheet = self._sheet()
        block = sheet[sheet.index('inv-parties'):sheet.index('sh-chain')]

        self.assertIn('NAME: Anwar Sadath', block)
        self.assertNotIn('OWNER:', block)
        self.assertIn('MILEAGE: ', block)
        self.assertNotIn('ODOMETER', sheet)

        # NAME, then MAKE, then MODEL — the bill's order.
        self.assertLess(block.index('NAME: '), block.index('MAKE: '))
        self.assertLess(block.index('MAKE: '), block.index('MODEL: '))

        # ⚠ ONE WORD FOR ONE FACT ACROSS THE WHOLE SHEET: the record block and
        # the PART LIFE column head the same figure, so they take the same
        # word. This is the half that made the rename worth doing rather than
        # trading one inconsistency for another.
        self.assertIn('>MILEAGE</th>', sheet)

        # The name and nothing else. This sheet is handed to a buyer, and the
        # only phone number on it should be the workshop's own.
        self.assertNotIn('9847012345', sheet)

    def test_a_car_with_no_customer_recorded_opens_on_MAKE(self):
        """
        ⚠ NAME IS THE ONE OPTIONAL LINE THAT COMES FIRST, so its `<br>` TRAILS
        where every other one leads. Most cards at this workshop carry no
        customer name at all, and on those MAKE has to be the first line with
        no break in front of it — a leading break would open the cell with a
        blank line. The bill instead prints a bare `NAME:` with nothing after
        it, which is fine on one bill and reads as missing data at the head of
        a document handed to a buyer.
        """
        self._visit(date(2026, 1, 1))
        block = self._sheet()
        block = block[block.index('inv-parties'):block.index('sh-chain')]

        self.assertNotIn('NAME:', block)
        cell = block[block.index('<tr>'):]
        self.assertIn('<td>MAKE: ', cell)
        self.assertNotIn('<td><br>', cell)

    def test_how_many_visits_and_how_long_are_two_lines(self):
        """
        ⚠ `VISITS: 5 over 3 years 5 months` PUT TWO FACTS BEHIND ONE LABEL,
        and the owner's word for the result was "confusion". It reads as a
        fraction at a glance — "5 over 3" — with a second 5 four words later.

        The span is not dropped: FIRST VISIT and LATEST VISIT do carry it, but
        only as two dates somebody has to subtract, and how long the workshop
        has known the car is the second thing a buyer asks. It gets its own
        label, on its own line, under the count it qualifies.
        """
        self._visit(date(2022, 6, 18))
        self._visit(date(2025, 12, 6))
        sheet = self._sheet()
        block = sheet[sheet.index('inv-parties'):sheet.index('sh-chain')]

        self.assertIn('VISITS: ', block)
        self.assertIn('<br>OVER: ', block)

        # The count stands alone on its line — no span riding with it.
        count = block[block.index('VISITS: '):]
        self.assertNotIn('over', count[:count.index('<br>')])

        # And each is said once, not once here and again in a heading.
        self.assertEqual(block.count('OVER: '), 1)

    def test_the_caveats_are_one_separated_run_above_the_sign_off(self):
        """
        ⚠ "SO MESS" WAS THE OWNER'S VERDICT ON THE FOOT, and it took two goes.

        First it was a paragraph: four sentences run together into three
        full-width CENTRED lines, ragged on both edges with no left margin for
        the eye to return to. Then it was one sentence per line, which fixed
        the raggedness and bought a new problem — four short centred statements
        floating in white, reported as "small and light grey" when every one of
        them is `rgb(0, 0, 0)` at the sheet's own body size.

        A middot-separated run is neither: the separators are anchors, so it
        reads as a LIST rather than as prose, and the block is dense enough to
        hold its own weight.

        ⚠ AND IT SITS ABOVE THE TWO CONTACT LINES. The caveats qualify the data
        they follow; the contact block is the sign-off, and "quote its number to
        Rijas Mohd" is a better last line than "work carried out elsewhere does
        not appear".

        Two sentences were deleted rather than rewrapped: the issued date is
        in the letterhead, and the run's own "Today's reading was supplied by
        the customer" item says what a blanket "apart from today's reading"
        clause was reaching for — and only when there IS such a reading, so it
        is never left false.
        """
        self._visit(date(2026, 1, 1), parts=['Wheel bearing left'])
        sheet = self._sheet()

        self.assertNotIn('Prepared from this workshop', sheet)
        self.assertNotIn('apart from today', sheet)
        self.assertNotIn('sh-fine-line', sheet)

        # ⚠ IT IS MAIN CONTENT, NOT FOOTER FURNITURE. It sat inside `.inv-foot`
        # on a narrow centred measure, which made it the one block on the sheet
        # that did not line up with the record it is about.
        self.assertLess(sheet.index('sh-notes'), sheet.index('inv-foot'))
        notes = sheet[sheet.index('sh-notes'):sheet.index('inv-foot')]
        self.assertIn('&middot;', notes)
        self.assertLess(notes.index('Every visit listed above'),
                        notes.index('Odometer readings are as recorded'))

        # …and the item that only appears when there IS such a reading.
        with_reading = self._sheet(dict(EVERYTHING, km='90000'))
        self.assertIn("Today's reading was supplied by the customer",
                      with_reading)

    def test_the_separator_is_never_the_asterisk(self):
        """
        ⚠ `*` ALREADY MEANS SOMETHING ON THIS SHEET — it marks a distance too
        large to be credible, and the legend explaining it is one of these very
        caveats. Bulleting the run with it would print "* A distance marked *
        is unusually large", which is one mark doing two jobs an inch apart.
        """
        self._visit(date(2026, 1, 1), '60000')
        self._visit(date(2026, 3, 1), '600000')          # trips the flag

        sheet = self._sheet()
        self.assertIn('unusually large', sheet)
        notes = sheet[sheet.index('sh-notes'):sheet.index('inv-foot')]
        self.assertNotIn('*&nbsp;', notes)
        self.assertNotIn('&middot; *', notes)

    def test_the_foot_is_set_exactly_like_the_bills(self):
        """
        Two centred 10pt lines in black, with no emphasis on either.

        "Every visit listed above has an invoice held by Formula D." carried
        bold navy through a `.sh-verify` class, for the sentence it is. The
        reasoning was right and the emphasis was wrong twice over: the
        invoice's own foot has no bold and no colour anywhere, and — measured —
        that one line pulled so much weight that the black caveats beside it
        were reported as faint. Nothing in the foot is emphasised, so nothing
        in the foot looks faint.
        """
        self._visit(date(2026, 1, 1), parts=['Wheel bearing left'])
        html = self._render()
        self.assertNotIn('sh-verify', html)

        sheet = self._sheet()
        foot = sheet[sheet.index('inv-foot'):]
        self.assertIn('Should you have any enquiries concerning this record '
                      'please contact:', foot)
        for loud in ('<b>', '<strong>', '#1f4e79', 'font-weight'):
            self.assertNotIn(loud, foot)

        # The promise it used to carry is a NOTE now, and still on the page.
        self.assertIn('Every visit listed above has an invoice held by '
                      'Formula D', sheet[:sheet.index('inv-foot')])

    def test_both_documents_end_the_same_way(self):
        """
        ⚠ THE SHAPE IS THE POINT, NOT ONLY THE SIZE AND THE COLOUR — so this
        renders the BILL as well and compares them, rather than asserting one
        page against a description of the other. The bill reads

            Should you have any enquiries concerning this invoice please contact:
            Rijas Mohd, +91 92 07 21 79 78

        and the name standing by ITSELF is what makes the last line read as a
        signature rather than as another sentence. This sheet had two centred
        10pt black lines of the right size and colour in the wrong shape, with
        the name buried mid-sentence: "quote its number to Rijas Mohd, +91 …".

        No full stop on that line, on either document.
        """
        card = self._visit(date(2026, 1, 1))
        bill = self.client.get(reverse('invoice_view', args=[card.pk]))
        self.assertEqual(bill.status_code, 200)

        def signature(html):
            foot = html[html.index('inv-foot'):]
            self.assertIn('please contact:<br>', foot)
            tail = foot.split('please contact:<br>')[1]
            return tail[:tail.index('</div>')].strip()

        self.assertEqual(signature(bill.content.decode()),
                         signature(self._sheet()))
        self.assertEqual(signature(self._sheet()),
                         'Rijas Mohd, +91 92 07 21 79 78')

    def test_the_caveats_recede_from_the_record(self):
        """
        ⚠ THE ONE GREY ON THE SHEET, AND THE ONE STATED EXCEPTION TO THE COLOUR
        RULE. Everything else is black, white, navy or the accent blue — all
        four the bill's. This earns the exception because it is the only thing
        on the page that is not part of the RECORD: every other line is a fact
        about the car, and this is a note about the document. At the same size
        and colour as the record it competed with it.

        ⚠ `#6E6E6E` MEASURES 5.1:1 ON WHITE, AND THE 4.5:1 FLOOR IS NOT
        NEGOTIABLE HERE. The obvious "light grey" `#808080` is 3.95:1 and
        fails. These are the caveats a BUYER relies on — where the odometer
        figures came from, that work done elsewhere is absent — and fine print
        somebody cannot read on a document about a car they are buying reads as
        the workshop hiding it.

        ⚠ 8.5pt IS THE SECOND HALF OF THE SAME EXCEPTION. It ran at 9.5pt,
        the PAID stamp's size, so it introduced no new size at all — and the
        owner's call (2026-09-08) was that the notes still sat too close to
        the record they are notes ABOUT. The size and the colour now say one
        thing together: read this second.

        ⚠ THE CONTRAST FLOOR DOES NOT BOUND THE SIZE. WCAG only RELAXES its
        ratio for LARGE text and never tightens it for small, so `#6E6E6E`
        clears 4.5:1 at 8.5pt exactly as it did at 9.5. What bounds it is
        paper, and that is a judgement nothing here can assert — so what IS
        asserted is the relationship: quieter than the record, never louder.
        """
        self._visit(date(2026, 1, 1))
        rule = self._css('.sh-notes', self._render())
        self.assertIn('font-size: 8.5pt', rule)
        self.assertIn('#6E6E6E', rule)

        # The invariant behind the number: the notes are smaller than the
        # 10pt body they sit under. A size bumped back up to the record's
        # would undo the whole point of the block being set apart.
        size = float(re.search(r'font-size: ([\d.]+)pt', rule).group(1))
        self.assertLess(size, 10)

        # 5.1:1 — recompute rather than trust the comment.
        def channel(value):
            value /= 255
            return (value / 12.92 if value <= 0.03928
                    else ((value + 0.055) / 1.055) ** 2.4)

        grey = channel(0x6E)
        luminance = 0.2126 * grey + 0.7152 * grey + 0.0722 * grey
        self.assertGreaterEqual(1.05 / (luminance + 0.05), 4.5)

    def test_a_part_still_on_the_car_prints_no_distance_when_there_is_none(self):
        """
        ⚠ EVERY PART FITTED AT THE LATEST VISIT READS ZERO, because the newest
        reading this workshop holds IS that visit's. A well-serviced car
        therefore opened PART LIFE with a column of "0 km", once per chain,
        which on a document a buyer is checking looks like the sheet is broken
        rather than like a part that is new.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])

        sheet = self._sheet()
        self.assertIn('ON THE CAR', sheet)
        self.assertNotIn('>0 km<', sheet)

    def test_but_a_finished_life_of_zero_still_prints(self):
        """
        The distinction is real: a part replaced at the same reading it was
        fitted at failed immediately, and that IS a measurement. Zero on a
        running fitting is not one — it means nobody has read the odometer
        since.
        """
        self._visit(date(2026, 1, 1), '60000', parts=['Wheel bearing left'])
        self._visit(date(2026, 2, 1), '60000', parts=['Wheel bearing left'])

        self.assertIn('>0 km<', self._sheet())


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
