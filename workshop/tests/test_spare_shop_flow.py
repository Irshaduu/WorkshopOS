"""
The Spare Shop route end to end: job-card spares, the shop ledger, unassigned
spares and payments.

Two defects found by audit on 2026-07-31 and fixed here:

  * archiving a shop, then editing ANY job card holding one of its spares,
    silently detached that spare and erased the debt from the shop's ledger
  * an unassigned spare could never be deleted, so a mistyped ledger entry
    inflated what the workshop owed that shop for ever
"""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from workshop import analysis_engine as engine
from workshop.models import (DeletionLog, JobCard, JobCardSpareItem, Mechanic,
                             SpareShop, SpareShopPayment)

SHOP = JobCardSpareItem.SOURCE_SHOP


class SpareFlowBase(TestCase):
    def setUp(self):
        g, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='off_flow', password='pw')
        self.user.groups.add(g)
        self.client = Client()
        self.client.login(username='off_flow', password='pw')

        self.mech = Mechanic.objects.create(name='Tech')
        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.jc = JobCard.objects.create(registration_number='KL20AA0001',
                                         admitted_date=date(2026, 3, 10))

    def owed(self):
        self.shop.refresh_from_db()
        return self.shop.get_pending_balance

    def spare(self, **over):
        kw = dict(job_card=self.jc, source=SHOP, shop=self.shop,
                  shop_name=self.shop.name, spare_part_name='Brake Pad',
                  quantity=D('2'), unit_price=D('1000'), total_price=D('2800'))
        kw.update(over)
        return JobCardSpareItem.objects.create(**kw)

    def payload(self, **over):
        d = {
            'registration_number': 'KL20AA0001', 'admitted_date': '2026-03-10',
            'customer_name': 'X', 'customer_contact': '9', 'brand_name': 'B',
            'model_name': 'M', 'mileage': '1', 'lead_mechanic': self.mech.id,
            'car_color': 'Black',
            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'spares-TOTAL_FORMS': '0', 'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '0', 'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',
        }
        d.update(over)
        return d

    def post_card_with_spare(self, row, shop_value):
        return self.client.post(reverse('jobcard_edit', args=[self.jc.pk]), self.payload(**{
            'spares-TOTAL_FORMS': '1', 'spares-INITIAL_FORMS': '1',
            'spares-0-id': str(row.pk),
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '2', 'spares-0-unit_price': '1000',
            'spares-0-total_price': '2800', 'spares-0-status': 'RECEIVED',
            'spares-0-shop_name': shop_value,
            'spares-0-ordered_date': '', 'spares-0-received_date': '',
        }))


class ArchivedShopKeepsItsDebtTests(SpareFlowBase):
    """
    The resolution pass rebuilds each spare's shop FK from the posted pk. It used
    to look only at active shops, so once a shop was archived, saving any job card
    holding one of its spares set `shop=None` and the purchase vanished from that
    shop's ledger — ₹2,000 owed became ₹0, silently, from an unrelated edit.
    """

    def archive(self):
        self.client.post(reverse('spare_shop_delete', args=[self.shop.pk]), {'reason': 'closed'})
        self.shop.refresh_from_db()

    def test_archiving_alone_changes_nothing(self):
        self.spare()
        self.assertEqual(self.owed(), D('2000.00'))
        self.archive()
        self.assertTrue(self.shop.is_trashed)
        self.assertEqual(self.owed(), D('2000.00'))

    def test_editing_a_card_after_archiving_keeps_the_link(self):
        row = self.spare()
        self.archive()
        self.post_card_with_spare(row, str(self.shop.pk))

        row.refresh_from_db()
        self.assertEqual(row.shop_id, self.shop.pk,
                         "an archived shop must stay attached to what was bought from it")
        self.assertEqual(self.owed(), D('2000.00'))

    def test_the_archived_shop_is_still_offered_and_preselected(self):
        """
        The display half of the same bug. With the archived shop missing from the
        options the select had nothing to mark, so the browser posted a blank
        value and the FK was cleared — fixing only the server lookup would have
        left that path live.
        """
        self.spare()
        self.archive()
        resp = self.client.get(reverse('jobcard_edit', args=[self.jc.pk]))

        self.assertIn(self.shop.pk, [s.pk for s in resp.context['spare_shops']])
        html = resp.content.decode()
        i = html.find('shop-name-select')
        chunk = html[i:i + 600]
        self.assertIn(f'value="{self.shop.pk}"', chunk)
        self.assertIn('selected', chunk)

    def test_an_archived_shop_is_not_offered_to_a_card_that_never_used_it(self):
        """Archiving still hides a shop from new work."""
        other = JobCard.objects.create(registration_number='KL20BB0002',
                                       admitted_date=date(2026, 3, 11))
        self.archive()
        resp = self.client.get(reverse('jobcard_edit', args=[other.pk]))
        self.assertNotIn(self.shop.pk, [s.pk for s in resp.context['spare_shops']])

    def test_clearing_the_dropdown_still_detaches_on_purpose(self):
        row = self.spare()
        self.post_card_with_spare(row, '')
        row.refresh_from_db()
        self.assertIsNone(row.shop_id)
        self.assertEqual(self.owed(), D('0.00'))


class DeletingAnUnassignedSpareTests(SpareFlowBase):
    """
    There was no way to delete one: no route, no button, and `/admin/` is
    unreachable by design. A mistyped ledger entry was therefore permanent.
    """

    def add_unassigned(self, price='1000', qty='2'):
        self.client.post(reverse('spare_shop_add_unassigned', args=[self.shop.pk]), {
            'spare_part_name': 'Brake Pad', 'unit_price': price, 'quantity': qty,
        })
        return JobCardSpareItem.objects.get(job_card__isnull=True)

    def test_deleting_one_clears_it_from_the_ledger(self):
        item = self.add_unassigned()
        self.assertEqual(self.owed(), D('2000.00'))

        self.client.post(reverse('spare_shop_delete_unassigned', args=[item.pk]),
                         {'reason': 'entered twice'})

        self.assertFalse(JobCardSpareItem.objects.filter(pk=item.pk).exists())
        self.assertEqual(self.owed(), D('0.00'))

    def test_the_deletion_is_logged(self):
        item = self.add_unassigned()
        self.client.post(reverse('spare_shop_delete_unassigned', args=[item.pk]),
                         {'reason': 'entered twice'})

        log = DeletionLog.objects.filter(
            entity_type=DeletionLog.ENTITY_UNASSIGNED_SPARE).first()
        self.assertIsNotNone(log, "a financial delete must reach Deletion History")
        self.assertEqual(log.amount, D('2000.00'))
        self.assertIn('Ajmal', log.entity_label)

    def test_a_spare_on_a_job_card_cannot_be_deleted_this_way(self):
        """One row, one screen that owns deleting it — the car's own section."""
        row = self.spare()
        resp = self.client.post(reverse('spare_shop_delete_unassigned', args=[row.pk]),
                                {'reason': 'nope'})
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(JobCardSpareItem.objects.filter(pk=row.pk).exists())

    def test_a_get_does_not_delete(self):
        item = self.add_unassigned()
        self.client.get(reverse('spare_shop_delete_unassigned', args=[item.pk]))
        self.assertTrue(JobCardSpareItem.objects.filter(pk=item.pk).exists())

    def test_the_hub_offers_the_delete(self):
        self.add_unassigned()
        resp = self.client.get(reverse('unassigned_spares_hub'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'spare-shops/items/')
        self.assertContains(resp, 'delete/')


class UnassignedLifecycleTests(SpareFlowBase):
    """Unassign off a card, and import back onto one."""

    def test_unassigning_drops_the_bill_but_keeps_the_debt(self):
        row = self.spare()
        self.jc.refresh_from_db()
        self.assertEqual(self.jc.total_bill_amount, D('2800.00'))

        self.client.post(reverse('spare_shop_unassign_item', args=[row.pk]))

        row.refresh_from_db()
        self.jc.refresh_from_db()
        self.assertIsNone(row.job_card_id)
        self.assertEqual(self.jc.total_bill_amount, D('0.00'))
        self.assertEqual(self.owed(), D('2000.00'),
                         "the part was still bought, whoever it ends up on")
        self.assertIn('KL20AA0001', row.original_vehicle_info)

    def test_importing_one_onto_a_card_does_not_double_the_debt(self):
        self.client.post(reverse('spare_shop_add_unassigned', args=[self.shop.pk]), {
            'spare_part_name': 'Brake Pad', 'unit_price': '1000', 'quantity': '2',
        })
        orphan = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertEqual(self.owed(), D('2000.00'))

        self.client.post(reverse('jobcard_edit', args=[self.jc.pk]), self.payload(**{
            'spares-TOTAL_FORMS': '1', 'spares-INITIAL_FORMS': '0',
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '2', 'spares-0-unit_price': '1000',
            'spares-0-total_price': '2800', 'spares-0-status': 'RECEIVED',
            'spares-0-shop_name': str(self.shop.pk),
            'spares-0-ordered_date': '', 'spares-0-received_date': '',
            'imported_unassigned_ids': str(orphan.pk),
        }))

        self.assertEqual(JobCardSpareItem.objects.filter(job_card__isnull=True).count(), 0)
        self.assertEqual(self.jc.spares.count(), 1)
        self.assertEqual(self.owed(), D('2000.00'), "one purchase, counted once")


class ShopPaymentTests(SpareFlowBase):
    def test_payment_then_reversal(self):
        self.spare(unit_price=D('10000'), quantity=D('1'), total_price=D('12000'))
        self.assertEqual(self.owed(), D('10000.00'))

        self.client.post(reverse('spare_shop_pay', args=[self.shop.pk]),
                         {'lump_sum': '4000', 'payment_method': 'CASH', 'note': 'part'})
        self.assertEqual(self.owed(), D('6000.00'))

        pay = SpareShopPayment.objects.get(shop=self.shop)
        self.client.post(reverse('spare_shop_payment_reverse', args=[self.shop.pk, pay.pk]),
                         {'reason': 'duplicate'})
        self.assertEqual(self.owed(), D('10000.00'))

    def test_a_zero_or_negative_payment_is_refused(self):
        self.spare()
        self.client.post(reverse('spare_shop_pay', args=[self.shop.pk]),
                         {'lump_sum': '0', 'payment_method': 'CASH'})
        self.assertEqual(SpareShopPayment.objects.count(), 0)
        self.assertEqual(self.owed(), D('2000.00'))

    def test_an_archived_shop_takes_no_new_payment(self):
        self.spare()
        self.client.post(reverse('spare_shop_delete', args=[self.shop.pk]), {'reason': 'closed'})
        resp = self.client.post(reverse('spare_shop_pay', args=[self.shop.pk]),
                                {'lump_sum': '500', 'payment_method': 'CASH'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(SpareShopPayment.objects.count(), 0)


class LedgerEditTests(SpareFlowBase):
    def test_editing_cost_from_the_shop_ledger(self):
        row = self.spare()
        self.client.post(reverse('spare_shop_update_item_price', args=[row.pk]),
                         {'unit_price': '1500', 'quantity': '3'})
        row.refresh_from_db()
        self.jc.refresh_from_db()

        self.assertEqual(row.unit_price, D('1500.00'))
        self.assertEqual(row.quantity, D('3.00'))
        self.assertEqual(self.owed(), D('4500.00'))
        # The customer price is typed separately and must not move on its own.
        self.assertEqual(row.total_price, D('2800.00'))
        self.assertEqual(self.jc.total_bill_amount, D('2800.00'))

    def test_the_analysis_follows_the_ledger(self):
        row = self.spare()
        s, e, _k, _l = engine.resolve_period('all_time')
        self.assertEqual(engine.spare_shop_expense(s, e), D('2000.00'))

        self.client.post(reverse('spare_shop_update_item_price', args=[row.pk]),
                         {'unit_price': '1500', 'quantity': '3'})
        self.assertEqual(engine.spare_shop_expense(s, e), D('4500.00'))


class AddUnassignedValidationTests(SpareFlowBase):
    """
    This row is money owed to a supplier. The create path used to accept a
    negative price (making the shop appear to owe the workshop), a negative or
    zero quantity, and an oversized price that did not fail cleanly — it was
    written, and every later read of that shop's ledger then raised
    InvalidOperation while aggregating it, leaving the shop's page permanently
    un-openable. All of it now goes through one validated helper.
    """

    def add(self, **over):
        data = {'spare_part_name': 'Brake Pad', 'unit_price': '1000', 'quantity': '2'}
        data.update(over)
        return self.client.post(
            reverse('spare_shop_add_unassigned', args=[self.shop.pk]), data, follow=True)

    def rows(self):
        return JobCardSpareItem.objects.filter(job_card__isnull=True).count()

    def test_a_normal_entry_is_accepted(self):
        self.add()
        self.assertEqual(self.rows(), 1)
        self.assertEqual(self.owed(), D('2000.00'))

    def test_a_negative_price_is_refused(self):
        resp = self.add(unit_price='-5000')
        self.assertEqual(self.rows(), 0)
        self.assertEqual(self.owed(), D('0.00'))
        self.assertContains(resp, 'cannot be negative')

    def test_a_negative_quantity_is_refused(self):
        self.add(quantity='-3')
        self.assertEqual(self.rows(), 0)
        self.assertEqual(self.owed(), D('0.00'))

    def test_a_zero_quantity_is_refused(self):
        self.add(quantity='0')
        self.assertEqual(self.rows(), 0)

    def test_an_oversized_price_is_refused_rather_than_poisoning_the_ledger(self):
        self.add(unit_price='99999999999')
        self.assertEqual(self.rows(), 0)
        # The ledger must still be readable — this is what used to break.
        self.assertEqual(self.owed(), D('0.00'))
        self.assertEqual(
            self.client.get(reverse('spare_shop_detail', args=[self.shop.pk])).status_code, 200)

    def test_an_oversized_quantity_is_refused(self):
        self.add(quantity='9999999')
        self.assertEqual(self.rows(), 0)

    def test_a_blank_name_is_refused(self):
        self.add(spare_part_name='   ')
        self.assertEqual(self.rows(), 0)

    def test_a_long_name_is_truncated_not_crashed(self):
        self.add(spare_part_name='X' * 250)
        self.assertEqual(self.rows(), 1)
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertEqual(len(row.spare_part_name), 100)

    def test_non_numeric_input_is_refused(self):
        self.add(unit_price='abc')
        self.assertEqual(self.rows(), 0)

    def test_an_archived_shop_takes_no_new_purchases(self):
        """Refused at the door with a 404, exactly as spare_shop_pay does."""
        self.client.post(reverse('spare_shop_delete', args=[self.shop.pk]), {'reason': 'closed'})
        resp = self.client.post(
            reverse('spare_shop_add_unassigned', args=[self.shop.pk]),
            {'spare_part_name': 'Ghost', 'unit_price': '500', 'quantity': '1'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.rows(), 0)

    def test_the_helper_also_refuses_an_archived_shop(self):
        """Belt and braces for any caller resolving the shop from form input."""
        from workshop.views.spare_shop import _build_unassigned_spare
        self.shop.is_trashed = True
        self.shop.save()
        item, error = _build_unassigned_spare(self.shop, 'Ghost', '500', '1')
        self.assertIsNone(item)
        self.assertIn('archived', error)

    def test_the_helper_requires_a_shop(self):
        """A row with no job card AND no shop would be invisible everywhere."""
        from workshop.views.spare_shop import _build_unassigned_spare
        item, error = _build_unassigned_spare(None, 'Ghost', '500', '1')
        self.assertIsNone(item)
        self.assertIn('Choose which shop', error)

    def test_a_blank_price_means_NOT_PRICED_and_is_allowed(self):
        """
        A legacy balance line with no price recorded is legitimate, and it
        stores NULL rather than zero: zero says the shop gave the part away,
        NULL says nobody has priced it yet. `SpareShop.update_totals()`
        coalesces NULL to 0, so an unpriced row adds nothing to the balance
        either way — the difference is what the row MEANS when somebody comes
        to price it, and it is the same distinction a Floor-recorded row starts
        life in.
        """
        self.add(unit_price='')
        self.assertEqual(self.rows(), 1)
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertIsNone(row.unit_price)
        self.assertEqual(self.owed(), D('0.00'))

    def test_an_explicit_zero_still_means_free(self):
        """The other half of the rule above — 0 is a price somebody chose."""
        self.add(unit_price='0')
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertEqual(row.unit_price, D('0.00'))

    def test_the_row_is_created_as_a_shop_purchase(self):
        self.add()
        row = JobCardSpareItem.objects.get(job_card__isnull=True)
        self.assertEqual(row.source, JobCardSpareItem.SOURCE_SHOP)
        self.assertIsNone(row.item_id, "a shop purchase must never point at warehouse stock")


class AddingFromTheHubTests(SpareFlowBase):
    """
    The Hub can record a purchase directly, without opening the shop's page.
    Same row, same rules — the shop just arrives as a form field.
    """

    def add(self, **over):
        data = {'shop': str(self.shop.pk), 'spare_part_name': 'Brake Pad',
                'unit_price': '1000', 'quantity': '2'}
        data.update(over)
        return self.client.post(reverse('unassigned_spare_add'), data, follow=True)

    def rows(self):
        return JobCardSpareItem.objects.filter(job_card__isnull=True)

    def test_it_creates_the_same_row_the_shop_page_would(self):
        self.add()
        row = self.rows().get()
        self.assertEqual(row.shop_id, self.shop.pk)
        self.assertIsNone(row.job_card_id)
        self.assertEqual(row.source, JobCardSpareItem.SOURCE_SHOP)
        self.assertIsNone(row.item_id)
        self.assertEqual(row.unit_price, D('1000.00'))
        self.assertEqual(row.quantity, D('2.00'))
        self.assertEqual(self.owed(), D('2000.00'))

    def test_the_new_row_shows_up_on_the_hub(self):
        self.add()
        resp = self.client.get(reverse('unassigned_spares_hub'))
        self.assertContains(resp, 'Brake Pad')

    def test_the_shop_is_required(self):
        """Without one the row would be invisible everywhere and undeletable."""
        resp = self.add(shop='')
        self.assertEqual(self.rows().count(), 0)
        self.assertContains(resp, 'Choose which shop')

    def test_a_junk_shop_value_is_refused(self):
        self.add(shop='not-a-number')
        self.assertEqual(self.rows().count(), 0)

    def test_an_unknown_shop_id_is_refused(self):
        self.add(shop='999999')
        self.assertEqual(self.rows().count(), 0)

    def test_an_archived_shop_cannot_be_chosen(self):
        self.client.post(reverse('spare_shop_delete', args=[self.shop.pk]), {'reason': 'closed'})
        self.add()
        self.assertEqual(self.rows().count(), 0)

    def test_it_shares_the_same_validation_as_the_shop_page(self):
        for over in ({'unit_price': '-5000'}, {'quantity': '-3'}, {'quantity': '0'},
                     {'unit_price': '99999999999'}, {'spare_part_name': '  '}):
            self.add(**over)
            self.assertEqual(self.rows().count(), 0, f"{over} should have been refused")
        self.assertEqual(self.owed(), D('0.00'))

    def test_a_get_creates_nothing(self):
        self.client.get(reverse('unassigned_spare_add'))
        self.assertEqual(self.rows().count(), 0)

    def test_the_hub_only_offers_active_shops(self):
        archived = SpareShop.objects.create(name='Closed Shop', is_trashed=True)
        resp = self.client.get(reverse('unassigned_spares_hub'))
        offered = [s.pk for s in resp.context['shops']]
        self.assertIn(self.shop.pk, offered)
        self.assertNotIn(archived.pk, offered)

    def test_a_row_added_here_can_be_deleted_here(self):
        self.add()
        row = self.rows().get()
        self.client.post(reverse('spare_shop_delete_unassigned', args=[row.pk]),
                         {'reason': 'mistake'})
        self.assertEqual(self.rows().count(), 0)
        self.assertEqual(self.owed(), D('0.00'))

    def test_adding_with_custom_dates(self):
        from datetime import date
        self.add(ordered_date='2026-08-10', received_date='2026-08-12')
        row = self.rows().get()
        self.assertEqual(row.ordered_date, date(2026, 8, 10))
        self.assertEqual(row.received_date, date(2026, 8, 12))

    def test_editing_an_unassigned_spare(self):
        from datetime import date
        self.add(spare_part_name='Old Part', unit_price='500', quantity='1')
        row = self.rows().get()

        shop2 = SpareShop.objects.create(name='Second Shop')

        resp = self.client.post(
            reverse('unassigned_spare_edit', args=[row.pk]),
            {
                'shop': str(shop2.pk),
                'spare_part_name': 'New Part',
                'unit_price': '800',
                'quantity': '3',
                'ordered_date': '2026-08-01',
                'received_date': '2026-08-05',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.shop_id, shop2.pk)
        self.assertEqual(row.spare_part_name, 'New Part')
        self.assertEqual(row.unit_price, D('800.00'))
        self.assertEqual(row.quantity, D('3.00'))
        self.assertEqual(row.ordered_date, date(2026, 8, 1))
        self.assertEqual(row.received_date, date(2026, 8, 5))

        # Check that shop totals reflect the move
        self.assertEqual(self.owed(), D('0.00'))
        shop2.refresh_from_db()
        self.assertEqual(shop2.total_purchased_amount, D('2400.00'))
        self.assertEqual(shop2.get_pending_balance, D('2400.00'))

