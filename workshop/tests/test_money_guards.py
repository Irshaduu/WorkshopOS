"""
Every screen that takes a typed rupee amount goes through `workshop/money.py`.

That rule was written for the Cashbook and Salary & Advance in the audit of
2026-08-02 and stopped there. The four screens where money actually MOVES —
settling a walk-in's bill, paying a Fleet Account, paying a spare shop, paying a
Supplies Shop — each kept a hand-rolled `try: Decimal(...)` plus a sign check,
and all four had the same three holes underneath:

  * **'Infinity'** parses as a perfectly valid Decimal and is genuinely greater
    than zero, so `received < 0` and `lump_sum <= 0` both agree with it. This is
    the one that corrupts rather than crashes: the bill settles at an infinite
    receipt and every SUM over that column is infinite afterwards.
  * **'NaN'** also parses, and an ORDERED comparison against it raises
    `decimal.InvalidOperation` — the `try/except` upstream only wrapped the
    parsing, so the raise landed outside it. A 500 on the settle screen rather
    than corruption, but a 500 the person taking the money cannot get past.
    (Worth being precise about: `Decimal('NaN') == 0` returns False quietly,
    while `< > <= >=` all raise. Float NaN does not behave this way.)
  * **11+ digits** overflows `numeric(12, 2)`. SQLite stores it anyway (violating
    the declared precision); Postgres — what ships — answers `numeric field
    overflow`, so a fat finger is a 500 on the one screen where money is taken.

These tests assert the PROPERTY rather than the message: nothing was written,
and the stored figure did not move. That is what has to hold whichever wording
the views end up carrying.
"""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from inventory.models import SupplierPayment, SupplierShop
from workshop.models import (BulkPayer, BulkPaymentHistory, JobCard,
                             SpareShop, SpareShopPayment)

# One list, used by every case below, so a hole cannot be closed on one screen
# and left open on the next.
POISON = ('NaN', 'Infinity', '-Infinity', '99999999999999', 'abc', '')


class MoneyGuardBase(TestCase):
    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='guard_office', password='pw')
        user.groups.add(office)
        self.client.login(username='guard_office', password='pw')


class SettlingABillRefusesAnImpossibleFigureTests(MoneyGuardBase):
    def setUp(self):
        super().setUp()
        self.job = JobCard.objects.create(
            registration_number='KL01MG0001', admitted_date=date(2026, 3, 1),
            brand_name='Toyota', model_name='Corolla',
            total_bill_amount=D('5000.00'),
        )
        self.url = reverse('update_bill_status', args=[self.job.pk])

    def test_a_poisoned_amount_never_reaches_the_column(self):
        for raw in POISON:
            with self.subTest(received=raw):
                self.client.post(self.url, {'received_amount': raw,
                                            'payment_method': 'CASH'})
                self.job.refresh_from_db()
                # A blank box is the one case that legitimately means zero, and
                # zero is what the card already holds — so in every case the
                # figure must be unchanged and the card unsettled.
                self.assertEqual(self.job.received_amount, D('0'))
                self.assertEqual(self.job.payment_status, 'PENDING')

    def test_a_blank_box_still_means_zero(self):
        """
        Typing a figure and then clearing it is how a card is put back to
        PENDING. parse_money refuses zero by default, so this path — and only
        this path — passes allow_zero=True. If that ever regresses, an office
        correcting a mis-keyed payment is stuck.
        """
        self.client.post(self.url, {'received_amount': '4000',
                                    'payment_method': 'CASH'})
        self.job.refresh_from_db()
        self.assertEqual(self.job.payment_status, 'PAID')

        self.client.post(self.url, {'received_amount': '',
                                    'payment_method': 'CASH'})
        self.job.refresh_from_db()
        self.assertEqual(self.job.received_amount, D('0'))
        self.assertEqual(self.job.payment_status, 'PENDING')
        self.assertIsNone(self.job.paid_date)

    def test_an_ordinary_settlement_is_untouched(self):
        """The guard must not become the thing that stops the workshop billing."""
        self.client.post(self.url, {'received_amount': '4500.50',
                                    'payment_method': 'CASH'})
        self.job.refresh_from_db()
        self.assertEqual(self.job.received_amount, D('4500.50'))
        self.assertEqual(self.job.payment_status, 'PAID')
        # The shortfall is the discount — the business rule, not a bug.
        self.assertEqual(self.job.discount_amount, D('499.50'))


class PayingAFleetAccountRefusesAnImpossibleFigureTests(MoneyGuardBase):
    def setUp(self):
        super().setUp()
        self.payer = BulkPayer.objects.create(customer_name='Fleet A')
        self.job = JobCard.objects.create(
            registration_number='KL01MG0002', admitted_date=date(2026, 3, 1),
            bulk_payer=self.payer, total_bill_amount=D('9000.00'),
            payment_status='PENDING',
        )
        self.url = reverse('bulk_payer_pay', args=[self.payer.pk])

    def test_a_poisoned_lump_sum_pays_nothing(self):
        for raw in POISON:
            with self.subTest(lump_sum=raw):
                self.client.post(self.url, {'lump_sum': raw,
                                            'payment_method': 'CASH'})
                self.assertEqual(BulkPaymentHistory.objects.count(), 0)
                self.job.refresh_from_db()
                self.payer.refresh_from_db()
                self.assertEqual(self.job.received_amount, D('0'))
                self.assertEqual(self.payer.advance_balance, D('0'))

    def test_a_real_lump_sum_still_cascades(self):
        self.client.post(self.url, {'lump_sum': '9000', 'payment_method': 'CASH'})
        self.job.refresh_from_db()
        self.assertEqual(self.job.received_amount, D('9000.00'))
        self.assertEqual(self.job.payment_status, 'BULK_PAID')


class PayingASpareShopRefusesAnImpossibleFigureTests(MoneyGuardBase):
    def setUp(self):
        super().setUp()
        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.url = reverse('spare_shop_pay', args=[self.shop.pk])

    def test_a_poisoned_lump_sum_records_no_payment(self):
        for raw in POISON + ('0', '-50'):
            with self.subTest(lump_sum=raw):
                self.client.post(self.url, {'lump_sum': raw,
                                            'payment_method': 'CASH'})
                self.assertEqual(SpareShopPayment.objects.count(), 0)

    def test_a_real_payment_is_still_recorded(self):
        self.client.post(self.url, {'lump_sum': '2500', 'payment_method': 'CASH'})
        self.assertEqual(SpareShopPayment.objects.count(), 1)
        self.assertEqual(SpareShopPayment.objects.get().amount, D('2500.00'))

    def test_an_oversized_note_is_trimmed_rather_than_crashed(self):
        """
        `note` is max_length=255 and comes straight off the POST. A pasted note
        longer than that is stored by SQLite and refused by Postgres with
        "value too long" — a 500 while somebody is recording a payment. Same
        answer as everywhere else: keep the record, lose the overflow.
        """
        self.client.post(self.url, {'lump_sum': '2500', 'payment_method': 'CASH',
                                    'note': 'x' * 400})
        payment = SpareShopPayment.objects.get()
        self.assertEqual(payment.amount, D('2500.00'))
        self.assertEqual(len(payment.note), 255)


class PayingASuppliesShopRefusesAnImpossibleFigureTests(MoneyGuardBase):
    def setUp(self):
        super().setUp()
        self.shop = SupplierShop.objects.create(name='Gulf Lubricants')
        self.url = reverse('add_shop_payment', args=[self.shop.pk])

    def test_a_poisoned_amount_records_no_payment(self):
        for raw in POISON + ('0', '-50'):
            with self.subTest(amount=raw):
                self.client.post(self.url, {'amount': raw,
                                            'payment_method': 'CASH'})
                self.assertEqual(SupplierPayment.objects.count(), 0)

    def test_a_real_payment_is_still_recorded(self):
        self.client.post(self.url, {'amount': '1750.25', 'payment_method': 'CASH'})
        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(SupplierPayment.objects.get().amount, D('1750.25'))

    def test_an_oversized_note_is_trimmed_rather_than_crashed(self):
        """The Supplies Shop twin of the spare-shop case above."""
        self.client.post(self.url, {'amount': '1750.25', 'payment_method': 'CASH',
                                    'note': 'x' * 400})
        payment = SupplierPayment.objects.get()
        self.assertEqual(payment.amount, D('1750.25'))
        self.assertEqual(len(payment.note), 255)


class ThePaymentMethodsAgreeAcrossTheAppTests(TestCase):
    """
    Five models carry their own copy-pasted `PAYMENT_METHODS` list: a job card's
    settlement, a fleet payment, a spare-shop payment, a Supplies Shop payment
    and a cashbook entry. Nothing joins them, so they are free to drift, and a
    method added to one is silently missing from the other four.

    Only the STORED VALUES are pinned. The labels are deliberately left free:
    `JobCard` already says "UPI / QR Code" and "Credit/Debit Card" where the
    others say "UPI" and "Card", and that is the customer-facing wording on the
    one document a customer actually reads.

    Deliberately NOT a validation rule in the views — every one of these is
    chosen from a `<select>` carrying exactly these four options, so an invalid
    value needs a crafted POST from someone already signed in as Office, who can
    record and delete real payments through the ordinary UI anyway.
    """

    def test_every_payment_screen_offers_the_same_methods(self):
        from workshop.models import CashbookEntry

        sets = {
            'JobCard': set(dict(JobCard.PAYMENT_METHOD_CHOICES)),
            'BulkPaymentHistory': set(dict(BulkPaymentHistory.PAYMENT_METHODS)),
            'SpareShopPayment': set(dict(SpareShopPayment.PAYMENT_METHODS)),
            'SupplierPayment': set(dict(SupplierPayment.PAYMENT_METHODS)),
            'CashbookEntry': set(dict(CashbookEntry.PAYMENT_METHODS)),
        }
        expected = {'CASH', 'UPI', 'CARD', 'TRANSFER'}

        for name, values in sets.items():
            with self.subTest(model=name):
                self.assertEqual(
                    values, expected,
                    f"{name} offers {sorted(values)}; every other payment screen "
                    f"offers {sorted(expected)}. Add the method to all five lists "
                    f"or to none — a method missing from one screen is a payment "
                    f"that cannot be recorded there."
                )

    def test_the_column_can_hold_every_method_it_offers(self):
        """
        `max_length=20` against the longest value. A choice longer than its own
        column is stored by SQLite and refused by Postgres with "value too long"
        — the failure would appear only on the method nobody tested with.
        """
        from workshop.models import CashbookEntry

        for model, field, choices in (
            (JobCard, 'payment_method', JobCard.PAYMENT_METHOD_CHOICES),
            (BulkPaymentHistory, 'payment_method', BulkPaymentHistory.PAYMENT_METHODS),
            (SpareShopPayment, 'payment_method', SpareShopPayment.PAYMENT_METHODS),
            (SupplierPayment, 'payment_method', SupplierPayment.PAYMENT_METHODS),
            (CashbookEntry, 'payment_method', CashbookEntry.PAYMENT_METHODS),
        ):
            limit = model._meta.get_field(field).max_length
            for value, _label in choices:
                with self.subTest(model=model.__name__, value=value):
                    self.assertLessEqual(len(value), limit)
