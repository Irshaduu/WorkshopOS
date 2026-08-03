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


class RenamingABrandOrModelReachesTheJobCardsTests(WorkshopTestCase):
    """
    Reports group by `JobCard.brand_name` / `model_name` (free text on the card,
    by deliberate design). Renaming a spare or a concern has always reached that
    history; renaming a BRAND or MODEL did not — so a brand recorded as "Toyta"
    on one card stayed a second brand in Deep Analysis forever, and correcting
    the master list changed nothing, which is the least useful place for a fix
    to fail.
    """

    def _card(self, reg, brand, model):
        jc = JobCard.objects.create(
            registration_number=reg, brand_name=brand, model_name=model,
            admitted_date=date.today(), lead_mechanic=self.mechanic)
        JobCardLabourItem.objects.create(job_card=jc, job_description='Service',
                                         amount=Decimal('1000'))
        return jc

    def test_renaming_a_brand_relabels_its_job_cards(self):
        from workshop.analysis_views import _insight_vehicles

        brand = CarBrand.objects.create(name='Toyta')
        self._card('KL01AA0001', 'Toyta', 'Corolla')
        self._card('KL02BB0002', 'Toyota', 'Corolla')
        self.assertEqual(len(_insight_vehicles(date.today(), date.today())['brands']), 2)

        self.client.post(reverse('brand_edit', args=[brand.pk]), {'name': 'Toyota'})

        self.assertEqual(
            set(JobCard.objects.values_list('brand_name', flat=True)), {'Toyota'})
        rows = _insight_vehicles(date.today(), date.today())['brands']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['jobs'], 2)

    def test_renaming_a_brand_onto_an_existing_one_merges_and_moves_its_models(self):
        typo = CarBrand.objects.create(name='Toyta')
        keeper = CarBrand.objects.create(name='Toyota')
        CarModel.objects.create(brand=typo, name='Innova')     # unique to the typo
        CarModel.objects.create(brand=typo, name='Corolla')    # duplicate
        CarModel.objects.create(brand=keeper, name='Corolla')
        self._card('KL01AA0001', 'Toyta', 'Innova')

        self.client.post(reverse('brand_edit', args=[typo.pk]), {'name': 'Toyota'})

        self.assertEqual(CarBrand.objects.count(), 1)
        self.assertEqual(
            sorted(keeper.models.values_list('name', flat=True)), ['Corolla', 'Innova'],
            "the unique model moves across; the duplicate is dropped, because "
            "CarModel is unique_together(brand, name)")
        self.assertEqual(JobCard.objects.get().brand_name, 'Toyota')

    def test_renaming_a_model_relabels_only_that_brands_job_cards(self):
        toyota = CarBrand.objects.create(name='Toyota')
        honda = CarBrand.objects.create(name='Honda')
        model = CarModel.objects.create(brand=toyota, name='Corola')
        CarModel.objects.create(brand=honda, name='Corola')
        self._card('KL01AA0001', 'Toyota', 'Corola')
        self._card('KL02BB0002', 'Honda', 'Corola')

        self.client.post(reverse('model_edit', args=[model.pk]),
                         {'brand': toyota.pk, 'name': 'Corolla'})

        self.assertEqual(
            JobCard.objects.get(registration_number='KL01AA0001').model_name, 'Corolla')
        self.assertEqual(
            JobCard.objects.get(registration_number='KL02BB0002').model_name, 'Corola',
            "a same-named model under another make is a different car")

    def test_a_brand_rename_is_logged_when_it_merges(self):
        typo = CarBrand.objects.create(name='Toyta')
        CarBrand.objects.create(name='Toyota')
        self.client.post(reverse('brand_edit', args=[typo.pk]), {'name': 'Toyota'})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_MASTER_DATA)
        self.assertIn('Toyta', log.entity_label)


class TheMasterListDecidesHowItsOwnEntriesAreSpelledTests(WorkshopTestCase):
    """
    `model_name` is free text on the job card and reports group by it, so
    'corolla' and 'COROLLA' were two different models everywhere they were
    counted. It is deliberately NOT title-cased the way `brand_name` is —
    that would turn 'i20' into 'I20' and 'CR-V' into 'Cr-V'. Instead a card
    snaps to the master list's own spelling when that brand already has the
    model recorded; a genuinely new model stays exactly as typed.
    """

    def test_a_known_model_snaps_to_the_master_spelling(self):
        brand = CarBrand.objects.create(name='Toyota')
        CarModel.objects.create(brand=brand, name='Corolla')
        for reg, typed in (('KL01AA0001', 'corolla'), ('KL02BB0002', 'COROLLA')):
            JobCard.objects.create(
                registration_number=reg, brand_name='toyota', model_name=typed,
                admitted_date=date.today(), lead_mechanic=self.mechanic)
        self.assertEqual(
            set(JobCard.objects.values_list('model_name', flat=True)), {'Corolla'})

    def test_an_unknown_model_keeps_the_typed_capitalisation(self):
        CarBrand.objects.create(name='Hyundai')
        jc = JobCard.objects.create(
            registration_number='KL03CC0003', brand_name='Hyundai', model_name='i20 Asta',
            admitted_date=date.today(), lead_mechanic=self.mechanic)
        jc.refresh_from_db()
        self.assertEqual(jc.model_name, 'i20 Asta',
                         "title-casing would have produced 'I20 Asta'")

    def test_surrounding_whitespace_is_collapsed(self):
        jc = JobCard.objects.create(
            registration_number='KL04DD0004', brand_name='Toyota',
            model_name='  Corolla   Altis ',
            admitted_date=date.today(), lead_mechanic=self.mechanic)
        jc.refresh_from_db()
        self.assertEqual(jc.model_name, 'Corolla Altis')


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


class TheSettlementFormWarnsBeforeTheButtonTests(WorkshopTestCase):
    """
    The POST guard refuses a settlement that would strand someone's advances —
    but a page that only reveals that on submit makes the office fill in a whole
    month's leave days first. Retired staff block too and are absent from the
    rows entirely, so the banner is the only place they are visible at all.

    The old copy said such staff "will be left out of this settlement", which
    stopped being true the moment the guard existed.
    """

    def test_the_form_names_who_is_blocking_it(self):
        m = timezone.localdate().replace(day=1)
        Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        bilal = Mechanic.objects.create(name='Bilal')
        SalaryAdvance.objects.create(staff=bilal, amount=Decimal('3000'), date=m)

        resp = self.client.get(reverse('salary_payment_form', args=[m.year, m.month]))

        # No apostrophe in the needle. This banner is literal template text, so
        # it is NOT autoescaped (only variables are) — unlike the messages
        # framework copy, which is. Sidestep the difference entirely.
        self.assertContains(resp, "be saved yet")
        self.assertContains(resp, 'Bilal')
        self.assertContains(resp, 'no salary recorded')

    def test_a_retired_blocker_is_visible_even_though_it_has_no_row(self):
        m = timezone.localdate().replace(day=1)
        Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        bilal = Mechanic.objects.create(name='Bilal', current_salary=Decimal('18000'),
                                        is_active=False)
        SalaryAdvance.objects.create(staff=bilal, amount=Decimal('4000'), date=m)

        resp = self.client.get(reverse('salary_payment_form', args=[m.year, m.month]))

        self.assertContains(resp, 'Bilal')
        self.assertNotIn(bilal.pk, [r['staff'].pk for r in resp.context['rows']])

    def test_someone_with_no_salary_and_no_advance_is_only_a_soft_note(self):
        m = timezone.localdate().replace(day=1)
        Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        Mechanic.objects.create(name='Bilal')

        resp = self.client.get(reverse('salary_payment_form', args=[m.year, m.month]))

        self.assertEqual(resp.context['blockers'], [])
        self.assertContains(resp, 'no salary set')
        self.assertContains(resp, 'nothing is lost')

    def test_a_blocker_is_not_also_listed_as_merely_left_out(self):
        """One person, one consequence."""
        m = timezone.localdate().replace(day=1)
        bilal = Mechanic.objects.create(name='Bilal')
        SalaryAdvance.objects.create(staff=bilal, amount=Decimal('3000'), date=m)
        resp = self.client.get(reverse('salary_payment_form', args=[m.year, m.month]))
        self.assertEqual([b['staff'].pk for b in resp.context['blockers']], [bilal.pk])
        # `self.mechanic` from the base fixture also has no salary, and it
        # legitimately belongs in the soft list — assert on Bilal specifically
        # rather than on the list being empty.
        self.assertNotIn(bilal.pk, [s.pk for s in resp.context['missing_salary']])


class DataCleanupShowsUsageBeforeDeletingTests(WorkshopTestCase):
    """
    The usage count has to be in the MODAL, because that is the only delete path
    a real user takes — Data Cleanup posts straight to the delete view, so the
    view's GET confirmation page is a safety net for a URL typed directly and is
    never reached by the UI. Checking only the server side would have missed
    this entirely.
    """

    def _spare_used_by(self, name, times):
        spare = SparePart.objects.create(name=name)
        jc = JobCard.objects.create(
            registration_number='KL01AA0001', brand_name='Toyota', model_name='Corolla',
            admitted_date=date.today(), lead_mechanic=self.mechanic)
        for _ in range(times):
            JobCardSpareItem.objects.create(
                job_card=jc, spare_part_name=name, quantity=Decimal('1'),
                total_price=Decimal('100'), source=JobCardSpareItem.SOURCE_SHOP)
        return spare

    def test_the_delete_modal_shows_the_usage_count_and_steers_to_merge(self):
        self._spare_used_by('Oil Filter', 3)
        page = self.client.get(reverse('data_cleanup')).content.decode()
        self.assertIn('Used on <strong>3 job cards</strong>', page)
        self.assertIn('to merge it into', page)

    def test_the_delete_modal_posts_a_reason(self):
        spare = self._spare_used_by('Oil Filter', 1)
        page = self.client.get(reverse('data_cleanup')).content.decode()
        self.assertIn('name="reason"', page)

        self.client.post(reverse('cleanup_delete_spare', args=[spare.pk]),
                         {'reason': 'duplicate'})
        self.assertEqual(
            DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_MASTER_DATA).reason,
            'duplicate')

    def test_an_unused_entry_shows_no_warning(self):
        SparePart.objects.create(name='Oil Filter')
        page = self.client.get(reverse('data_cleanup')).content.decode()
        self.assertNotIn('to merge it into', page)


class AMonthKeepsTheSalaryItWasSettledAtTests(WorkshopTestCase):
    """
    This workshop settles a month in the first days of the NEXT one, and
    salaries are revised at that same boundary. The working rule is: settle the
    finished month, THEN apply the raise. `salary_used` is frozen onto the line
    at settle time, so July keeps July's salary and August gets the new one.

    What this pins down is the other half — a month already settled must never
    be repriced by a raise entered afterwards, even when it is re-saved for an
    unrelated reason. There is deliberately no salary field on the settlement
    screen: to settle a month at a different figure, delete the settlement and
    settle again.
    """

    def _last_month(self):
        return (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)

    def _month_end(self, m):
        return (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    def test_settle_then_raise_keeps_the_month_correct(self):
        """The everyday order of work."""
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('3000'), date=last)

        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})
        self.client.post(reverse('salary_set_amount', args=[anil.pk]), {'amount': '25000'})

        line = SalaryPaymentLine.objects.get()
        self.assertEqual(line.salary_used, Decimal('20000.00'))
        self.assertEqual(line.net_amount, Decimal('17000.00'))
        self.assertEqual(
            ae.salary_expense(last, self._month_end(last))['total'], Decimal('20000.00'))

    def test_re_saving_after_a_raise_does_not_reprice_the_month(self):
        """The trap: correcting leave days later must not pull in a salary the
        person was not on at the time."""
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})
        self.client.post(reverse('salary_set_amount', args=[anil.pk]), {'amount': '25000'})

        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '2', 'settlement_unlock': 'true'})

        line = SalaryPaymentLine.objects.get()
        self.assertEqual(line.salary_used, Decimal('20000.00'))
        self.assertEqual(line.leave_days, Decimal('2.0'))

    def test_there_is_no_salary_field_to_edit(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        resp = self.client.get(reverse('salary_payment_form', args=[last.year, last.month]))
        self.assertNotContains(resp, f'name="salary_{anil.pk}"')
        self.assertEqual(resp.context['rows'][0]['salary_used'], Decimal('20000.00'))

    def test_a_posted_salary_field_is_ignored(self):
        """Even a crafted payload cannot set a month's salary."""
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0', f'salary_{anil.pk}': '99000'})
        self.assertEqual(SalaryPaymentLine.objects.get().salary_used, Decimal('20000.00'))

    def test_deleting_and_re_settling_picks_up_the_new_salary(self):
        """The documented way to settle a month at a different figure."""
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})
        self.client.post(reverse('salary_set_amount', args=[anil.pk]), {'amount': '25000'})

        payment = SalaryPayment.objects.get()
        self.client_for('owner').post(reverse('salary_payment_delete', args=[payment.pk]),
                                      {'reason': 'wrong salary'})
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})

        self.assertEqual(SalaryPaymentLine.objects.get().salary_used, Decimal('25000.00'))


class ASettledMonthIsLockedTests(WorkshopTestCase):
    """
    A settled month opens read-only — the same rule the Job Card applies to a
    PAID bill, and enforced the same way. The client-side lock is not the
    guarantee: the view refuses the POST outright, because a lock that only
    disables inputs is bypassed by a raw request.
    """

    def _last_month(self):
        return (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)

    def test_saving_a_settled_month_without_unlocking_is_refused(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})

        resp = self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                                {f'leave_days_{anil.pk}': '5'}, follow=True)

        self.assertEqual(SalaryPaymentLine.objects.get().leave_days, Decimal('0.0'))
        self.assertContains(resp, 'locked')

    def test_unlocking_lets_the_correction_through(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('30000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})

        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '3', 'settlement_unlock': 'true'})

        line = SalaryPaymentLine.objects.get()
        self.assertEqual(line.leave_days, Decimal('3.0'))
        self.assertEqual(line.net_amount, Decimal('27000.00'))

    def test_a_first_settlement_needs_no_unlock(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})
        self.assertEqual(SalaryPaymentLine.objects.count(), 1)

    def test_the_locked_page_uses_readonly_so_the_fields_still_post(self):
        """A disabled input is never submitted, and the settlement loop skips
        any staff member whose leave_days key is missing — disabling would
        silently write no line for anyone."""
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})

        page = self.client.get(
            reverse('salary_payment_form', args=[last.year, last.month])).content.decode()
        self.assertIn('readonly', page)
        self.assertIn(f'name="leave_days_{anil.pk}"', page)


class OnlyTheMostRecentSettlementCanBeChangedTests(WorkshopTestCase):
    """
    Salary is worked out within a week of a month ending and the cash is handed
    over immediately, so once the NEXT month has been settled the previous one
    is history — it must not be one unlock away from changing, for anyone.

    Closure is a stored one-way flag, not a computed "is this the latest?".
    The computed version reopened the previous month whenever the newest
    settlement was deleted, which let the whole history be walked backwards one
    delete at a time — see test_the_history_cannot_be_walked_backwards_by_deleting.

    It is keyed to being superseded rather than to a date, deliberately: a rule
    like "July closes once August opens for settling" would close a month the
    instant it was settled whenever settlement ran late, punishing exactly the
    month that was hardest to get right.
    """

    def _month(self, back):
        m = timezone.localdate().replace(day=1)
        for _ in range(back):
            m = (m - timedelta(days=1)).replace(day=1)
        return m

    def _settle(self, month, staff, **extra):
        data = {f'leave_days_{staff.pk}': '0'}
        data.update(extra)
        return self.client.post(
            reverse('salary_payment_form', args=[month.year, month.month]), data)

    def setUp(self):
        super().setUp()
        self.anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.older = self._month(2)
        self.newer = self._month(1)
        self._settle(self.older, self.anil)
        self._settle(self.newer, self.anil)

    def test_the_older_month_cannot_be_edited(self):
        resp = self.client.post(
            reverse('salary_payment_form', args=[self.older.year, self.older.month]),
            {f'leave_days_{self.anil.pk}': '9', 'settlement_unlock': 'true'}, follow=True)

        line = SalaryPaymentLine.objects.get(payment__month=self.older)
        self.assertEqual(line.leave_days, Decimal('0.0'))
        self.assertContains(resp, 'closed')

    def test_the_older_month_cannot_be_deleted(self):
        payment = SalaryPayment.objects.get(month=self.older)
        self.client_for('owner').post(
            reverse('salary_payment_delete', args=[payment.pk]), {'reason': 'nope'})
        self.assertTrue(SalaryPayment.objects.filter(pk=payment.pk).exists())

    def test_even_the_confirmation_page_will_not_render_for_a_closed_month(self):
        payment = SalaryPayment.objects.get(month=self.older)
        resp = self.client_for('owner').get(
            reverse('salary_payment_delete', args=[payment.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_an_owner_gets_no_exception(self):
        """Fully closed means closed for everyone."""
        resp = self.client_for('owner').post(
            reverse('salary_payment_form', args=[self.older.year, self.older.month]),
            {f'leave_days_{self.anil.pk}': '9', 'settlement_unlock': 'true'}, follow=True)
        self.assertEqual(
            SalaryPaymentLine.objects.get(payment__month=self.older).leave_days,
            Decimal('0.0'))

    def test_the_latest_month_is_still_editable(self):
        self.client.post(
            reverse('salary_payment_form', args=[self.newer.year, self.newer.month]),
            {f'leave_days_{self.anil.pk}': '3', 'settlement_unlock': 'true'})
        self.assertEqual(
            SalaryPaymentLine.objects.get(payment__month=self.newer).leave_days,
            Decimal('3.0'))

    def test_the_latest_month_is_still_deletable(self):
        payment = SalaryPayment.objects.get(month=self.newer)
        self.client_for('owner').post(
            reverse('salary_payment_delete', args=[payment.pk]), {'reason': 'mistake'})
        self.assertFalse(SalaryPayment.objects.filter(pk=payment.pk).exists())

    def test_deleting_the_newest_does_NOT_reopen_the_one_before_it(self):
        """
        Closure is a one-way door.

        This test originally asserted the opposite — that deleting the newest
        settlement handed the frontier back to the month before it, which read
        like a tidy reversal. On live data it turned out to be a ratchet that
        turns both ways: delete the newest, the previous becomes editable,
        delete that, and you can walk backwards through the whole history one
        delete at a time. Thirteen settled months went to ten that way.
        """
        payment = SalaryPayment.objects.get(month=self.newer)
        self.client_for('owner').post(
            reverse('salary_payment_delete', args=[payment.pk]), {'reason': 'mistake'})

        self.client.post(
            reverse('salary_payment_form', args=[self.older.year, self.older.month]),
            {f'leave_days_{self.anil.pk}': '4', 'settlement_unlock': 'true'})

        self.assertEqual(
            SalaryPaymentLine.objects.get(payment__month=self.older).leave_days,
            Decimal('0.0'), "a closed month must stay closed")

    def test_the_history_cannot_be_walked_backwards_by_deleting(self):
        """The whole point: repeated deletes must not unlock month after month."""
        third = self._month(3)
        SalaryPayment.objects.all().delete()
        for m in (third, self.older, self.newer):
            self._settle(m, self.anil)

        owner = self.client_for('owner')
        for m in (self.newer, self.older):
            payment = SalaryPayment.objects.filter(month=m).first()
            if payment:
                owner.post(reverse('salary_payment_delete', args=[payment.pk]),
                           {'reason': 'walking back'})

        # `third` was superseded the moment a later month was settled and must
        # still be refused, however many newer settlements have been removed.
        self.assertTrue(SalaryPayment.objects.get(month=third).superseded)
        self.client.post(
            reverse('salary_payment_form', args=[third.year, third.month]),
            {f'leave_days_{self.anil.pk}': '7', 'settlement_unlock': 'true'})
        self.assertEqual(
            SalaryPaymentLine.objects.get(payment__month=third).leave_days,
            Decimal('0.0'))

        payment = SalaryPayment.objects.get(month=third)
        owner.post(reverse('salary_payment_delete', args=[payment.pk]), {'reason': 'nope'})
        self.assertTrue(SalaryPayment.objects.filter(pk=payment.pk).exists(),
                        "a closed month cannot be deleted either")

    def test_a_month_never_superseded_is_not_closed(self):
        """
        The only settlement ever made stays open.

        This used to delete the newer settlement and expect the older one to
        reopen — which is precisely the ratchet the one-way door removes. What
        it was really checking is that closure requires a LATER month to have
        been settled at some point, so it now starts from a clean slate.
        """
        SalaryPayment.objects.all().delete()
        self._settle(self.older, self.anil)
        resp = self.client.get(
            reverse('salary_payment_form', args=[self.older.year, self.older.month]))
        self.assertFalse(resp.context['is_closed'])
        self.assertContains(resp, 'Edit this settlement')

    def test_a_closed_month_offers_no_menu(self):
        resp = self.client_for('owner').get(
            reverse('salary_payment_form', args=[self.older.year, self.older.month]))
        self.assertTrue(resp.context['is_closed'])
        self.assertNotContains(resp, 'Edit this settlement')
        self.assertNotContains(resp, 'Delete Settlement')

    def test_the_open_month_still_offers_its_menu(self):
        resp = self.client_for('owner').get(
            reverse('salary_payment_form', args=[self.newer.year, self.newer.month]))
        self.assertFalse(resp.context['is_closed'])
        self.assertContains(resp, 'Edit this settlement')
        self.assertContains(resp, 'Delete Settlement')

    def test_settling_a_newer_month_closes_the_previous_one(self):
        """Nothing closes until you actually move on."""
        SalaryPayment.objects.all().delete()
        self._settle(self.older, self.anil)
        resp = self.client.get(
            reverse('salary_payment_form', args=[self.older.year, self.older.month]))
        self.assertFalse(resp.context['is_closed'])

        self._settle(self.newer, self.anil)
        resp = self.client.get(
            reverse('salary_payment_form', args=[self.older.year, self.older.month]))
        self.assertTrue(resp.context['is_closed'])


class AnAdvanceCannotEnterASettledMonthTests(WorkshopTestCase):
    """
    Once a month is settled the money has been worked out and paid, so an
    advance dated inside it would leave the saved net silently wrong.

    Blocked rather than detected. A "needs re-settling" flag used to catch this
    afterwards, but it nagged from another screen days later and, by existing,
    invited people back into reopening a closed month. Refusing it puts the
    guidance at the moment of the mistake — and the guidance differs by ROLE,
    because deleting a settlement is Owner-only and telling Office to do it
    would point them at a button they cannot see.
    """

    def _last_month(self):
        return (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)

    def _settle(self, month, staff):
        self.client.post(reverse('salary_payment_form', args=[month.year, month.month]),
                         {f'leave_days_{staff.pk}': '0'})

    def test_an_advance_into_a_settled_month_is_refused(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self._settle(last, anil)

        self.client.post(reverse('salary_advance_add'), {
            'staff_id': anil.pk, 'amount': '5000',
            'date': str(last + timedelta(days=10))})

        self.assertEqual(SalaryAdvance.objects.count(), 0)

    def test_office_is_told_to_ask_an_owner(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self._settle(last, anil)

        resp = self.client.post(reverse('salary_advance_add'), {
            'staff_id': anil.pk, 'amount': '5000',
            'date': str(last + timedelta(days=10))}, follow=True)

        self.assertContains(resp, 'Ask an owner')

    def test_an_owner_is_told_to_delete_it_themselves(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self._settle(last, anil)

        resp = self.client_for('owner').post(reverse('salary_advance_add'), {
            'staff_id': anil.pk, 'amount': '5000',
            'date': str(last + timedelta(days=10))}, follow=True)

        self.assertContains(resp, 'Delete that settlement')

    def test_an_advance_into_an_unsettled_month_is_still_fine(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_advance_add'), {
            'staff_id': anil.pk, 'amount': '5000',
            'date': str(last + timedelta(days=10))})
        self.assertEqual(SalaryAdvance.objects.count(), 1)

    def test_the_documented_route_works_end_to_end(self):
        """Delete the settlement, record the advance, settle again."""
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self._settle(last, anil)

        payment = SalaryPayment.objects.get()
        self.client_for('owner').post(reverse('salary_payment_delete', args=[payment.pk]),
                                      {'reason': 'forgotten advance'})
        self.client.post(reverse('salary_advance_add'), {
            'staff_id': anil.pk, 'amount': '5000',
            'date': str(last + timedelta(days=10)), 'note': 'forgotten'})
        self._settle(last, anil)

        line = SalaryPaymentLine.objects.get()
        self.assertEqual(line.advance_used, Decimal('5000.00'))
        self.assertEqual(line.net_amount, Decimal('15000.00'))

    def test_a_late_discovery_can_go_in_the_current_month(self):
        """The other route the message offers: keep the closed month closed."""
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self._settle(last, anil)

        self.client.post(reverse('salary_advance_add'), {
            'staff_id': anil.pk, 'amount': '5000',
            'date': str(timezone.localdate()), 'note': 'was from last month'})

        advance = SalaryAdvance.objects.get()
        self.assertEqual(advance.date, timezone.localdate())
        self.assertEqual(SalaryPaymentLine.objects.get().advance_used, Decimal('0'))


class OvertimeIsAddedToThePayTests(WorkshopTestCase):
    """
    A few staff have overtime in a given month, entered as one amount at
    settlement. It is added to the net, so the wage cost the Profit page reads
    (net + advance) includes it with no further arithmetic.
    """

    def _last_month(self):
        return (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)

    def _month_end(self, m):
        return (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    def test_overtime_increases_the_net(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0', f'overtime_{anil.pk}': '2500'})

        line = SalaryPaymentLine.objects.get()
        self.assertEqual(line.overtime_amount, Decimal('2500.00'))
        self.assertEqual(line.net_amount, Decimal('22500.00'))

    def test_overtime_reaches_the_profit_page(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('3000'), date=last)
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0', f'overtime_{anil.pk}': '2500'})

        self.assertEqual(
            ae.salary_expense(last, self._month_end(last))['total'], Decimal('22500.00'),
            "salary + overtime, with the advance counted exactly once")

    def test_overtime_combines_with_leave_and_advance(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('30000'))
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('4000'), date=last)
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '2', f'overtime_{anil.pk}': '1500'})

        # 30000 - (30000/30 * 2) + 1500 - 4000
        self.assertEqual(SalaryPaymentLine.objects.get().net_amount, Decimal('25500.00'))

    def test_no_overtime_is_the_normal_case(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                         {f'leave_days_{anil.pk}': '0'})
        line = SalaryPaymentLine.objects.get()
        self.assertEqual(line.overtime_amount, Decimal('0'))
        self.assertEqual(line.net_amount, Decimal('20000.00'))

    def test_a_junk_overtime_value_is_treated_as_none(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        for bad in ('abc', '-500', 'Infinity', '999999999999'):
            SalaryPayment.objects.all().delete()
            self.client.post(reverse('salary_payment_form', args=[last.year, last.month]),
                             {f'leave_days_{anil.pk}': '0', f'overtime_{anil.pk}': bad})
            self.assertEqual(SalaryPaymentLine.objects.get().overtime_amount, Decimal('0'),
                             f"overtime={bad!r}")

    def test_the_form_offers_an_overtime_box(self):
        last = self._last_month()
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        resp = self.client.get(reverse('salary_payment_form', args=[last.year, last.month]))
        self.assertContains(resp, f'name="overtime_{anil.pk}"')


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


class RetiringStaffWarnsAboutTheirUnsettledAdvancesTests(WorkshopTestCase):
    """
    Control Hub retires someone; Salary & Advance is where it bites.

    Retiring a staff member who still holds advances in an unsettled month is
    legitimate, but the settle-guard will then refuse that month until they are
    reactivated. Without a word at the moment of the click, the owner got a tick
    and Office hit a wall days later in a different section with nothing
    connecting the two.
    """

    def test_retiring_someone_with_unsettled_advances_says_so(self):
        m = timezone.localdate().replace(day=1)
        bilal = Mechanic.objects.create(name='Bilal', current_salary=Decimal('18000'))
        SalaryAdvance.objects.create(staff=bilal, amount=Decimal('4000'), date=m)

        resp = self.client_for('owner').post(
            reverse('manage_toggle_mechanic', args=[bilal.pk]), follow=True)

        self.assertContains(resp, 'advances in a month')
        bilal.refresh_from_db()
        self.assertFalse(bilal.is_active, "the warning must not block the action")

    def test_retiring_someone_with_nothing_outstanding_is_quiet(self):
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        resp = self.client_for('owner').post(
            reverse('manage_toggle_mechanic', args=[anil.pk]), follow=True)
        self.assertNotContains(resp, 'advances in a month')

    def test_an_advance_inside_a_settled_month_raises_no_warning(self):
        """It is already on a payment line, so retiring them changes nothing."""
        m = timezone.localdate().replace(day=1)
        bilal = Mechanic.objects.create(name='Bilal', current_salary=Decimal('18000'))
        SalaryAdvance.objects.create(staff=bilal, amount=Decimal('4000'), date=m)
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{bilal.pk}': '0'})

        resp = self.client_for('owner').post(
            reverse('manage_toggle_mechanic', args=[bilal.pk]), follow=True)
        self.assertNotContains(resp, 'advances in a month')


class DeletingASettlementReturnsItsAdvancesToTheBooksTests(WorkshopTestCase):
    """Un-recording a month must put its advances back as loose cash — that
    money left the drawer whether or not the month is settled."""

    def test_the_wage_cost_falls_back_to_the_advances_alone(self):
        m = timezone.localdate().replace(day=1)
        end = (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        SalaryAdvance.objects.create(staff=anil, amount=Decimal('2000'), date=m)
        self.client.post(reverse('salary_payment_form', args=[m.year, m.month]),
                         {f'leave_days_{anil.pk}': '0'})
        self.assertEqual(ae.salary_expense(m, end)['total'], Decimal('20000.00'))

        payment = SalaryPayment.objects.get()
        self.client_for('owner').post(reverse('salary_payment_delete', args=[payment.pk]),
                                      {'reason': 'settled by mistake'})

        self.assertEqual(ae.salary_expense(m, end)['total'], Decimal('2000.00'))
        self.assertTrue(
            DeletionLog.objects.filter(entity_type=DeletionLog.ENTITY_SALARY_PAYMENT).exists())


class SalaryAmountsAreBoundedByTheirColumnTests(WorkshopTestCase):
    """
    A rupee figure too large for its column behaved differently on each
    database: SQLite stored it (silently violating the declared precision) while
    PostgreSQL — what actually ships — raises `numeric field overflow` and 500s.
    Neither is an answer to a typo. Bounds are read from `max_digits` /
    `decimal_places` so they cannot drift from the schema.
    """

    def test_an_oversized_advance_is_refused(self):
        staff = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_advance_add'),
                         {'staff_id': staff.pk, 'amount': '999999999999'})
        self.assertEqual(SalaryAdvance.objects.count(), 0)

    def test_an_oversized_salary_is_refused(self):
        staff = Mechanic.objects.create(name='Anil')
        self.client.post(reverse('salary_set_amount', args=[staff.pk]),
                         {'amount': '999999999999'})
        staff.refresh_from_db()
        self.assertIsNone(staff.current_salary)

    def test_nan_and_infinity_are_refused(self):
        staff = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        for bad in ('NaN', 'Infinity', '-Infinity'):
            self.client.post(reverse('salary_advance_add'),
                             {'staff_id': staff.pk, 'amount': bad})
        self.assertEqual(SalaryAdvance.objects.count(), 0,
                         "'NaN' parses as a Decimal and would poison every SUM")

    def test_an_ordinary_amount_still_works(self):
        staff = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_advance_add'),
                         {'staff_id': staff.pk, 'amount': '2500.50'})
        self.assertEqual(SalaryAdvance.objects.get().amount, Decimal('2500.50'))

    def test_an_overlong_note_is_trimmed_not_crashed(self):
        """400 chars into max_length=255: stored by SQLite, rejected by
        Postgres with 'value too long'. Trimmed, like an oversized spare name."""
        staff = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_advance_add'),
                         {'staff_id': staff.pk, 'amount': '500', 'note': 'x' * 400})
        self.assertEqual(len(SalaryAdvance.objects.get().note), 255)

    def test_a_garbled_staff_id_is_a_404_not_a_500(self):
        """get_object_or_404(pk='abc') raises ValueError, not Http404."""
        resp = self.client.post(reverse('salary_advance_add'),
                                {'staff_id': 'abc', 'amount': '500'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(SalaryAdvance.objects.count(), 0)

    def test_settling_the_same_month_twice_writes_one_settlement(self):
        """SalaryPayment.month is unique — a double-click must not 500."""
        m = timezone.localdate().replace(day=1)
        anil = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        url = reverse('salary_payment_form', args=[m.year, m.month])
        self.client.post(url, {f'leave_days_{anil.pk}': '2'})
        self.client.post(url, {f'leave_days_{anil.pk}': '2'})
        self.assertEqual(SalaryPayment.objects.count(), 1)
        self.assertEqual(SalaryPaymentLine.objects.count(), 1)

    def test_a_retired_staff_member_can_still_be_given_a_salary(self):
        """Not a loophole — it is the documented way out of a blocked month.
        The settle-guard tells the owner to 'set a salary or reactivate them'."""
        staff = Mechanic.objects.create(name='Anil', is_active=False)
        self.client.post(reverse('salary_set_amount', args=[staff.pk]), {'amount': '20000'})
        staff.refresh_from_db()
        self.assertEqual(staff.current_salary, Decimal('20000.00'))

    def test_a_future_dated_advance_is_refused(self):
        staff = Mechanic.objects.create(name='Anil', current_salary=Decimal('20000'))
        self.client.post(reverse('salary_advance_add'), {
            'staff_id': staff.pk, 'amount': '1000',
            'date': str(timezone.localdate() + timedelta(days=400))})
        self.assertEqual(SalaryAdvance.objects.count(), 0,
                         "cash cannot have been handed over on a day that hasn't come")


class BrandAndModelDeletesAreDisclosedAndLoggedTests(WorkshopTestCase):
    """
    Deleting a brand takes every model under it by CASCADE — the largest
    permanent delete in the app, and it left no record of what went. The confirm
    page said only "this will also delete all car models", never how many or
    which.
    """

    def test_the_confirm_page_names_the_models_that_will_go(self):
        brand = CarBrand.objects.create(name='Toyota')
        for n in ('Corolla', 'Camry'):
            CarModel.objects.create(brand=brand, name=n)
        resp = self.client.get(reverse('brand_delete', args=[brand.pk]))
        self.assertContains(resp, 'Corolla')
        self.assertContains(resp, 'Camry')
        self.assertContains(resp, '2 car models')
        self.assertEqual(CarModel.objects.count(), 2, "GET must delete nothing")

    def test_deleting_a_brand_is_logged_with_its_models(self):
        brand = CarBrand.objects.create(name='Toyota')
        CarModel.objects.create(brand=brand, name='Corolla')
        self.client.post(reverse('brand_delete', args=[brand.pk]), {'reason': 'never serviced'})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_MASTER_DATA)
        self.assertIn('Toyota', log.entity_label)
        self.assertEqual(log.snapshot.get('models_deleted'), ['Corolla'])
        self.assertEqual(log.reason, 'never serviced')

    def test_deleting_a_single_model_is_logged(self):
        brand = CarBrand.objects.create(name='Toyota')
        model = CarModel.objects.create(brand=brand, name='Corolla')
        self.client.post(reverse('model_delete', args=[model.pk]))
        self.assertTrue(
            DeletionLog.objects.filter(entity_type=DeletionLog.ENTITY_MASTER_DATA).exists())


class CreatingALoginIsAllOrNothingTests(WorkshopTestCase):
    """
    `create_user()` ran before `Group.objects.get(name=role)`, so a missing group
    row 500'd the panel having already created the account. That login had no
    group at all: invisible in this hub (which lists strictly by group), able to
    sign in, and then 403'd by every RBAC decorator — a ghost nobody could see
    to delete.
    """

    def test_a_missing_role_creates_no_account_and_does_not_crash(self):
        Group.objects.filter(name='Floor').delete()
        resp = self.client_for('owner').post(
            reverse('manage_create_user'),
            {'username': 'newfloor', 'password': 'password123', 'role': 'Floor'}, follow=True)
        self.assertFalse(User.objects.filter(username='newfloor').exists())
        self.assertContains(resp, 'missing from this database')

    def test_every_created_account_has_its_group(self):
        self.client_for('owner').post(
            reverse('manage_create_user'),
            {'username': 'reception', 'password': 'password123', 'role': 'Office'})
        user = User.objects.get(username='reception')
        self.assertEqual(list(user.groups.values_list('name', flat=True)), ['Office'])


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
