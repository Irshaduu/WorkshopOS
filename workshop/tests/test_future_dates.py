"""
Nothing is dated forward — one rule, and the two places that were missing it.

`workshop/money_dates.is_future()` has guarded every typed money date for
months: both Cashbook forms, the fleet payment, the spare-shop payment and the
Supplies Shop payment. `workshop/spare_dates.pair_problem()` carries the same
refusal for an ordered/received pair. Two dates were never wired to it, and both
were found by auditing the About page's claim that "no date can be in the
future" — which was false when it was written.

They are tested together because they are ONE rule, not two: both refusals reuse
`is_future` rather than restating the comparison, so the day somebody decides the
workshop *does* book cars ahead there is one function to change, and these tests
are what say what else moves with it.

⚠ THE WIDGET `max` IS NOT THE CONTROL, on either side. A picker that will not
offer tomorrow saves the round trip; a crafted POST ignores it entirely. Every
test here that matters goes through the server.
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User, Group
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    Category, Item, ShopCatalogItem, SupplierShop,
    SupplierRestockBill, SupplierRestockItem,
)
from workshop.forms import JobCardForm
from workshop.models import JobCard, Mechanic


class ACarCannotBeAdmittedOnADateThatHasNotComeTests(TestCase):
    """
    `JobCard.admitted_date` was the most expensive unguarded date in the app.

    `analysis_engine` dates a card's WHOLE LIFE on it — revenue and both parts
    costs — so a card typed 2027 for 2026 lifts one entire job out of the month
    that earned it, and then hides it: This Month and This Year both end on a
    calendar boundary the card sits past.

    Ruled on by the owner (2026-08-30). The workshop is appointment-driven, so a
    card opened for a car arriving next week was the one plausible reading; it is
    not one they want. A card is opened when the car is admitted.
    """

    def setUp(self):
        self.mechanic = Mechanic.objects.create(name='Lead Tech')
        self.today = timezone.localdate()

    def _payload(self, admitted):
        return {
            'admitted_date': admitted,
            'registration_number': 'KL01AA1000',
            'brand_name': 'Toyota',
            'model_name': 'Corolla',
            'lead_mechanic': self.mechanic.pk,
            'labour_amount': '0',
        }

    def test_a_forward_dated_card_is_refused(self):
        form = JobCardForm(data=self._payload(self.today + timedelta(days=1)))
        self.assertFalse(form.is_valid())
        self.assertIn('admitted_date', form.errors)

    def test_a_mistyped_YEAR_is_refused(self):
        """The commonest real shape of this mistake — 2027 typed for 2026."""
        next_year = self.today.replace(year=self.today.year + 1)
        form = JobCardForm(data=self._payload(next_year))
        self.assertFalse(form.is_valid())
        self.assertIn('admitted_date', form.errors)

    def test_today_is_still_accepted(self):
        """
        The boundary, and the case that matters most: almost every card is
        opened on the day the car arrives, so an off-by-one here would refuse
        the ordinary job rather than the mistyped one.
        """
        form = JobCardForm(data=self._payload(self.today))
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_a_back_dated_card_is_still_accepted(self):
        """
        Nothing about this rule narrows the past. A card keyed a few days late
        is ordinary, and back-dating is the workshop's own rhythm everywhere
        else money is entered.
        """
        form = JobCardForm(data=self._payload(self.today - timedelta(days=30)))
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_it_is_REFUSED_and_never_quietly_moved_to_today(self):
        """
        The codebase's rule wherever a value is typed: a fallback saves a value
        nobody typed. Clamping a 2027 card to today would file the job under the
        wrong month just the same — one month closer, and with nothing said.
        """
        form = JobCardForm(data=self._payload(self.today + timedelta(days=1)))
        self.assertFalse(form.is_valid())
        self.assertEqual(JobCard.objects.count(), 0)

    def test_the_picker_will_not_offer_tomorrow(self):
        """Presentation half — the box is capped at today."""
        form = JobCardForm()
        self.assertEqual(
            form.fields['admitted_date'].widget.attrs.get('max'),
            self.today.isoformat(),
        )

    def test_the_cap_is_computed_PER_REQUEST_not_frozen_at_import(self):
        """
        Why the attribute is set in `__init__` and not in `Meta.widgets`.

        A `max` declared on the widget class is evaluated once, when the module
        is imported — so a server left running past midnight would cap the box
        at the day it booted and start refusing today's cards in the browser.
        """
        far_off = date(2030, 5, 17)
        with mock.patch('workshop.forms.timezone.localdate', return_value=far_off):
            form = JobCardForm()
        self.assertEqual(
            form.fields['admitted_date'].widget.attrs.get('max'),
            far_off.isoformat(),
        )


class NoForwardDatedCardCanBeCreatedThroughTheAppTests(TestCase):
    """
    The end-to-end half. The form is the control; this is what says the control
    is actually reached, and that the refusal is visible rather than a silent
    non-save.
    """

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='fd_office', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='fd_office', password='pw')
        self.mechanic = Mechanic.objects.create(name='Lead Tech')

    def _payload(self, reg):
        tomorrow = timezone.localdate() + timedelta(days=1)
        return {
            'admitted_date': str(tomorrow),
            'registration_number': reg,
            'brand_name': 'Toyota',
            'model_name': 'Corolla',
            'lead_mechanic': self.mechanic.pk,
            'labour_amount': '0',
            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'spares-TOTAL_FORMS': '0', 'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '0', 'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',
        }

    def test_the_post_is_refused_and_writes_no_card(self):
        response = self.client.post(
            reverse('jobcard_create'), self._payload('KL01ZZ9999'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            JobCard.objects.filter(registration_number='KL01ZZ9999').exists(),
            "A forward-dated job card was created.",
        )

    def test_the_refusal_is_SAID_not_just_a_silent_non_save(self):
        """
        `_collect_problems` reads the form's own field errors, so this rides the
        existing banner and the error tone with nothing added. A refused save
        that looks like the Save button doing nothing is the defect that
        function exists to stop.
        """
        response = self.client.post(
            reverse('jobcard_create'), self._payload('KL01ZZ8888'))
        self.assertContains(response, "future")


class ASuppliesShopBillCannotBeDatedInTheFutureTests(TestCase):
    """
    `SupplierRestockBill.bill_date` — and the edit form is the ONLY door to it.

    `shop_restock_bill` takes no date at all and falls to the column default, so
    every bill starts on the day it was keyed. `edit_restock_bill` is where a
    date is chosen, and it assigned the raw POST string onto the field: no
    parse, no bound.

    A forward date here is worse than a wrong label. `inventory/costing.py`
    replays receipts in DATE ORDER, so a bill dated ahead of today sorts after
    every real draw and establishes a cost basis for none of them — the goods
    sit on the shelf while the Profit page charges those draws nothing.
    """

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='bill_dates', password='pw')
        self.user.groups.add(office)
        self.client = Client()
        self.client.login(username='bill_dates', password='pw')

        self.shop = SupplierShop.objects.create(name='Fluid Manjeri')
        self.category = Category.objects.create(name='Oils')
        self.item = Item.objects.create(
            category=self.category, name='Engine Oil', average_stock=20)
        ShopCatalogItem.objects.create(shop=self.shop, item=self.item)

        self.today = timezone.localdate()
        self.bill = SupplierRestockBill.objects.create(
            supplier=self.shop, bill_date=self.today - timedelta(days=10))
        self.line = SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=Decimal('10'),
            total_price=Decimal('5000'))
        self.bill.update_totals()
        self.bill.refresh_from_db()

    def _url(self):
        return reverse('edit_restock_bill', args=[self.shop.pk, self.bill.pk])

    def _payload(self, bill_date):
        data = {
            'discount_amount': '0',
            'qty_{}'.format(self.line.pk): '10',
            'price_{}'.format(self.line.pk): '5000',
        }
        if bill_date is not None:
            data['bill_date'] = bill_date
        return data

    def test_a_forward_dated_bill_is_refused(self):
        original = self.bill.bill_date
        tomorrow = self.today + timedelta(days=1)
        self.client.post(self._url(), self._payload(str(tomorrow)))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.bill_date, original)

    def test_today_is_still_accepted(self):
        self.client.post(self._url(), self._payload(str(self.today)))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.bill_date, self.today)

    def test_a_BACK_dated_bill_is_still_accepted(self):
        """
        The workshop's actual rhythm, and the reason this column is editable at
        all: a Supplies Shop delivers, keeps its own book, and the bill is keyed
        when the collector comes at month end. Narrowing the past here would
        break the workflow the column exists for.
        """
        long_ago = self.today - timedelta(days=45)
        self.client.post(self._url(), self._payload(str(long_ago)))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.bill_date, long_ago)

    def test_an_unreadable_date_no_longer_reaches_the_database(self):
        """
        The second half of the same one-line defect. The raw string went
        straight onto a DateField, so garbage reached Postgres as a `DataError`
        — a 500, and NOT caught by the view's `except ValueError`. SQLite is
        laxer, so this asserts the OUTCOME (no crash, a sane stored date)
        rather than an exception that only ever fired on one backend.
        """
        response = self.client.post(self._url(), self._payload('not-a-date'))
        self.assertLess(response.status_code, 500)
        self.bill.refresh_from_db()
        self.assertLessEqual(self.bill.bill_date, self.today)

    def test_an_ABSENT_date_box_leaves_the_stored_date_alone(self):
        """
        Why the `if bill_date_str:` guard is kept rather than folded into
        `posted_date`, which answers unreadable input with TODAY. A payload that
        never carried the box must not drag a correctly back-dated bill onto the
        keystroke day — the exact thing the money-date columns exist to stop.
        """
        original = self.bill.bill_date
        self.client.post(self._url(), self._payload(None))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.bill_date, original)

    def test_the_picker_will_not_offer_tomorrow(self):
        """Presentation half — the view above is the control."""
        response = self.client.get(self._url())
        self.assertContains(response, 'max="{}"'.format(self.today.isoformat()))
