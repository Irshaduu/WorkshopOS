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
from workshop.settlement import settlement_readiness, unfilled

#: The rendered <dialog> tag, never the bare class name. `.pf-critical` is also
#: a rule in the page's own stylesheet, which is present on EVERY render — the
#: same trap `ThePaidStampAppearsOnlyOnceSettledTests` records for `.paid-box`.
CRITICAL_TAG = 'class="preflight no-print pf-critical"'

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
        self.assertFalse(unfilled(self.a_clean_card()))

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
        self.assertTrue(unfilled(card) or not card.completed)

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
    """
    The checks themselves, read off the one structure both surfaces draw:
    the card's own chips, then a row per concern, per draw and per spare.
    """

    def card_chips(self, card):
        return unfilled(card).card

    def test_a_missing_mechanic_is_reported(self):
        card = self.a_clean_card()
        card.lead_mechanic = None
        card.save()
        self.assertIn('Mechanic', self.card_chips(card))

    def test_a_missing_mileage_is_reported(self):
        card = self.a_clean_card()
        card.mileage = '   '
        card.save()
        self.assertIn('Mileage', self.card_chips(card))

    def test_an_unfixed_concern_is_named_AND_carries_its_status(self):
        """
        This reverses an earlier decision, on the owner's redesign. The dialog
        used to name concerns by status alone ("1 Working") because quoting a
        TextField cost three lines. Both surfaces now show the wording — it is
        what tells you WHICH concern — and clamp it in CSS, so the stored text
        is never what gets shortened.
        """
        card = self.a_clean_card()
        card.concerns.update(status='WORKING')
        concerns = unfilled(card).concerns
        self.assertEqual(len(concerns), 1)
        self.assertEqual(concerns[0].text, 'Brake noise')
        self.assertEqual(concerns[0].status, 'Working')

    def test_a_fixed_concern_is_not_reported(self):
        self.assertEqual(unfilled(self.a_clean_card()).concerns, ())

    def test_work_listed_but_not_priced_is_reported(self):
        card = self.a_clean_card()
        card.labour_amount = D('0')
        card.save()
        self.assertIn('Job Amount', self.card_chips(card))

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
        self.assertNotIn('Job Amount', self.card_chips(card))

    def spare_tags(self, card):
        spares = unfilled(card).spares
        return spares[0].tags if spares else ()

    def test_a_part_with_no_customer_price_is_reported(self):
        card = self.a_clean_card()
        card.spares.update(total_price=None)
        self.assertIn('Customer Price', self.spare_tags(card))

    def test_a_part_with_no_shop_price_is_reported(self):
        card = self.a_clean_card()
        card.spares.update(unit_price=None)
        self.assertIn('Shop Price', self.spare_tags(card))

    def test_a_part_with_no_shop_is_reported(self):
        card = self.a_clean_card()
        card.spares.update(shop=None)
        self.assertIn('Shop', self.spare_tags(card))

    def test_the_two_dates_are_chased_as_ONE_chip(self):
        """
        A spare is finished when it has been ordered AND received, so a
        half-filled pair is still incomplete and gets the same single chip.
        Which of the two is missing is answered by opening the date panel on the
        job card, not by a second chip here.
        """
        for missing in ('ordered_date', 'received_date'):
            with self.subTest(missing=missing):
                card = self.a_clean_card()
                card.spares.update(**{missing: None})
                self.assertEqual(list(self.spare_tags(card)).count('Dates'), 1)

    def test_a_part_carries_its_OWN_name_and_its_OWN_chips(self):
        """
        One row per part, not one row for "Spare parts" with every problem on
        the card mixed into it. Two parts wrong in two different ways used to
        collapse into a single line of six chips describing neither of them.
        """
        card = self.a_clean_card()
        card.spares.update(shop=None)
        JobCardSpareItem.objects.create(
            job_card=card, source=SHOP, spare_part_name='Oil Filter',
            quantity=D('1'), status='RECEIVED', shop=self.shop,
            ordered_date=date.today(), received_date=date.today(),
            unit_price=D('300'), total_price=None)

        spares = {part.name: part.tags for part in unfilled(card).spares}

        self.assertEqual(spares['Brake Pad'], ('Shop',))
        self.assertEqual(spares['Oil Filter'], ('Customer Price',))

    def test_a_part_missing_everything_carries_every_chip(self):
        card = self.a_clean_card()
        card.spares.update(shop=None, ordered_date=None, received_date=None,
                           status='PENDING', unit_price=None, total_price=None)
        self.assertEqual(
            self.spare_tags(card),
            ('Shop', 'Dates', 'Shop Price', 'Customer Price'))

    def test_a_chip_is_a_label_not_a_sentence(self):
        """
        The whole dialog is scanned, not read. Anything long enough to be prose
        here defeats it — so nothing has a full stop and nothing runs on. The
        concern wording is exempt: it is the customer's words, and clamping it
        is the template's job.
        """
        card = self.a_clean_card()
        card.lead_mechanic = None
        card.mileage = ''
        card.save()
        card.spares.update(shop=None, unit_price=None)

        holes = unfilled(card)
        chips = list(holes.card)
        for part in holes.spares + holes.inventory:
            chips.extend(part.tags)

        self.assertTrue(chips)
        for chip in chips:
            self.assertNotIn('.', chip, chip)
            self.assertLess(len(chip), 24, chip)

    def test_the_count_is_in_chips_not_rows(self):
        """
        A spare missing four things is four problems, not one. The headline
        number is what tells an owner whether this is a typo or a card nobody
        filled in at all.
        """
        card = self.a_clean_card()
        card.mileage = ''
        card.save()
        card.spares.update(shop=None, ordered_date=None, received_date=None,
                           unit_price=None, total_price=None)

        self.assertEqual(unfilled(card).count, 5)


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
        self.assertEqual(unfilled(card).spares, ())

    def test_a_draw_with_no_customer_price_IS_reported(self):
        card = self.draw_card(total_price=None)
        drawn = unfilled(card).inventory
        self.assertEqual(len(drawn), 1)
        self.assertEqual(drawn[0].name, 'Liqui Moly 5W-30')
        self.assertEqual(drawn[0].tags, ('Customer Price',))

    def test_a_priced_draw_is_not_reported_at_all(self):
        self.assertFalse(unfilled(self.draw_card(status='PENDING')))


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
        self.assertFalse(readiness['unfilled'])
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


class TheFrameSaysWhichKindOfPauseThisIsTests(PreflightBase):
    """
    Two states, two colours, on the owner's instruction.

    AMBER is a question. Everything on the card is filled in and the only thing
    to say is that nobody has marked the car Completed — a contradiction worth
    pausing on, not a fault in the data, and one this screen can fix with the
    button beside it.

    RED is a warning. Something is genuinely unfilled, and settling is what
    closes the door on correcting it: the card goes PAID, the shortfall becomes
    a permanent discount, and the Financial Lock stands between it and anyone
    who notices later.

    `is_critical` decides in Python so the frame and the body cannot come to
    disagree about which of the two this is.
    """

    def test_only_uncompleted_is_a_question_and_stays_amber(self):
        card = self.a_clean_card()
        card.completed = False
        card.save()

        readiness = settlement_readiness(card)
        self.assertTrue(readiness['needs_confirmation'])
        self.assertFalse(readiness['is_critical'])
        self.assertNotIn(CRITICAL_TAG, self.page(card))

    def test_anything_unfilled_turns_the_frame_red(self):
        card = self.a_clean_card()
        card.mileage = ''
        card.save()

        self.assertTrue(settlement_readiness(card)['is_critical'])
        self.assertIn(CRITICAL_TAG, self.page(card))

    def test_an_unfilled_card_that_is_ALSO_uncompleted_is_red(self):
        """Red outranks amber: the unfilled boxes are the part that cannot be
        put right afterwards."""
        card = self.a_clean_card()
        card.mileage = ''
        card.completed = False
        card.save()

        self.assertTrue(settlement_readiness(card)['is_critical'])
        self.assertIn(CRITICAL_TAG, self.page(card))

    def test_a_clean_card_renders_no_dialog_of_either_colour(self):
        self.assertNotIn('id="preflightDialog"', self.page(self.a_clean_card()))


class TheDialogAndTheChaseListSayTheSameThingTests(PreflightBase):
    """
    The settle dialog asks "you are about to skip this"; the Live Report's
    "Billed but not filled" container asks "you skipped this". They are two
    moments of one rule, so there is one implementation of it — a second copy
    would drift exactly where it matters, as a card the dialog waved through
    turning up on the chase list, or the reverse.

    These tests assert the SHARED READING rather than either surface's markup:
    both call `settlement.unfilled`, and both render the chip wordings it names.
    """

    def a_holey_card(self):
        card = self.a_clean_card()
        card.mileage = ''
        card.save()
        card.spares.update(shop=None, unit_price=None)
        return card

    def test_both_surfaces_read_one_function(self):
        """
        Not a style check. If either view ever grows its own idea of "unfilled",
        this is what fails — and the failure it prevents is silent, because both
        pages would still render perfectly well while disagreeing.
        """
        import workshop.settlement as settlement
        import workshop.views.billing as billing
        import workshop.views.dashboard as dashboard

        self.assertIs(dashboard.unfilled, settlement.unfilled)
        self.assertIs(billing.settlement_readiness, settlement.settlement_readiness)

    def test_the_dialog_and_the_chase_list_agree_on_one_card(self):
        card = self.a_holey_card()

        self.assertEqual(settlement_readiness(card)['unfilled'], unfilled(card))

    def test_the_dialog_prints_the_chip_wordings_the_module_names(self):
        card = self.a_holey_card()
        holes = unfilled(card)
        body = self.page(card)

        chips = list(holes.card)
        for part in holes.spares + holes.inventory:
            chips.extend(part.tags)

        self.assertTrue(chips)
        for chip in chips:
            self.assertIn('<em>%s</em>' % chip, body)

    def test_the_dialog_names_the_part_each_chip_belongs_to(self):
        """
        One row per part, so "Shop Price" is attached to the part that is
        missing it rather than floating over the whole section.
        """
        card = self.a_holey_card()
        body = self.page(card)

        self.assertIn('Brake Pad', body)
        self.assertIn('Spare Parts', body)
