"""
The Unassigned Spares Hub — who may do what, and what the rules refuse.

The section was opened to Floor on 2026-08-16: a mechanic takes delivery of the
part, so letting them record it is what keeps the shop ledger same-day. Floor is
add-only and is shown no cost anywhere, and BOTH halves of that are asserted
here — the page not rendering a price, and the server not writing one when a
crafted POST supplies it. The second half is the one that matters; the first is
only presentation, and this is the exact shape of AUD-0081.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.models import DeletionLog, JobCardSpareItem, SpareShop


class HubBase(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user('hub_owner', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.office = User.objects.create_user('hub_office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.floor = User.objects.create_user('hub_floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))

        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.other = SpareShop.objects.create(name='Kochi Spares')
        self.client = Client()

    def as_(self, user):
        self.client.force_login(user)
        return self.client

    def row(self, **over):
        kw = dict(job_card=None, shop=self.shop,
                  source=JobCardSpareItem.SOURCE_SHOP,
                  spare_part_name='Brake Pad', quantity=D('2'),
                  unit_price=D('1000'), status='RECEIVED',
                  ordered_date=date(2026, 8, 1), received_date=date(2026, 8, 2))
        kw.update(over)
        return JobCardSpareItem.objects.create(**kw)

    def add_payload(self, **over):
        d = {'shop': str(self.shop.pk), 'spare_part_name': 'Oil Filter',
             'quantity': '2'}
        d.update(over)
        return d

    def edit_payload(self, **over):
        d = {'shop': str(self.shop.pk), 'spare_part_name': 'Brake Pad',
             'quantity': '2', 'unit_price': '1000',
             'ordered_date': '2026-08-01', 'received_date': '2026-08-02'}
        d.update(over)
        return d

    def owed(self, shop=None):
        shop = shop or self.shop
        shop.refresh_from_db()
        return shop.get_pending_balance


class FloorMayRecordAPurchaseButNeverAPriceTests(HubBase):
    """
    The whole point of opening this page to Floor: the person who receives the
    part records it, and the office prices it when the shop's bill arrives.
    """

    def test_floor_can_open_the_hub(self):
        self.assertEqual(self.as_(self.floor).get(reverse('unassigned_spares_hub')).status_code, 200)

    def test_floor_can_add_one(self):
        self.as_(self.floor).post(reverse('unassigned_spare_add'), self.add_payload())
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertEqual(row.spare_part_name, 'Oil Filter')
        self.assertEqual(row.shop_id, self.shop.pk)
        self.assertEqual(row.source, JobCardSpareItem.SOURCE_SHOP)

    def test_a_floor_added_row_is_UNPRICED_not_free(self):
        """
        NULL, never 0. Zero says the shop gave it away and would settle the
        ledger at a figure nobody agreed; NULL says nobody has priced it yet,
        which is the truth until the bill is keyed.
        """
        self.as_(self.floor).post(reverse('unassigned_spare_add'), self.add_payload())
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertIsNone(row.unit_price)
        self.assertEqual(self.owed(), D('0.00'))

    def test_a_crafted_price_from_floor_is_ignored(self):
        """
        The page renders no price box, so this can only arrive by hand — which
        is exactly why the strip lives in the view and not in the template.
        """
        self.as_(self.floor).post(reverse('unassigned_spare_add'),
                                  self.add_payload(unit_price='9999'))
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertIsNone(row.unit_price)
        self.assertEqual(self.owed(), D('0.00'))

    def test_office_priced_the_same_way_still_stores_the_price(self):
        self.as_(self.office).post(reverse('unassigned_spare_add'),
                                   self.add_payload(unit_price='9999'))
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertEqual(row.unit_price, D('9999.00'))

    def test_floor_is_shown_no_price_anywhere_on_the_page(self):
        self.row(unit_price=D('7777'))
        page = self.as_(self.floor).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertNotIn('7777', page)
        self.assertNotIn('Shop Price', page)
        self.assertNotIn('name="unit_price"', page)

    def test_office_is_shown_the_price(self):
        self.row(unit_price=D('7777'))
        page = self.as_(self.office).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertIn('7777', page)
        self.assertIn('Shop Price', page)

    def test_an_unpriced_row_reads_as_unpriced_not_as_zero(self):
        self.row(unit_price=None)
        page = self.as_(self.office).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertIn('Not priced', page)
        self.assertNotIn('₹0', page)


class FloorCannotChangeWhatIsAlreadyThereTests(HubBase):
    """Add-only. Both are refused by the decorator, not by hiding a button."""

    def test_floor_cannot_edit(self):
        row = self.row()
        resp = self.as_(self.floor).post(reverse('unassigned_spare_edit', args=[row.pk]),
                                         self.edit_payload(spare_part_name='Hijacked'))
        self.assertEqual(resp.status_code, 403)
        row.refresh_from_db()
        self.assertEqual(row.spare_part_name, 'Brake Pad')

    def test_floor_cannot_delete(self):
        row = self.row()
        resp = self.as_(self.floor).post(
            reverse('spare_shop_delete_unassigned', args=[row.pk]), {'reason': 'x'})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(JobCardSpareItem.objects.filter(pk=row.pk).exists())

    #: The rendered BUTTON, not the bare class name — the stylesheet names both
    #: classes and is served to everyone, so a bare-name search would pass for
    #: the wrong reason.
    EDIT_BTN = 'class="ua-act ua-act-edit"'
    DELETE_BTN = 'class="ua-act ua-act-del"'

    def test_floor_sees_neither_control(self):
        self.row()
        page = self.as_(self.floor).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertNotIn(self.EDIT_BTN, page)
        self.assertNotIn(self.DELETE_BTN, page)

    def test_office_sees_both(self):
        self.row()
        page = self.as_(self.office).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertIn(self.EDIT_BTN, page)
        self.assertIn(self.DELETE_BTN, page)

    def test_floor_is_not_even_sent_the_script_behind_them(self):
        """
        The buttons are gone and so is the code that would drive them — there is
        nothing for a console to poke at. Presentation either way; the refusals
        above are the control.
        """
        self.row()
        page = self.as_(self.floor).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertNotIn('/edit/', page)
        self.assertNotIn('uaEditForm', page)

    def test_floor_is_not_offered_the_shop_ledgers(self):
        """
        The Hub is Floor's only door into Spare Shops. A link to a page they
        would be 403'd on is worse than no link.
        """
        self.row()
        page = self.as_(self.floor).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertNotIn('href="%s"' % reverse('spare_shop_list'), page)
        self.assertNotIn('href="%s"' % reverse('spare_shop_detail', args=[self.shop.pk]), page)

    def test_office_is_offered_them(self):
        self.row()
        page = self.as_(self.office).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertIn('href="%s"' % reverse('spare_shop_list'), page)
        self.assertIn('href="%s"' % reverse('spare_shop_detail', args=[self.shop.pk]), page)


class AnArchivedShopKeepsItsUnassignedPurchasesTests(HubBase):
    """
    Archiving hides a shop from the pickers. It must not hide what is owed to
    it, and correcting a typo on one of its rows must not walk that debt over to
    another shop — the same rule `_resolvable_shops()` enforces on the job card.
    """

    def archive(self):
        self.shop.is_trashed = True
        self.shop.save(update_fields=['is_trashed'])

    def test_its_rows_are_still_listed(self):
        self.row()
        self.archive()
        page = self.as_(self.office).get(reverse('unassigned_spares_hub')).content.decode()
        self.assertIn('Brake Pad', page)
        self.assertIn('Archived', page)

    def test_a_row_can_be_edited_and_stays_on_its_own_archived_shop(self):
        row = self.row()
        self.archive()
        self.as_(self.office).post(reverse('unassigned_spare_edit', args=[row.pk]),
                                   self.edit_payload(spare_part_name='Brake Pad Set'))
        row.refresh_from_db()
        self.assertEqual(row.shop_id, self.shop.pk)
        self.assertEqual(row.spare_part_name, 'Brake Pad Set')

    def test_a_row_cannot_be_moved_TO_an_archived_shop(self):
        row = self.row(shop=self.other)
        self.archive()
        self.as_(self.office).post(reverse('unassigned_spare_edit', args=[row.pk]),
                                   self.edit_payload(shop=str(self.shop.pk)))
        row.refresh_from_db()
        self.assertEqual(row.shop_id, self.other.pk)

    def test_an_archived_shop_takes_no_NEW_purchase(self):
        self.archive()
        self.as_(self.office).post(reverse('unassigned_spare_add'), self.add_payload())
        self.assertEqual(JobCardSpareItem.objects.filter(job_card__isnull=True).count(), 0)


class EditingAnUnassignedSpareIsValidatedTests(HubBase):
    """
    An edit can reach every bad state a create can, and this row is money — so
    every rule `_build_unassigned_spare` applies is applied again on the way in.
    """

    def refused(self, **over):
        row = self.row()
        self.as_(self.office).post(reverse('unassigned_spare_edit', args=[row.pk]),
                                   self.edit_payload(**over))
        row.refresh_from_db()
        return row

    def test_a_negative_price_is_refused(self):
        self.assertEqual(self.refused(unit_price='-5000').unit_price, D('1000.00'))

    def test_an_oversized_price_is_refused(self):
        self.assertEqual(self.refused(unit_price='99999999999').unit_price, D('1000.00'))

    def test_a_zero_quantity_is_refused(self):
        self.assertEqual(self.refused(quantity='0').quantity, D('2.00'))

    def test_a_negative_quantity_is_refused(self):
        self.assertEqual(self.refused(quantity='-3').quantity, D('2.00'))

    def test_an_oversized_quantity_is_refused(self):
        self.assertEqual(self.refused(quantity='9999999').quantity, D('2.00'))

    def test_junk_numbers_are_refused(self):
        self.assertEqual(self.refused(unit_price='abc').unit_price, D('1000.00'))

    def test_a_blank_name_is_refused(self):
        self.assertEqual(self.refused(spare_part_name='   ').spare_part_name, 'Brake Pad')

    def test_a_missing_shop_is_refused(self):
        self.assertEqual(self.refused(shop='').shop_id, self.shop.pk)

    def test_a_blank_price_clears_it_back_to_unpriced(self):
        """Not to zero — see the Floor rule above; the two mean different things."""
        self.assertIsNone(self.refused(unit_price='').unit_price)

    def test_a_spare_already_on_a_car_cannot_be_edited_here(self):
        """
        Scoped to `job_card__isnull=True`: a fitted part is corrected from the
        car's own Spare Parts section, so every row has exactly one screen that
        owns it.
        """
        from workshop.models import JobCard
        jc = JobCard.objects.create(registration_number='KL01AA1111',
                                    admitted_date=date(2026, 8, 1))
        fitted = self.row(job_card=jc)
        resp = self.as_(self.office).post(
            reverse('unassigned_spare_edit', args=[fitted.pk]),
            self.edit_payload(spare_part_name='Hijacked'))
        self.assertEqual(resp.status_code, 404)

    def test_moving_it_refreshes_BOTH_shop_ledgers(self):
        row = self.row()
        self.assertEqual(self.owed(), D('1000.00'))
        self.as_(self.office).post(reverse('unassigned_spare_edit', args=[row.pk]),
                                   self.edit_payload(shop=str(self.other.pk)))
        self.assertEqual(self.owed(), D('0.00'))
        self.assertEqual(self.owed(self.other), D('1000.00'))


class TheTwoDatesAreCheckedAsAPairTests(HubBase):
    """
    Unparseable is refused rather than quietly stamped with today — writing a
    date nobody chose onto a supplier's ledger is worse than refusing the POST.
    """

    def test_a_future_date_is_refused_on_add(self):
        soon = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.as_(self.office).post(reverse('unassigned_spare_add'),
                                   self.add_payload(received_date=soon))
        self.assertEqual(JobCardSpareItem.objects.filter(job_card__isnull=True).count(), 0)

    def test_received_before_ordered_is_refused(self):
        self.as_(self.office).post(reverse('unassigned_spare_add'),
                                   self.add_payload(ordered_date='2026-08-10',
                                                    received_date='2026-08-01'))
        self.assertEqual(JobCardSpareItem.objects.filter(job_card__isnull=True).count(), 0)

    def test_an_unparseable_date_is_refused_never_replaced_with_today(self):
        self.as_(self.office).post(reverse('unassigned_spare_add'),
                                   self.add_payload(ordered_date='not-a-date'))
        self.assertEqual(JobCardSpareItem.objects.filter(job_card__isnull=True).count(), 0)

    def test_blank_dates_on_ADD_mean_today(self):
        """The boxes arrive pre-filled with today, so empty means "the usual"."""
        self.as_(self.office).post(reverse('unassigned_spare_add'),
                                   self.add_payload(ordered_date='', received_date=''))
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertEqual(row.ordered_date, timezone.localdate())
        self.assertEqual(row.received_date, timezone.localdate())

    def test_blank_dates_on_EDIT_mean_cleared(self):
        """There, emptying a box is a deliberate act and has to stick."""
        row = self.row()
        self.as_(self.office).post(reverse('unassigned_spare_edit', args=[row.pk]),
                                   self.edit_payload(ordered_date='', received_date=''))
        row.refresh_from_db()
        self.assertIsNone(row.ordered_date)
        self.assertIsNone(row.received_date)

    def test_good_dates_are_stored_as_typed(self):
        self.as_(self.office).post(reverse('unassigned_spare_add'),
                                   self.add_payload(ordered_date='2026-08-10',
                                                    received_date='2026-08-12'))
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertEqual(row.ordered_date, date(2026, 8, 10))
        self.assertEqual(row.received_date, date(2026, 8, 12))


class DeletingAnUnassignedSpareIsLoggedTests(HubBase):
    def test_it_is_removed_logged_and_taken_off_the_ledger(self):
        row = self.row()
        self.assertEqual(self.owed(), D('1000.00'))
        self.as_(self.office).post(
            reverse('spare_shop_delete_unassigned', args=[row.pk]),
            {'reason': 'entered twice'})
        self.assertFalse(JobCardSpareItem.objects.filter(pk=row.pk).exists())
        self.assertEqual(self.owed(), D('0.00'))
        self.assertTrue(
            DeletionLog.objects.filter(entity_type=DeletionLog.ENTITY_UNASSIGNED_SPARE).exists())


class TheShopLedgerPriceEditIsBoundedToTests(HubBase):
    """
    AUD-0095. `spare_shop_update_item_price` was the last door into these rows
    that did not go through the shared bounds — it took a negative price, a zero
    quantity and a figure past `max_digits` alike. The last of those is the one
    that bites: it is written, and then every later read of that shop's ledger
    raises `InvalidOperation` while aggregating it, leaving the shop's page
    permanently un-openable.
    """

    def edit(self, row, **over):
        data = {'unit_price': '1500', 'quantity': '3'}
        data.update(over)
        self.as_(self.office).post(
            reverse('spare_shop_update_item_price', args=[row.pk]), data)
        row.refresh_from_db()
        return row

    def test_a_good_correction_is_applied(self):
        row = self.edit(self.row())
        self.assertEqual(row.unit_price, D('1500.00'))
        self.assertEqual(row.quantity, D('3.00'))

    def test_a_negative_price_is_refused(self):
        self.assertEqual(self.edit(self.row(), unit_price='-5000').unit_price, D('1000.00'))

    def test_an_oversized_price_is_refused(self):
        self.assertEqual(self.edit(self.row(), unit_price='99999999999').unit_price,
                         D('1000.00'))

    def test_a_zero_quantity_is_refused(self):
        self.assertEqual(self.edit(self.row(), quantity='0').quantity, D('2.00'))

    def test_junk_is_refused(self):
        row = self.edit(self.row(), unit_price='abc')
        self.assertEqual(row.unit_price, D('1000.00'))
        self.assertEqual(row.quantity, D('2.00'))

    def test_a_blank_field_leaves_that_value_alone(self):
        """This form posts only the figure being corrected."""
        row = self.edit(self.row(), unit_price='')
        self.assertEqual(row.unit_price, D('1000.00'))
        self.assertEqual(row.quantity, D('3.00'))

    def test_the_ledger_stays_readable_after_a_refused_edit(self):
        """
        The point of the bound: an oversized write is what makes the shop's own
        page raise while it aggregates.
        """
        row = self.row()
        self.edit(row, unit_price='99999999999')
        self.assertEqual(self.owed(), D('1000.00'))
        self.assertEqual(
            self.as_(self.office).get(
                reverse('spare_shop_detail', args=[self.shop.pk])).status_code, 200)
