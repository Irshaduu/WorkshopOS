"""
The printed bill.

Every rule in `workshop/invoice.py` that a customer would notice if it broke,
asserted against the rendered page rather than only the context — a figure that
is correct in a dict and missing from the paper is still a wrong invoice.
"""

import re
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User, Group
from django.test import TestCase, Client
from django.urls import reverse

from inventory.models import Category, Item
from workshop.invoice import (
    MIN_JOB_ROWS, MIN_PART_ROWS, build_invoice, effective_quantity,
)
from workshop.models import (
    BulkPayer, JobCard, JobCardLabourItem, JobCardSpareItem, SpareShop,
)


def _tables(html, css_class):
    """The one table carrying `css_class`, markup and all."""
    match = re.search(
        r'<table class="[^"]*\b' + css_class + r'\b[^"]*">.*?</table>',
        html, re.S,
    )
    return match.group(0) if match else ''


def _sheet(html):
    """
    Just the paper: the `.sheet` element and everything nested inside it.

    Used to prove that no control, script or dialog lives INSIDE the document
    that prints, which is the structural half of "clean print view" — hiding
    something with CSS is one stylesheet edit away from being un-hidden.

    Balanced-tag scan rather than a search for a closing marker. The first
    attempt looked for the footer's class name, found it in the STYLESHEET
    instead (which sits earlier in the file), and returned an empty string — so
    every `assertNotIn(..., _sheet(html))` here passed by asserting against
    nothing at all. If this ever returns '' again, the tests using it say so.
    """
    start = html.find('<div class="sheet"')
    if start == -1:
        return ''
    depth = 0
    for match in re.finditer(r'<div\b|</div>', html[start:]):
        if match.group(0) == '</div>':
            depth -= 1
            if depth == 0:
                return html[start:start + match.end()]
        else:
            depth += 1
    return ''


class InvoiceTestCase(TestCase):
    """One Office login, one job card, and the two spare routes to hang off it."""

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
            average_stock=Decimal('40'), current_stock=Decimal('50'),
            avg_cost=Decimal('420'),
        )

    def _jobcard(self, **kwargs):
        defaults = dict(
            admitted_date=date(2026, 1, 15),
            brand_name='Volkswagen', model_name='Polo',
            registration_number='HR26X1003', customer_name='Ramesh',
            mileage='106000',
        )
        defaults.update(kwargs)
        return JobCard.objects.create(**defaults)

    def _shop_spare(self, job, name='Fuel injector', **kwargs):
        defaults = dict(
            job_card=job, spare_part_name=name,
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=Decimal('1'), unit_price=Decimal('9000'),
            total_price=Decimal('12000'),
        )
        defaults.update(kwargs)
        return JobCardSpareItem.objects.create(**defaults)

    def _draw(self, job, **kwargs):
        defaults = dict(
            job_card=job, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.product, quantity=Decimal('6.5'),
            customer_rate=Decimal('1280'),
        )
        defaults.update(kwargs)
        return JobCardSpareItem.objects.create(**defaults)

    def _labour(self, charge, *descriptions):
        """
        A card carrying `descriptions` as jobs done and `charge` for all of them.

        Two steps on purpose, because that is the shape of the rule: the lines
        say what happened, the card says what it costs.
        """
        job = self._jobcard(labour_amount=charge)
        for description in descriptions:
            JobCardLabourItem.objects.create(job_card=job, job_description=description)
        job.update_totals()
        job.refresh_from_db()
        return job

    def _render(self, job):
        response = self.client.get(reverse('invoice_view', args=[job.pk]))
        self.assertEqual(response.status_code, 200)
        return response


class BothSpareRoutesPrintAsOneSectionTests(InvoiceTestCase):
    """
    The job card edits shop purchases and warehouse draws as two sections
    because they are two workflows. The customer is buying parts, not workflows,
    so the bill has exactly one PART NAME list and one subtotal under it.
    """

    def test_a_shop_part_and_a_warehouse_draw_share_one_list(self):
        job = self._jobcard()
        self._shop_spare(job)
        self._draw(job)

        report = build_invoice(job)
        self.assertEqual(len(report['part_lines']), 2)
        self.assertEqual(
            [line.name for line in report['part_lines']],
            ['Fuel injector', 'Engine Oil'],
        )
        # 12,000 shop + (1,280 x 6.5) drawn
        self.assertEqual(report['part_subtotal'], Decimal('20320.00'))

    def test_the_page_has_one_parts_heading_and_no_route_labels(self):
        job = self._jobcard()
        self._shop_spare(job)
        self._draw(job)
        sheet = _sheet(self._render(job).content.decode())

        self.assertNotEqual(sheet, '')
        self.assertEqual(sheet.count('PART NAME'), 1)
        # The routes are an internal distinction; naming either one on a
        # customer's bill would re-split the section this merges.
        for leak in ('Inventory', 'Warehouse', 'Spare Shop', 'Pullara Spares'):
            self.assertNotIn(leak, sheet)

    def test_parts_keep_the_order_they_were_added(self):
        job = self._jobcard()
        self._shop_spare(job, name='First')
        self._draw(job)
        self._shop_spare(job, name='Third')

        names = [line.name for line in build_invoice(job)['part_lines']]
        self.assertEqual(names, ['First', 'Engine Oil', 'Third'])


class AWarehouseDrawIsBilledByItsCategoryTests(InvoiceTestCase):
    """
    `Item.name` is the branded SKU the workshop buys; `Category.name` is what it
    is. The bill says what was fitted — both because that is what the customer
    is being told, and because printing the brand publishes the workshop's
    supply chain on a document it hands out.
    """

    def test_the_category_prints_and_the_product_brand_does_not(self):
        job = self._jobcard()
        self._draw(job)
        html = self._render(job).content.decode()

        self.assertIn('Engine Oil', _tables(html, 'inv-parts'))
        self.assertNotIn('Castrol', html)

    def test_a_shop_part_keeps_the_name_office_typed(self):
        job = self._jobcard()
        self._shop_spare(job, name='Return hose fuel')
        self.assertEqual(
            build_invoice(job)['part_lines'][0].name, 'Return hose fuel',
        )

    def test_a_draw_with_no_product_falls_back_to_its_stored_name(self):
        """
        `item` is NULL on a draw only through a data anomaly. Falling back keeps
        a line on the bill: printing an empty row for a part the customer is
        being charged for is the worse failure.
        """
        job = self._jobcard()
        JobCardSpareItem.objects.create(
            job_card=job, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=None, spare_part_name='Recovered name',
            quantity=Decimal('1'), total_price=Decimal('500'),
        )
        self.assertEqual(
            build_invoice(job)['part_lines'][0].name, 'Recovered name',
        )


class LabourPrintsAsOneSubtotalTests(InvoiceTestCase):
    """
    What was done, and one price for doing it — because that is how the workshop
    sells. The charge is a single figure on the job card; the job lines are
    descriptions and carry no money at all.
    """

    def test_descriptions_print_and_the_cards_one_charge_is_the_subtotal(self):
        job = self._labour(
            Decimal('2500'), 'Misfire diagnosing', 'Injector replaced')
        table = _tables(self._render(job).content.decode(), 'inv-jobs')

        self.assertIn('Misfire diagnosing', table)
        self.assertIn('Injector replaced', table)
        self.assertIn('2,500.00', table)

    def test_the_amount_column_is_empty_on_every_job_row(self):
        """
        The reference invoice rules an AMOUNT column beside the jobs and leaves
        every row of it blank. Only the SUBTOTAL cell carries a figure.
        """
        job = self._labour(Decimal('2500'), 'Misfire diagnosing')
        table = _tables(self._render(job).content.decode(), 'inv-jobs')

        self.assertIn('AMOUNT', table)
        self.assertEqual(table.count('2,500.00'), 1)

    def test_a_labour_line_carries_no_amount_in_the_context(self):
        job = self._labour(Decimal('800'), 'Service')
        line = build_invoice(job)['job_lines'][0]
        self.assertFalse(hasattr(line, 'amount'))

    def test_the_subtotal_comes_off_the_card_not_off_the_lines(self):
        """
        The regression guard for the dormant column. If someone reinstates
        `Sum(labours.amount)`, a card priced the new way reports ₹0 of labour.
        """
        job = self._labour(Decimal('2500'), 'Diagnosis', 'Repair')

        self.assertTrue(all(l.amount is None for l in job.labours.all()))
        self.assertEqual(build_invoice(job)['job_subtotal'], Decimal('2500'))

    def test_a_charge_with_no_job_lines_still_prints(self):
        """Office may price the work before anyone types what it was."""
        job = self._jobcard(labour_amount=Decimal('900'))
        job.update_totals()

        report = build_invoice(job)
        self.assertEqual(report['job_lines'], [])
        self.assertEqual(report['job_subtotal'], Decimal('900'))
        self.assertIn('900.00', _tables(self._render(job).content.decode(), 'inv-jobs'))


class ABlankQuantityIsOneTests(InvoiceTestCase):
    """
    Staff leave the box empty for a single part. The two states mean the same
    thing to the person filling it in, so they must mean the same thing on the
    paper — down to the markup.
    """

    def test_blank_and_one_produce_an_identical_parts_table(self):
        blank = self._jobcard(registration_number='KL01AA1111')
        self._shop_spare(blank, quantity=None, total_price=Decimal('12000'))

        typed = self._jobcard(registration_number='KL01AA2222')
        self._shop_spare(typed, quantity=Decimal('1'), total_price=Decimal('12000'))

        blank_html = _tables(self._render(blank).content.decode(), 'inv-parts')
        typed_html = _tables(self._render(typed).content.decode(), 'inv-parts')

        self.assertNotEqual(blank_html, '')
        self.assertEqual(blank_html, typed_html)

    def test_a_blank_quantity_prints_as_one_not_as_nothing(self):
        job = self._jobcard()
        self._shop_spare(job, quantity=None)
        line = build_invoice(job)['part_lines'][0]
        self.assertEqual(line.quantity, Decimal('1'))

    def test_the_unit_price_of_a_blank_quantity_is_the_whole_amount(self):
        """
        The regression this closes: the column used to divide by a missing
        quantity and print ₹0.00 beside a real amount on the same row.
        """
        job = self._jobcard()
        self._shop_spare(job, quantity=None, total_price=Decimal('12000'))
        line = build_invoice(job)['part_lines'][0]
        self.assertEqual(line.unit_price, Decimal('12000.00'))
        self.assertIn('12,000.00', _tables(self._render(job).content.decode(), 'inv-parts'))

    def test_zero_and_negative_are_treated_as_one_as_well(self):
        """
        No form should produce either, and both would divide the unit-price
        column by nothing. One rule covers all three rather than leaving the
        last two to crash the page.
        """
        self.assertEqual(effective_quantity(None), Decimal('1'))
        self.assertEqual(effective_quantity(Decimal('0')), Decimal('1'))
        self.assertEqual(effective_quantity(Decimal('-3')), Decimal('1'))
        self.assertEqual(effective_quantity(Decimal('2.5')), Decimal('2.5'))


class TheUnitPriceColumnIsNeverTheWorkshopsCostTests(InvoiceTestCase):
    """
    `JobCardSpareItem.unit_price` is COST per unit — the shop's price, or the
    warehouse average. It has never been what the customer pays, and printing it
    would put the margin on every part into the customer's hand.
    """

    def test_the_printed_rate_is_the_customer_total_divided_by_quantity(self):
        job = self._jobcard()
        self._shop_spare(
            job, quantity=Decimal('2'),
            unit_price=Decimal('2000'),    # what the shop charged the workshop
            total_price=Decimal('5000'),   # what the customer is charged
        )
        html = self._render(job).content.decode()
        table = _tables(html, 'inv-parts')

        self.assertIn('2,500.00', table)      # 5000 / 2
        self.assertNotIn('2,000.00', table)   # the cost, nowhere on the page
        self.assertNotIn('2,000.00', _sheet(html))

    def test_a_drawn_part_prints_its_customer_rate_not_the_average_cost(self):
        job = self._jobcard()
        self._draw(job, quantity=Decimal('6.5'), customer_rate=Decimal('1280'))
        table = _tables(self._render(job).content.decode(), 'inv-parts')

        self.assertIn('1,280.00', table)   # customer rate
        self.assertIn('8,320.00', table)   # 1,280 x 6.5
        self.assertNotIn('420', table)     # Item.avg_cost


class ARowWithNoPriceYetStaysBlankTests(InvoiceTestCase):
    """
    A part fitted but not yet costed prints an empty cell. A part genuinely
    given away prints ₹0.00. Collapsing the two would report the first as free.
    """

    def test_an_uncosted_part_prints_no_figure_at_all(self):
        job = self._jobcard()
        self._shop_spare(job, name='Drive belt', total_price=None, unit_price=None)
        line = build_invoice(job)['part_lines'][0]

        self.assertIsNone(line.amount)
        self.assertIsNone(line.unit_price)
        self.assertFalse(line.priced)

    def test_a_part_given_away_prints_zero(self):
        job = self._jobcard()
        self._shop_spare(job, name='Washer', total_price=Decimal('0'))
        line = build_invoice(job)['part_lines'][0]

        self.assertTrue(line.priced)
        self.assertIn('0.00', _tables(self._render(job).content.decode(), 'inv-parts'))


class TheBillIsPaddedToAFixedHeightTests(InvoiceTestCase):
    """
    Blank rows are not decoration: they are what puts the footer at the same
    height on a three-part bill and an eleven-part one. The minimum never
    truncates — a longer list is simply not padded.
    """

    def test_an_empty_bill_still_prints_a_full_skeleton(self):
        report = build_invoice(self._jobcard())
        self.assertEqual(len(report['job_pad']), MIN_JOB_ROWS)
        self.assertEqual(len(report['part_pad']), MIN_PART_ROWS)

    def test_padding_shrinks_as_lines_are_added(self):
        job = self._jobcard()
        for i in range(3):
            self._shop_spare(job, name=f'Part {i}')
        self.assertEqual(len(build_invoice(job)['part_pad']), MIN_PART_ROWS - 3)

    def test_a_long_bill_is_not_padded_and_is_not_truncated(self):
        job = self._jobcard()
        for i in range(MIN_PART_ROWS + 9):
            self._shop_spare(job, name=f'Part {i}')
        report = build_invoice(job)

        self.assertEqual(len(report['part_pad']), 0)
        self.assertEqual(len(report['part_lines']), MIN_PART_ROWS + 9)


class TheTotalsAgreeWithTheJobCardTests(InvoiceTestCase):
    """
    The two subtotals are the same rows the job card totalled, re-added for
    display. If they ever stopped agreeing with `total_bill_amount` the page
    would show a bill that does not add up — which is the point: it is visible
    rather than reconciled away.
    """

    def test_the_printed_total_is_the_job_cards_own(self):
        job = self._labour(Decimal('2500'), 'Diagnosis')
        self._shop_spare(job, total_price=Decimal('12000'))
        job.refresh_from_db()

        report = build_invoice(job)
        self.assertEqual(report['job_subtotal'], Decimal('2500'))
        self.assertEqual(report['part_subtotal'], Decimal('12000'))
        self.assertEqual(report['grand_total'], Decimal('14500'))
        self.assertEqual(report['grand_total'], job.total_bill_amount)

    def test_the_page_prints_the_grand_total(self):
        job = self._labour(Decimal('2500'), 'Diagnosis')
        self._shop_spare(job, total_price=Decimal('17800'))
        job.refresh_from_db()

        html = self._render(job).content.decode()
        self.assertIn('20,300.00', html)

    def test_the_two_subtotals_add_up_to_the_total(self):
        """
        The identity a customer checks by eye. It holds because
        `JobCard.update_totals` computes spares + labour_amount, which is the
        same pair the page prints — not a coincidence worth leaving untested.
        """
        job = self._labour(Decimal('3532'), 'Electrical Fault Finding')
        self._shop_spare(job, total_price=Decimal('7575'))
        job.refresh_from_db()

        report = build_invoice(job)
        self.assertEqual(
            report['job_subtotal'] + report['part_subtotal'],
            report['grand_total'],
        )
        self.assertEqual(report['grand_total'], Decimal('11107'))


class NothingInteractiveLivesOnThePaperTests(InvoiceTestCase):
    """
    Requirement: the print view carries no app chrome. Asserted STRUCTURALLY —
    the controls live outside the sheet entirely, not merely behind a
    `display: none`, which is one stylesheet edit away from printing.
    """

    def setUp(self):
        super().setUp()
        self.job = self._jobcard()
        self._shop_spare(self.job)

    def test_the_sheet_contains_no_buttons_links_scripts_or_dialogs(self):
        sheet = _sheet(self._render(self.job).content.decode())

        self.assertNotEqual(sheet, '')
        for control in ('<button', '<a ', '<form', '<script', '<dialog', '<input'):
            self.assertNotIn(control, sheet)

    def test_the_controls_are_all_marked_no_print(self):
        html = self._render(self.job).content.decode()

        for label in ('Print / Save PDF', 'Edit Job', 'Settle Bill'):
            self.assertIn(label, html)
        # Every control block declares itself unprintable, and the stylesheet
        # backs that with a single `display: none !important` rule.
        self.assertIn('.no-print', html)
        self.assertNotIn('Print / Save PDF', _sheet(html))

    def test_the_payment_status_is_not_on_the_bill(self):
        """
        It is workshop metadata, not part of the reference invoice. It stays in
        the screen toolbar where the workshop reads it.
        """
        self.job.payment_status = 'PENDING'
        self.job.save()
        html = self._render(self.job).content.decode()

        self.assertIn('Pending (Unpaid)', html)
        self.assertNotIn('Pending (Unpaid)', _sheet(html))

    def test_the_page_loads_nothing_from_a_third_party(self):
        """
        A bill that arrives unstyled because a CDN is slow is not a bill. The
        page carries its own stylesheet and its own icons.
        """
        html = self._render(self.job).content.decode()
        self.assertNotIn('cdn.', html)
        self.assertNotIn('http://', html)
        self.assertNotIn('https://', html)


class ALongBillPaginatesCleanlyTests(InvoiceTestCase):
    """
    Pagination is a browser behaviour and cannot be asserted from Python — what
    CAN be asserted is that the markup and rules the browser needs are present.
    Each of these is something a later edit could silently remove, taking the
    repeated column headings or the row-splitting guard with it.
    """

    def setUp(self):
        super().setUp()
        self.job = self._jobcard()
        for i in range(40):
            self._shop_spare(self.job, name=f'Part number {i}')
        self.html = self._render(self.job).content.decode()

    def test_every_row_is_rendered_no_matter_how_many(self):
        self.assertIn('Part number 0', self.html)
        self.assertIn('Part number 39', self.html)

    def test_both_tables_put_their_column_headings_in_a_thead(self):
        """A <thead> is what `table-header-group` repeats. Without it the
        heading prints once and pages 2+ carry unlabelled columns."""
        for css_class in ('inv-jobs', 'inv-parts'):
            self.assertIn('<thead>', _tables(self.html, css_class))

    def test_the_print_rules_that_paginate_are_present(self):
        self.assertIn('display: table-header-group', self.html)
        self.assertIn('page-break-inside: avoid', self.html)

    def test_the_closing_totals_are_grouped_so_they_cannot_be_split(self):
        """
        SUBTOTAL and TOTAL sit in their own <tbody class="totals">, which the
        print rules keep whole. A <tfoot> would have repeated them at the foot
        of every page.
        """
        parts = _tables(self.html, 'inv-parts')
        self.assertIn('<tbody class="totals">', parts)
        self.assertNotIn('<tfoot', parts)


class AFleetBilledJobOffersNoSettlementHereTests(InvoiceTestCase):
    """
    A fleet card's money moves through the Bulk Payer cascade, and
    `update_bill_status` refuses one outright. So the control must not be on the
    page at all — an enabled button that always errors is worse than no button,
    and the dialog behind it has no business existing either.
    """

    def setUp(self):
        super().setUp()
        self.payer = BulkPayer.objects.create(customer_name='Malabar Cars')
        self.job = self._jobcard(bulk_payer=self.payer)
        self._shop_spare(self.job)

    def test_the_settle_control_and_its_dialog_are_both_absent(self):
        html = self._render(self.job).content.decode()

        self.assertNotIn('Settle Bill', html)
        self.assertNotIn('<dialog', html)
        self.assertNotIn(reverse('update_bill_status', args=[self.job.pk]), html)

    def test_it_points_at_the_account_that_actually_settles(self):
        html = self._render(self.job).content.decode()

        self.assertIn(reverse('bulk_payer_detail', args=[self.payer.pk]), html)
        self.assertIn('Malabar Cars', html)

    def test_the_fleet_account_is_not_named_on_the_bill_itself(self):
        """Who the workshop invoices internally is not part of the document."""
        self.assertNotIn('Malabar Cars', _sheet(self._render(self.job).content.decode()))


class TheLabourChargeLivesOnTheCardTests(InvoiceTestCase):
    """
    The workshop prices a job as a whole, so there is ONE labour figure per card
    and the job lines are descriptions. These guard the move off
    `JobCardLabourItem.amount` — the parts of it that are not about printing.
    """

    def setUp(self):
        super().setUp()
        office = Group.objects.get(name='Office')
        self.floor = User.objects.create_user(username='floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))
        self.office_group = office

    def test_the_bill_total_includes_the_cards_labour(self):
        job = self._jobcard(labour_amount=Decimal('22300'))
        job.update_totals()
        job.refresh_from_db()
        self.assertEqual(job.total_bill_amount, Decimal('22300'))

    def test_job_lines_no_longer_move_the_bill(self):
        """
        A description is not a price. Adding one must not change what the
        customer owes — the guard against anyone reinstating the old sum.
        """
        job = self._jobcard(labour_amount=Decimal('2500'))
        job.update_totals()
        job.refresh_from_db()
        before = job.total_bill_amount

        JobCardLabourItem.objects.create(job_card=job, job_description='Another job')
        job.refresh_from_db()

        self.assertEqual(job.total_bill_amount, before)

    def test_the_labour_charge_survives_a_spare_being_saved(self):
        """
        `update_totals` runs from the spare's save and recomputes the whole bill.
        If it stopped reading `labour_amount`, fitting a part would silently wipe
        the labour off the card.
        """
        job = self._jobcard(labour_amount=Decimal('2500'))
        self._shop_spare(job, total_price=Decimal('12000'))
        job.refresh_from_db()

        self.assertEqual(job.total_bill_amount, Decimal('14500'))

    def test_a_parts_only_card_saves_with_the_box_left_empty(self):
        """
        Plenty of job cards carry no labour at all. An empty box is "no labour",
        never a validation error, and never a NULL into a NOT NULL column.
        """
        from workshop.forms import JobCardForm
        form = JobCardForm({
            'registration_number': 'KL01AA9999',
            'admitted_date': str(date(2026, 1, 15)),
            'brand_name': 'Honda', 'model_name': 'City',
            'labour_amount': '',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['labour_amount'], Decimal('0'))

    def test_a_post_that_omits_the_field_keeps_the_stored_charge(self):
        """
        Absent and blank are different answers. Floor's job card never renders
        the box, and a disabled input is not submitted — treating either as
        "zero" would wipe the charge off a card nobody meant to reprice.
        """
        from workshop.forms import JobCardForm
        job = self._jobcard(labour_amount=Decimal('2500'))
        form = JobCardForm({
            'registration_number': job.registration_number,
            'admitted_date': str(job.admitted_date),
            'brand_name': 'Volkswagen', 'model_name': 'Polo',
        }, instance=job)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['labour_amount'], Decimal('2500'))

    def test_a_floor_post_cannot_rewrite_the_labour_charge(self):
        """
        The server-side half of the price lock, for the one price that is not in
        a formset. Floor sees no labour box anywhere; a crafted POST is inert.
        """
        job = self._jobcard(labour_amount=Decimal('2500'))
        job.update_totals()
        self.client.logout()
        self.client.login(username='floor', password='pw')

        self.client.post(reverse('jobcard_edit', args=[job.pk]), {
            'registration_number': job.registration_number,
            'admitted_date': str(job.admitted_date),
            'brand_name': 'Volkswagen', 'model_name': 'Polo',
            'labour_amount': '1',
            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'spares-TOTAL_FORMS': '0', 'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '0', 'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',
        })

        job.refresh_from_db()
        self.assertEqual(job.labour_amount, Decimal('2500'))
        self.assertEqual(job.total_bill_amount, Decimal('2500'))

    def test_a_negative_labour_charge_is_refused(self):
        from workshop.forms import JobCardForm
        form = JobCardForm({
            'registration_number': 'KL01AA9998',
            'admitted_date': str(date(2026, 1, 15)),
            'brand_name': 'Honda', 'model_name': 'City',
            'labour_amount': '-500',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('labour_amount', form.errors)

    def test_office_gets_the_subtotal_box_and_floor_does_not(self):
        """
        The box is the only place labour is priced, so it must be on Office's
        job card and absent from Floor's — matching every other price on the
        page, and matching the server-side lock rather than contradicting it.
        """
        job = self._jobcard(labour_amount=Decimal('2500'))
        url = reverse('jobcard_edit', args=[job.pk])

        office_html = self.client.get(url).content.decode()
        self.assertIn('Total Labour', office_html)
        self.assertIn('name="labour_amount"', office_html)

        self.client.logout()
        self.client.login(username='floor', password='pw')
        floor_html = self.client.get(url).content.decode()
        self.assertNotIn('Total Labour', floor_html)
        # The field itself is the assertion that matters — a label can be
        # reworded, but an input Floor can post from cannot exist.
        self.assertNotIn('name="labour_amount"', floor_html)

    def test_no_job_row_offers_an_amount_box_to_anyone(self):
        """Per-line pricing is gone from the screen, not merely from the model."""
        job = self._jobcard(labour_amount=Decimal('2500'))
        JobCardLabourItem.objects.create(job_card=job, job_description='Service')
        html = self.client.get(reverse('jobcard_edit', args=[job.pk])).content.decode()

        self.assertIn('labours-0-job_description', html)
        self.assertNotIn('labours-0-amount', html)
        self.assertNotIn('labours-__prefix__-amount', html)

    def test_the_job_line_form_has_no_amount_field(self):
        """
        Not cosmetic: while `labours-N-amount` existed it was rendered hidden for
        Floor and `_price_locked_data` never covered the `labours` prefix, so a
        Floor login could POST it. A field that does not exist cannot be posted.
        """
        from workshop.forms import JobCardLabourFormSet
        self.assertNotIn('amount', JobCardLabourFormSet.form.base_fields)
        self.assertIn('job_description', JobCardLabourFormSet.form.base_fields)


class TheBackLinkCannotLeaveTheSiteTests(InvoiceTestCase):
    """
    Every screen that links here appends its own path so the invoice can offer a
    way home. The value arrives from the URL and used to be written straight
    into an href.
    """

    def setUp(self):
        super().setUp()
        self.job = self._jobcard()
        self.url = reverse('invoice_view', args=[self.job.pk])

    def test_a_local_path_is_honoured(self):
        response = self.client.get(self.url, {'back': '/completed/'})
        self.assertEqual(response.context['back_url'], '/completed/')

    def test_another_origin_is_dropped(self):
        response = self.client.get(self.url, {'back': 'https://evil.example/x'})
        self.assertIsNone(response.context['back_url'])
        self.assertNotIn('evil.example', response.content.decode())

    def test_a_script_url_is_dropped(self):
        response = self.client.get(self.url, {'back': 'javascript:alert(1)'})
        self.assertIsNone(response.context['back_url'])
        self.assertNotIn('javascript:', response.content.decode())


class TheTitleIsTheFilenameTests(InvoiceTestCase):
    """
    `document.title` is what every browser suggests as the filename when this
    sheet goes through Print → Save as PDF. So the title is not decoration: it
    names the file an owner keeps in a folder of hundreds, and "Invoice —
    Formula D" named every one of them the same thing.

    The rule is shared with the estimate (`workshop/invoice.py`), because the
    two documents are handed to the same customer for the same car and should
    not name themselves by different rules.
    """

    def test_the_title_is_car_then_document_number(self):
        job = self._jobcard(
            brand_name='Audi', model_name='A4', registration_number='KL11 AJ 2266',
        )
        expected = f"Audi A4 KL11 AJ 2266 ({job.bill_number})"

        self.assertEqual(build_invoice(job)['document_title'], expected)
        self.assertIn(f"<title>{expected}</title>", self._render(job).content.decode())

    def test_a_missing_part_of_the_car_is_dropped_not_left_as_a_gap(self):
        """
        Most of an estimate is optional and a job card can be saved thin. A
        blank make must not print as a double space or the word "None" — the
        parts that exist are joined, the ones that do not are simply absent.
        """
        job = self._jobcard(brand_name='', model_name='', registration_number='KL11 AJ 2266')

        self.assertEqual(
            build_invoice(job)['document_title'],
            f"KL11 AJ 2266 ({job.bill_number})",
        )

    def test_a_car_with_nothing_recorded_falls_back_to_the_document_number(self):
        job = self._jobcard(brand_name='', model_name='', registration_number='')

        self.assertEqual(build_invoice(job)['document_title'], job.bill_number)

    def test_the_title_is_never_empty(self):
        """
        A blank <title> makes a browser fall back to showing the URL, which as a
        filename is worse than useless. There is always a word.
        """
        job = self._jobcard(brand_name='', model_name='', registration_number='')
        # .update(), not .save() — save() regenerates a bill number the moment it
        # finds the field empty, which is exactly the behaviour being worked
        # around to reach the state this asserts about.
        JobCard.objects.filter(pk=job.pk).update(bill_number='')
        job.refresh_from_db()

        self.assertEqual(build_invoice(job)['document_title'], 'Invoice')

    def test_characters_a_filesystem_would_reject_are_stripped(self):
        """
        A '/' in a registration number becomes a path separator in the saved
        file's name; ':' and '?' are outright illegal on Windows. Stripped
        rather than substituted — a plate reading "KL11 AJ 2266" beats one
        reading "KL11_AJ_2266".
        """
        job = self._jobcard(
            brand_name='Audi', model_name='A/4', registration_number='KL11:AJ?2266',
        )
        title = build_invoice(job)['document_title']

        for character in r'/\:*?"<>|':
            self.assertNotIn(character, title)
        self.assertIn('A4', title)

    def test_stray_whitespace_and_newlines_never_reach_the_filename(self):
        job = self._jobcard(
            brand_name='  Audi  ', model_name='A4\n', registration_number=' KL11   AJ 2266 ',
        )

        self.assertEqual(
            build_invoice(job)['document_title'],
            f"Audi A4 KL11 AJ 2266 ({job.bill_number})",
        )


class ALargeDiscountIsConfirmedBeforeItHappensTests(InvoiceTestCase):
    """
    A part-paid walk-in books its shortfall as a DISCOUNT and is marked PAID —
    the business rule in CLAUDE.md, not a bug. What was missing is that nothing
    on the settle screen said so: Office typed the figure the owner agreed at
    the counter and the difference became a permanent write-off, named nowhere.

    Two things now say it, and the split matters. The running shortfall is shown
    on EVERY settlement, because it costs nothing and is always the truth. The
    confirmation fires only past `HIGH_DISCOUNT_AMOUNT` — confirming what cannot
    surprise anyone is exactly how confirmations stop being read.
    """

    def setUp(self):
        super().setUp()
        self.job = self._jobcard()
        self.job.total_bill_amount = Decimal('22300.00')
        self.job.save(update_fields=['total_bill_amount'])

    def test_the_settle_dialog_shows_the_running_shortfall(self):
        page = self._render(self.job).content.decode()

        self.assertIn('id="payGap"', page)
        self.assertIn('will be recorded as a discount', page)

    def test_the_threshold_reaches_the_page_from_the_model(self):
        """
        Not typed into the template. The confirmation, the HIGH_DISCOUNT alert
        and the audit page have to read one number, or they come to mean three
        different things.
        """
        response = self._render(self.job)

        self.assertEqual(
            response.context['high_discount_threshold'], JobCard.HIGH_DISCOUNT_AMOUNT,
        )
        self.assertIn(str(int(JobCard.HIGH_DISCOUNT_AMOUNT)), response.content.decode())

    def test_the_confirmation_is_not_on_the_paper(self):
        """
        Same rule as every other control here: it lives OUTSIDE `.sheet`. A
        CSS-only `no-print` is one stylesheet edit from printing a dialog on a
        customer's bill.
        """
        sheet = _sheet(self._render(self.job).content.decode())

        self.assertNotEqual(sheet, '', "the sheet scan found nothing — the test would pass vacuously")
        self.assertNotIn('confirmGapDialog', sheet)
        self.assertNotIn('<dialog', sheet)

    def test_a_fleet_card_gets_neither_dialog(self):
        """
        Fleet money moves through the Bulk Payer cascade and `update_bill_status`
        refuses it here anyway, so no settlement control renders at all.
        """
        payer = BulkPayer.objects.create(customer_name='Skyline Fleet')
        self.job.bulk_payer = payer
        self.job.save(update_fields=['bulk_payer'])

        page = self._render(self.job).content.decode()

        self.assertNotIn('confirmGapDialog', page)
        self.assertNotIn('id="payDialog"', page)


class TheDiscountAuditListsByAmountTests(InvoiceTestCase):
    """
    The audit page and the alert must agree about what "large" means. It was a
    30% ratio on both until 2026-08-10; it is a flat ₹3,500 on both now.
    """

    def setUp(self):
        super().setUp()
        owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='owner_audit', password='pw')
        self.owner.groups.add(owner_group)
        self.client.login(username='owner_audit', password='pw')

    def _settled(self, reg, total, discount):
        job = self._jobcard(registration_number=reg)
        job.total_bill_amount = Decimal(total)
        job.discount_amount = Decimal(discount)
        job.received_amount = Decimal(total) - Decimal(discount)
        job.payment_status = 'PAID'
        job.save()
        return job

    def test_a_big_rupee_discount_is_listed_even_at_a_small_percentage(self):
        self._settled('KL01C0001', '60000.00', '7000.00')  # 12%

        page = self.client.get(reverse('audit_high_discounts')).content.decode()

        self.assertIn('KL01C0001', page)

    def test_a_big_percentage_of_a_small_bill_is_not(self):
        self._settled('KL01C0002', '1000.00', '400.00')  # 40%

        page = self.client.get(reverse('audit_high_discounts')).content.decode()

        self.assertNotIn('KL01C0002', page)

    def test_the_page_states_the_threshold_it_is_actually_using(self):
        page = self.client.get(reverse('audit_high_discounts')).content.decode()

        self.assertIn('3,500', page)
        self.assertNotIn('30%', page)
