"""
Every bill for one car, one per page, as a single PDF.

The other half of what a customer asks for. The service history SUMMARISES the
visits; this hands over the bills themselves — which today means opening each
job card, printing it, and sending them one at a time.

⚠ **THE CLASS THAT MATTERS IS `ItIsTheSameBillNotACopyTests`.** A customer
holding this PDF and the paper invoice they were handed last year must find
them identical, and the only way to promise that is for there to be ONE
implementation of the bill. Both documents render
`includes/_invoice_sheet.html` over `build_invoice()`; the test asserts the
rendered sheets match character for character, so a change to one that does
not reach the other fails loudly instead of shipping.
"""

import re
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Category, Item
from workshop.models import (
    JobCard, JobCardLabourItem, JobCardSpareItem, SpareShop,
)


REG = 'KL 10 AA 1000'


class AllInvoicesTestCase(TestCase):

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

    def _visit(self, admitted, completed=True, labour='4000', **kwargs):
        defaults = dict(
            admitted_date=admitted, brand_name='Audi', model_name='A4',
            registration_number=REG, mileage='60000', completed=completed,
            completed_date=admitted if completed else None,
            labour_amount=Decimal(labour),
        )
        defaults.update(kwargs)
        card = JobCard.objects.create(**defaults)
        JobCardLabourItem.objects.create(
            job_card=card, job_description='Engine oil replaced')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Oil Filter',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=Decimal('1'), unit_price=Decimal('700'),
            total_price=Decimal('1200'),
        )
        card.update_totals()
        return card

    def _url(self, registration=REG):
        return reverse('car_all_invoices', args=[registration])

    def _render(self, registration=REG):
        response = self.client.get(self._url(registration))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    @staticmethod
    def _sheets(html):
        """
        Every `.sheet` block on the page, as text.

        Split on the opening div rather than parsed, because what this file
        needs to compare is the rendered STRING — two documents producing the
        same markup is the whole assertion, and a parser would normalise away
        exactly the differences worth catching.
        """
        parts = html.split('<div class="sheet">')[1:]
        return ['<div class="sheet">' + p.split('\n    </div>')[0] for p in parts]


class ItIsTheSameBillNotACopyTests(AllInvoicesTestCase):
    """
    ⚠ THE POINT OF THE WHOLE FEATURE.

    The tempting build is a second template that lays out a bill the same way.
    It would look right on the day and drift on some later one — a column
    width, a rounding, a label — and the customer would be the one to find it,
    holding both documents at once.
    """

    def test_the_rendered_sheet_is_identical_to_the_invoices_own(self):
        card = self._visit(date(2026, 1, 1))

        invoice = self.client.get(reverse('invoice_view', args=[card.pk]))
        self.assertEqual(invoice.status_code, 200)

        from_invoice, = self._sheets(invoice.content.decode())
        from_bundle, = self._sheets(self._render())
        self.assertEqual(from_invoice, from_bundle)

    def test_both_pages_render_the_shared_partial(self):
        """
        Named directly, so deleting the include from one of them fails here
        rather than at the moment a customer compares two documents.
        """
        card = self._visit(date(2026, 1, 1))
        for response in (self.client.get(reverse('invoice_view', args=[card.pk])),
                         self.client.get(self._url())):
            self.assertContains(response, 'class="sheet"')

    def test_the_bundle_carries_no_sheet_id(self):
        """
        With several sheets on one page an id is no longer unique, and
        `getElementById` would silently scale only the first — leaving every
        bill after it overflowing a phone sideways.
        """
        self._visit(date(2026, 1, 1))
        self._visit(date(2026, 6, 1))
        self.assertNotIn('id="sheet"', self._render())


class WhichBillsAppearTests(AllInvoicesTestCase):

    def test_newest_first(self):
        """
        Matching the service history. One vocabulary: an owner opening both
        documents for the same car in one sitting should not have to work out
        that they run in opposite directions.
        """
        old = self._visit(date(2024, 1, 1))
        new = self._visit(date(2026, 1, 1))

        html = self._render()
        self.assertLess(html.index(new.bill_number), html.index(old.bill_number))

    def test_a_car_still_on_the_floor_has_no_bill_to_print(self):
        """Its total is not final, so it is not a bill yet."""
        done = self._visit(date(2026, 1, 1))
        open_card = self._visit(date(2026, 6, 1), completed=False)

        html = self._render()
        self.assertIn(done.bill_number, html)
        self.assertNotIn(open_card.bill_number, html)

    def test_a_deleted_card_is_out(self):
        kept = self._visit(date(2026, 1, 1))
        gone = self._visit(date(2026, 3, 1), is_deleted=True)

        html = self._render()
        self.assertIn(kept.bill_number, html)
        self.assertNotIn(gone.bill_number, html)

    def test_the_count_matches_what_is_printed(self):
        for month in (1, 3, 6):
            self._visit(date(2026, month, 1))
        html = self._render()
        self.assertEqual(len(self._sheets(html)), 3)
        self.assertIn('3 invoices', html)

    def test_the_singular(self):
        self._visit(date(2026, 1, 1))
        self.assertIn('1 invoice<', self._render())

    def test_a_car_with_nothing_billed_says_so_rather_than_printing_nothing(self):
        """An empty page under a print button reads as a broken document."""
        self._visit(date(2026, 6, 1), completed=False)
        html = self._render()
        self.assertEqual(self._sheets(html), [])
        self.assertIn('No bills yet', html)


class WhoMayOpenItTests(AllInvoicesTestCase):

    def test_office_may(self):
        self._visit(date(2026, 1, 1))
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_floor_is_refused_with_a_403(self):
        self._visit(date(2026, 1, 1))
        floor = User.objects.create_user(username='floor', password='pw')
        floor.groups.add(Group.objects.get(name='Floor'))
        client = Client()
        client.login(username='floor', password='pw')
        self.assertEqual(client.get(self._url()).status_code, 403)

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        self._visit(date(2026, 1, 1))
        response = Client().get(self._url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_a_registration_with_no_cards_is_a_404(self):
        self.assertEqual(self.client.get(self._url('KL 99 ZZ 9999')).status_code, 404)


class TheDocumentItselfTests(AllInvoicesTestCase):

    def test_it_is_named_to_file_beside_the_bills_it_contains(self):
        self._visit(date(2026, 1, 1))
        self.assertIn(
            '<title>Audi A4 KL 10 AA 1000 (All Invoices)</title>',
            self._render(),
        )

    def test_there_is_always_a_way_out(self):
        self._visit(date(2026, 1, 1))
        profile = reverse('car_profile_detail', args=[REG])
        self.assertIn(f'href="{profile}"', self._render())

    def test_an_off_site_back_is_refused(self):
        self._visit(date(2026, 1, 1))
        response = self.client.get(self._url(), {'back': 'https://evil.example/x'})
        self.assertNotContains(response, 'evil.example')

    def test_the_page_loads_nothing_from_a_third_party(self):
        """
        The invoice's rule. A bundle that arrives unstyled because a CDN is
        slow is not a set of invoices.

        Asserted on what causes a REQUEST, never on the string "http" — every
        SVG declares `xmlns="http://www.w3.org/2000/svg"`, a namespace NAME no
        browser resolves.
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
                f'unexpected third-party reference: {match}',
            )

    def test_a_bill_is_a_page(self):
        """
        Two bills sharing a sheet of paper would make this a printout rather
        than a set of invoices, and the customer could not hand any single one
        of them to anybody.
        """
        self._visit(date(2026, 1, 1))
        html = self._render()
        self.assertIn('.sheet-wrap + .sheet-wrap', html)
        self.assertIn('page-break-before: always', html)

    def test_the_workshops_own_cost_is_nowhere_on_it(self):
        """
        `unit_price` is what the shop charged the workshop. The shared partial
        already refuses to print it; asserted here too, because this document
        renders SIX bills at once and a leak would be six leaks.
        """
        self._visit(date(2026, 1, 1))
        self.assertNotIn('700.00', self._render())


class TheTwoDoorsOnTheCarProfileTests(AllInvoicesTestCase):
    """
    They are a PAIR: a customer asks for one of two things, and both used to
    mean opening every job card by hand.
    """

    def test_both_buttons_are_offered(self):
        self._visit(date(2026, 1, 1))
        response = self.client.get(reverse('car_profile_detail', args=[REG]))
        self.assertContains(response, reverse('car_service_history', args=[REG]))
        self.assertContains(response, self._url())
        self.assertContains(response, 'Service History')
        self.assertContains(response, 'All Invoices')

    def test_they_sit_in_one_row_on_desktop_and_tablet(self):
        """
        Nothing in the Django suite executes CSS, so the declaration is
        asserted directly — the alternative is a layout rule nothing protects.
        Stacked below the app's own 576px phone line, where two buttons would
        have about 160px each and the longer name would not fit.
        """
        self._visit(date(2026, 1, 1))
        html = self.client.get(reverse('car_profile_detail', args=[REG])).content.decode()
        self.assertIn('@media (min-width: 576px)', html)
        self.assertIn('.cd-docs { flex-direction: row; }', html)

    def test_neither_disappears_on_a_car_with_nothing_completed(self):
        """
        A button that disappears on some cars is one nobody learns is there,
        and both documents handle that case honestly on their own.
        """
        self._visit(date(2026, 6, 1), completed=False)
        response = self.client.get(reverse('car_profile_detail', args=[REG]))
        self.assertContains(response, reverse('car_service_history', args=[REG]))
        self.assertContains(response, self._url())
