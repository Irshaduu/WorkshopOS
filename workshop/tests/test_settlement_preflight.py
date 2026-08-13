"""
The pre-flight in front of Settle Bill.

Settling is the last thing that happens to a job card and the only irreversible
one: a walk-in has exactly one payment event, so the moment a figure is typed
the card is PAID, the shortfall becomes a permanent discount, and the Financial
Lock stands between the card and anyone correcting it. Everything that was going
to be filled in has to be filled in before that, and this screen is the last
moment anyone is looking.

Two properties are worth more than the individual checks and are asserted first:
it never blocks, and it does not appear when there is nothing to say.
"""

from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Category, Item
from workshop.models import (
    JobCard, JobCardConcern, JobCardLabourItem, JobCardSpareItem, Mechanic,
    SpareShop,
)
from workshop.settlement import settlement_gaps, settlement_readiness

SHOP = JobCardSpareItem.SOURCE_SHOP
INVENTORY = JobCardSpareItem.SOURCE_INVENTORY


class PreflightBase(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='off', password='pw')
        self.user.groups.add(Group.objects.get(name='Office'))
        self.client = Client()
        self.client.login(username='off', password='pw')

        self.mechanic = Mechanic.objects.create(name='Lead Tech')
        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.category = Category.objects.create(name='Engine Oil')
        self.item = Item.objects.create(
            category=self.category, name='Liqui Moly 5W-30',
            average_stock=D('20'), current_stock=D('20'), avg_cost=D('400'))

    def a_clean_card(self):
        """A card with nothing outstanding — every check below satisfied."""
        card = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Audi', model_name='A4',
            registration_number='KL11AJ2266', customer_name='Rahim',
            customer_contact='9567494933', mileage='51000',
            lead_mechanic=self.mechanic, labour_amount=D('2500'),
            completed=True, completed_date=date.today(),
        )
        JobCardConcern.objects.create(
            job_card=card, concern_text='Brake noise', status='FIXED')
        JobCardLabourItem.objects.create(
            job_card=card, job_description='Brake service')
        JobCardSpareItem.objects.create(
            job_card=card, source=SHOP, spare_part_name='Brake Pad',
            quantity=D('1'), status='RECEIVED', shop=self.shop,
            shop_name=self.shop.name, ordered_date=date.today(),
            received_date=date.today(), unit_price=D('900'), total_price=D('1400'))
        return card

    def page(self, card):
        return self.client.get(reverse('invoice_view', args=[card.pk])).content.decode()


class NothingToSayMeansNoDialogTests(PreflightBase):
    """
    The half people forget. A confirmation that fires on every settlement — most
    of them fine — is one that gets dismissed without reading by the third time,
    and then it is not protecting the settlements that were NOT fine.
    """

    def test_a_complete_card_reports_no_gaps(self):
        self.assertEqual(settlement_gaps(self.a_clean_card()), [])

    def test_a_complete_card_needs_no_confirmation(self):
        readiness = settlement_readiness(self.a_clean_card())
        self.assertFalse(readiness['needs_confirmation'])

    def test_the_dialog_is_not_even_rendered_for_a_complete_card(self):
        """
        Absence from the DOM is what the button reads to decide, so the decision
        is made once in Python and the markup cannot come to disagree with it.
        """
        self.assertNotIn('id="preflightDialog"', self.page(self.a_clean_card()))

    def test_the_dialog_is_rendered_when_something_is_missing(self):
        card = self.a_clean_card()
        card.mileage = ''
        card.save()
        self.assertIn('id="preflightDialog"', self.page(card))


class ItNeverBlocksTests(PreflightBase):
    """
    The workshop settles at the counter with the customer standing there. A
    checklist that refused to let them pay would be worked around within a week
    — by not opening this screen until afterwards, which loses the check
    altogether.
    """

    def test_a_card_with_every_gap_still_settles(self):
        card = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Audi', model_name='A4',
            registration_number='KL11AJ9999', customer_name='Rahim',
            customer_contact='9567494933', total_bill_amount=D('1000'),
        )
        self.assertTrue(settlement_gaps(card) or not card.completed)

        resp = self.client.post(
            reverse('update_bill_status', args=[card.pk]),
            {'received_amount': '1000', 'payment_method': 'CASH'},
        )
        self.assertRedirects(resp, reverse('invoice_view', args=[card.pk]))
        card.refresh_from_db()
        self.assertEqual(card.payment_status, 'PAID')

    def test_the_dialog_offers_a_way_forward_as_well_as_a_way_back(self):
        card = self.a_clean_card()
        card.mileage = ''
        card.save()
        body = self.page(card)
        self.assertIn('Open job card', body)
        self.assertIn('Continue settling', body)


class WhatItChecksTests(PreflightBase):
    def gap_keys(self, card):
        return {gap.key for gap in settlement_gaps(card)}

    def test_a_missing_mechanic_is_reported(self):
        card = self.a_clean_card()
        card.lead_mechanic = None
        card.save()
        self.assertIn('mechanic', self.gap_keys(card))

    def test_a_missing_mileage_is_reported(self):
        card = self.a_clean_card()
        card.mileage = '   '
        card.save()
        self.assertIn('mileage', self.gap_keys(card))

    def test_an_unfixed_concern_is_reported_by_its_STATUS_not_its_wording(self):
        """
        `concern_text` is a TextField and staff write sentences into it. Quoting
        one costs three lines of a dialog read in two seconds and still only
        describes one of them; the status is the thing being asked about.
        """
        card = self.a_clean_card()
        card.concerns.update(status='WORKING')
        gaps = {g.key: g for g in settlement_gaps(card)}
        self.assertIn('concerns', gaps)
        self.assertEqual(gaps['concerns'].tags, ('1 Working',))
        self.assertNotIn('Brake noise', gaps['concerns'].label)

    def test_work_listed_but_not_priced_is_reported(self):
        card = self.a_clean_card()
        card.labour_amount = D('0')
        card.save()
        self.assertIn('labour', self.gap_keys(card))

    def test_a_parts_only_card_is_not_nagged_about_labour(self):
        """
        ₹0 labour is the CORRECT answer on a card with no work listed on it, and
        reporting it on every such card is how this list would come to be
        clicked past without being read.
        """
        card = self.a_clean_card()
        card.labours.all().delete()
        card.labour_amount = D('0')
        card.save()
        self.assertNotIn('labour', self.gap_keys(card))

    def spare_tags(self, card):
        gaps = {g.key: g for g in settlement_gaps(card)}
        return gaps['spares'].tags if 'spares' in gaps else ()

    def test_a_part_with_no_customer_price_is_reported(self):
        card = self.a_clean_card()
        card.spares.update(total_price=None)
        self.assertIn('No customer price', self.spare_tags(card))

    def test_a_spare_not_yet_received_is_reported(self):
        card = self.a_clean_card()
        card.spares.update(status='ORDERED')
        self.assertIn('Not received', self.spare_tags(card))

    def test_missing_order_dates_are_reported(self):
        card = self.a_clean_card()
        card.spares.update(received_date=None)
        self.assertIn('No received date', self.spare_tags(card))

    def test_a_spare_with_no_shop_is_reported(self):
        card = self.a_clean_card()
        card.spares.update(shop=None)
        self.assertIn('No shop', self.spare_tags(card))

    def test_a_spare_with_no_shop_price_is_reported(self):
        card = self.a_clean_card()
        card.spares.update(unit_price=None)
        self.assertIn('No shop price', self.spare_tags(card))

    def test_everything_wrong_with_the_parts_is_ONE_row_of_chips(self):
        """
        Five separate lines all beginning "Spare parts" is five times the height
        for one fact — that the parts section is unfinished — and it buries the
        rows above it, which are about something else entirely. Chips read in
        one sweep and wrap without pushing the buttons off a phone.
        """
        card = self.a_clean_card()
        card.spares.update(shop=None, ordered_date=None, received_date=None,
                           status='PENDING', unit_price=None, total_price=None)
        gaps = [g for g in settlement_gaps(card) if g.key == 'spares']
        self.assertEqual(len(gaps), 1)
        self.assertEqual(len(gaps[0].tags), 6)

    def test_a_label_is_a_phrase_not_a_sentence(self):
        """
        The whole dialog is scanned, not read. Anything long enough to be prose
        here defeats it — so nothing has a full stop and nothing runs on.
        """
        card = self.a_clean_card()
        card.lead_mechanic = None
        card.mileage = ''
        card.save()
        for gap in settlement_gaps(card):
            self.assertNotIn('.', gap.label, gap.label)
            self.assertLess(len(gap.label), 40, gap.label)
            for tag in gap.tags:
                self.assertLess(len(tag), 24, tag)


class AWarehouseDrawIsNotChasedTests(PreflightBase):
    """
    The `source` rule again. A warehouse draw came off the shelf already fitted:
    it has no shop, no order, no arrival, and its `status` column is meaningless
    — so asking about any of them would report a problem that cannot exist and
    cannot be fixed, which is exactly how a checklist teaches people to click
    past it. The one check that DOES span both routes is the customer price,
    because that is the figure that bills whichever shelf the part came off.
    """

    def draw_card(self, **overrides):
        card = self.a_clean_card()
        card.spares.all().delete()
        fields = dict(
            job_card=card, source=INVENTORY, item=self.item,
            spare_part_name='Liqui Moly 5W-30', quantity=D('4'),
            total_price=D('3200'),
        )
        fields.update(overrides)
        JobCardSpareItem.objects.create(**fields)
        return card

    def test_a_draw_raises_none_of_the_shop_workflow_chips(self):
        card = self.draw_card(status='PENDING')
        self.assertNotIn('spares', {g.key for g in settlement_gaps(card)})

    def test_a_draw_with_no_customer_price_IS_reported(self):
        card = self.draw_card(total_price=None)
        gaps = {g.key: g for g in settlement_gaps(card)}
        self.assertIn('inventory', gaps)
        self.assertEqual(gaps['inventory'].tags, ('No customer price',))


class CompleteAndSettleTests(PreflightBase):
    """
    An uncompleted card is not one more unfilled box — it is a contradiction
    (money taken for a car the board still shows as in the workshop), and it is
    the only item here with a fix that can be applied from the invoice screen.
    """

    def open_card(self):
        return JobCard.objects.create(
            admitted_date=date.today(), brand_name='Audi', model_name='A4',
            registration_number='KL11AJ7777', customer_name='Rahim',
            customer_contact='9567494933', mileage='51000',
            lead_mechanic=self.mechanic, labour_amount=D('2500'),
            total_bill_amount=D('2500'), completed=False,
        )

    def test_an_uncompleted_card_alone_triggers_the_dialog(self):
        card = self.open_card()
        readiness = settlement_readiness(card)
        self.assertEqual(readiness['gaps'], [])
        self.assertTrue(readiness['needs_confirmation'])

    def test_the_dialog_offers_complete_and_settle(self):
        self.assertIn('Complete &amp; settle', self.page(self.open_card()))

    def test_a_completed_card_is_not_offered_it(self):
        self.assertNotIn('Complete &amp; settle', self.page(self.a_clean_card()))

    def test_complete_and_settle_does_both(self):
        card = self.open_card()
        resp = self.client.post(
            reverse('update_bill_status', args=[card.pk]),
            {'received_amount': '2500', 'payment_method': 'CASH',
             'complete_card': 'true'},
        )
        self.assertRedirects(resp, reverse('invoice_view', args=[card.pk]))
        card.refresh_from_db()
        self.assertTrue(card.completed)
        self.assertEqual(card.completed_date, date.today())
        self.assertEqual(card.payment_status, 'PAID')

    def test_a_plain_settlement_does_not_complete_the_card(self):
        """The field is only ever set by that one button; anything else must
        leave the board alone."""
        card = self.open_card()
        self.client.post(
            reverse('update_bill_status', args=[card.pk]),
            {'received_amount': '2500', 'payment_method': 'CASH'},
        )
        card.refresh_from_db()
        self.assertFalse(card.completed)

    def test_re_settling_never_moves_the_day_the_car_was_handed_over(self):
        """
        `completed_date` is what the Completed list filters and sorts on. A
        correction to the amount weeks later must not restamp it to today.
        """
        card = self.a_clean_card()
        original = date(2026, 1, 5)
        JobCard.objects.filter(pk=card.pk).update(completed_date=original)
        card.refresh_from_db()

        self.assertFalse(card.mark_completed())
        card.refresh_from_db()
        self.assertEqual(card.completed_date, original)
