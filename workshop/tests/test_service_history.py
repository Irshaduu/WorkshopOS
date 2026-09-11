"""
A car's whole life at this workshop, as one document.

The third customer document, and the first that describes a CAR rather than a
visit. It exists because customers ask for their full record — most often when
selling the car — and today that means opening every job card, printing each
bill and working out the intervals by hand.

What these tests protect is the arithmetic BETWEEN the rows, because that is
the part a customer cannot check against anything. An invoice proves what one
visit cost; nothing proves that '(2) Wheel Bearing — 12,800 km' is right except
this module. So the weight is on the joins: the chain that follows one part
across four years, the gap between two visits, the reading a customer gives
over the phone, and every case where one end of a subtraction is missing.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from inventory.models import Category, Item
from workshop.models import (
    JobCard, JobCardConcern, JobCardLabourItem, JobCardSpareItem, SpareShop,
)
from workshop.service_history import (
    build_service_history, current_km_problem, part_key, span_label,
)


class ServiceHistoryTestCase(TestCase):
    """One car, and helpers to give it a past."""

    REG = 'KL 10 AA 1000'

    def setUp(self):
        self.shop = SpareShop.objects.create(name='Pullara Spares')
        self.category = Category.objects.create(name='Engine Oil')
        self.product = Item.objects.create(
            category=self.category, name='Castrol Edge 5W-30',
            average_stock=Decimal('40'), current_stock=Decimal('500'),
            avg_cost=Decimal('420'),
        )

    def _visit(self, admitted, mileage=None, parts=(), completed=True, **kwargs):
        defaults = dict(
            admitted_date=admitted,
            brand_name='Audi', model_name='A4',
            registration_number=self.REG,
            mileage=mileage,
            completed=completed,
            completed_date=admitted if completed else None,
        )
        defaults.update(kwargs)
        card = JobCard.objects.create(**defaults)
        for name in parts:
            JobCardSpareItem.objects.create(
                job_card=card, spare_part_name=name,
                source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
                quantity=Decimal('1'), total_price=Decimal('1000'),
            )
        return card

    def _build(self, current_km=None):
        """Exactly what the view does: resolve, prefetch, hand over."""
        cards = (
            JobCard.objects
            .filter(registration_number=self.REG)
            .prefetch_related('labours', 'concerns', 'spares__item__category')
        )
        return build_service_history(list(cards), current_km=current_km)

    def _chain(self, key, current_km=None):
        for chain in self._build(current_km=current_km)['chains']:
            if chain.key == key:
                return chain
        self.fail(f'no chain for {key!r}')


class TheOrderOnThePageTests(ServiceHistoryTestCase):

    def test_the_newest_visit_comes_first(self):
        """
        The owner's instruction, and it is what the sheet is read for: the
        visit somebody is ringing up about is nearly always the last one.
        """
        self._visit(date(2024, 1, 10), '60000')
        self._visit(date(2025, 3, 4), '75000')
        self._visit(date(2026, 5, 1), '90000')

        visits = self._build()['visits']
        self.assertEqual(
            [v.date for v in visits],
            [date(2026, 5, 1), date(2025, 3, 4), date(2024, 1, 10)],
        )

    def test_the_numbers_still_count_from_the_OLDEST(self):
        """
        Printed newest first, but VISIT 1 is the first time the car came here.
        A number that counted down the page would change meaning the moment
        another visit happened.
        """
        self._visit(date(2024, 1, 10), '60000')
        self._visit(date(2025, 3, 4), '75000')
        self._visit(date(2026, 5, 1), '90000')
        self.assertEqual([v.number for v in self._build()['visits']], [3, 2, 1])

    def test_a_same_day_tie_is_broken_so_the_order_is_stable(self):
        """
        `admitted_date` is a DateField and cars share dates. Without the pk
        tiebreak the order inside a day is whatever the database returns, which
        differs between PostgreSQL and SQLite — so the document would not be
        stable between production and these tests.
        """
        first = self._visit(date(2026, 4, 1), '80000')
        second = self._visit(date(2026, 4, 1), '80100')
        self.assertEqual(
            [v.bill_number for v in self._build()['visits']],
            [second.bill_number, first.bill_number],
        )


class WhichVisitsAppearTests(ServiceHistoryTestCase):

    def test_a_car_still_on_the_floor_is_counted_but_not_listed(self):
        """
        Its parts are still being added and its total is not final, so listing
        it would put a figure on a customer's document that changes after they
        were handed it. It is REPORTED, because a customer whose car is in the
        workshop today must not read a history that silently omits it.
        """
        self._visit(date(2026, 1, 10), '60000')
        self._visit(date(2026, 6, 1), '70000', completed=False)

        history = self._build()
        self.assertEqual(len(history['visits']), 1)
        self.assertEqual(history['summary'].in_progress, 1)

    def test_a_deleted_card_is_out_of_everything(self):
        self._visit(date(2026, 1, 10), '60000')
        self._visit(date(2026, 2, 10), '65000', is_deleted=True)
        self._visit(date(2026, 3, 10), '70000', completed=False, is_deleted=True)

        history = self._build()
        self.assertEqual(len(history['visits']), 1)
        self.assertEqual(history['summary'].in_progress, 0)


class TheGapBetweenTwoVisitsTests(ServiceHistoryTestCase):
    """What the join between two cards prints."""

    def test_a_visit_carries_the_gap_since_the_one_before_it(self):
        self._visit(date(2026, 1, 1), '60000')
        self._visit(date(2026, 4, 11), '69800')

        newest, oldest = self._build()['visits']
        self.assertEqual(newest.gap_km, 9800)
        self.assertEqual(newest.gap_days, 100)
        # Nothing came before the first visit, so it offers no join at all —
        # a zero there would be a claim.
        self.assertIsNone(oldest.gap_km)
        self.assertIsNone(oldest.gap_days)

    def test_BOTH_figures_measure_from_the_IMMEDIATELY_previous_visit(self):
        """
        ⚠ ONE ANCHOR. The tempting alternative is to reach back past a visit
        with no reading to the last one that had one — which makes the two
        halves of one join describe two different baselines. Here the newest
        visit's distance is simply absent rather than quietly reporting the
        distance since two visits ago.
        """
        self._visit(date(2026, 1, 1), '60000')
        self._visit(date(2026, 3, 1), None)
        self._visit(date(2026, 5, 1), '70000')

        newest = self._build()['visits'][0]
        self.assertIsNone(newest.gap_km)
        self.assertEqual(newest.gap_days, 61)

    def test_an_unreadable_reading_is_still_printed(self):
        """
        'cluster not working' is a fact about that visit. Blanking it would
        read as nobody having recorded one.
        """
        self._visit(date(2026, 1, 1), 'odometer replaced')
        visit, = self._build()['visits']
        self.assertIsNone(visit.reading)
        self.assertEqual(visit.reading_text, 'odometer replaced')


class AnOdometerThatWentBackwardsTests(ServiceHistoryTestCase):
    """
    A cluster swap, a replaced odometer, or a mistyped digit — the one thing on
    this document a buyer most wants flagged, and the one thing a naive
    subtraction reports as a negative distance.
    """

    def test_a_lower_reading_is_flagged_and_offers_no_distance(self):
        self._visit(date(2026, 1, 1), '90000')
        self._visit(date(2026, 6, 1), '40000')

        newest = self._build()['visits'][0]
        self.assertTrue(newest.reading_dropped)
        self.assertIsNone(newest.gap_km)
        self.assertEqual(newest.gap_days, 151)

    def test_an_unchanged_reading_is_not_a_drop(self):
        """
        A car brought straight back for something missed. Zero kilometres is a
        real interval, and marking it would be crying wolf.
        """
        self._visit(date(2026, 1, 1), '90000')
        self._visit(date(2026, 1, 3), '90000')

        newest = self._build()['visits'][0]
        self.assertFalse(newest.reading_dropped)
        self.assertEqual(newest.gap_km, 0)

    def test_a_slipped_digit_is_marked_but_the_figure_still_stands(self):
        """
        The second odometer guard. `mileage.MAX_KM` refuses garbage; it cannot
        refuse 850,000, which is plausible alone and only wrong beside the
        85,000 before it. Unlike a drop this MIGHT be true, so the arithmetic
        stands and the sheet only marks it.
        """
        self._visit(date(2026, 1, 1), '85000')
        self._visit(date(2026, 3, 1), '850000')

        newest = self._build()['visits'][0]
        self.assertTrue(newest.rate_implausible)
        self.assertEqual(newest.gap_km, 765000)

    def test_hard_use_and_a_long_absence_are_never_marked(self):
        """
        300 km a day for two months is a lot and is real; a car serviced
        elsewhere for four years comes back with a big honest jump. A false
        flag on a customer's own document is worse than a missed one.
        """
        self._visit(date(2026, 1, 1), '60000')
        self._visit(date(2026, 3, 2), '78000')          # 18,000 over 60 days
        self.assertFalse(self._build()['visits'][0].rate_implausible)

        JobCard.objects.all().delete()
        self._visit(date(2022, 1, 1), '60000')
        self._visit(date(2026, 1, 1), '160000')
        self.assertFalse(self._build()['visits'][0].rate_implausible)


class FollowingAPartAcrossVisitsTests(ServiceHistoryTestCase):
    """
    ⚠ THE FEATURE THIS DOCUMENT EXISTS FOR, and the one thing no other screen
    in the app does. Three job cards each list a wheel bearing and nothing
    joins them up; here they are one numbered chain carrying the distance each
    one covered.
    """

    def _wheel_bearing_history(self):
        """The owner's own worked example, to the kilometre."""
        self._visit(date(2022, 2, 3), '94200', ['Wheel Bearing - Left'])
        self._visit(date(2024, 5, 9), '107200', ['Wheel Bearing - Left'])
        self._visit(date(2026, 8, 7), '120000', ['Wheel Bearing - Left'])

    def test_the_owners_worked_example(self):
        """
        Fitted at 94,200 / 1,07,200 / 1,20,000, and the customer says the car
        reads 1,30,000 today.

            (3) 10,000 km  RUNNING
            (2) 12,800 km
            (1) 13,000 km
        """
        self._wheel_bearing_history()
        chain = self._chain('wheel bearing - left', current_km=130000)

        self.assertEqual(
            [(i.number, i.life_km, i.running) for i in chain.instances],
            [(3, 10000, True), (2, 12800, False), (1, 13000, False)],
        )

    def test_number_one_is_the_first_time_this_car_ever_had_the_part(self):
        self._wheel_bearing_history()
        chain = self._chain('wheel bearing - left')
        self.assertEqual(chain.instances[-1].number, 1)
        self.assertEqual(chain.instances[-1].fitted_date, date(2022, 2, 3))

    def test_with_no_reading_from_the_customer_it_measures_to_the_last_visit(self):
        """
        Which on a part fitted at that very visit is nought — honest, and
        exactly what the owner's first block shows before the phone call.
        """
        self._wheel_bearing_history()
        chain = self._chain('wheel bearing - left')
        self.assertEqual(chain.instances[0].life_km, 0)
        self.assertTrue(chain.instances[0].running)

    def test_only_the_newest_fitting_is_running(self):
        self._wheel_bearing_history()
        chain = self._chain('wheel bearing - left')
        self.assertEqual([i.running for i in chain.instances], [True, False, False])

    def test_one_chain_survives_three_spellings_of_the_same_part(self):
        """
        Free text on the shop side, typed by different people over four years.
        An en dash from the master list, a hyphen from a keyboard and a
        lower-case entry are one part, or the chain is three chains each
        looking complete and each wrong.
        """
        self._visit(date(2022, 2, 3), '94200', ['Wheel Bearing - Left'])
        self._visit(date(2024, 5, 9), '107200', ['Wheel Bearing – Left'])
        self._visit(date(2026, 8, 7), '120000', ['wheel bearing - left'])
        self.assertEqual(len(self._chain('wheel bearing - left').instances), 3)

    def test_the_chain_prints_ONE_spelling_and_it_is_the_commonest(self):
        """
        Left alone each instance prints the spelling it happened to be typed
        with, so a chain of three reads as three different parts under one
        number sequence. The newest was the tempting rule and is wrong: a part
        name is typed fresh every visit, so the most recent is as likely to be
        the typo as the fix.
        """
        self._visit(date(2022, 2, 3), '94200', ['Wheel Bearing - Left'])
        self._visit(date(2024, 5, 9), '107200', ['Wheel Bearing - Left'])
        self._visit(date(2026, 8, 7), '120000', ['wheel bearing - left'])

        chain = self._chain('wheel bearing - left')
        self.assertEqual(
            {i.name for i in chain.instances}, {'Wheel Bearing - Left'})

    def test_a_warehouse_draw_and_a_shop_purchase_share_one_chain(self):
        """
        The customer's question is "when was the engine oil last changed", not
        "which shelf did it come off". Both routes arrive already named by
        `part_display_name`, which is what makes them comparable.
        """
        card = self._visit(date(2024, 1, 1), '60000')
        JobCardSpareItem.objects.create(
            job_card=card, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.product, quantity=Decimal('5'),
            customer_rate=Decimal('1200'),
        )
        self._visit(date(2026, 1, 1), '80000', ['Engine Oil'])

        chain = self._chain('engine oil')
        self.assertEqual(len(chain.instances), 2)
        self.assertEqual(chain.instances[-1].life_km, 20000)

    def test_a_part_fitted_once_is_a_chain_of_one_and_is_running(self):
        self._visit(date(2024, 1, 1), '60000', ['Radiator'])
        self._visit(date(2026, 1, 1), '80000')

        chain = self._chain('radiator')
        self.assertEqual(len(chain.instances), 1)
        self.assertTrue(chain.instances[0].running)
        self.assertEqual(chain.instances[0].life_km, 20000)

    def test_a_missing_reading_at_either_end_costs_that_life_and_no_other(self):
        self._visit(date(2022, 1, 1), '60000', ['Oil Filter'])
        self._visit(date(2023, 1, 1), None, ['Oil Filter'])
        self._visit(date(2024, 1, 1), '80000', ['Oil Filter'])
        self._visit(date(2025, 1, 1), '90000', ['Oil Filter'])

        lives = [i.life_km for i in self._chain('oil filter').instances]
        # newest first: running(0), 80k->90k, ?->80k, 60k->?
        self.assertEqual(lives, [0, 10000, None, None])

    def test_an_odometer_that_dropped_gives_no_life_rather_than_a_negative(self):
        self._visit(date(2024, 1, 1), '90000', ['Oil Filter'])
        self._visit(date(2026, 1, 1), '40000', ['Oil Filter'])
        self.assertIsNone(self._chain('oil filter').instances[-1].life_km)

    def test_the_parts_on_a_visit_are_the_chain_instances_for_that_visit(self):
        """
        The card shows the same numbered instance the summary does — one
        object, so the two sections cannot disagree about which fitting this
        was.
        """
        self._wheel_bearing_history()
        history = self._build(current_km=130000)
        newest_visit_part = history['visits'][0].parts[0]
        self.assertEqual(newest_visit_part.number, 3)
        self.assertEqual(newest_visit_part.life_km, 10000)
        self.assertTrue(newest_visit_part.running)


class HowLongItUsuallyLastsTests(ServiceHistoryTestCase):
    """
    The only forward-looking line on the sheet, and it is built entirely from
    this car's own completed lives. This system holds no manufacturer
    intervals, and inventing one would be the document asserting something
    nobody at this workshop agreed.
    """

    def _oil_history(self):
        for admitted, km in (
            (date(2022, 1, 1), '60000'), (date(2023, 1, 1), '70000'),
            (date(2024, 1, 1), '80000'), (date(2025, 1, 1), '90000'),
        ):
            self._visit(admitted, km, ['Engine Oil'])

    def test_the_average_is_over_completed_lives_only(self):
        """
        Three completed lives of 10,000 km each. The running one is still
        accumulating, so including it would drag the average down and make a
        part look shorter-lived the longer it survives.
        """
        self._oil_history()
        chain = self._chain('engine oil', current_km=92000)
        self.assertEqual(chain.typical_km, 10000)
        self.assertEqual(chain.sample_count, 3)

    def test_it_says_so_once_the_fitted_one_is_near_that_distance(self):
        self._oil_history()
        # 9,000 of a usual 10,000 — at the 90% mark exactly.
        self.assertTrue(self._chain('engine oil', current_km=99000).due_soon)

    def test_it_stays_quiet_while_the_part_is_young(self):
        self._oil_history()
        self.assertFalse(self._chain('engine oil', current_km=93000).due_soon)

    def test_a_part_with_no_completed_life_says_nothing(self):
        """
        The first time a part is fitted there is nothing to compare against,
        and a prediction from one data point is not a prediction.
        """
        self._visit(date(2024, 1, 1), '60000', ['Radiator'])
        chain = self._chain('radiator', current_km=200000)
        self.assertIsNone(chain.typical_km)
        self.assertFalse(chain.due_soon)

    def test_the_visit_card_and_the_summary_agree(self):
        """
        Both read the same PartInstance, so the inline note on the card and the
        section at the end cannot come to say different things.
        """
        self._oil_history()
        history = self._build(current_km=99000)
        on_card = history['visits'][0].parts[0]
        in_summary = self._chain('engine oil', current_km=99000).instances[0]
        self.assertEqual(on_card.due_soon, in_summary.due_soon)
        self.assertEqual(on_card.typical_km, in_summary.typical_km)


class TheReadingTheCustomerGivesTests(ServiceHistoryTestCase):
    """
    The one figure on the sheet the workshop did not measure. It arrives over
    the phone, it is never stored, and it moves exactly one class of number.
    """

    def test_it_only_moves_the_running_figures(self):
        self._visit(date(2024, 1, 1), '60000', ['Oil Filter'])
        self._visit(date(2026, 1, 1), '80000', ['Oil Filter'])

        without = self._build()
        with_km = self._build(current_km=95000)

        self.assertEqual(without['chains'][0].instances[0].life_km, 0)
        self.assertEqual(with_km['chains'][0].instances[0].life_km, 15000)
        # Completed lives and every visit gap are untouched.
        self.assertEqual(without['chains'][0].instances[1].life_km,
                         with_km['chains'][0].instances[1].life_km)
        self.assertEqual(without['visits'][0].gap_km, with_km['visits'][0].gap_km)

    def test_a_reading_below_the_last_visit_is_refused_with_a_reason(self):
        problem = current_km_problem(90000, 120000)
        self.assertIsNotNone(problem)
        self.assertIn('1,20,000', problem)
        self.assertIn('90,000', problem)

    def test_no_reading_is_never_a_problem(self):
        self.assertIsNone(current_km_problem(None, 120000))

    def test_a_car_with_no_recorded_reading_accepts_anything(self):
        """Nothing to contradict, so nothing to refuse."""
        self.assertIsNone(current_km_problem(90000, None))

    def test_a_crafted_url_cannot_poison_every_running_figure(self):
        """
        The options page shows the message; the sheet is reachable without it,
        so an impossible reading is DROPPED there rather than clamped. One bad
        number would otherwise make every RUNNING row on the page wrong at
        once.
        """
        self._visit(date(2024, 1, 1), '60000', ['Oil Filter'])
        self._visit(date(2026, 1, 1), '80000', ['Oil Filter'])

        history = self._build(current_km=10)
        self.assertIsNone(history['summary'].current_km)
        self.assertEqual(history['summary'].reference_km, 80000)
        self.assertEqual(history['chains'][0].instances[0].life_km, 0)


class WhatEachVisitCarriesTests(ServiceHistoryTestCase):

    def test_the_concerns_and_the_work_as_typed(self):
        card = self._visit(date(2026, 1, 1), '60000')
        JobCardConcern.objects.create(
            job_card=card, concern_text='Noise from front left wheel')
        JobCardLabourItem.objects.create(
            job_card=card, job_description='Wheel bearing replaced')

        visit = self._build()['visits'][0]
        self.assertEqual(visit.concerns, ('Noise from front left wheel',))
        self.assertEqual(visit.jobs, ('Wheel bearing replaced',))

    def test_a_warehouse_draw_is_named_by_its_CATEGORY(self):
        """
        `Item.name` is the branded SKU the workshop buys; `Category.name` is
        what the part is. Naming the brand on a document the workshop hands out
        publishes its supply chain — the invoice's rule, imported rather than
        restated.
        """
        card = self._visit(date(2026, 1, 1), '60000')
        JobCardSpareItem.objects.create(
            job_card=card, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.product, quantity=Decimal('5'),
            customer_rate=Decimal('1200'),
        )
        part, = self._build()['visits'][0].parts
        self.assertEqual(part.name, 'Engine Oil')
        self.assertNotIn('Castrol', part.name)

    def test_a_quantity_of_one_prints_nothing_and_more_than_one_prints(self):
        """
        The invoice's rule: this workshop writes a quantity down only when
        there is more than one of something, so 'Oil Filter x1' here beside
        'Oil Filter' on the bill would read as two records of one job.
        """
        card = self._visit(date(2026, 1, 1), '60000', ['Oil Filter'])
        JobCardSpareItem.objects.create(
            job_card=card, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.product, quantity=Decimal('5.5'),
            customer_rate=Decimal('1200'),
        )
        by_name = {p.name: p for p in self._build()['visits'][0].parts}
        self.assertIsNone(by_name['Oil Filter'].quantity)
        self.assertEqual(by_name['Engine Oil'].quantity, Decimal('5.5'))


class TheAmountIsWhatTheInvoiceSaidTests(ServiceHistoryTestCase):
    """
    ⚠ The most important class in this file.

    A customer reads this beside the invoices it summarises. If one visit's
    figure disagrees with the paper they were handed, nothing else on the
    document is believed either.
    """

    def _settled(self, admitted, billed, discount, received):
        card = self._visit(admitted, '60000', labour_amount=Decimal(billed))
        card.update_totals()
        card.discount_amount = Decimal(discount)
        card.received_amount = Decimal(received)
        card.payment_status = 'PAID'
        card.save()
        return card

    def test_a_part_paid_walk_in_prints_what_it_was_BILLED(self):
        """
        The workshop's own rule: a part-paid walk-in is marked PAID with the
        shortfall booked as a discount. So revenue (₹500) and the printed bill
        (₹600) differ on exactly the visits a customer is most likely to check.
        The invoice said ₹600, and so does this.
        """
        self._settled(date(2026, 1, 1), '600', '100', '500')
        visit = self._build()['visits'][0]
        self.assertEqual(visit.amount, Decimal('600'))
        # ...and the ₹100 it was given travels beside it, by the owners'
        # decision, so the sheet can print what the invoice alone does not.
        self.assertEqual(visit.discount, Decimal('100'))

    def test_the_receipt_and_the_payment_state_never_reach_the_sheet(self):
        """
        ⚠ THE DISCOUNT WAS ON THIS LIST UNTIL 2026-09-11 and came off it by
        the owners' decision: Formula D discounts every customer on purpose and
        wants it seen. What a discount is NOT is payment state — it is what
        the workshop took off the bill. What was received, and whether anything
        is still owed, stay off this document: it records work, not debt.
        """
        self._settled(date(2026, 1, 1), '600', '100', '500')
        visit = self._build()['visits'][0]
        for banned in ('received', 'payment_status', 'balance', 'paid'):
            self.assertFalse(
                hasattr(visit, banned),
                f"A visit must not carry {banned!r} — this document records "
                f"work, not debt.",
            )

    def test_a_discount_is_never_negative(self):
        """
        Floored at zero, so a mistyped negative could never ADD to a bill on a
        document handed to a buyer.
        """
        card = self._settled(date(2026, 1, 1), '600', '0', '600')
        type(card).objects.filter(pk=card.pk).update(discount_amount=Decimal('-50'))
        self.assertEqual(self._build()['visits'][0].discount, Decimal('0'))

    def test_the_lifetime_total_is_the_sum_of_the_cards(self):
        self._settled(date(2026, 1, 1), '600', '100', '500')
        self._settled(date(2026, 6, 1), '2400', '0', '2400')

        history = self._build()
        summary = history['summary']
        self.assertEqual(summary.total_billed, Decimal('3000'))
        self.assertEqual(
            summary.total_billed,
            sum(v.amount for v in history['visits']),
        )

        # The discount and the net close the sheet, so both have to add up from
        # the same rows as well.
        self.assertEqual(summary.total_discount, Decimal('100'))
        self.assertEqual(
            summary.total_discount,
            sum(v.discount for v in history['visits']),
        )
        self.assertEqual(summary.net_total, Decimal('2900'))
        self.assertEqual(summary.net_total,
                         summary.total_billed - summary.total_discount)


class TheCarOverItsWholeLifeTests(ServiceHistoryTestCase):

    def test_the_distance_is_between_the_first_and_last_READINGS(self):
        """
        Not between the first and last visits — a history whose middle visits
        have no reading still spans exactly the two that do.
        """
        self._visit(date(2024, 1, 1), '60000')
        self._visit(date(2025, 1, 1), None)
        self._visit(date(2026, 1, 1), '92000')

        summary = self._build()['summary']
        self.assertEqual(summary.first_reading, 60000)
        self.assertEqual(summary.latest_reading, 92000)
        self.assertEqual(summary.distance, 32000)

    def test_one_reading_is_not_a_distance(self):
        self._visit(date(2026, 1, 1), '60000')
        self.assertIsNone(self._build()['summary'].distance)

    def test_a_replaced_odometer_reports_no_distance_rather_than_a_negative(self):
        self._visit(date(2024, 1, 1), '90000')
        self._visit(date(2026, 1, 1), '40000')
        self.assertIsNone(self._build()['summary'].distance)

    def test_how_hard_the_car_is_used(self):
        self._visit(date(2025, 1, 1), '60000')
        self._visit(date(2026, 1, 1), '72000')          # 12,000 over 12 months
        self.assertEqual(self._build()['summary'].km_per_month, 1000)

    def test_a_short_history_reports_no_usage_figure(self):
        """Two months of history describes one trip, not a habit."""
        self._visit(date(2026, 1, 1), '60000')
        self._visit(date(2026, 2, 1), '61000')
        self.assertIsNone(self._build()['summary'].km_per_month)

    def test_the_span_and_the_dates(self):
        self._visit(date(2022, 2, 3), '20000')
        self._visit(date(2026, 4, 5), '95000')

        summary = self._build()['summary']
        self.assertEqual(summary.visits, 2)
        self.assertEqual(summary.first_date, date(2022, 2, 3))
        self.assertEqual(summary.last_date, date(2026, 4, 5))
        self.assertEqual(summary.span_label, '4 years 2 months')


class HowRegularlyTheCarIsServicedTests(ServiceHistoryTestCase):
    """
    The buyer's own question, and the one thing a stack of invoices cannot
    answer without doing arithmetic on the kitchen table.

    Averaged over the GAPS, so N visits give N-1 of them — the same
    distinction `Chain.typical_km` records as "between changes, never over N
    changes".
    """

    def test_it_averages_the_gaps_between_visits(self):
        self._visit(date(2024, 1, 1), '60000')
        self._visit(date(2024, 7, 1), '70000')          # +10,000 over 182 days
        self._visit(date(2025, 1, 1), '82000')          # +12,000 over 184 days

        summary = self._build()['summary']
        self.assertEqual(summary.service_every_km, 11000)
        self.assertEqual(summary.service_every_days, 183)

    def test_one_visit_says_nothing_about_regularity(self):
        """A car with no gap has no answer, and inventing one would be a
        claim about a pattern of exactly one event."""
        self._visit(date(2026, 1, 1), '60000')

        summary = self._build()['summary']
        self.assertIsNone(summary.service_every_km)
        self.assertIsNone(summary.service_every_days)

    def test_an_impossible_gap_is_kept_out_of_the_distance(self):
        """
        ⚠ 85,000 typed as 850,000 is the case this exists for. One of those in
        a mean of three moves it by more than every real gap put together, and
        the figure lands on a document a buyer is checking.
        """
        self._visit(date(2024, 1, 1), '60000')
        self._visit(date(2024, 7, 1), '70000')          # +10,000, believable
        self._visit(date(2024, 7, 10), '700000')        # +630,000 in 9 days

        summary = self._build()['summary']
        self.assertEqual(summary.service_every_km, 10000)

    def test_but_its_DAYS_still_count(self):
        """
        The asymmetry is the point. A mistyped odometer says nothing about the
        two admission dates either side of it, so dropping them would discard a
        good figure over a fault in a different column.
        """
        self._visit(date(2024, 1, 1), '60000')
        self._visit(date(2024, 7, 1), '70000')          # 182 days
        self._visit(date(2024, 7, 10), '700000')        # 9 days

        self.assertEqual(self._build()['summary'].service_every_days, 96)

    def test_a_visit_with_no_reading_costs_the_distance_and_not_the_days(self):
        """
        `gap_km` needs a reading at BOTH ends and `gap_days` needs neither, so
        a blank odometer thins one average and leaves the other whole — which
        is the same rule as the mistyped one, reached from the other side.
        """
        self._visit(date(2024, 1, 1), '60000')
        self._visit(date(2024, 7, 1), None)             # 182 days, no reading
        self._visit(date(2025, 1, 1), '82000')          # 184 days, no reading

        summary = self._build()['summary']
        self.assertIsNone(summary.service_every_km)
        self.assertEqual(summary.service_every_days, 183)


class APartKeyJoinsWhatIsTheSamePartTests(TestCase):

    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(part_key('  Engine   OIL '), part_key('engine oil'))

    def test_the_three_dashes_are_one_dash(self):
        self.assertEqual(part_key('Brake Pads – Front'),
                         part_key('Brake Pads - Front'))
        self.assertEqual(part_key('Brake Pads—Front'),
                         part_key('Brake Pads - Front'))

    def test_different_parts_stay_different(self):
        self.assertNotEqual(part_key('Brake Pads - Front'),
                            part_key('Brake Pads - Rear'))

    def test_nothing_is_not_a_part(self):
        self.assertEqual(part_key(None), '')


class ASpanIsWrittenOutInWordsTests(TestCase):
    """
    Left to a template this reads '1 years 1 months' on the one car whose
    history is exactly that long.
    """

    def test_years_and_months(self):
        self.assertEqual(
            span_label(date(2022, 2, 3), date(2026, 4, 5)), '4 years 2 months')

    def test_the_singular_of_each(self):
        self.assertEqual(
            span_label(date(2025, 1, 1), date(2026, 2, 1)), '1 year 1 month')

    def test_a_whole_number_of_years_says_nothing_about_months(self):
        self.assertEqual(span_label(date(2024, 1, 1), date(2026, 1, 1)), '2 years')

    def test_under_a_year_is_months_and_under_a_month_is_days(self):
        self.assertEqual(span_label(date(2026, 1, 1), date(2026, 6, 1)), '5 months')
        self.assertEqual(span_label(date(2026, 1, 1), date(2026, 1, 10)), '9 days')
        self.assertEqual(span_label(date(2026, 1, 1), date(2026, 1, 2)), '1 day')

    def test_the_day_of_the_month_has_to_come_round(self):
        """3 Feb to 2 Mar is nought months, the way a person counts."""
        self.assertEqual(span_label(date(2026, 2, 3), date(2026, 3, 2)), '27 days')

    def test_no_span_says_nothing_rather_than_zero(self):
        self.assertEqual(span_label(date(2026, 1, 1), date(2026, 1, 1)), '')
        self.assertEqual(span_label(None, date(2026, 1, 1)), '')


class AHistoryWithNothingInItTests(ServiceHistoryTestCase):
    """
    A customer can perfectly well ask for their record while their car is in
    the workshop for the first time. Nothing here may raise.
    """

    def test_a_first_visit_still_on_the_floor(self):
        self._visit(date(2026, 6, 1), '70000', completed=False)

        history = self._build()
        summary = history['summary']
        self.assertEqual(history['visits'], ())
        self.assertEqual(history['chains'], ())
        self.assertEqual(summary.visits, 0)
        self.assertEqual(summary.in_progress, 1)
        self.assertEqual(summary.total_billed, Decimal('0'))
        self.assertEqual(summary.total_discount, Decimal('0'))
        self.assertEqual(summary.net_total, Decimal('0'))
        self.assertIsNone(summary.first_date)
        self.assertEqual(summary.span_label, '')

    def test_the_document_is_still_named_after_the_car(self):
        self._visit(date(2026, 6, 1), '70000', completed=False)
        title = self._build()['document_title']
        self.assertIn('Audi A4', title)
        self.assertIn('Service History', title)

    def test_no_cards_at_all_does_not_raise(self):
        history = build_service_history([])
        self.assertEqual(history['visits'], ())
        self.assertEqual(history['chains'], ())
        self.assertEqual(history['document_title'], 'Service History')


class TheSavedPdfIsNamedForTheCarTests(ServiceHistoryTestCase):
    """
    `document.title` becomes the filename, so this lands in a customer's folder
    beside their invoices under the same naming — the reason `document_title`
    is imported rather than reimplemented.
    """

    def test_it_reads_like_an_invoice_from_the_same_folder(self):
        self._visit(date(2026, 1, 1), '60000')
        self.assertEqual(
            self._build()['document_title'],
            'Audi A4 KL 10 AA 1000 (Service History)',
        )

    def test_a_later_correction_to_the_car_is_what_reaches_the_customer(self):
        self._visit(date(2024, 1, 1), '60000', model_name='A3')
        self._visit(date(2026, 1, 1), '90000', model_name='A4')
        self.assertIn('Audi A4', self._build()['document_title'])
