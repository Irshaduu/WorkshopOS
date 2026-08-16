"""
"Billed but not filled" — the critical container at the top of the Live Report.

Every other box on that page is about work in progress, where an empty box is a
task nobody has got to yet. These cards have been BILLED: the money moved, the
card went PAID, whatever was not handed over became a permanent discount, and
the Financial Lock now stands between the card and anyone correcting it. An
empty box on one of these is a hole in the books, and that is why it leads the
page.

What counts as unfilled is `workshop.settlement.unfilled` — the same function
the settle dialog reads *before* the money moves. One rule, asked at two
moments. These tests assert the container reads it rather than a second copy.
"""

import re
from datetime import date
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from inventory.models import Category, Item
from workshop.models import (
    JobCardConcern, JobCardLabourItem, JobCardSpareItem, Mechanic, SpareShop,
)
from workshop.tests.test_live_report import LiveReportTestCase

SHOP = JobCardSpareItem.SOURCE_SHOP
INVENTORY = JobCardSpareItem.SOURCE_INVENTORY


class BilledButNotFilledTests(LiveReportTestCase):

    def setUp(self):
        super().setUp()
        self.mech = Mechanic.objects.create(name='Rafeeq')
        self.shop = SpareShop.objects.create(name='Kochi Auto Spares')
        self.category = Category.objects.create(name='Engine Oil')
        self.item = Item.objects.create(
            category=self.category, name='Liqui Moly 5W-30',
            average_stock=Decimal('20'), current_stock=Decimal('20'),
            avg_cost=Decimal('400'))

    def _billed(self, reg='KL01AA1111', **kwargs):
        """A settled card with nothing wrong with it."""
        fields = dict(
            mileage='51000', lead_mechanic=self.mech, labour_amount=Decimal('2500'),
            completed=True, completed_date=date(2026, 8, 2),
            payment_status='PAID', paid_date=timezone.now(),
            total_bill_amount=Decimal('2500'), received_amount=Decimal('2500'),
        )
        fields.update(kwargs)
        # `_car` names it `mechanic` and assigns it to `lead_mechanic` itself.
        return self._car(reg, mechanic=fields.pop('lead_mechanic'), **fields)

    def _box(self, page):
        """Just this section, never the whole page.

        Scoping matters: the two boxes below it name every car on the floor, so
        an unscoped assertion on a registration would pass whatever this
        container actually rendered.
        """
        return page.split('Billed but not filled', 1)[1].split('</section>', 1)[0]

    def _regs(self, page):
        return re.findall(r'<span class="lr-bill-reg">([^<]+)</span>', self._box(page))

    # ── which cards are in it ──────────────────────────────────────────

    def test_a_billed_card_with_nothing_missing_is_not_listed(self):
        self._billed()
        page = self._page(self.owner)
        self.assertIn('Every billed card is filled in.', page)
        self.assertEqual(self._regs(page), [])

    def test_a_billed_card_with_no_mileage_is_listed(self):
        self._billed(mileage='')
        self.assertEqual(self._regs(self._page(self.owner)), ['KL01AA1111'])

    def test_whitespace_is_not_a_mileage(self):
        """
        The database narrowing TRIMs, exactly as the Python check does. A card
        carrying three spaces would otherwise satisfy the SQL and fail the
        Python — which is the shape of bug that puts an empty red box on the
        page, and an empty warning is how an owner learns to stop reading one.
        """
        self._billed(mileage='   ')
        self.assertEqual(self._regs(self._page(self.owner)), ['KL01AA1111'])

    def test_an_UNBILLED_card_is_never_listed_however_empty_it_is(self):
        """
        The whole point of the box is money that has already moved. A card still
        being worked on has empty boxes because it is not finished, and chasing
        those is what the rest of the page is for.
        """
        self._billed(mileage='', lead_mechanic=None,
                     payment_status='PENDING', paid_date=None, completed=False)
        self.assertEqual(self._regs(self._page(self.owner)), [])

    def test_a_deleted_card_is_never_listed(self):
        self._billed(mileage='', is_deleted=True)
        self.assertEqual(self._regs(self._page(self.owner)), [])

    def test_a_fleet_card_still_being_collected_counts_as_billed(self):
        """
        PARTIAL never happens to a walk-in — a part-paid walk-in books the
        shortfall as a discount and is marked PAID. So every PARTIAL here is a
        Fleet card that has been invoiced and is still being collected, and it
        has been billed.
        """
        self._billed(mileage='', payment_status='PARTIAL', paid_date=None)
        page = self._page(self.owner)
        self.assertEqual(self._regs(page), ['KL01AA1111'])
        # …and it must not wear the same green as a closed bill.
        self.assertIn('lr-bill-paid--partial', self._box(page))

    # ── what it says is missing ────────────────────────────────────────

    def test_the_cards_own_boxes_are_chips_with_no_name_over_them(self):
        card = self._billed(mileage='', lead_mechanic=None)
        JobCardLabourItem.objects.create(job_card=card, job_description='Brake service')
        card.labour_amount = Decimal('0')
        card.save()

        box = self._box(self._page(self.owner))

        self.assertIn('<em>Mileage</em>', box)
        self.assertIn('<em>Mechanic</em>', box)
        self.assertIn('<em>Job Amount</em>', box)

    def test_a_parts_only_bill_is_not_nagged_about_labour(self):
        """Zero labour is the CORRECT answer on a card with no work listed on
        it, and saying so on every one of them is how this container would come
        to be scrolled past without being read."""
        self._billed(mileage='', labour_amount=Decimal('0'))
        self.assertNotIn('<em>Job Amount</em>', self._box(self._page(self.owner)))

    def test_an_unfixed_concern_is_named_and_carries_its_status(self):
        card = self._billed()
        JobCardConcern.objects.create(
            job_card=card, concern_text='Brake noise at low speed', status='WORKING')

        box = self._box(self._page(self.owner))

        self.assertIn('Customer Concerns', box)
        self.assertIn('Brake noise at low speed', box)
        self.assertIn('<em>Working</em>', box)

    def test_a_fixed_concern_puts_the_card_on_no_list(self):
        card = self._billed()
        JobCardConcern.objects.create(
            job_card=card, concern_text='Brake noise', status='FIXED')
        self.assertEqual(self._regs(self._page(self.owner)), [])

    def test_a_spare_names_itself_and_every_box_left_empty_on_it(self):
        card = self._billed()
        JobCardSpareItem.objects.create(
            job_card=card, source=SHOP,
            spare_part_name='Wheel Bearing', quantity=Decimal('2'))

        box = self._box(self._page(self.owner))

        self.assertIn('Spare Parts', box)
        self.assertIn('Wheel Bearing', box)
        for chip in ('Shop', 'Dates', 'Shop Price', 'Customer Price'):
            self.assertIn('<em>%s</em>' % chip, box)

    def test_the_two_dates_are_chased_as_ONE_chip(self):
        """
        A spare is finished when it has been ordered AND received, so half
        filled is still incomplete — and which of the two is missing is answered
        by opening the date panel on the job card, not by a second chip here.
        """
        card = self._billed()
        JobCardSpareItem.objects.create(
            job_card=card, source=SHOP,
            spare_part_name='Wheel Bearing', shop=self.shop,
            ordered_date=date(2026, 8, 1), received_date=None,
            unit_price=Decimal('900'), total_price=Decimal('1400'))

        box = self._box(self._page(self.owner))

        self.assertEqual(box.count('<em>Dates</em>'), 1)

    def test_a_warehouse_draw_is_chased_ONLY_for_its_customer_price(self):
        """
        The `source` rule. A draw came off the shelf already fitted: it has no
        shop, no order and no arrival, and its `status` column is meaningless.
        Reporting a problem that cannot exist and cannot be fixed is exactly how
        a checklist teaches people to click past it.
        """
        card = self._billed()
        JobCardSpareItem.objects.create(
            job_card=card, source=INVENTORY,
            item=self.item, spare_part_name='Liqui Moly 5W-30',
            quantity=Decimal('4'), status='PENDING', total_price=None)

        box = self._box(self._page(self.owner))

        self.assertIn('Inventory Items', box)
        self.assertIn('Liqui Moly 5W-30', box)
        self.assertIn('<em>Customer Price</em>', box)
        self.assertNotIn('<em>Shop</em>', box)
        self.assertNotIn('<em>Dates</em>', box)
        self.assertNotIn('<em>Shop Price</em>', box)

    def test_a_priced_draw_puts_the_card_on_no_list(self):
        card = self._billed()
        JobCardSpareItem.objects.create(
            job_card=card, source=INVENTORY,
            item=self.item, spare_part_name='Liqui Moly 5W-30',
            quantity=Decimal('4'), status='PENDING', total_price=Decimal('3200'))
        self.assertEqual(self._regs(self._page(self.owner)), [])

    # ── the shape of the container ─────────────────────────────────────

    def test_every_row_opens_its_job_card(self):
        card = self._billed(mileage='')
        target = reverse('jobcard_edit', args=[card.pk])
        self.assertIn('href="%s?next=mini"' % target, self._box(self._page(self.owner)))

    def test_the_count_beside_the_title_is_the_whole_backlog(self):
        for n in range(3):
            self._billed('KL01A%04d' % n, mileage='')

        page = self._page(self.owner)
        match = re.search(
            r'Billed but not filled</span>\s*<span class="lr-box-count">(\d+)</span>',
            page)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 3)

    def test_the_narrowing_and_the_detail_never_disagree(self):
        """
        The queryset is an index lookup in front of `settlement.unfilled`, not a
        second opinion. Every card the database offers must have something for
        the page to draw under it.
        """
        self._billed('KL01AA1111', mileage='')
        self._billed('KL01BB2222')
        card = self._billed('KL01CC3333')
        JobCardSpareItem.objects.create(
            job_card=card, source=SHOP, spare_part_name='Wheel Bearing')

        self.client.force_login(self.owner)
        context = self.client.get(self.url).context

        self.assertEqual(
            {job.registration_number for job in context['unfilled_cards']},
            {'KL01AA1111', 'KL01CC3333'})
        self.assertEqual(context['unfilled_count'], 2)
        for job in context['unfilled_cards']:
            self.assertTrue(job.unfilled, job.registration_number)

    def test_a_long_section_stops_and_says_how_many_are_left(self):
        """
        A rebuild in the live data carries 91 spares. A cap is safe here for the
        usual reason: no total sits above these rows, the exact remainder is
        printed rather than implied, the section heading still reports the true
        count so the two add back up, and every hidden row is on the job card
        the header already opens.
        """
        from workshop.views.dashboard import UNFILLED_ROW_CAP

        card = self._billed()
        for n in range(UNFILLED_ROW_CAP + 3):
            JobCardSpareItem.objects.create(
                job_card=card, source=SHOP, spare_part_name='Part %d' % n)

        box = self._box(self._page(self.owner))

        self.assertEqual(box.count('class="lr-bill-row"'), UNFILLED_ROW_CAP)
        self.assertIn('+3 more — open the card', box)
        # The heading still reports the TRUE count, so the two add back up.
        self.assertIn('>%d</span>' % (UNFILLED_ROW_CAP + 3), box)
