"""
The WhatsApp icon on the invoice.

A door into the customer's chat and nothing more: drawn for an Owner, on a card
whose number is genuinely an Indian mobile, and it opens the chat empty. The PDF
is attached by hand from Print → Save, because a chat link can carry text but
never a file. See CLAUDE.md, "WhatsApp the customer".
"""

import re
from datetime import date

from django.contrib.auth.models import Group, User
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from workshop.invoice import whatsapp_chat_url
from workshop.models import JobCard
from workshop.tests.test_invoice import _sheet


CHAT = 'https://wa.me/919207217978'


class TheNumberIsReadOnlyWhenItIsAMobileTests(SimpleTestCase):

    def test_every_shape_a_mobile_is_written_in_opens_the_same_chat(self):
        for typed in ('9207217978', '09207217978', '+91 92072 17978',
                      '919207217978', '92072-17978'):
            with self.subTest(typed=typed):
                self.assertEqual(whatsapp_chat_url(typed), CHAT)

    def test_anything_that_is_not_a_mobile_gives_no_link_rather_than_a_guess(self):
        """
        The login lookup keeps the last ten digits of anything. Here that would
        open a chat with a stranger, so junk, a landline and a foreign number are
        all refused outright.
        """
        for typed in (None, '', '   ', '12345', '0483 271 2345', '5207217978',
                      '9207217978123', '44 20 7946 0958'):
            with self.subTest(typed=typed):
                self.assertEqual(whatsapp_chat_url(typed), '')

    def test_the_chat_opens_empty(self):
        self.assertNotIn('?', whatsapp_chat_url('9207217978'))


class _InvoicePage(TestCase):

    def setUp(self):
        for name, group in (('owner', 'Owner'), ('office', 'Office')):
            user = User.objects.create_user(username=name, password='pw')
            user.groups.add(Group.objects.get_or_create(name=group)[0])

    def _card(self, contact):
        return JobCard.objects.create(
            admitted_date=date(2026, 1, 15), brand_name='Volkswagen',
            model_name='Polo', registration_number='HR26X1003',
            customer_name='Ramesh', customer_contact=contact,
        )

    def _page(self, username, card):
        client = Client()
        client.login(username=username, password='pw')
        response = client.get(reverse('invoice_view', args=[card.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class OnlyAnOwnerSeesTheButtonTests(_InvoicePage):

    def test_an_owner_on_a_card_with_a_mobile_gets_the_chat(self):
        html = self._page('owner', self._card('+91 92072 17978'))
        self.assertIn(f'href="{CHAT}"', html)

    def test_office_does_not_see_it_even_with_a_number(self):
        html = self._page('office', self._card('9207217978'))
        self.assertNotIn('wa.me', html)

    def test_no_number_or_a_number_that_is_not_a_mobile_draws_nothing(self):
        for contact in (None, '', '0483 271 2345'):
            with self.subTest(contact=contact):
                html = self._page('owner', self._card(contact))
                self.assertNotIn('wa.me', html)
                self.assertNotIn('btn-whatsapp"', html)


class TheWhatsAppLinkIsNotAFetchTests(_InvoicePage):
    """
    The bill loads nothing from anywhere, and this link does not change that: it
    is a place to GO, not something the page fetches.

    ⚠ `test_the_page_loads_nothing_from_a_third_party` renders as Office on a
    card with no number, so it never sees this link at all. This renders the one
    page that carries it and allows exactly that one address.
    """

    def setUp(self):
        super().setUp()
        self.html = self._page('owner', self._card('9207217978'))

    def test_it_lives_outside_the_paper(self):
        sheet = _sheet(self.html)
        self.assertNotEqual(sheet, '')
        self.assertNotIn('wa.me', sheet)

    def test_it_opens_beside_the_bill_and_says_what_it_is(self):
        anchor = re.search(r'<a [^>]*wa\.me[^>]*>', self.html).group(0)
        self.assertIn('target="_blank"', anchor)
        self.assertIn('rel="noopener"', anchor)
        self.assertIn('aria-label="', anchor)

    def test_the_page_still_fetches_nothing_from_another_host(self):
        self.assertNotIn('<link', self.html)
        for value in re.findall(r'\bsrc="([^"]*)"', self.html):
            self.assertFalse(value.startswith(('http://', 'https://', '//')), value)

        allowed = ('http://www.w3.org/2000/svg', 'http://www.w3.org/1999/xlink', CHAT)
        for match in re.findall(r'https?://[^\s"\'<>)]+', self.html):
            self.assertIn(match, allowed)
