"""
OFFICE CORRECTS A RECENT MISTAKE; AN OWNER TAKES ANYTHING OLDER.

Six money deletes are `@office_required`, so before this Office could remove a
six-month-old fleet payment exactly as easily as one keyed this morning. Those
are two different acts: deleting something recorded an hour ago is a
correction, and deleting something recorded six weeks ago changes a period an
owner has already read the Profit page against.

The rule lives in `workshop/delete_window.py` and is measured on `created_at`.
The tests that matter most here are the ones proving the window follows the
KEYSTROKE and not the MONEY DATE — every one of these forms back-dates
deliberately, so a money-date window would refuse Office permission to delete a
typo they made thirty seconds earlier, which is the exact case the feature
exists to keep easy.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.delete_window import OFFICE_DELETE_WINDOW_DAYS, refusal
from workshop.models import (BulkPayer, BulkPaymentHistory, CashbookEntry,
                             DeletionLog, FailedAttempt, JobCard, Mechanic,
                             SalaryAdvance, SpareShop, SpareShopPayment)

from inventory.models import SupplierPayment, SupplierShop

WINDOW = OFFICE_DELETE_WINDOW_DAYS


def _age(instance, days):
    """
    Push a row's `created_at` back by `days`, with `.update()` so `auto_now_add`
    cannot stamp it back to now.
    """
    type(instance).objects.filter(pk=instance.pk).update(
        created_at=timezone.now() - timedelta(days=days))
    instance.refresh_from_db()
    return instance


class WindowBase(TestCase):
    def setUp(self):
        # FailedAttempt is cleared for the reason CLAUDE.md records: the IP
        # backstop counts across tests otherwise.
        FailedAttempt.objects.all().delete()

        office, _ = Group.objects.get_or_create(name='Office')
        owner_group, _ = Group.objects.get_or_create(name='Owner')

        self.office_user = User.objects.create_user(username='dw_office', password='pw')
        self.office_user.groups.add(office)
        self.owner_user = User.objects.create_user(username='dw_owner', password='pw')
        self.owner_user.groups.add(owner_group)

        self.client = Client()
        self.as_office()

    def as_office(self):
        self.client.force_login(self.office_user)

    def as_owner(self):
        self.client.force_login(self.owner_user)

    def refused(self, url, **post):
        """
        POST and return the refusal text, asserting one was actually shown.

        Checking only that the row survived would pass for the wrong reasons —
        a 403, a bad url name, or some other guard entirely. The message is
        what proves THIS rule fired.
        """
        resp = self.client.post(url, post, follow=True)
        notes = [str(m) for m in resp.context['messages']]
        self.assertTrue(notes, f"POST to {url} said nothing at all")
        text = ' '.join(notes)
        self.assertIn('ask an owner', text.lower())
        return text


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------

class TheRuleItselfTests(WindowBase):

    def test_an_owner_is_never_refused(self):
        old = timezone.now() - timedelta(days=365)
        self.assertIsNone(refusal(self.owner_user, old, 'This payment'))

    def test_office_keeps_todays_row_and_the_last_day_of_the_window(self):
        for days in (0, 1, WINDOW):
            with self.subTest(days=days):
                stamp = timezone.now() - timedelta(days=days)
                self.assertIsNone(refusal(self.office_user, stamp, 'This payment'))

    def test_office_loses_it_the_day_after_the_window(self):
        stamp = timezone.now() - timedelta(days=WINDOW + 1)
        self.assertIsNotNone(refusal(self.office_user, stamp, 'This payment'))

    def test_the_refusal_names_the_row_the_age_and_the_route(self):
        """
        A lock that says "you cannot" without saying why gives the reader
        nothing to act on — the rule the frozen-advance menu already follows.
        """
        stamp = timezone.now() - timedelta(days=40)
        msg = refusal(self.office_user, stamp, 'This ₹15,000 payment')
        self.assertIn('₹15,000', msg)          # which row
        self.assertIn('40 days ago', msg)      # how old
        self.assertIn(str(WINDOW), msg)        # what the rule is
        self.assertIn('owner', msg.lower())    # who can do it

    def test_the_message_quotes_the_window_it_actually_enforces(self):
        """
        One constant behind both, so the number on screen can never disagree
        with the number enforced.
        """
        msg = refusal(self.office_user, timezone.now() - timedelta(days=99), 'This entry')
        self.assertIn(f'last {WINDOW} days', msg)

    def test_a_row_with_no_recorded_time_is_not_refused(self):
        """
        Every column this covers is auto_now_add, so None cannot legitimately
        happen — and guessing "too old" about a row whose age is unknowable
        would block a delete on no evidence.
        """
        self.assertIsNone(refusal(self.office_user, None, 'This payment'))


# ---------------------------------------------------------------------------
# THE INVARIANT: the window follows the keystroke, never the money date
# ---------------------------------------------------------------------------

class TheWindowFollowsTheKeystrokeTests(WindowBase):
    """
    Every form covered here can back-date. If the window read the money date,
    Office would key a back-dated row, mistype it, and be refused permission to
    delete their own typo seconds later — breaking the workflow the feature is
    meant to protect.
    """

    def test_a_back_dated_cashbook_entry_keyed_today_is_still_office_to_delete(self):
        entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Electricity', amount=D('900'),
            date=date.today() - timedelta(days=60),   # money moved two months ago
        )                                             # ...but keyed just now
        resp = self.client.post(reverse('manage_delete_cashbook_entry', args=[entry.pk]),
                                {'reason': 'wrong category'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(CashbookEntry.objects.filter(pk=entry.pk).exists())

    def test_a_back_dated_spare_shop_payment_keyed_today_is_still_office_to_delete(self):
        shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        pay = SpareShopPayment.objects.create(
            shop=shop, amount=D('5000'), payment_method='CASH',
            date=date.today() - timedelta(days=45),
        )
        resp = self.client.post(
            reverse('spare_shop_payment_reverse', args=[shop.pk, pay.pk]),
            {'reason': 'paid twice'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(SpareShopPayment.objects.filter(pk=pay.pk).exists())

    def test_a_back_dated_supplier_payment_keyed_today_is_still_office_to_delete(self):
        shop = SupplierShop.objects.create(name='Fluid Manjeri')
        pay = SupplierPayment.objects.create(
            supplier=shop, amount=D('12000'), payment_method='CASH',
            date=date.today() - timedelta(days=50),
        )
        resp = self.client.post(
            reverse('delete_shop_payment', args=[shop.id, pay.id]),
            {'reason': 'wrong shop'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(SupplierPayment.objects.filter(pk=pay.pk).exists())

    def test_an_old_row_keyed_today_is_kept_even_when_the_money_date_is_ancient(self):
        """
        The property, stated once without a view in the way: a year-old money
        date does not age the row at all.
        """
        entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Rent', amount=D('20000'),
            date=date.today() - timedelta(days=365),
        )
        self.assertIsNone(
            refusal(self.office_user, entry.created_at, 'This entry'))


# ---------------------------------------------------------------------------
# Each view: Office refused past the window, owner allowed, nothing deleted
# ---------------------------------------------------------------------------

class EveryCoveredDeleteRefusesOfficePastTheWindowTests(WindowBase):

    # -- cashbook ----------------------------------------------------------
    def _old_entry(self):
        entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Electricity', amount=D('900'),
            date=date.today())
        return _age(entry, WINDOW + 30)

    def test_cashbook_refuses_office_and_deletes_nothing(self):
        entry = self._old_entry()
        before = DeletionLog.objects.count()
        self.refused(reverse('manage_delete_cashbook_entry', args=[entry.pk]))
        self.assertTrue(CashbookEntry.objects.filter(pk=entry.pk).exists())
        # No half-done delete: nothing was logged either.
        self.assertEqual(DeletionLog.objects.count(), before)

    def test_cashbook_lets_an_owner_through(self):
        entry = self._old_entry()
        self.as_owner()
        self.client.post(reverse('manage_delete_cashbook_entry', args=[entry.pk]),
                         {'reason': 'duplicate'})
        self.assertFalse(CashbookEntry.objects.filter(pk=entry.pk).exists())

    # -- spare shop payment ------------------------------------------------
    def _old_spare_payment(self):
        shop = SpareShop.objects.create(name='Spare Club')
        pay = SpareShopPayment.objects.create(
            shop=shop, amount=D('5000'), payment_method='CASH', date=date.today())
        return shop, _age(pay, WINDOW + 30)

    def test_spare_shop_payment_refuses_office(self):
        shop, pay = self._old_spare_payment()
        self.refused(reverse('spare_shop_payment_reverse', args=[shop.pk, pay.pk]))
        self.assertTrue(SpareShopPayment.objects.filter(pk=pay.pk).exists())

    def test_spare_shop_payment_lets_an_owner_through(self):
        shop, pay = self._old_spare_payment()
        self.as_owner()
        self.client.post(reverse('spare_shop_payment_reverse', args=[shop.pk, pay.pk]),
                         {'reason': 'paid twice'})
        self.assertFalse(SpareShopPayment.objects.filter(pk=pay.pk).exists())

    # -- supplier payment --------------------------------------------------
    def _old_supplier_payment(self):
        shop = SupplierShop.objects.create(name='Lubricant')
        pay = SupplierPayment.objects.create(
            supplier=shop, amount=D('9000'), payment_method='CASH', date=date.today())
        return shop, _age(pay, WINDOW + 30)

    def test_supplier_payment_refuses_office(self):
        shop, pay = self._old_supplier_payment()
        self.refused(reverse('delete_shop_payment', args=[shop.id, pay.id]))
        self.assertTrue(SupplierPayment.objects.filter(pk=pay.pk).exists())

    def test_supplier_payment_lets_an_owner_through(self):
        shop, pay = self._old_supplier_payment()
        self.as_owner()
        self.client.post(reverse('delete_shop_payment', args=[shop.id, pay.id]),
                         {'reason': 'wrong shop'})
        self.assertFalse(SupplierPayment.objects.filter(pk=pay.pk).exists())

    # -- fleet payment -----------------------------------------------------
    def _old_fleet_payment(self):
        payer = BulkPayer.objects.create(customer_name='Hafsi')
        hist = BulkPaymentHistory.objects.create(
            bulk_payer=payer, amount=D('110000'), payment_method='UPI',
            jobs_affected=0, details='[]', date=date.today())
        return payer, _age(hist, WINDOW + 30)

    def test_fleet_payment_refuses_office(self):
        payer, hist = self._old_fleet_payment()
        self.refused(reverse('bulk_payment_history_delete', args=[payer.pk, hist.pk]))
        self.assertTrue(BulkPaymentHistory.objects.filter(pk=hist.pk).exists())

    def test_fleet_payment_lets_an_owner_through(self):
        payer, hist = self._old_fleet_payment()
        self.as_owner()
        self.client.post(
            reverse('bulk_payment_history_delete', args=[payer.pk, hist.pk]),
            {'reason': 'cheque bounced'})
        self.assertFalse(BulkPaymentHistory.objects.filter(pk=hist.pk).exists())

    # -- salary advance ----------------------------------------------------
    def _old_advance(self):
        staff = Mechanic.objects.create(name='Amlah', current_salary=D('20000'))
        adv = SalaryAdvance.objects.create(
            staff=staff, amount=D('3000'), date=date.today())
        return _age(adv, WINDOW + 30)

    def test_salary_advance_refuses_office(self):
        adv = self._old_advance()
        self.refused(reverse('salary_advance_delete', args=[adv.pk]))
        self.assertTrue(SalaryAdvance.objects.filter(pk=adv.pk).exists())

    def test_salary_advance_lets_an_owner_through(self):
        adv = self._old_advance()
        self.as_owner()
        self.client.post(reverse('salary_advance_delete', args=[adv.pk]),
                         {'reason': 'wrong staff member'})
        self.assertFalse(SalaryAdvance.objects.filter(pk=adv.pk).exists())


# ---------------------------------------------------------------------------
# What the window must NOT touch
# ---------------------------------------------------------------------------

class TheWindowLeavesTheRestAloneTests(WindowBase):

    def test_a_fresh_row_is_untouched_on_every_covered_view(self):
        """
        The everyday case. Office keys something and removes it minutes later on
        all six screens — if any of these starts refusing, the window has been
        wired to the wrong column.
        """
        shop = SpareShop.objects.create(name='Calicut')
        pay = SpareShopPayment.objects.create(
            shop=shop, amount=D('1000'), payment_method='CASH', date=date.today())
        self.client.post(reverse('spare_shop_payment_reverse', args=[shop.pk, pay.pk]),
                         {'reason': 'mistake'})
        self.assertFalse(SpareShopPayment.objects.filter(pk=pay.pk).exists())

        entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Tea', amount=D('50'), date=date.today())
        self.client.post(reverse('manage_delete_cashbook_entry', args=[entry.pk]),
                         {'reason': 'mistake'})
        self.assertFalse(CashbookEntry.objects.filter(pk=entry.pk).exists())

    def test_an_old_EMPTY_job_card_is_still_office_to_delete(self):
        """
        `jobcard_delete` is deliberately NOT covered. It already refuses a card
        carrying spares, labour or a received payment, so a deletable card holds
        no money — a window there would be friction buying nothing.
        """
        jc = JobCard.objects.create(registration_number='KL10ZZ9999',
                                    admitted_date=date(2025, 1, 1))
        JobCard.objects.filter(pk=jc.pk).update(
            created_at=timezone.now() - timedelta(days=400))
        resp = self.client.post(reverse('jobcard_delete', args=[jc.pk]),
                                {'reason': 'created in error'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(JobCard.objects.filter(pk=jc.pk).exists())
