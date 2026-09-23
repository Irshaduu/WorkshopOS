"""
WHO MAY MOVE MONEY IN TIME OR RETYPE IT — and who is told (2026-09-22, the
owner's decision).

Office:
  * dates money at most `money_dates.BACKDATE_DAYS` (3) days back;
  * changes or deletes a money record only within
    `delete_window.OFFICE_WINDOW_HOURS` (24) of recording it — a settled job
    card counts from when it was settled.

Owners do all of it with no limit, and nothing happens silently:

  * anything OFFICE IS ALLOWED TO DO goes to the bell (INFO), whoever did it;
  * anything ONLY AN OWNER CAN DO goes to the other owner's phone (CRITICAL);
  * every delete was already a phone alert, through `DeletionLog.record`.

The tier is decided by the RECORD — its date, its age — never by who is asking,
so the constant that refuses Office is the one that raises the push. That is
the property these tests hold: the rule enforced and the rule announced cannot
drift apart.

The limits themselves are pinned in `test_backdate_floor.py` and
`test_delete_window.py`; this file is the screens and the alerts.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.contrib.messages import get_messages
from django.template import Context, Template
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.models import (BulkPayer, CashbookEntry, FailedAttempt, JobCard,
                             JobCardLabourItem, Mechanic, Notification,
                             OwnerWithdrawal, RentDeposit, SalaryAdvance,
                             SpareShop, SpareShopPayment)
from workshop.notifications import (CRITICAL, EVENTS, INFO, notify_changed,
                                    notify_dated_back)

from inventory.models import SupplierPayment, SupplierRestockBill, SupplierShop


def _age(instance, stamp_field='created_at', **delta):
    """Push a stamp back with `.update()`, so `auto_now_add` cannot restamp it."""
    type(instance).objects.filter(pk=instance.pk).update(
        **{stamp_field: timezone.now() - timedelta(**delta)})
    instance.refresh_from_db()
    return instance


class _People(TestCase):
    """One Office login and TWO owners, so an owner's act has somebody to tell."""

    def setUp(self):
        FailedAttempt.objects.all().delete()
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.office = User.objects.create_user('mc_office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.owner = User.objects.create_user('mc_owner', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.other = User.objects.create_user('mc_other', password='pw')
        self.other.groups.add(Group.objects.get(name='Owner'))
        self.c = Client()
        self.today = timezone.localdate()

    def as_(self, user):
        self.c.force_login(user)
        return self.c

    def said(self, response):
        return ' '.join(str(m) for m in get_messages(response.wsgi_request))

    def told(self, user):
        """The events this user received, newest first."""
        return list(Notification.objects.filter(recipient=user)
                    .order_by('-id').values_list('event', flat=True))


# ---------------------------------------------------------------------------
# The two helpers: where the tier is decided
# ---------------------------------------------------------------------------

class TheTierIsDecidedByTheRecordTests(_People):

    def test_today_is_not_announced_at_all(self):
        self.assertEqual(notify_dated_back('x', self.today, actor=self.office), 0)
        self.assertFalse(Notification.objects.exists())

    def test_inside_the_three_days_is_the_bell(self):
        notify_dated_back('x', self.today - timedelta(days=3), actor=self.office)
        self.assertEqual(set(Notification.objects.values_list('event', flat=True)),
                         {'DATED_BACK'})

    def test_past_the_three_days_is_the_phone(self):
        notify_dated_back('x', self.today - timedelta(days=4), actor=self.owner)
        self.assertEqual(self.told(self.other), ['DATED_BACK_PAST_LIMIT'])

    def test_an_edit_inside_the_window_is_the_bell(self):
        notify_changed('x', timezone.now() - timedelta(hours=2), actor=self.office)
        self.assertEqual(self.told(self.owner), ['RECORD_CHANGED'])

    def test_an_edit_past_the_window_is_the_phone(self):
        notify_changed('x', timezone.now() - timedelta(hours=25), actor=self.owner)
        self.assertEqual(self.told(self.other), ['OLD_RECORD_CHANGED'])

    def test_a_fresh_row_moved_past_the_back_date_limit_is_the_phone_too(self):
        """Only an owner can move a date that far, even on a row typed a minute
        ago — so that is an owner-only act and is announced as one."""
        notify_changed('x', timezone.now(), moved_to=self.today - timedelta(days=30),
                       actor=self.owner)
        self.assertEqual(self.told(self.other), ['OLD_RECORD_CHANGED'])

    def test_the_bell_events_are_INFO_and_the_phone_events_CRITICAL(self):
        for key in ('DATED_BACK', 'RECORD_CHANGED'):
            self.assertEqual(EVENTS[key].severity, INFO, key)
        for key in ('DATED_BACK_PAST_LIMIT', 'OLD_RECORD_CHANGED', 'WITHDRAWAL_ADDED'):
            self.assertEqual(EVENTS[key].severity, CRITICAL, key)

    def test_the_retired_single_section_events_are_gone(self):
        """One section each had the rule, every other money screen had none."""
        for key in ('RENT_BACKDATED', 'CASHBOOK_EDITED'):
            self.assertNotIn(key, EVENTS)

    def test_an_owner_is_never_told_about_their_own_act(self):
        notify_dated_back('x', self.today - timedelta(days=40), actor=self.owner)
        self.assertEqual(self.told(self.owner), [])


# ---------------------------------------------------------------------------
# Every screen that takes a typed money date announces a back-dated one
# ---------------------------------------------------------------------------

class EveryMoneyDateAnnouncesABackDatedEntryTests(_People):

    def setUp(self):
        super().setUp()
        self.two_back = (self.today - timedelta(days=2)).isoformat()
        self.spare_shop = SpareShop.objects.create(name='MC Spares')
        self.supplier = SupplierShop.objects.create(name='MC Supplies')
        self.fleet = BulkPayer.objects.create(customer_name='MC Fleet')

    def post_each(self):
        c = self.as_(self.office)
        c.post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE', 'category': 'Tea', 'amount': '100',
            'payment_method': 'CASH', 'date': self.two_back})
        c.post(reverse('spare_shop_pay', args=[self.spare_shop.pk]), {
            'lump_sum': '100', 'payment_method': 'CASH', 'date': self.two_back})
        c.post(reverse('add_shop_payment', args=[self.supplier.pk]), {
            'amount': '100', 'payment_method': 'CASH', 'date': self.two_back})
        c.post(reverse('bulk_payer_pay', args=[self.fleet.pk]), {
            'lump_sum': '100', 'payment_method': 'CASH', 'date': self.two_back})
        c.post(reverse('rent_deposit_add'), {'amount': '100', 'date': self.two_back})

    def test_all_five_screens_save_and_reach_the_bell(self):
        self.post_each()
        self.assertEqual(CashbookEntry.objects.count(), 1)
        self.assertEqual(SpareShopPayment.objects.count(), 1)
        self.assertEqual(SupplierPayment.objects.count(), 1)
        self.assertEqual(self.fleet.payment_history.count(), 1)
        self.assertEqual(RentDeposit.objects.count(), 1)
        # Five bell rows per owner — one per screen — and not one phone alert.
        self.assertEqual(self.told(self.owner).count('DATED_BACK'), 5)
        self.assertNotIn('DATED_BACK_PAST_LIMIT', self.told(self.owner))

    def test_each_alert_names_the_day_it_was_filed_under(self):
        self.post_each()
        day = f"{self.today - timedelta(days=2):%d %b %Y}"
        for body in Notification.objects.filter(recipient=self.owner).values_list('body', flat=True):
            self.assertIn(day, body)

    def test_todays_entries_raise_nothing(self):
        self.two_back = self.today.isoformat()
        self.post_each()
        self.assertFalse(Notification.objects.filter(
            event__in=('DATED_BACK', 'DATED_BACK_PAST_LIMIT')).exists())

    def test_every_alert_opens_a_page_that_renders(self):
        self.post_each()
        c = self.as_(self.owner)
        for row in Notification.objects.filter(recipient=self.owner):
            self.assertEqual(c.get(row.url).status_code, 200, row.url)


class TheSalaryAdvanceHasTheSameLimitTests(_People):
    """It had no back-date floor at all — only the settled-month freeze."""

    def setUp(self):
        super().setUp()
        self.staff = Mechanic.objects.create(name='Anil', current_salary=D('20000'))

    def give(self, user, days_back):
        return self.as_(user).post(reverse('salary_advance_add'), {
            'staff_id': self.staff.pk, 'amount': '1000',
            'date': (self.today - timedelta(days=days_back)).isoformat()})

    def test_office_inside_three_days_is_saved_with_the_usual_bell_note(self):
        self.give(self.office, 3)
        self.assertEqual(SalaryAdvance.objects.count(), 1)
        self.assertEqual(self.told(self.owner), ['SALARY_ADVANCE'])

    def test_office_past_three_days_is_refused(self):
        res = self.give(self.office, 4)
        self.assertEqual(SalaryAdvance.objects.count(), 0)
        self.assertIn('Ask an owner', self.said(res))

    def test_an_owner_past_three_days_reaches_the_other_owners_phone_once(self):
        """One act, one alert: the phone alert INSTEAD of the bell note."""
        self.give(self.owner, 20)
        self.assertEqual(SalaryAdvance.objects.count(), 1)
        self.assertEqual(self.told(self.other), ['DATED_BACK_PAST_LIMIT'])

    def test_the_date_box_carries_the_floor_for_office_only(self):
        office = self.as_(self.office).get(reverse('salary_advance_home'))
        floor = (self.today - timedelta(days=3)).isoformat()
        self.assertEqual(office.context['floor_iso'], floor)
        self.assertContains(office, f'min="{floor}"')
        owner = self.as_(self.owner).get(reverse('salary_advance_home'))
        self.assertEqual(owner.context['floor_iso'], '')

    def test_an_old_advance_says_ask_an_owner_in_its_menu(self):
        adv = SalaryAdvance.objects.create(staff=self.staff, amount=D('500'),
                                           date=self.today)
        _age(adv, hours=30)
        html = self.as_(self.office).get(
            reverse('salary_advance_staff_detail', args=[self.staff.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()
        self.assertIn('Too old to delete here', html)
        self.assertNotIn('Delete advance', html)


class AnOwnerWithdrawalIsAnnouncedTests(_People):
    """Deleting one was always announced; recording one was silent."""

    def test_the_other_owner_is_told_and_the_actor_is_not(self):
        self.as_(self.owner).post(reverse('withdrawal_add'), {
            'owner': self.owner.pk, 'amount': '5000', 'payment_method': 'CASH',
            'date': self.today.isoformat()})
        self.assertEqual(OwnerWithdrawal.objects.count(), 1)
        self.assertEqual(self.told(self.other), ['WITHDRAWAL_ADDED'])
        self.assertEqual(self.told(self.owner), [])
        row = Notification.objects.get(recipient=self.other)
        self.assertIn('₹5,000', row.body)
        self.assertEqual(self.as_(self.other).get(row.url).status_code, 200)


# ---------------------------------------------------------------------------
# Office's 24 hours on every screen that can EDIT money
# ---------------------------------------------------------------------------

class TheSuppliesShopBillFollowsTheWindowTests(_People):

    def setUp(self):
        super().setUp()
        self.shop = SupplierShop.objects.create(name='MC Supplies')
        self.bill = SupplierRestockBill.objects.create(supplier=self.shop)
        self.edit_url = reverse('edit_restock_bill', args=[self.shop.pk, self.bill.pk])

    def test_office_may_edit_a_bill_keyed_today(self):
        self.assertEqual(self.as_(self.office).get(self.edit_url).status_code, 200)

    def test_office_is_sent_back_on_the_GET_once_it_is_24_hours_old(self):
        """Asked before the form, so nobody fills in a whole bill to be refused."""
        _age(self.bill, hours=25)
        res = self.as_(self.office).get(self.edit_url)
        self.assertEqual(res.status_code, 302)
        self.assertIn('ask an owner', self.said(res).lower())

    def test_office_is_refused_on_the_POST_as_well(self):
        _age(self.bill, hours=25)
        was = self.bill.bill_date
        self.as_(self.office).post(self.edit_url, {
            'bill_date': (self.today - timedelta(days=1)).isoformat(),
            'discount_amount': '0'})
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.bill_date, was)

    def test_an_owner_may_edit_it_and_the_other_owner_is_told(self):
        _age(self.bill, hours=25)
        self.as_(self.owner).post(self.edit_url, {
            'bill_date': (self.today - timedelta(days=20)).isoformat(),
            'discount_amount': '0'})
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.bill_date, self.today - timedelta(days=20))
        self.assertEqual(self.told(self.other), ['OLD_RECORD_CHANGED'])

    def _discount(self, user, amount):
        # A bill with a real total, so ₹500 is a discount the box would
        # otherwise accept — without one, "more than the bill total" refuses
        # it and the test passes for the wrong reason.
        SupplierRestockBill.objects.filter(pk=self.bill.pk).update(total_amount=D('5000'))
        return self.as_(user).post(
            reverse('update_bill_discount', args=[self.shop.pk, self.bill.pk]),
            {'discount_amount': amount})

    def test_the_discount_box_refuses_office_past_24_hours(self):
        _age(self.bill, hours=25)
        res = self._discount(self.office, '500')
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.discount_amount, D('0'))
        self.assertIn('ask an owner', self.said(res).lower())
        self.assertFalse(Notification.objects.filter(
            event__in=('RECORD_CHANGED', 'OLD_RECORD_CHANGED')).exists())

    def test_the_discount_box_lets_office_in_inside_24_hours_with_a_bell_note(self):
        self._discount(self.office, '500')
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.discount_amount, D('500'))
        self.assertEqual(self.told(self.owner), ['RECORD_CHANGED'])

    def test_the_discount_box_lets_an_owner_in_past_24_hours_and_the_phone_is_told(self):
        _age(self.bill, hours=25)
        self._discount(self.owner, '500')
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.discount_amount, D('500'))
        self.assertEqual(self.told(self.other), ['OLD_RECORD_CHANGED'])


class ASettledJobCardFollowsTheWindowTests(_People):
    """
    The customer's own bill: Office may unlock it only within 24 hours of
    SETTLING (`paid_date`), and a changed bill is announced.
    """

    def setUp(self):
        super().setUp()
        self.mechanic = Mechanic.objects.create(name='Mech')
        self.card = JobCard.objects.create(
            registration_number='KL01MC0001', brand_name='Toyota', model_name='Corolla',
            admitted_date=self.today, lead_mechanic=self.mechanic)
        JobCardLabourItem.objects.create(job_card=self.card, job_description='Service')
        self.card.labour_amount = D('1000')
        self.card.save()
        self.card.update_totals()
        self.card.refresh_from_db()
        # Settled through the real screen, so `paid_date` is what it would be.
        self.as_(self.office).post(reverse('update_bill_status', args=[self.card.pk]),
                                   {'received_amount': '1000', 'payment_method': 'CASH'})
        self.card.refresh_from_db()
        Notification.objects.all().delete()

    def payload(self, labour):
        return {
            'registration_number': self.card.registration_number,
            'admitted_date': str(self.card.admitted_date),
            'brand_name': 'Toyota', 'model_name': 'Corolla',
            'lead_mechanic': self.mechanic.id, 'car_color': 'Black',
            'labour_amount': str(labour), 'financial_unlock': 'true',
            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'spares-TOTAL_FORMS': '0', 'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '1', 'labours-INITIAL_FORMS': '1',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',
            'labours-0-id': str(self.card.labours.first().pk),
            'labours-0-job_card': str(self.card.pk),
            'labours-0-job_description': 'Service',
        }

    def test_office_inside_24_hours_may_change_it_and_the_bell_is_told(self):
        self.assertEqual(self.card.payment_status, 'PAID')
        self.as_(self.office).post(reverse('jobcard_edit', args=[self.card.pk]),
                                   self.payload(1500))
        self.card.refresh_from_db()
        self.assertEqual(self.card.total_bill_amount, D('1500.00'))
        self.assertEqual(self.told(self.owner), ['RECORD_CHANGED'])

    def test_office_past_24_hours_is_refused(self):
        _age(self.card, stamp_field='paid_date', days=2)
        res = self.as_(self.office).post(reverse('jobcard_edit', args=[self.card.pk]),
                                         self.payload(1500))
        self.card.refresh_from_db()
        self.assertEqual(self.card.total_bill_amount, D('1000.00'))
        self.assertIn('settled', self.said(res))

    def test_office_past_24_hours_is_shown_why_instead_of_the_unlock_button(self):
        _age(self.card, stamp_field='paid_date', days=2)
        html = self.as_(self.office).get(
            reverse('jobcard_edit', args=[self.card.pk])).content.decode()
        self.assertIn('Settled over 24 hours ago', html)
        self.assertNotIn('UNLOCK RECORD', html)

    def test_an_owner_past_24_hours_may_change_it_and_the_other_owners_phone_is_told(self):
        _age(self.card, stamp_field='paid_date', days=2)
        self.as_(self.owner).post(reverse('jobcard_edit', args=[self.card.pk]),
                                  self.payload(1500))
        self.card.refresh_from_db()
        self.assertEqual(self.card.total_bill_amount, D('1500.00'))
        self.assertIn('OLD_RECORD_CHANGED', self.told(self.other))

    def test_a_large_discount_created_by_the_edit_raises_the_large_discount_alert(self):
        """
        The recomputed discount is the settle screen's rule applied to the new
        total, and the Unlock dialog promised the owners are told. Until
        2026-09-22 nothing was raised at all.
        """
        self.as_(self.office).post(reverse('jobcard_edit', args=[self.card.pk]),
                                   self.payload(1000 + JobCard.HIGH_DISCOUNT_AMOUNT + 500))
        self.card.refresh_from_db()
        self.assertGreater(self.card.discount_amount, JobCard.HIGH_DISCOUNT_AMOUNT)
        self.assertIn('HIGH_DISCOUNT', self.told(self.owner))

    def test_a_large_discount_already_reported_is_not_reported_again(self):
        """An edit that leaves a large discount SMALLER is not news."""
        big = 1000 + JobCard.HIGH_DISCOUNT_AMOUNT + 500
        self.as_(self.office).post(reverse('jobcard_edit', args=[self.card.pk]),
                                   self.payload(big))
        Notification.objects.all().delete()
        self.as_(self.office).post(reverse('jobcard_edit', args=[self.card.pk]),
                                   self.payload(big - 200))
        self.assertNotIn('HIGH_DISCOUNT', self.told(self.owner))
        self.assertIn('RECORD_CHANGED', self.told(self.owner))

    # -- the SETTLE BILL button: the second door onto a paid bill ------------

    def settle(self, user, received):
        return self.as_(user).post(reverse('update_bill_status', args=[self.card.pk]),
                                   {'received_amount': str(received), 'payment_method': 'CASH'})

    def test_office_past_24_hours_cannot_re_settle_it(self):
        _age(self.card, stamp_field='paid_date', days=2)
        res = self.settle(self.office, 500)
        self.card.refresh_from_db()
        self.assertEqual(self.card.received_amount, D('1000.00'))
        self.assertIn('ask an owner', self.said(res).lower())

    def test_office_past_24_hours_cannot_undo_the_payment_either(self):
        """A zero puts a card back to PENDING — the same door, the same rule."""
        _age(self.card, stamp_field='paid_date', days=2)
        self.settle(self.office, 0)
        self.card.refresh_from_db()
        self.assertEqual(self.card.payment_status, 'PAID')

    def test_office_past_24_hours_is_not_offered_the_button(self):
        _age(self.card, stamp_field='paid_date', days=2)
        office = self.as_(self.office).get(reverse('invoice_view', args=[self.card.pk]))
        self.assertTrue(office.context['settle_past_window'])
        self.assertNotContains(office, 'id="settleBtn"')
        owner = self.as_(self.owner).get(reverse('invoice_view', args=[self.card.pk]))
        self.assertContains(owner, 'id="settleBtn"')

    def test_office_inside_24_hours_may_re_settle_with_a_bell_note(self):
        self.settle(self.office, 900)
        self.card.refresh_from_db()
        self.assertEqual(self.card.received_amount, D('900.00'))
        self.assertEqual(self.card.discount_amount, D('100.00'))
        self.assertEqual(self.told(self.owner), ['RECORD_CHANGED'])

    def test_an_owner_past_24_hours_may_re_settle_and_the_other_owner_is_told(self):
        _age(self.card, stamp_field='paid_date', days=2)
        self.settle(self.owner, 900)
        self.card.refresh_from_db()
        self.assertEqual(self.card.received_amount, D('900.00'))
        self.assertEqual(self.told(self.other), ['OLD_RECORD_CHANGED'])

    def test_a_re_settle_keeps_the_day_the_bill_was_settled(self):
        """
        `paid_date` is what Paid Bills files a bill under. Restamping it on a
        correction moved an old bill to "Today" — and restarted the 24-hour
        window, handing Office the bill back.
        """
        _age(self.card, stamp_field='paid_date', days=2)
        settled_at = self.card.paid_date
        self.settle(self.owner, 900)
        self.card.refresh_from_db()
        self.assertEqual(self.card.paid_date, settled_at)
        self.assertTrue(self.as_(self.office).get(
            reverse('invoice_view', args=[self.card.pk])).context['settle_past_window'])

    def test_a_re_settle_that_changes_nothing_raises_nothing(self):
        self.settle(self.office, 1000)
        self.assertEqual(self.told(self.owner), [])

    def test_a_re_settle_does_not_repeat_a_large_discount_alert(self):
        """A large discount is reported once, when it appears or grows."""
        self.card.labour_amount = D('10000')
        self.card.save()
        self.card.update_totals()
        self.settle(self.office, 1000)                  # ₹9,000 off — reported
        self.assertIn('HIGH_DISCOUNT', self.told(self.owner))
        Notification.objects.all().delete()
        self.settle(self.office, 1500)                  # smaller — not news
        self.assertNotIn('HIGH_DISCOUNT', self.told(self.owner))


# ---------------------------------------------------------------------------
# A row past the window says so in its own menu
# ---------------------------------------------------------------------------

class AnOldRowSaysAskAnOwnerTests(_People):
    """
    Presentation — every view refuses again — but a list of rows Office can no
    longer change must not offer buttons that will be refused on every one.
    """

    def test_the_filter_reads_the_same_rule_as_the_views(self):
        tpl = Template("{% load custom_filters %}{{ s|past_office_window:u }}")
        old = timezone.now() - timedelta(hours=25)
        fresh = timezone.now() - timedelta(hours=1)
        self.assertEqual(tpl.render(Context({'s': old, 'u': self.office})), 'True')
        self.assertEqual(tpl.render(Context({'s': fresh, 'u': self.office})), 'False')
        self.assertEqual(tpl.render(Context({'s': old, 'u': self.owner})), 'False')

    def test_the_cashbook_row_menu(self):
        entry = CashbookEntry.objects.create(entry_type='EXPENSE', category='Tea',
                                             amount=D('100'), date=self.today)
        _age(entry, hours=25)
        office = self.as_(self.office).get(reverse('cashbook')).content.decode()
        self.assertIn('Too old to change here', office)
        owner = self.as_(self.owner).get(reverse('cashbook')).content.decode()
        self.assertNotIn('Too old to change here', owner)

    def test_the_spare_shop_payment_menu(self):
        shop = SpareShop.objects.create(name='MC Spares')
        pay = SpareShopPayment.objects.create(shop=shop, amount=D('100'),
                                              payment_method='CASH', date=self.today)
        _age(pay, hours=25)
        html = self.as_(self.office).get(
            reverse('spare_shop_detail', args=[shop.pk]), {'filter': 'all'}).content.decode()
        self.assertIn('Too old to delete here', html)
