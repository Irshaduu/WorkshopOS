"""
Regression guards for the Fleet Account (BulkPayer) ledger, the billing
cascade, and the Cashbook — from the audit of 2026-08-02.

Every class here is named for the RULE it protects, and every one of them
failed before its fix. They drive the real views with the test Client rather
than the ORM: the two worst defects found (a fleet credited with money it never
handed over, and unpaid job cards stranded on an archived account) were
invisible at model level and only appeared through an actual form POST.

The invariant most of the fleet tests turn on:

    Σ(card.received_amount) + advance_balance == Σ(history.amount)

i.e. every rupee a fleet is recorded as having handed over is either sitting on
one of its job cards or held as credit — never invented, never lost.
"""
from decimal import Decimal
from datetime import date, timedelta

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from workshop.models import (
    JobCard, Mechanic, JobCardLabourItem, CashbookEntry, DeletionLog,
    BulkPayer, FailedAttempt,
)
from workshop import analysis_engine as ae

ZERO = Decimal('0')


class FleetLedgerTestCase(TestCase):
    """Shared fixtures: an Office login, a mechanic, and helpers for building
    job cards whose bills are real (built through labour rows, so
    JobCard.update_totals() runs exactly as it does in the app)."""

    def setUp(self):
        FailedAttempt.objects.all().delete()
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        Group.objects.get_or_create(name='Owner')
        self.office = User.objects.create_user(username='office', password='pass')
        self.office.groups.add(self.office_group)
        self.client = Client()
        self.client.login(username='office', password='pass')
        self.mechanic = Mechanic.objects.create(name='Mech')

    def make_card(self, reg, amount, days_ago=0):
        jc = JobCard.objects.create(
            registration_number=reg, brand_name='Toyota', model_name='Corolla',
            admitted_date=date.today() - timedelta(days=days_ago),
            lead_mechanic=self.mechanic,
        )
        JobCardLabourItem.objects.create(
            job_card=jc, job_description='Service', amount=Decimal(amount))
        jc.refresh_from_db()
        return jc

    def assign(self, payer, card):
        return self.client.post(
            reverse('move_jobcard_to_bulk'),
            {'job_card_id': card.pk, 'bulk_payer_id': payer.pk}, follow=True)

    def pay(self, payer, amount):
        return self.client.post(
            reverse('bulk_payer_pay', args=[payer.pk]),
            {'lump_sum': str(amount), 'payment_method': 'CASH'}, follow=True)

    def assert_ledger_balances(self, payer, note=''):
        """Σ(received) + advance == Σ(history) — the fleet conservation law."""
        payer.refresh_from_db()
        received = payer.job_cards.aggregate(
            s=Coalesce(Sum('received_amount'), ZERO))['s']
        history = payer.payment_history.aggregate(
            s=Coalesce(Sum('amount'), ZERO))['s']
        self.assertEqual(
            received + payer.advance_balance, history,
            f"{note}: this fleet's job cards hold ₹{received} and it has "
            f"₹{payer.advance_balance} of credit, but it has only ever paid "
            f"₹{history}"
        )


class ReversingAFleetPaymentOutOfOrderIsRefusedTests(FleetLedgerTestCase):
    """
    A Fleet payment may only be reversed while its effects are still intact.

    Reversal used to clamp at zero in two places, which silently absorbed the
    difference when a LATER payment had already consumed this one's leftover
    credit. Overpaying ₹1,500 on a ₹1,000 bill leaves ₹500 credit; a following
    ₹300 payment spends it on a second car; reversing the first payment then
    found no credit to take back, wrote 0 instead of −500, and left the second
    car settled on ₹800 the fleet never handed over. The fleet's two balance
    figures disagreed by exactly that ₹500.
    """

    def test_reversal_that_would_clamp_the_advance_is_refused(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        a = self.make_card('KL01AA0001', '1000', days_ago=30)
        self.assign(payer, a)
        self.pay(payer, '1500')          # ₹500 becomes advance credit

        b = self.make_card('KL01BB0002', '800', days_ago=10)
        self.assign(payer, b)
        self.pay(payer, '300')           # spends the ₹500 credit on card B
        self.assert_ledger_balances(payer, 'after two payments')

        first = payer.payment_history.order_by('created_at', 'pk').first()
        resp = self.client.post(
            reverse('bulk_payment_history_delete', args=[payer.pk, first.pk]),
            follow=True)

        self.assertTrue(
            payer.payment_history.filter(pk=first.pk).exists(),
            "the older payment must not be reversed once its credit is spent")
        self.assertContains(resp, "Reverse the newer payment")
        self.assert_ledger_balances(payer, 'after the refused reversal')

    def test_reversing_newest_first_still_works_and_restores_the_credit(self):
        """The guard must not block the legitimate order."""
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        a = self.make_card('KL01AA0001', '1000', days_ago=30)
        self.assign(payer, a)
        self.pay(payer, '1500')
        b = self.make_card('KL01BB0002', '800', days_ago=10)
        self.assign(payer, b)
        self.pay(payer, '300')

        newest = payer.payment_history.order_by('-created_at', '-pk').first()
        self.client.post(
            reverse('bulk_payment_history_delete', args=[payer.pk, newest.pk]),
            follow=True)

        payer.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(payer.advance_balance, Decimal('500.00'),
                         "reversing the newer payment must hand its ₹500 credit back")
        self.assertEqual(b.received_amount, ZERO)
        self.assertEqual(b.payment_status, 'PENDING')
        self.assert_ledger_balances(payer, 'after reversing newest-first')

        # And now the older one becomes reversible, as it should.
        oldest = payer.payment_history.order_by('created_at', 'pk').first()
        self.client.post(
            reverse('bulk_payment_history_delete', args=[payer.pk, oldest.pk]),
            follow=True)
        self.assertEqual(payer.payment_history.count(), 0)
        self.assert_ledger_balances(payer, 'after reversing both')

    def test_a_simple_single_payment_reversal_is_unaffected(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        a = self.make_card('KL01AA0001', '1000')
        self.assign(payer, a)
        self.pay(payer, '1000')

        h = payer.payment_history.first()
        self.client.post(
            reverse('bulk_payment_history_delete', args=[payer.pk, h.pk]), follow=True)

        a.refresh_from_db()
        self.assertEqual(a.received_amount, ZERO)
        self.assertEqual(a.payment_status, 'PENDING')
        self.assertIsNone(a.paid_date)
        self.assertTrue(
            DeletionLog.objects.filter(entity_type=DeletionLog.ENTITY_BULK_PAYMENT).exists())
        self.assert_ledger_balances(payer, 'after a plain reversal')


class ArchivingAFleetAccountCannotStrandItsDebtTests(FleetLedgerTestCase):
    """
    A Fleet Account holding unsettled job cards cannot be archived.

    Archiving hid the account from every screen at once — the detail page 404s,
    the picker drops it, Pending Bills already excludes any card carrying a
    bulk_payer, and update_bill_status refuses to settle one, pointing at "that
    account's page" which no longer existed. A PARTIAL card could not even be
    detached, because the received-money guard (correctly) blocks that. So one
    click made real debt unreachable by every route, while the archived list
    went on displaying the balance next to a lone Reactivate button.
    """

    def test_archiving_is_refused_while_a_card_is_unsettled(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        card = self.make_card('KL01AA0001', '1000')
        self.assign(payer, card)

        resp = self.client.post(reverse('bulk_payer_delete', args=[payer.pk]), follow=True)

        payer.refresh_from_db()
        self.assertFalse(payer.is_trashed)
        self.assertContains(resp, "still unsettled")
        self.assertContains(resp, "KL01AA0001")
        # The money is still reachable: its account page opens.
        self.assertEqual(
            self.client.get(reverse('bulk_payer_detail', args=[payer.pk])).status_code, 200)

    def test_archiving_is_refused_for_a_partially_paid_card(self):
        """The worst case: a PARTIAL card cannot be detached either, so
        archiving it would leave no route to the balance at all."""
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        card = self.make_card('KL01AA0001', '1000')
        self.assign(payer, card)
        self.pay(payer, '400')
        card.refresh_from_db()
        self.assertEqual(card.payment_status, 'PARTIAL')

        self.client.post(reverse('bulk_payer_delete', args=[payer.pk]), follow=True)

        payer.refresh_from_db()
        self.assertFalse(payer.is_trashed)

    def test_archiving_a_fully_settled_account_still_works(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        card = self.make_card('KL01AA0001', '1000')
        self.assign(payer, card)
        self.pay(payer, '1000')

        self.client.post(reverse('bulk_payer_delete', args=[payer.pk]), follow=True)

        payer.refresh_from_db()
        self.assertTrue(payer.is_trashed)

    def test_an_empty_account_can_be_archived(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        self.client.post(reverse('bulk_payer_delete', args=[payer.pk]), follow=True)
        payer.refresh_from_db()
        self.assertTrue(payer.is_trashed)

    def test_an_archived_account_takes_no_new_job_cards(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co', is_trashed=True)
        card = self.make_card('KL01AA0001', '1000')

        resp = self.assign(payer, card)

        card.refresh_from_db()
        self.assertIsNone(card.bulk_payer_id)
        self.assertContains(resp, "archived")

    def test_an_archived_account_takes_no_new_payments(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co', is_trashed=True)
        resp = self.client.post(reverse('bulk_payer_pay', args=[payer.pk]),
                                {'lump_sum': '5000', 'payment_method': 'CASH'})
        payer.refresh_from_db()
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(payer.payment_history.count(), 0)
        self.assertEqual(payer.advance_balance, ZERO)

    def test_an_archived_accounts_payments_cannot_be_reversed(self):
        """Reversal un-settles job cards, which would recreate the stranding
        the archive guard exists to prevent."""
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        card = self.make_card('KL01AA0001', '1000')
        self.assign(payer, card)
        self.pay(payer, '1000')
        self.client.post(reverse('bulk_payer_delete', args=[payer.pk]), follow=True)
        payer.refresh_from_db()
        self.assertTrue(payer.is_trashed)

        h = payer.payment_history.first()
        resp = self.client.post(
            reverse('bulk_payment_history_delete', args=[payer.pk, h.pk]))

        self.assertEqual(resp.status_code, 404)
        card.refresh_from_db()
        self.assertEqual(card.payment_status, 'BULK_PAID')


class EditingASettledBillKeepsThePaymentHonestTests(FleetLedgerTestCase):
    """
    After an unlocked edit moves the bill on an already-settled card, the
    payment state must still add up.

    Nothing used to follow the money. A walk-in kept its old `discount_amount`,
    so the Profit page read revenue as `bill − discount` off the NEW total while
    `received_amount` never moved — ₹500 of turnover nobody ever paid. A fleet
    card kept BULK_PAID, and since the cascade only walks PENDING/PARTIAL cards,
    the extra was uncollectable forever: the fleet page showed "₹0 outstanding
    across 0 cards" while get_pending_balance said ₹500, and a further payment
    parked itself as advance credit instead of clearing the card.
    """

    def _edit_payload(self, jc, extra_labour):
        return {
            'registration_number': jc.registration_number,
            'admitted_date': str(jc.admitted_date),
            'customer_name': 'Alice', 'customer_contact': '9876543210',
            'brand_name': 'Toyota', 'model_name': 'Corolla',
            'lead_mechanic': self.mechanic.id, 'car_color': 'Black',
            'financial_unlock': 'true',
            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'spares-TOTAL_FORMS': '0', 'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '2', 'labours-INITIAL_FORMS': '1',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',
            'labours-0-id': str(jc.labours.first().pk),
            'labours-0-job_card': str(jc.pk),
            'labours-0-job_description': 'Service',
            'labours-0-amount': '1000',
            'labours-1-job_description': 'Extra part fitted',
            'labours-1-amount': str(extra_labour),
        }

    def test_a_walkin_bill_that_grows_after_payment_books_the_shortfall_as_discount(self):
        jc = self.make_card('KL01AA0001', '1000')
        self.client.post(reverse('update_bill_status', args=[jc.pk]),
                         {'received_amount': '800', 'payment_method': 'CASH'})

        self.client.post(reverse('jobcard_edit', args=[jc.pk]),
                         self._edit_payload(jc, '500'), follow=True)

        jc.refresh_from_db()
        self.assertEqual(jc.total_bill_amount, Decimal('1500.00'))
        self.assertEqual(jc.discount_amount, Decimal('700.00'))
        window = (jc.admitted_date, jc.admitted_date)
        self.assertEqual(
            ae.car_bill_turnover(*window)['net'], jc.received_amount,
            "a settled card's revenue (bill − discount) must equal what was received")

    def test_a_fleet_bill_that_grows_after_settlement_is_collectable_again(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        jc = self.make_card('KL01AA0001', '1000', days_ago=30)
        self.assign(payer, jc)
        self.pay(payer, '1000')
        jc.refresh_from_db()
        self.assertEqual(jc.payment_status, 'BULK_PAID')

        self.client.post(reverse('jobcard_edit', args=[jc.pk]),
                         self._edit_payload(jc, '500'), follow=True)

        jc.refresh_from_db()
        self.assertEqual(jc.payment_status, 'PARTIAL',
                         "the extra must go back in front of the cascade")
        self.assertIsNone(jc.paid_date)

        resp = self.client.get(reverse('bulk_payer_detail', args=[payer.pk]))
        self.assertEqual(resp.context['total_balance'], Decimal('500.00'))
        payer.refresh_from_db()
        self.assertEqual(payer.get_pending_balance, Decimal('500.00'),
                         "the fleet page and get_pending_balance must agree")

        self.pay(payer, '500')
        jc.refresh_from_db()
        payer.refresh_from_db()
        self.assertEqual(jc.received_amount, Decimal('1500.00'))
        self.assertEqual(jc.payment_status, 'BULK_PAID')
        self.assertEqual(payer.advance_balance, ZERO,
                         "the payment must clear the card, not become credit")
        self.assert_ledger_balances(payer, 'after re-settling an edited card')

    def test_an_unsettled_card_is_not_given_a_discount_by_an_edit(self):
        """The reconciliation must only touch cards that were already settled."""
        jc = self.make_card('KL01AA0001', '1000')
        self.client.post(reverse('jobcard_edit', args=[jc.pk]),
                         self._edit_payload(jc, '500'), follow=True)
        jc.refresh_from_db()
        self.assertEqual(jc.payment_status, 'PENDING')
        self.assertEqual(jc.discount_amount, ZERO)


class FleetDueIsTheFleetSliceOfReceivableTests(FleetLedgerTestCase):
    """
    The Profit page's "Of that, fleet accounts" line claims to be part of the
    "Customers owe us" figure directly above it, so the two must be drawn from
    the same population. `fleet_due` filtered out archived accounts and
    `receivable` did not, so an archived account with an unpaid card made the
    page contradict itself: "Customers owe us ₹1,000 / of that, fleet ₹0".
    """

    def test_an_archived_account_still_counts_in_fleet_due(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        card = self.make_card('KL01AA0001', '1000')
        self.assign(payer, card)
        # Force the archived state that pre-guard data can still be in.
        BulkPayer.objects.filter(pk=payer.pk).update(is_trashed=True)

        position = ae.financial_position()
        self.assertEqual(position['fleet_due'], Decimal('1000.00'))
        self.assertEqual(position['receivable'], Decimal('1000.00'))


class CashbookEntriesAreDatedByTheDayTheMoneyMovedTests(TestCase):
    """
    A Cashbook entry carries the date the money moved, not the date it was
    typed.

    The model has always had a `date` field, the page has always offered
    Last Week / Last Month filters, and analysis_engine has always dated the
    whole stream by it — but no form rendered a date input and neither view
    read one, so every entry was stamped "now". A month-end expense keyed the
    following week landed in the wrong month on the Profit page, permanently:
    the edit form could not move it either.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        group, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='office', password='pass')
        user.groups.add(group)
        self.client = Client()
        self.client.login(username='office', password='pass')

    def test_the_add_form_renders_a_date_input(self):
        """Guards the half a server-side test cannot see: a browser posts only
        what is rendered."""
        page = self.client.get(reverse('cashbook')).content.decode()
        self.assertIn('name="date"', page)

    def test_an_entry_is_stored_on_the_posted_date(self):
        backdated = timezone.localdate() - timedelta(days=40)
        self.client.post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE', 'category': 'Electricity',
            'amount': '5000', 'payment_method': 'CASH', 'date': str(backdated),
        })
        entry = CashbookEntry.objects.get(category='Electricity')
        self.assertEqual(entry.date, backdated)
        self.assertEqual(
            ae.cashbook_expense(backdated, backdated)['total'], Decimal('5000.00'),
            "the Profit page must file it under the month it belongs to")

    def test_an_entrys_date_can_be_corrected(self):
        entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Rent', amount=Decimal('9000'),
            payment_method='CASH', date=timezone.localdate())
        corrected = timezone.localdate() - timedelta(days=3)
        self.client.post(reverse('manage_edit_cashbook_entry', args=[entry.pk]), {
            'category': 'Rent', 'amount': '9000',
            'payment_method': 'CASH', 'date': str(corrected),
        })
        entry.refresh_from_db()
        self.assertEqual(entry.date, corrected)

    def test_a_missing_or_unparseable_date_falls_back_to_today(self):
        for payload_date in ('', 'not-a-date', '2026-13-45'):
            CashbookEntry.objects.all().delete()
            self.client.post(reverse('manage_add_cashbook_entry'), {
                'entry_type': 'INCOME', 'category': 'Scrap',
                'amount': '100', 'payment_method': 'CASH', 'date': payload_date,
            })
            entry = CashbookEntry.objects.get(category='Scrap')
            self.assertEqual(entry.date, timezone.localdate(),
                             f"date={payload_date!r} should fall back to today")


class CashbookAmountsAreBoundedByTheirColumnTests(TestCase):
    """
    `Decimal(amount) > 0` was the only guard, and it let two things through.

    'Infinity' parses as a perfectly valid Decimal and IS greater than zero, so
    it went straight into a money column and made every aggregate touching it
    meaningless. And a 12-digit figure in a `numeric(10,2)` column split by
    database: SQLite stored it, silently violating the declared precision,
    while PostgreSQL — what ships — raises `numeric field overflow` and 500s.
    Bounds now come from the column itself (workshop/money.py).
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        group, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='office', password='pass')
        user.groups.add(group)
        self.client = Client()
        self.client.login(username='office', password='pass')

    def _add(self, **over):
        data = {'entry_type': 'EXPENSE', 'category': 'Electricity',
                'amount': '500', 'payment_method': 'CASH'}
        data.update(over)
        return self.client.post(reverse('manage_add_cashbook_entry'), data)

    def test_an_oversized_amount_is_refused(self):
        self._add(amount='999999999999')
        self.assertEqual(CashbookEntry.objects.count(), 0)

    def test_infinity_and_nan_are_refused(self):
        for bad in ('Infinity', '-Infinity', 'NaN'):
            self._add(amount=bad)
        self.assertEqual(CashbookEntry.objects.count(), 0)

    def test_an_edit_cannot_smuggle_an_oversized_amount_in(self):
        self._add(amount='500')
        entry = CashbookEntry.objects.get()
        self.client.post(reverse('manage_edit_cashbook_entry', args=[entry.pk]),
                         {'category': 'Electricity', 'amount': '999999999999',
                          'payment_method': 'CASH', 'date': str(entry.date)})
        entry.refresh_from_db()
        self.assertEqual(entry.amount, Decimal('500.00'))

    def test_an_ordinary_amount_still_works(self):
        self._add(amount='1234.56')
        self.assertEqual(CashbookEntry.objects.get().amount, Decimal('1234.56'))

    def test_a_future_dated_entry_is_refused(self):
        self._add(date=str(timezone.localdate() + timedelta(days=400)))
        self.assertEqual(CashbookEntry.objects.count(), 0,
                         "money cannot have moved on a day that hasn't come")


class AMiskeyedIncomeOrExpenseCanBeCorrectedTests(TestCase):
    """
    Income keyed as an expense lands on the WRONG SIDE of the Profit equation —
    a double-sized error — and the only way back was deleting the row and
    re-adding it. The edit modal renders the control, because a server-side fix
    with nothing posting to it would have been unreachable.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        group, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='office', password='pass')
        user.groups.add(group)
        self.client = Client()
        self.client.login(username='office', password='pass')

    def test_the_edit_modal_offers_the_type(self):
        CashbookEntry.objects.create(
            entry_type='INCOME', category='Scrap Sell', amount=Decimal('1000'),
            payment_method='CASH', date=timezone.localdate())
        page = self.client.get(reverse('cashbook')).content.decode()
        self.assertIn('name="entry_type"', page)

    def test_an_entry_type_can_be_flipped(self):
        entry = CashbookEntry.objects.create(
            entry_type='INCOME', category='Scrap Sell', amount=Decimal('1000'),
            payment_method='CASH', date=timezone.localdate())
        self.client.post(reverse('manage_edit_cashbook_entry', args=[entry.pk]),
                         {'category': 'Scrap Sell', 'amount': '1000',
                          'payment_method': 'CASH', 'entry_type': 'EXPENSE',
                          'date': str(entry.date)})
        entry.refresh_from_db()
        self.assertEqual(entry.entry_type, 'EXPENSE')

    def test_a_payload_without_a_type_keeps_the_one_it_has(self):
        """Never silently flip a row because a field was absent."""
        entry = CashbookEntry.objects.create(
            entry_type='INCOME', category='Scrap Sell', amount=Decimal('1000'),
            payment_method='CASH', date=timezone.localdate())
        self.client.post(reverse('manage_edit_cashbook_entry', args=[entry.pk]),
                         {'category': 'Scrap Sell', 'amount': '1000',
                          'payment_method': 'CASH', 'date': str(entry.date)})
        entry.refresh_from_db()
        self.assertEqual(entry.entry_type, 'INCOME')


class CashbookCategoriesDoNotSplitTheProfitPageTests(TestCase):
    """
    The Profit page breaks General Cashbook down with `values('category')`, and
    the category is free text with no picker — so "Electricity", "electricity"
    and "ELECTRICITY" were three separate lines for one real cost. The rupee
    total stayed right, but the breakdown an owner reads to see *where* money
    goes was split three ways.

    There is no master list for these, so the entries already recorded are the
    list: whichever spelling got there first wins, the same way a job card
    snaps to the master list's spelling of a car model.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        group, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='office', password='pass')
        user.groups.add(group)
        self.client = Client()
        self.client.login(username='office', password='pass')

    def _add(self, category, amount='1000'):
        return self.client.post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE', 'category': category, 'amount': amount,
            'payment_method': 'CASH', 'date': str(timezone.localdate())})

    def test_case_variants_collapse_onto_the_first_spelling(self):
        for cat in ('Electricity', 'electricity', 'ELECTRICITY', '  Electricity  '):
            self._add(cat)
        today = timezone.localdate()
        report = ae.cashbook_expense(today, today)
        self.assertEqual(len(report['by_category']), 1)
        self.assertEqual(report['by_category'][0]['category'], 'Electricity')
        self.assertEqual(report['by_category'][0]['count'], 4)
        self.assertEqual(report['total'], Decimal('4000.00'))

    def test_a_genuinely_new_category_keeps_what_was_typed(self):
        self._add('Electricity')
        self._add('UPI charges')
        self.assertIn('UPI charges',
                      CashbookEntry.objects.values_list('category', flat=True))

    def test_editing_the_only_entry_of_its_kind_can_recase_it(self):
        self._add('electricity')
        entry = CashbookEntry.objects.get()
        self.client.post(reverse('manage_edit_cashbook_entry', args=[entry.pk]), {
            'category': 'Electricity', 'amount': '1000',
            'payment_method': 'CASH', 'date': str(entry.date)})
        entry.refresh_from_db()
        self.assertEqual(entry.category, 'Electricity',
                         "the row being edited is excluded from the snap, so a "
                         "deliberate correction is not undone")

    def test_wage_looking_categories_are_still_flagged_not_filtered(self):
        for cat in ('Staff Salaries', 'Wages', 'Electricity'):
            self._add(cat, '500')
        today = timezone.localdate()
        report = ae.cashbook_expense(today, today)
        self.assertEqual(
            sorted(r['category'] for r in report['wage_suspects']),
            ['Staff Salaries', 'Wages'])
        self.assertEqual(report['total'], Decimal('1500.00'),
                         "flagged, never filtered — the money stays counted")


class ACappedCashbookListSaysSoTests(TestCase):
    """
    The lists are capped for performance while the totals above them come from
    the full queryset. A period holding more than the cap therefore showed a
    total that plainly did not add up from the rows on screen, with nothing to
    explain the gap.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        group, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='office', password='pass')
        user.groups.add(group)
        self.client = Client()
        self.client.login(username='office', password='pass')

    def test_a_truncated_list_states_the_real_count(self):
        today = timezone.localdate()
        CashbookEntry.objects.bulk_create([
            CashbookEntry(entry_type='EXPENSE', category=f'Item {i}',
                          amount=Decimal('10'), payment_method='CASH', date=today)
            for i in range(320)
        ])
        resp = self.client.get(reverse('cashbook') + '?filter=today')
        self.assertEqual(resp.context['expense_count'], 320)
        self.assertEqual(resp.context['expense_hidden'], 20)
        self.assertContains(resp, 'Showing the 300 most recent of 320')
        self.assertEqual(resp.context['cashbook_totals']['expense'], Decimal('3200'))

    def test_the_ajax_partial_says_it_too(self):
        """The filter buttons swap in the partial, not the full page."""
        today = timezone.localdate()
        CashbookEntry.objects.bulk_create([
            CashbookEntry(entry_type='INCOME', category=f'Sale {i}',
                          amount=Decimal('10'), payment_method='CASH', date=today)
            for i in range(310)
        ])
        resp = self.client.get(reverse('cashbook') + '?filter=today',
                               HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertContains(resp, 'Showing the 300 most recent of 310')

    def test_a_short_list_says_nothing(self):
        CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Rent', amount=Decimal('9000'),
            payment_method='CASH', date=timezone.localdate())
        resp = self.client.get(reverse('cashbook') + '?filter=today')
        self.assertEqual(resp.context['expense_hidden'], 0)
        self.assertNotContains(resp, 'most recent of')


class DeleteFormsPostTheReasonTheirViewsRecordTests(TestCase):
    """
    Both of these views read `reason` and DeletionLog stores it, but neither
    form rendered an input — so every Fleet payment reversal and every deleted
    Cashbook entry reached the Owner's Deletion History with a blank reason,
    the one field that explains why the money moved.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        group, _ = Group.objects.get_or_create(name='Office')
        Group.objects.get_or_create(name='Owner')
        user = User.objects.create_user(username='office', password='pass')
        user.groups.add(group)
        self.client = Client()
        self.client.login(username='office', password='pass')

    def test_the_cashbook_delete_form_offers_a_reason(self):
        CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Rent', amount=Decimal('9000'),
            payment_method='CASH', date=timezone.localdate())
        page = self.client.get(reverse('cashbook')).content.decode()
        self.assertIn('name="reason"', page)

    def test_a_cashbook_deletion_records_the_reason(self):
        entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Rent', amount=Decimal('9000'),
            payment_method='CASH', date=timezone.localdate())
        self.client.post(reverse('manage_delete_cashbook_entry', args=[entry.pk]),
                         {'reason': 'Duplicate of the 3rd'})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_CASHBOOK)
        self.assertEqual(log.reason, 'Duplicate of the 3rd')

    def test_the_fleet_payment_reversal_form_offers_a_reason(self):
        mechanic = Mechanic.objects.create(name='Mech')
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        jc = JobCard.objects.create(
            registration_number='KL01AA0001', brand_name='Toyota',
            model_name='Corolla', admitted_date=date.today(),
            lead_mechanic=mechanic, bulk_payer=payer)
        JobCardLabourItem.objects.create(
            job_card=jc, job_description='Service', amount=Decimal('1000'))
        self.client.post(reverse('bulk_payer_pay', args=[payer.pk]),
                         {'lump_sum': '1000', 'payment_method': 'CASH'})

        page = self.client.get(reverse('bulk_payer_detail', args=[payer.pk])).content.decode()
        self.assertIn('name="reason"', page)
