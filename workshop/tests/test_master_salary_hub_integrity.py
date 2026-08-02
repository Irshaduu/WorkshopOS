"""
Regression guards for Master Data, the Owner Control Hub, and Salary & Advance
— from the audit of 2026-08-02.

Each class is named for the RULE it protects, and each one failed before its
fix. They drive the real views with the test Client: the worst defect here
(settling a month silently dropped cash off the Profit page) was invisible at
model level and only appeared through the actual settlement POST.
"""
from decimal import Decimal
from datetime import date, timedelta

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone

from workshop.models import (
    JobCard, JobCardSpareItem, JobCardLabourItem, JobCardConcern, Mechanic,
    SparePart, ConcernSolution, CarBrand, CarModel, SpareShop, DeletionLog,
    SalaryAdvance, SalaryPayment, SalaryPaymentLine, Notification, FailedAttempt,
)
from workshop import analysis_engine as ae

ZERO = Decimal('0')


class WorkshopTestCase(TestCase):
    def setUp(self):
        FailedAttempt.objects.all().delete()
        for g in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=g)
        self.owner = User.objects.create_user(username='owner', password='pass')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.second_owner = User.objects.create_user(username='rijas', password='pass')
        self.second_owner.groups.add(Group.objects.get(name='Owner'))
        self.office = User.objects.create_user(username='office', password='pass')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.floor = User.objects.create_user(username='floor', password='pass')
        self.floor.groups.add(Group.objects.get(name='Floor'))
        self.client = Client()
        self.client.login(username='office', password='pass')
        self.mechanic = Mechanic.objects.create(name='Mech')

    def client_for(self, username):
        c = Client()
        c.login(username=username, password='pass')
        return c


# ===========================================================================
# MASTER DATA
# ===========================================================================
class MasterListsDedupeCaseInsensitivelyTests(WorkshopTestCase):
    """
    Brands, models, spares and concerns dedupe on `__iexact`.

    The models' `unique=True` is case-sensitive, so "Toyota"/"toyota" and
    "Oil Filter"/"oil filter" were both insertable, and ConcernSolution had no
    uniqueness at all — the same concern could be added any number of times.
    Every duplicate then appeared twice in autocomplete and staff picked
    whichever came first. The job-card auto-learn path has always deduped this
    way; these are the manual entry points that did not.
    """

    def test_a_case_variant_brand_is_rejected(self):
        self.client.post(reverse('brand_add'), {'name': 'Toyota'})
        self.client.post(reverse('brand_add'), {'name': 'toyota'})
        self.assertEqual(list(CarBrand.objects.values_list('name', flat=True)), ['Toyota'])

    def test_a_case_variant_spare_is_rejected(self):
        self.client.post(reverse('spare_add'), {'name': 'Oil Filter'})
        self.client.post(reverse('spare_add'), {'name': 'oil filter'})
        self.assertEqual(list(SparePart.objects.values_list('name', flat=True)), ['Oil Filter'])

    def test_a_duplicate_concern_is_rejected(self):
        for _ in range(3):
            self.client.post(reverse('concern_add'), {'concern': 'Brake noise'})
        self.assertEqual(ConcernSolution.objects.count(), 1)

    def test_a_case_variant_model_is_rejected_within_its_brand(self):
        brand = CarBrand.objects.create(name='Toyota')
        other = CarBrand.objects.create(name='Honda')
        self.client.post(reverse('model_add', args=[brand.pk]),
                         {'brand': brand.pk, 'name': 'Corolla'})
        self.client.post(reverse('model_add', args=[brand.pk]),
                         {'brand': brand.pk, 'name': 'corolla'})
        self.assertEqual(CarModel.objects.filter(brand=brand).count(), 1)
        # Same name under a different make is a different car, and must stay allowed.
        self.client.post(reverse('model_add', args=[other.pk]),
                         {'brand': other.pk, 'name': 'Corolla'})
        self.assertEqual(CarModel.objects.filter(brand=other).count(), 1)

    def test_saving_an_entry_without_changing_its_name_is_not_blocked(self):
        """The dedupe check must exclude the row being edited."""
        spare = SparePart.objects.create(name='Oil Filter')
        self.client.post(reverse('spare_edit', args=[spare.pk]), {'name': 'Oil Filter'})
        spare.refresh_from_db()
        self.assertEqual(spare.name, 'Oil Filter')


class RenamingAMasterEntryMeansTheSameThingFromBothScreensTests(WorkshopTestCase):
    """
    Master Lists and Data Cleanup both rename a spare/concern, and both now go
    through workshop.master_data.

    They used to be two implementations of one rule: Data Cleanup deduped
    case-insensitively, merged into an existing entry and rewrote the job-card
    lines carrying the old name; Master Lists saved a plain ModelForm and did
    none of it. Which screen someone happened to open decided what a rename
    meant, and the Master Lists path left the history stranded on the old
    spelling.
    """

    def _card_with_spare(self, reg, spare_name):
        jc = JobCard.objects.create(
            registration_number=reg, brand_name='Toyota', model_name='Corolla',
            admitted_date=date.today(), lead_mechanic=self.mechanic)
        JobCardSpareItem.objects.create(
            job_card=jc, spare_part_name=spare_name, quantity=Decimal('1'),
            unit_price=Decimal('500'), total_price=Decimal('500'),
            source=JobCardSpareItem.SOURCE_SHOP)
        return jc

    def test_master_lists_rename_reaches_job_cards(self):
        spare = SparePart.objects.create(name='Oil Fillter')
        self._card_with_spare('KL01AA0001', 'Oil Fillter')
        self.client.post(reverse('spare_edit', args=[spare.pk]), {'name': 'Oil Filter'})
        self.assertEqual(
            list(JobCardSpareItem.objects.values_list('spare_part_name', flat=True)),
            ['Oil Filter'])

    def test_master_lists_rename_onto_an_existing_entry_merges(self):
        typo = SparePart.objects.create(name='Wheel Bearing Front Left')
        SparePart.objects.create(name='Front Left Wheel Bearing')
        self._card_with_spare('KL01AA0001', 'Wheel Bearing Front Left')
        self._card_with_spare('KL02BB0002', 'Front Left Wheel Bearing')

        self.client.post(reverse('spare_edit', args=[typo.pk]),
                         {'name': 'Front Left Wheel Bearing'})

        self.assertEqual(SparePart.objects.count(), 1)
        self.assertEqual(
            set(JobCardSpareItem.objects.values_list('spare_part_name', flat=True)),
            {'Front Left Wheel Bearing'})

    def test_both_screens_produce_the_identical_result(self):
        for view_name, field, pk_getter in (
            ('spare_edit', 'name', lambda s: [s.pk]),
            ('cleanup_rename_spare', 'new_name', lambda s: [s.pk]),
        ):
            SparePart.objects.all().delete()
            JobCardSpareItem.objects.all().delete()
            JobCard.objects.all().delete()
            typo = SparePart.objects.create(name='Brake Pd')
            SparePart.objects.create(name='Brake Pad')
            self._card_with_spare('KL01AA0001', 'Brake Pd')

            self.client.post(reverse(view_name, args=pk_getter(typo)), {field: 'Brake Pad'})

            self.assertEqual(SparePart.objects.count(), 1, view_name)
            self.assertEqual(
                list(JobCardSpareItem.objects.values_list('spare_part_name', flat=True)),
                ['Brake Pad'], view_name)

    def test_the_surviving_entrys_spelling_wins(self):
        typo = SparePart.objects.create(name='Brake Pd')
        SparePart.objects.create(name='Brake Pad')
        self._card_with_spare('KL01AA0001', 'Brake Pd')
        self.client.post(reverse('spare_edit', args=[typo.pk]), {'name': 'BRAKE PAD'})
        self.assertEqual(SparePart.objects.get().name, 'Brake Pad')
        self.assertEqual(JobCardSpareItem.objects.get().spare_part_name, 'Brake Pad')

    def test_a_merge_is_recorded_in_deletion_history(self):
        typo = SparePart.objects.create(name='Brake Pd')
        SparePart.objects.create(name='Brake Pad')
        self._card_with_spare('KL01AA0001', 'Brake Pd')
        self.client.post(reverse('spare_edit', args=[typo.pk]), {'name': 'Brake Pad'})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_MASTER_DATA)
        self.assertIn('Brake Pd', log.entity_label)
        self.assertEqual(log.snapshot.get('job_card_lines_relabelled'), 1)


class MergingAMasterEntryNeverMovesMoneyOrStockTests(WorkshopTestCase):
    """
    A merge relabels job-card text. It must not touch a bill, a shop ledger, a
    report — or a WAREHOUSE DRAW.

    That last one is the sharp edge: `JobCardSpareItem.item` is a real FK for
    inventory rows, and their displayed name comes from the Item they point at.
    Relabelling one from the spare-parts master list would put a job card's text
    out of step with the product it is actually linked to, and the rename uses
    `.update()`, which fires no signals — so nothing downstream would notice.
    `rename_spare` is scoped to `source=SHOP` for exactly this reason.
    """

    def test_a_merge_leaves_every_figure_untouched(self):
        shop = SpareShop.objects.create(name='ABC Spares')
        typo = SparePart.objects.create(name='Wheel Bearing Front Left')
        SparePart.objects.create(name='Front Left Wheel Bearing')

        cards = []
        for reg, name, price in (('KL01AA0001', 'Wheel Bearing Front Left', '2000'),
                                 ('KL02BB0002', 'Front Left Wheel Bearing', '1800')):
            jc = JobCard.objects.create(
                registration_number=reg, brand_name='Toyota', model_name='Corolla',
                admitted_date=date.today(), lead_mechanic=self.mechanic)
            JobCardSpareItem.objects.create(
                job_card=jc, spare_part_name=name, quantity=Decimal('1'),
                unit_price=Decimal(price), total_price=Decimal(price),
                source=JobCardSpareItem.SOURCE_SHOP, shop=shop)
            JobCardLabourItem.objects.create(job_card=jc, job_description='Fit',
                                             amount=Decimal('500'))
            jc.refresh_from_db()
            cards.append(jc)
        shop.refresh_from_db()
        window = (date.today(), date.today())

        def snapshot():
            shop.refresh_from_db()
            return {
                'bills': [JobCard.objects.get(pk=c.pk).total_bill_amount for c in cards],
                'prices': sorted(JobCardSpareItem.objects.values_list('total_price', flat=True)),
                'shop_owed': shop.total_purchased_amount,
                'expense': ae.spare_shop_expense(*window),
                'turnover': ae.car_bill_turnover(*window)['net'],
                'card_count': JobCard.objects.count(),
                'line_count': JobCardSpareItem.objects.count(),
            }

        before = snapshot()
        self.client.post(reverse('spare_edit', args=[typo.pk]),
                         {'name': 'Front Left Wheel Bearing'})
        self.assertEqual(before, snapshot(),
                         "a merge must relabel text only — no figure may move")

    def test_a_merge_never_relabels_a_warehouse_draw(self):
        from inventory.models import Category, Item

        category = Category.objects.create(name='Oils')
        item = Item.objects.create(category=category, name='Engine Oil',
                                   current_stock=Decimal('10'), average_stock=Decimal('20'))
        typo = SparePart.objects.create(name='Engine Oill')
        SparePart.objects.create(name='Engine Oil')

        jc = JobCard.objects.create(
            registration_number='KL01AA0001', brand_name='Toyota', model_name='Corolla',
            admitted_date=date.today(), lead_mechanic=self.mechanic)
        draw = JobCardSpareItem.objects.create(
            job_card=jc, spare_part_name='Engine Oill', quantity=Decimal('2'),
            total_price=Decimal('900'),
            source=JobCardSpareItem.SOURCE_INVENTORY, item=item)
        item.refresh_from_db()
        stock_before = item.current_stock

        self.client.post(reverse('spare_edit', args=[typo.pk]), {'name': 'Engine Oil'})

        draw.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(draw.spare_part_name, 'Engine Oill',
                         "an inventory draw takes its name from its Item FK and must "
                         "never be relabelled by the spare-parts master list")
        self.assertEqual(draw.item_id, item.pk)
        self.assertEqual(item.current_stock, stock_before,
                         "a rename must move no stock")


class MasterDataDeleteTouchesNoHistoryTests(WorkshopTestCase):
    """
    Deleting a master-list entry cannot alter a job card or a figure — the names
    live on job cards as free text, never as a FK (a deliberate decision, see
    CLAUDE.md). This test exists to keep that true: the day someone converts one
    of these to a ForeignKey, it fails loudly instead of a delete quietly
    cascading a car's history away.

    What the delete DID lack was any trace at all, which is why it is now
    confirmed and logged.
    """

    def test_deleting_master_entries_changes_no_job_card_and_no_figure(self):
        shop = SpareShop.objects.create(name='ABC Spares')
        SparePart.objects.create(name='Oil Filter')
        ConcernSolution.objects.create(concern='Brake noise')
        brand = CarBrand.objects.create(name='Toyota')
        CarModel.objects.create(brand=brand, name='Corolla')

        jc = JobCard.objects.create(
            registration_number='KL01AA0001', brand_name='Toyota',
            model_name='Corolla', admitted_date=date.today(), lead_mechanic=self.mechanic)
        JobCardSpareItem.objects.create(
            job_card=jc, spare_part_name='Oil Filter', quantity=Decimal('2'),
            unit_price=Decimal('300'), total_price=Decimal('800'),
            source=JobCardSpareItem.SOURCE_SHOP, shop=shop)
        JobCardLabourItem.objects.create(job_card=jc, job_description='Service',
                                         amount=Decimal('500'))
        JobCardConcern.objects.create(job_card=jc, concern_text='Brake noise')
        jc.refresh_from_db()
        shop.refresh_from_db()
        window = (jc.admitted_date, jc.admitted_date)

        def snapshot():
            card = JobCard.objects.get(pk=jc.pk)
            shop.refresh_from_db()
            return {
                'bill': card.total_bill_amount,
                'spare_name': card.spares.first().spare_part_name,
                'concern': card.concerns.first().concern_text,
                'brand_on_card': card.brand_name,
                'shop_owed': shop.total_purchased_amount,
                'expense': ae.spare_shop_expense(*window),
                'turnover': ae.car_bill_turnover(*window)['net'],
            }

        before = snapshot()
        self.client.post(reverse('cleanup_delete_spare', args=[SparePart.objects.get().pk]))
        self.client.post(reverse('cleanup_delete_concern', args=[ConcernSolution.objects.get().pk]))
        self.client.post(reverse('brand_delete', args=[brand.pk]))

        self.assertEqual(before, snapshot(),
                         "master-list deletes must never reach a job card or a report")
        self.assertTrue(JobCard.objects.filter(pk=jc.pk).exists())

    def test_the_confirm_page_shows_how_many_job_cards_use_it(self):
        spare = SparePart.objects.create(name='Oil Filter')
        jc = JobCard.objects.create(
            registration_number='KL01AA0001', brand_name='Toyota', model_name='Corolla',
            admitted_date=date.today(), lead_mechanic=self.mechanic)
        for _ in range(3):
            JobCardSpareItem.objects.create(
                job_card=jc, spare_part_name='Oil Filter', quantity=Decimal('1'),
                total_price=Decimal('100'), source=JobCardSpareItem.SOURCE_SHOP)

        resp = self.client.get(reverse('cleanup_delete_spare', args=[spare.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Used on 3 job cards')
        self.assertTrue(SparePart.objects.filter(pk=spare.pk).exists(),
                        "GET must not delete anything")

    def test_a_master_delete_is_logged(self):
        spare = SparePart.objects.create(name='Oil Filter')
        self.client.post(reverse('cleanup_delete_spare', args=[spare.pk]),
                         {'reason': 'typo, never used'})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_MASTER_DATA)
        self.assertIn('Oil Filter', log.entity_label)
        self.assertEqual(log.reason, 'typo, never used')


class MasterListsRbacMatchesItsNeighboursTests(WorkshopTestCase):
    """
    `concern_edit` was `@staff_required` while every other view in the section
    was `@office_required`. Floor got 200 there and could rewrite any master
    concern, while `concern_list` next door returned 403 — the section's list was
    forbidden but editing its contents was not.
    """

    def test_floor_cannot_edit_a_master_concern(self):
        concern = ConcernSolution.objects.create(concern='Brake noise')
        floor = self.client_for('floor')
        self.assertEqual(floor.get(reverse('concern_edit', args=[concern.pk])).status_code, 403)
        self.assertEqual(
            floor.post(reverse('concern_edit', args=[concern.pk]),
                       {'concern': 'Changed by floor'}).status_code, 403)
        concern.refresh_from_db()
        self.assertEqual(concern.concern, 'Brake noise')

    def test_office_still_can(self):
        concern = ConcernSolution.objects.create(concern='Brake noise')
        self.client.post(reverse('concern_edit', args=[concern.pk]),
                         {'concern': 'Brake squeal'})
        concern.refresh_from_db()
        self.assertEqual(concern.concern, 'Brake squeal')


# ===========================================================================
# SALARY & ADVANCE
# ===========================================================================
class EveryAdvanceLandsOnASettlementLineTests(WorkshopTestCase):
    """
    A month cannot be settled while someone who was handed an advance would get
    no settlement line.

    `salary_payment_form` writes a line only for active staff with a salary, and
    `salary_expense()` stops counting a month's advances as "loose" the moment
    the month is settled. So an advance belonging to anyone else was counted in
    NEITHER place: settling the month dropped that cash off the Profit page
    permanently. Both states are ordinary — the home page has a "needs a salary"
    list, and staff leave.
    """

    def _month(self):
        return timezone.localdate().replace(day=1)

    def _end(self, m):
        return (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    def test_settling_is_refused_when_a_staff_member_has_no_salary(self):
        m = self._month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        bilal = Mechanic.objects.create(name='Bilal')
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('2000'), date=m)
        SalaryAdvance.objects.create(staff=bilal, amount=Decimal('3000'), date=m)

        resp = self.client.post(
            reverse('salary_payment_form', args=[m.year, m.month]),
            {f'leave_days_{anil.pk}': '0', f'leave_days_{bilal.pk}': '0'}, follow=True)

        self.assertFalse(SalaryPayment.objects.exists())
        self.assertContains(resp, 'Bilal')
        self.assertContains(resp, 'no salary recorded')

    def test_settling_is_refused_when_a_staff_member_has_retired(self):
        m = self._month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        bilal = Mechanic.objects.create(name='Bilal', current_salary=Decimal('18000'))
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('2000'), date=m)
        SalaryAdvance.objects.create(staff=bilal, amount=Decimal('4000'), date=m)
        self.client_for('owner').post(reverse('manage_toggle_mechanic', args=[bilal.pk]))

        resp = self.client.post(
            reverse('salary_payment_form', args=[m.year, m.month]),
            {f'leave_days_{anil.pk}': '0'}, follow=True)

        self.assertFalse(SalaryPayment.objects.exists())
        self.assertContains(resp, 'Bilal')

    def test_once_the_salary_is_set_the_month_settles_and_nothing_is_lost(self):
        m, end = self._month(), self._end(timezone.localdate().replace(day=1))
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        bilal = Mechanic.objects.create(name='Bilal')
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('2000'), date=m)
        SalaryAdvance.objects.create(staff=bilal, amount=Decimal('3000'), date=m)

        self.client.post(reverse('salary_set_amount', args=[bilal.pk]), {'amount': '15000'})
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '0', f'leave_days_{bilal.pk}': '0'})

        self.assertEqual(SalaryPaymentLine.objects.count(), 2)
        # Both salaries are now the wage cost, and both advances sit inside it.
        self.assertEqual(ae.salary_expense(m, end)['total'], Decimal('35000.00'))

    def test_a_month_with_no_advances_at_all_still_settles(self):
        """The guard must only fire on staff who were actually handed money."""
        m = self._month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        Mechanic.objects.create(name='Bilal')      # no salary, and no advances
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '0'})
        self.assertTrue(SalaryPayment.objects.filter(month=m).exists())


class ASettledMonthSaysWhenItHasGoneStaleTests(WorkshopTestCase):
    """
    A settlement freezes `advance_used` and `net_amount`. An advance recorded (or
    deleted) for that month afterwards makes both wrong — the office would hand
    over the stale net — and nothing said so. Detected by comparing the saved
    lines against the live advance totals, so no stored flag can itself drift,
    and months settled before this existed are covered too.
    """

    def test_a_late_advance_marks_the_month_for_re_settlement(self):
        m = timezone.localdate().replace(day=1)
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('2000'), date=m)
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '0'})

        clean = self.client.get(reverse('salary_advance_home'))
        self.assertEqual(list(clean.context['stale_months']), [])

        self.client.post(reverse('salary_advance_add'),
                         {'staff_id': anil.pk, 'amount': '5000', 'date': str(m)})

        stale = self.client.get(reverse('salary_advance_home'))
        self.assertEqual(list(stale.context['stale_months']), [m])
        self.assertContains(stale, 'needs re-settling')

    def test_re_saving_the_month_clears_the_flag_and_corrects_the_net(self):
        m = timezone.localdate().replace(day=1)
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('2000'), date=m)
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '0'})
        self.client.post(reverse('salary_advance_add'),
                         {'staff_id': anil.pk, 'amount': '5000', 'date': str(m)})

        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '0'})

        line = SalaryPaymentLine.objects.get()
        self.assertEqual(line.advance_used, Decimal('7000.00'))
        self.assertEqual(line.net_amount, Decimal('13000.00'))
        self.assertEqual(
            list(self.client.get(reverse('salary_advance_home')).context['stale_months']), [])

    def test_deleting_an_advance_from_a_settled_month_also_flags_it(self):
        m = timezone.localdate().replace(day=1)
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        advance = SalaryAdvance.objects.create(staff=anil, amount=Decimal('2000'), date=m)
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '0'})
        self.client.post(reverse('salary_advance_delete', args=[advance.pk]))
        self.assertEqual(
            list(self.client.get(reverse('salary_advance_home')).context['stale_months']), [m])


class SalarySettlementInputsAreBoundedTests(WorkshopTestCase):
    """
    Leave days must be a real number of days in the month being settled, and a
    month that has not started cannot be settled at all.

    Unvalidated, `-10` produced a net of ₹26,666.67 on a ₹20,000 salary — a
    negative deduction pays MORE than the salary — and `400` produced
    -₹246,666.67. The month came straight off the URL, so
    /salary-advance/payment/2099/12/ created a Dec 2099 settlement that then
    counted as a settled month forever.
    """

    def test_negative_leave_days_are_rejected(self):
        m = timezone.localdate().replace(day=1)
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        resp = self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                                {f'leave_days_{anil.pk}': '-10'}, follow=True)
        self.assertFalse(SalaryPaymentLine.objects.exists())
        self.assertContains(resp, 'Leave days must be between')

    def test_more_leave_days_than_the_month_has_are_rejected(self):
        m = timezone.localdate().replace(day=1)
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '400'})
        self.assertFalse(SalaryPaymentLine.objects.exists())

    def test_a_sensible_leave_value_still_works(self):
        m = timezone.localdate().replace(day=1)
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('30000'))
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '3'})
        line = SalaryPaymentLine.objects.get()
        self.assertEqual(line.leave_days, Decimal('3.0'))
        self.assertEqual(line.net_amount, Decimal('27000.00'))

    def test_a_future_month_cannot_be_settled(self):
        resp = self.client.post(reverse('salary_payment_form', args=[2099, 12]), {}, follow=True)
        self.assertFalse(SalaryPayment.objects.filter(month=date(2099, 12, 1)).exists())
        # No apostrophe in the needle: the template escapes "hasn't" to
        # "hasn&#x27;t", so matching the raw message would never fire.
        self.assertContains(resp, "started yet")


class RetiredStaffTakeNoNewAdvancesTests(WorkshopTestCase):
    def test_an_advance_to_a_retired_staff_member_is_refused(self):
        staff = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'),
                                        is_active=False)
        resp = self.client.post(reverse('salary_advance_add'),
                                {'staff_id': staff.pk, 'amount': '1000'}, follow=True)
        self.assertEqual(SalaryAdvance.objects.count(), 0)
        self.assertContains(resp, 'retired')


class SalaryNextParameterCannotLeaveTheSiteTests(WorkshopTestCase):
    """`next` on set-salary went straight to redirect(), so a POST carrying an
    external URL bounced an authenticated user off-site — a convincing place for
    a fake "session expired" sign-in page. Validated like the login form's."""

    def test_an_external_next_is_ignored(self):
        staff = Mechanic.objects.create(name='Anil')
        resp = self.client.post(reverse('salary_set_amount', args=[staff.pk]),
                                {'amount': '20000', 'next': 'https://evil.example.com/steal'})
        self.assertEqual(resp['Location'], reverse('salary_advance_home'))

    def test_an_internal_next_is_honoured(self):
        staff = Mechanic.objects.create(name='Anil')
        target = reverse('salary_advance_staff_detail', args=[staff.pk])
        resp = self.client.post(reverse('salary_set_amount', args=[staff.pk]),
                                {'amount': '20000', 'next': target})
        self.assertEqual(resp['Location'], target)


# ===========================================================================
# OWNER CONTROL HUB
# ===========================================================================
class ControlHubAnnouncesAccessChangesTests(WorkshopTestCase):
    """
    Creating a login raised a CRITICAL notification while deleting one and
    changing its password were silent — the two actions that actually revoke or
    hand over access. An owner could remove the Office account overnight and the
    other owner's only clue would be a staff member failing to sign in.
    """

    def test_deleting_a_login_notifies_the_other_owner(self):
        Notification.objects.all().delete()
        self.client_for('owner').post(reverse('manage_delete_user', args=[self.floor.pk]))
        notes = Notification.objects.filter(event='USER_DELETED')
        self.assertTrue(notes.exists())
        # Fanned out to the OTHER owner, never the actor.
        self.assertEqual(list(notes.values_list('recipient__username', flat=True)), ['rijas'])
        self.assertIn('floor', notes.first().body)

    def test_resetting_a_staff_password_notifies_the_other_owner(self):
        Notification.objects.all().delete()
        self.client_for('owner').post(
            reverse('manage_reset_password', args=[self.floor.pk]),
            {'new_password': 'brandnewpass1'})
        notes = Notification.objects.filter(event='STAFF_PASSWORD_SET')
        self.assertTrue(notes.exists())
        self.assertEqual(list(notes.values_list('recipient__username', flat=True)), ['rijas'])

    def test_owner_accounts_are_still_refused(self):
        for route in ('manage_delete_user', 'manage_reset_password', 'manage_unlock_account'):
            self.client_for('owner').post(reverse(route, args=[self.second_owner.pk]),
                                          {'new_password': 'somethinglong1'})
        self.assertTrue(User.objects.filter(pk=self.second_owner.pk).exists())


class LoginNamesDedupeCaseInsensitivelyTests(WorkshopTestCase):
    """Django's username uniqueness is case-sensitive, so 'Office' and 'office'
    were two separate logins. Sign-in matches the username exactly, so whoever
    typed the wrong case just got "invalid credentials" with no way to tell
    why. Matches how Mechanic names have always deduped."""

    def test_a_case_variant_username_is_rejected(self):
        resp = self.client_for('owner').post(
            reverse('manage_create_user'),
            {'username': 'Office', 'password': 'password123', 'role': 'Office'}, follow=True)
        self.assertEqual(User.objects.filter(username__iexact='office').count(), 1)
        self.assertContains(resp, 'already taken')

    def test_a_genuinely_new_username_is_still_accepted(self):
        self.client_for('owner').post(
            reverse('manage_create_user'),
            {'username': 'reception', 'password': 'password123', 'role': 'Office'})
        self.assertTrue(User.objects.filter(username='reception').exists())
