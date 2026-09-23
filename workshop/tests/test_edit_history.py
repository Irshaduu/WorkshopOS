"""
EDIT HISTORY — every edit that moved money is KEPT, not only announced
(2026-09-23, Pass 2 of the owners' money-change rules).

Until this, an edit's only trace was its bell note or phone alert, and a
notification is a FEED: read rows are swept after `RETENTION_DAYS`, and
`notify()` excludes the actor. Two weeks on, nothing said what a figure used to
be — while a delete had kept a permanent Deletion History row from day one.

`EditLog.record()` writes the row AND raises the alert, the way
`DeletionLog.record()` does for deletes, so the property these tests hold is:
**an edit is announced if and only if it is kept**, on every door, with the
figures it moved and nothing it did not.

⚠ THE CASHBOOK KEEPS ONLY WHAT ONLY AN OWNER COULD DO (2026-09-24). Its daily
rhythm is an edit or a delete-and-re-add — cash handed out, settled hours later
— so an edit inside Office's 24 hours is neither kept nor announced there.
`EditLog.record(..., only_past_limits=True)`. The other four doors keep every
money edit.
"""
import ast
import io
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.urls import reverse

from inventory.models import (Category, Item, SupplierRestockBill,
                              SupplierRestockItem, SupplierShop)
from workshop.models import (CashbookEntry, EditLog, JobCard, JobCardLabourItem,
                             Mechanic, Notification)
from workshop.tests.test_money_change_rules import _age, _People

EDITED = ('RECORD_CHANGED', 'OLD_RECORD_CHANGED')


def _lines(log):
    """A row's changes as {field: (before, after)}, for readable assertions."""
    return {c['field']: (c['before'], c['after']) for c in log.changes}


# ---------------------------------------------------------------------------
# Each door keeps what it moved
# ---------------------------------------------------------------------------

class TheCashbookKeepsOnlyWhatOnlyAnOwnerCouldDoTests(_People):

    def setUp(self):
        super().setUp()
        self.entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Electricity', amount=D('50000.00'),
            payment_method='CASH', created_by=self.office, date=self.today)

    def edit(self, user, **over):
        payload = {'category': 'Electricity', 'amount': '50000.00',
                   'payment_method': 'CASH', 'entry_type': 'EXPENSE',
                   'date': self.today.isoformat()}
        payload.update(over)
        return self.as_(user).post(
            reverse('manage_edit_cashbook_entry', args=[self.entry.pk]), payload)

    def test_a_same_day_edit_is_the_days_work_and_is_not_kept(self):
        self.edit(self.office, amount='1800')
        self.assertFalse(EditLog.objects.exists())
        self.assertFalse(Notification.objects.filter(event__in=EDITED).exists())

    def test_an_owner_edit_past_24_hours_keeps_what_it_was(self):
        _age(self.entry, days=5)
        self.edit(self.owner, amount='5')
        log = EditLog.objects.get()
        self.assertEqual(log.entity_type, EditLog.ENTITY_CASHBOOK)
        self.assertEqual(log.object_id, self.entry.pk)
        self.assertEqual(log.entity_label, 'Electricity')
        self.assertEqual(log.edited_by, self.owner)
        self.assertEqual(_lines(log), {'Amount': ('50000.00', '5.00')})

    def test_it_is_announced_once_to_the_other_owner(self):
        _age(self.entry, days=5)
        self.edit(self.owner, amount='5')
        self.assertEqual(self.told(self.other), ['OLD_RECORD_CHANGED'])
        self.assertEqual(Notification.objects.filter(event__in=EDITED).count(), 1,
                         'the actor is never told about their own act')

    def test_only_the_fields_that_moved_are_kept(self):
        _age(self.entry, days=5)
        yesterday = self.today - timedelta(days=1)
        self.edit(self.owner, date=yesterday.isoformat(), entry_type='INCOME')
        self.assertEqual(_lines(EditLog.objects.get()), {
            'Date': (self.today.isoformat(), yesterday.isoformat()),
            'Type': ('Expense (Cash Out)', 'Income (Cash In)'),
        })

    def test_a_note_or_a_spelling_is_not_history(self):
        _age(self.entry, days=5)
        self.edit(self.owner, description='meter reading 4411', payment_method='UPI')
        self.assertFalse(EditLog.objects.exists())
        self.assertFalse(Notification.objects.filter(event__in=EDITED).exists())

    def test_a_refused_edit_keeps_nothing(self):
        _age(self.entry, hours=25)
        self.edit(self.office, amount='5')
        self.assertFalse(EditLog.objects.exists())

    def test_the_history_outlives_the_record(self):
        """No foreign key: a record edited and then deleted keeps its edits,
        and the deletion is in Deletion History beside them."""
        _age(self.entry, days=5)
        self.edit(self.owner, amount='5')
        self.as_(self.owner).post(
            reverse('manage_delete_cashbook_entry', args=[self.entry.pk]),
            {'reason': 'duplicate'})
        self.assertFalse(CashbookEntry.objects.filter(pk=self.entry.pk).exists())
        self.assertEqual(EditLog.objects.get().object_id, self.entry.pk)


class TheSuppliesShopBillKeepsItsEditsTests(_People):

    def setUp(self):
        super().setUp()
        self.shop = SupplierShop.objects.create(name='Fluid manjeri')
        category = Category.objects.create(name='Oils')
        item = Item.objects.create(category=category, name='Castrol 5w30',
                                   average_stock=D('40'), current_stock=D('0'))
        self.bill = SupplierRestockBill.objects.create(supplier=self.shop, bill_date=self.today)
        self.line = SupplierRestockItem.objects.create(
            bill=self.bill, item=item, quantity=D('10'), total_price=D('5000'))
        self.bill.update_totals()
        self.bill.refresh_from_db()

    def test_the_discount_box_keeps_the_discount_it_was(self):
        self.as_(self.office).post(
            reverse('update_bill_discount', args=[self.shop.pk, self.bill.pk]),
            {'discount_amount': '500'})
        log = EditLog.objects.get()
        self.assertEqual(log.entity_type, EditLog.ENTITY_RESTOCK_BILL)
        self.assertEqual(log.entity_label, f'Fluid manjeri · Bill #{self.bill.pk}')
        self.assertEqual(_lines(log), {'Discount': ('0.00', '500.00')})
        self.assertEqual(self.told(self.owner), ['RECORD_CHANGED'])

    def test_the_same_discount_again_keeps_nothing(self):
        self.as_(self.office).post(
            reverse('update_bill_discount', args=[self.shop.pk, self.bill.pk]),
            {'discount_amount': '0'})
        self.assertFalse(EditLog.objects.exists())

    def test_the_edit_page_keeps_the_total_and_the_date_it_moved(self):
        earlier = self.today - timedelta(days=2)
        self.as_(self.office).post(
            reverse('edit_restock_bill', args=[self.shop.pk, self.bill.pk]),
            {'bill_date': earlier.isoformat(), 'discount_amount': '0',
             f'qty_{self.line.pk}': '10', f'price_{self.line.pk}': '6000'})
        self.assertEqual(_lines(EditLog.objects.get()), {
            'Bill total': ('5000.00', '6000.00'),
            'Bill date': (self.today.isoformat(), earlier.isoformat()),
        })


class ASettledBillKeepsItsEditsTests(_People):

    def setUp(self):
        super().setUp()
        self.mechanic = Mechanic.objects.create(name='Mech')
        self.card = JobCard.objects.create(
            registration_number='KL01EH0001', brand_name='Toyota', model_name='Corolla',
            admitted_date=self.today, lead_mechanic=self.mechanic)
        JobCardLabourItem.objects.create(job_card=self.card, job_description='Service')
        self.card.labour_amount = D('1000')
        self.card.save()
        self.card.update_totals()
        self.settle(self.office, 1000)
        self.card.refresh_from_db()

    def settle(self, user, received):
        return self.as_(user).post(reverse('update_bill_status', args=[self.card.pk]),
                                   {'received_amount': str(received), 'payment_method': 'CASH'})

    def test_a_first_settlement_is_not_an_edit(self):
        """Taking the money is the ordinary act — setUp just did it."""
        self.assertEqual(self.card.payment_status, 'PAID')
        self.assertFalse(EditLog.objects.exists())

    def test_a_re_settle_keeps_what_was_received_and_the_discount_it_made(self):
        self.settle(self.office, 900)
        log = EditLog.objects.get()
        self.assertEqual(log.entity_type, EditLog.ENTITY_JOBCARD)
        self.assertEqual(log.entity_label, f'KL01EH0001 · {self.card.bill_number}')
        self.assertEqual(_lines(log), {'Received': ('1000.00', '900.00'),
                                       'Discount': ('0.00', '100.00')})

    def test_taking_the_payment_off_is_kept(self):
        self.settle(self.owner, 0)
        self.assertEqual(_lines(EditLog.objects.get())['Received'], ('1000.00', '0.00'))

    def test_a_re_settle_that_changes_nothing_keeps_nothing(self):
        self.settle(self.office, 1000)
        self.assertFalse(EditLog.objects.exists())

    def test_an_unlocked_edit_keeps_the_bill_it_moved(self):
        payload = {
            'registration_number': self.card.registration_number,
            'admitted_date': str(self.card.admitted_date),
            'brand_name': 'Toyota', 'model_name': 'Corolla',
            'lead_mechanic': self.mechanic.id, 'car_color': 'Black',
            'labour_amount': '1500', 'financial_unlock': 'true',
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
        self.as_(self.office).post(reverse('jobcard_edit', args=[self.card.pk]), payload)
        self.assertEqual(_lines(EditLog.objects.get()), {
            'Bill total': ('1000.00', '1500.00'),
            'Discount': ('0.00', '500.00'),
        })


# ---------------------------------------------------------------------------
# No door can announce an edit without keeping it
# ---------------------------------------------------------------------------

class NoDoorGoesRoundTheHistoryTests(_People):
    """
    ⚠ A NEW DOOR THAT CALLS `notify_changed()` DIRECTLY WOULD ANNOUNCE AN EDIT
    AND KEEP NOTHING — and every other kind of test would stay green, because
    the alert still arrives. So the calls are read out of the source: only
    `EditLog.record()` may make one.
    """

    ALLOWED = {'workshop/models.py'}

    def _app_files(self):
        base = settings.BASE_DIR
        for app in ('workshop', 'inventory'):
            for path in (base / app).rglob('*.py'):
                rel = path.relative_to(base).as_posix()
                if '/tests' in rel or rel.rsplit('/', 1)[-1].startswith('test'):
                    continue
                if '/migrations/' in rel:
                    continue
                yield rel, io.open(path, encoding='utf-8').read()

    def test_the_scan_actually_reads_the_codebase(self):
        files = dict(self._app_files())
        self.assertGreater(len(files), 40, 'the scan read almost nothing - check BASE_DIR')
        self.assertIn('workshop/cashbook_views.py', files)

    def test_no_door_announces_an_edit_without_keeping_it(self):
        offenders = []
        for rel, src in self._app_files():
            if rel in self.ALLOWED:
                continue
            for node in ast.walk(ast.parse(src)):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, 'attr', '')
                if name == 'notify_changed':
                    offenders.append(f'{rel}:{node.lineno}')
        self.assertEqual(offenders, [],
                         'call EditLog.record() instead, which keeps the edit and then announces it')

    def test_the_one_allowed_caller_is_edit_log_record(self):
        src = io.open(settings.BASE_DIR / 'workshop' / 'models.py', encoding='utf-8').read()
        tree = ast.parse(src)
        record = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.ClassDef) and n.name == 'EditLog')
        calls = [n for n in ast.walk(record) if isinstance(n, ast.Call)
                 and getattr(n.func, 'id', '') == 'notify_changed']
        self.assertEqual(len(calls), 1)


# ---------------------------------------------------------------------------
# The Edited tab
# ---------------------------------------------------------------------------

class TheEditedTabTests(_People):

    def setUp(self):
        super().setUp()
        self.entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Electricity', amount=D('50000.50'),
            payment_method='CASH', created_by=self.office, date=self.today)
        # Past Office's window, so the Cashbook keeps the edit (its quiet rule
        # keeps only what only an owner could do).
        _age(self.entry, days=5)
        self.url = reverse('edit_history')

    def _edit(self, **over):
        payload = {'category': 'Electricity', 'amount': '50000.50',
                   'payment_method': 'CASH', 'entry_type': 'EXPENSE',
                   'date': self.today.isoformat()}
        payload.update(over)
        self.as_(self.owner).post(
            reverse('manage_edit_cashbook_entry', args=[self.entry.pk]), payload)

    def test_it_is_owner_only_like_deletion_history(self):
        self.assertEqual(self.as_(self.office).get(self.url).status_code, 403)
        self.assertEqual(self.as_(self.owner).get(self.url).status_code, 200)

    def test_a_row_says_what_moved_from_what_to_what_and_who(self):
        self._edit(amount='50000')
        res = self.as_(self.owner).get(self.url)
        self.assertContains(res, 'class="hx-row"', count=1)
        self.assertContains(res, 'Electricity')
        self.assertContains(res, '<span class="f">Amount</span>')
        # Paise kept where there are any, dropped where there are none — the
        # app's `inr_amount`. A `:,.0f` would have printed "₹50,000 → ₹50,000".
        self.assertContains(res, '<span class="was">₹50,000.50</span>')
        self.assertContains(res, '<span class="now">₹50,000</span>')
        self.assertContains(res, 'mc_owner')

    def test_a_date_reads_as_a_date(self):
        earlier = self.today - timedelta(days=2)
        self._edit(date=earlier.isoformat())
        res = self.as_(self.owner).get(self.url)
        self.assertContains(res, f'<span class="now">{earlier:%d %b %Y}</span>')

    def test_the_type_filter_narrows_it(self):
        self._edit(amount='50000')
        cash = self.as_(self.owner).get(self.url, {'type': 'CASHBOOK'})
        bills = self.as_(self.owner).get(self.url, {'type': 'RESTOCK_BILL'})
        self.assertContains(cash, 'class="hx-row"', count=1)
        self.assertNotContains(bills, 'class="hx-row"')
        self.assertContains(bills, 'of this type')

    def test_the_page_is_named_once_and_the_tab_marks_the_view(self):
        """One page, CHANGE HISTORY — the heading names the page and the
        open tab names the view, so neither says another tab's contents."""
        month = self.today.strftime('%Y-%m')
        edited = self.as_(self.owner).get(self.url).content.decode()
        deleted = self.as_(self.owner).get(reverse('deletion_history')).content.decode()
        for html in (edited, deleted):
            self.assertIn('Change History</h1>', html)
        self.assertIn(f'href="{self.url}?month={month}" aria-current="page"', edited)
        self.assertIn(f'href="{reverse("deletion_history")}?month={month}" aria-current="page"', deleted)

    def test_the_drawer_lights_deletion_history_on_both_tabs(self):
        """One menu entry, not a second one — the Edited tab lives under the
        same address prefix."""
        self.assertTrue(self.url.startswith('/deletion-history/'))


# ---------------------------------------------------------------------------
# The Deleted tab: one month at a time, the amount said once
# ---------------------------------------------------------------------------

class TheDeletedTabTests(_People):

    def _log(self, label, amount=None, days_ago=0):
        from workshop.models import DeletionLog
        log = DeletionLog.objects.create(
            entity_type=DeletionLog.ENTITY_RESTOCK_BILL, entity_label=label,
            amount=amount, deleted_by=self.office)
        if days_ago:
            _age(log, stamp_field='deleted_at', days=days_ago)
        return log

    def test_it_shows_one_month_and_no_pager(self):
        self._log('Restock Bill #1 · Fluid manjeri', D('100'))
        self._log('Restock Bill #2 · Old shop', D('100'), days_ago=40)
        res = self.as_(self.owner).get(reverse('deletion_history'))
        self.assertContains(res, 'Fluid manjeri')
        self.assertNotContains(res, 'Old shop')
        self.assertNotIn('page_obj', res.context)

    def test_the_amount_is_printed_once_not_in_the_title_as_well(self):
        """Nine delete paths put the figure in their label; the row prints it
        on the right, so the title drops its own copy."""
        self._log('Restock Bill #669 · Fluid manjeri · ₹31,500', D('31500'))
        res = self.as_(self.owner).get(reverse('deletion_history'))
        (row,) = res.context['rows']
        self.assertEqual(row['title'], 'Restock Bill #669 · Fluid manjeri')
        # The template writes the rupee sign as `&#8377;`, so the figure is
        # counted on its own digits — once on the page, on the right.
        self.assertContains(res, '31,500', count=1)

    def test_a_title_that_opens_with_its_type_does_not_repeat_it(self):
        self._log('Restock Bill #669 · Fluid manjeri', D('100'))
        (row,) = self.as_(self.owner).get(reverse('deletion_history')).context['rows']
        self.assertEqual(row['kind'], '')

    def test_a_row_opens_its_snapshot(self):
        log = self._log('Restock Bill #7 · Somewhere', D('100'))
        self.assertContains(self.as_(self.owner).get(reverse('deletion_history')),
                            f'href="{reverse("deletion_history_detail", args=[log.pk])}"')


# ---------------------------------------------------------------------------
# The pre-go-live purge clears it
# ---------------------------------------------------------------------------

class ThePurgeClearsEditHistoryTests(_People):

    def test_purge_business_data_clears_it(self):
        EditLog.objects.create(
            entity_type=EditLog.ENTITY_CASHBOOK, object_id=1, entity_label='Petrol',
            changes=[{'field': 'Amount', 'kind': 'money', 'before': '500.00', 'after': '400.00'}])
        self.assertEqual(EditLog.objects.count(), 1)
        call_command('purge_business_data', yes=True, stdout=StringIO())
        self.assertEqual(EditLog.objects.count(), 0)
