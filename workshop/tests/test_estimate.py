"""
ESTIMATES — the rules a wrong answer would be noticed for.

Three groups, matching the three things that can actually go wrong here:

  * **What prints.** The estimate and the invoice are one family of paper built
    by one module, so the tests that matter are the ones asserting they agree —
    a blank quantity, an unpriced part, labour as one subtotal.
  * **What is isolated.** An estimate touches no job card, no stock, no ledger
    and no report. That is the whole design, and it is worth a test rather than
    a comment, because the day someone wires it up this fails loudly.
  * **What is numbered.** EST- and not JB-, numerically sequenced, unique.

Plus the price hint, which is the one piece of cleverness in the section and is
therefore the piece most worth pinning down: it is a SUGGESTION, it is a
CUSTOMER price, and it never reaches the database.
"""

from decimal import Decimal

from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.invoice import MIN_JOB_ROWS, MIN_PART_ROWS, build_estimate
from workshop.models import (
    Estimate, EstimateJobLine, EstimatePartLine,
    JobCard, JobCardSpareItem, SparePart, SpareShop,
)


def _office_user():
    user = User.objects.create_user(username='office_est', password='pw')
    group, _ = Group.objects.get_or_create(name='Office')
    user.groups.add(group)
    return user


def _estimate(**kwargs):
    defaults = {
        'date': timezone.localdate(),
        'customer_name': 'Mr Nadeem',
        'brand_name': 'Toyota',
        'model_name': 'Corolla',
        'registration_number': 'KL 10 AB 1234',
        'mileage': '82000',
    }
    defaults.update(kwargs)
    return Estimate.objects.create(**defaults)


# =============================================================================
# WHAT PRINTS
# =============================================================================

class TheEstimatePrintsWhatSomebodyTypedTests(TestCase):
    """
    Where the estimate deliberately DIFFERS from the bill — two columns, one
    reason. A bill records work that happened, so a blank box is a fact too
    obvious to type; an estimate describes work that has not, so a blank box is
    something nobody has decided. Filling either in with a computed number would
    put a figure on the page that no one chose.

    These are the assertions someone tidying towards "one rule for both
    documents" would delete. They are the rule.
    """

    def test_a_blank_quantity_prints_blank_but_still_counts_as_one(self):
        est = _estimate()
        EstimatePartLine.objects.create(estimate=est, name='Timing chain kit', amount=Decimal('4400'))

        line = build_estimate(est)['part_lines'][0]
        self.assertIsNone(line.display_quantity, "a QTY nobody typed must print empty")
        self.assertEqual(line.quantity, Decimal('1'), "but the money still treats it as one")
        self.assertEqual(line.amount, Decimal('4400'))

    def test_a_typed_quantity_prints(self):
        est = _estimate()
        EstimatePartLine.objects.create(
            estimate=est, name='Engine Oil', quantity=Decimal('4'), amount=Decimal('4400')
        )
        self.assertEqual(build_estimate(est)['part_lines'][0].display_quantity, Decimal('4'))

    def test_blank_and_typed_quantities_do_NOT_print_the_same(self):
        """
        The exact inverse of the invoice's rule, and deliberately so. On a bill
        blank and 1 are byte-for-byte identical (`test_invoice.py` asserts it);
        here they must be distinguishable, because one is a decision and the
        other is its absence.
        """
        blank = _estimate()
        EstimatePartLine.objects.create(estimate=blank, name='Air Filter', amount=Decimal('900'))

        typed = _estimate(registration_number='KL 10 AB 9999')
        EstimatePartLine.objects.create(
            estimate=typed, name='Air Filter', quantity=Decimal('1'), amount=Decimal('900')
        )

        self.assertNotEqual(
            build_estimate(blank)['part_lines'],
            build_estimate(typed)['part_lines'],
        )

    def test_the_unit_price_is_printed_only_when_a_rate_was_entered(self):
        """
        Never derived. The bill divides the total by the quantity every time,
        which is right there — every billed part has a real quantity. Deriving
        here would present the workshop's own arithmetic as a quoted rate, and
        on a row with no quantity it would divide by a 1 nobody agreed to.
        """
        est = _estimate()
        EstimatePartLine.objects.create(
            estimate=est, name='Total only', quantity=Decimal('4'), amount=Decimal('4400')
        )
        self.assertIsNone(build_estimate(est)['part_lines'][0].unit_price)

    def test_an_entered_rate_prints_and_still_reconciles(self):
        est = _estimate()
        EstimatePartLine.objects.create(
            estimate=est, name='Engine Oil',
            quantity=Decimal('4'), customer_rate=Decimal('1100'),
        )
        line = build_estimate(est)['part_lines'][0]
        self.assertEqual(line.unit_price, Decimal('1100'))
        self.assertEqual(line.unit_price * line.display_quantity, line.amount)


class TheEstimatePrintsLikeTheBillTests(TestCase):
    """
    Everything the two documents DO share. The customer receives the estimate
    first and the invoice after it, so each of these has a twin in
    test_invoice.py; one failing while its twin passes means they have started
    to drift.
    """

    def test_an_unpriced_part_prints_no_figure_but_a_free_one_prints_zero(self):
        """
        `priced` exists so a truthiness check cannot collapse these two. A part
        the workshop still has to ring a supplier about prints an empty cell; a
        part being given away prints ₹0.00.
        """
        est = _estimate()
        EstimatePartLine.objects.create(estimate=est, name='Not costed yet')
        EstimatePartLine.objects.create(estimate=est, name='Goodwill washer', amount=Decimal('0'))

        unpriced, free = build_estimate(est)['part_lines']
        self.assertFalse(unpriced.priced)
        self.assertIsNone(unpriced.unit_price)
        self.assertTrue(free.priced)
        self.assertEqual(free.amount, Decimal('0'))

    def test_labour_prints_as_one_subtotal_with_no_per_line_amounts(self):
        """The workshop quotes work whole. EstimateJobLine has no money column
        at all — this asserts the printed section reflects that."""
        est = _estimate(labour_amount=Decimal('8500'))
        EstimateJobLine.objects.create(estimate=est, description='Timing chain replacement')
        EstimateJobLine.objects.create(estimate=est, description='Engine oil + filter change')

        report = build_estimate(est)
        self.assertEqual(report['job_subtotal'], Decimal('8500'))
        self.assertEqual([line.description for line in report['job_lines']],
                         ['Timing chain replacement', 'Engine oil + filter change'])
        for line in report['job_lines']:
            self.assertFalse(hasattr(line, 'amount'))

    def test_both_tables_pad_to_the_same_minimum_as_the_invoice(self):
        """Padding is what keeps the footer at the same height on every sheet,
        and both documents pad to the same figures by sharing the constants."""
        est = _estimate()
        EstimateJobLine.objects.create(estimate=est, description='One job')
        EstimatePartLine.objects.create(estimate=est, name='One part')

        report = build_estimate(est)
        self.assertEqual(len(report['job_pad']), MIN_JOB_ROWS - 1)
        self.assertEqual(len(report['part_pad']), MIN_PART_ROWS - 1)

    def test_a_long_list_is_never_truncated_by_the_minimum(self):
        est = _estimate()
        for i in range(MIN_PART_ROWS + 4):
            EstimatePartLine.objects.create(estimate=est, name=f'Part {i}')

        report = build_estimate(est)
        self.assertEqual(len(report['part_lines']), MIN_PART_ROWS + 4)
        self.assertEqual(len(report['part_pad']), 0)


class TheEstimateNamesItselfLikeTheBillTests(TestCase):
    """
    `document.title` is the filename a browser suggests when either sheet is
    saved as a PDF. The estimate is handed over first and the invoice follows it
    for the same car, so the two must name themselves by one rule — which is why
    `document_title()` lives in `workshop/invoice.py` beside everything else the
    two documents share.

    The estimate is the harder case: make, model and registration are all
    optional on a quote, so this is where a naive f-string prints "None" or a
    double space into somebody's Downloads folder.
    """

    def test_the_title_is_car_then_estimate_number(self):
        est = _estimate(brand_name='Audi', model_name='A4', registration_number='KL11 AJ 2266')

        self.assertEqual(
            build_estimate(est)['document_title'],
            f"Audi A4 KL11 AJ 2266 ({est.estimate_number})",
        )

    def test_a_quote_with_no_car_details_still_names_itself(self):
        est = _estimate(brand_name='', model_name='', registration_number='')

        self.assertEqual(build_estimate(est)['document_title'], est.estimate_number)

    def test_the_printed_page_carries_it_as_the_title(self):
        self.client.force_login(_office_user())
        est = _estimate(brand_name='Audi', model_name='A4', registration_number='KL11 AJ 2266')

        response = self.client.get(reverse('estimate_print', args=[est.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"<title>Audi A4 KL11 AJ 2266 ({est.estimate_number})</title>",
            response.content.decode(),
        )

    def test_it_is_numbered_EST_not_JB(self):
        """
        The one thing that must never leak across from the invoice's naming: a
        quote is not a bill.
        """
        title = build_estimate(_estimate())['document_title']

        self.assertIn('EST-', title)
        self.assertNotIn('JB-', title)


class TheTotalIsTheStoredColumnTests(TestCase):

    def test_update_totals_is_parts_plus_the_one_labour_figure(self):
        est = _estimate(labour_amount=Decimal('2500'))
        EstimatePartLine.objects.create(estimate=est, name='A', amount=Decimal('1000'))
        EstimatePartLine.objects.create(estimate=est, name='B', amount=Decimal('500'))

        est.update_totals()
        est.refresh_from_db()
        self.assertEqual(est.total_amount, Decimal('4000'))

    def test_an_unpriced_part_contributes_nothing_rather_than_crashing(self):
        est = _estimate(labour_amount=Decimal('1000'))
        EstimatePartLine.objects.create(estimate=est, name='No price yet')

        est.update_totals()
        est.refresh_from_db()
        self.assertEqual(est.total_amount, Decimal('1000'))

    def test_a_rate_times_quantity_requotes_the_line(self):
        """Editing 7 L down to 4 L must requote, not leave a stale figure —
        the same rule as JobCardSpareItem.customer_rate."""
        est = _estimate()
        part = EstimatePartLine.objects.create(
            estimate=est, name='Engine Oil', quantity=Decimal('7'), customer_rate=Decimal('1100')
        )
        self.assertEqual(part.amount, Decimal('7700.00'))

        part.quantity = Decimal('4')
        part.save()
        self.assertEqual(part.amount, Decimal('4400.00'))


# =============================================================================
# WHAT IS ISOLATED
# =============================================================================

class AnEstimateIsConnectedToNothingTests(TestCase):
    """
    The isolation is the design (see the Estimate model). These assert it in the
    two directions that would actually cost money if they were ever wired up.
    """

    def setUp(self):
        self.user = _office_user()
        self.client.force_login(self.user)

    def test_writing_an_estimate_creates_no_job_card_and_no_spare(self):
        est = _estimate(labour_amount=Decimal('5000'))
        EstimatePartLine.objects.create(estimate=est, name='Brake Pad Set', amount=Decimal('3200'))
        est.update_totals()

        self.assertEqual(JobCard.objects.count(), 0)
        self.assertEqual(JobCardSpareItem.objects.count(), 0)

    def test_quoting_a_stock_product_moves_no_warehouse_stock(self):
        """
        The part name is free text and matches nothing by design. A quote for a
        part the workshop has on the shelf must not deduct it — nothing has
        physically been taken.
        """
        from inventory.models import Category, Item

        category = Category.objects.create(name='Engine Oil')
        item = Item.objects.create(
            category=category, name='Castrol Edge 5W-30',
            current_stock=Decimal('20'), average_stock=Decimal('20'),
        )

        est = _estimate()
        EstimatePartLine.objects.create(
            estimate=est, name='Castrol Edge 5W-30',
            quantity=Decimal('5'), amount=Decimal('5500'),
        )

        item.refresh_from_db()
        self.assertEqual(item.current_stock, Decimal('20'))

    def test_deleting_an_estimate_removes_only_its_own_lines(self):
        est = _estimate()
        EstimateJobLine.objects.create(estimate=est, description='Job')
        EstimatePartLine.objects.create(estimate=est, name='Part', amount=Decimal('100'))
        other = _estimate(registration_number='KL 10 ZZ 0001')
        EstimatePartLine.objects.create(estimate=other, name='Untouched', amount=Decimal('50'))

        self.client.post(reverse('estimate_delete', args=[est.pk]))

        self.assertFalse(Estimate.objects.filter(pk=est.pk).exists())
        self.assertEqual(EstimateJobLine.objects.count(), 0)
        self.assertEqual(EstimatePartLine.objects.count(), 1)
        self.assertTrue(Estimate.objects.filter(pk=other.pk).exists())

    def test_deleting_an_estimate_writes_no_deletion_log(self):
        """
        Deliberate, and the one place this section departs from the app's
        deletion model. `DeletionLog.record()` also raises the CRITICAL
        RECORD_DELETED notification, which pushes to both owners' phones — and
        an estimate is a draft expected to be rewritten and discarded. See
        `estimate_delete` for the full reasoning. If this test starts failing,
        someone has wired estimates into Deletion History and owners are now
        being buzzed for housekeeping.
        """
        from workshop.models import DeletionLog

        est = _estimate()
        self.client.post(reverse('estimate_delete', args=[est.pk]))
        self.assertEqual(DeletionLog.objects.count(), 0)


# =============================================================================
# NUMBERING
# =============================================================================

class EstimateNumberingTests(TestCase):

    def test_the_number_is_est_and_never_jb(self):
        """A quotation and a bill must not be confusable in the workshop's own
        books — the prefix is the only thing that distinguishes them at a glance."""
        est = _estimate(date=timezone.localdate().replace(year=2026, month=8, day=5))
        self.assertTrue(est.estimate_number.startswith('EST-26-'))
        self.assertNotIn('JB', est.estimate_number)

    def test_numbers_increment_within_a_year(self):
        day = timezone.localdate().replace(year=2026, month=8, day=5)
        first = _estimate(date=day)
        second = _estimate(date=day, registration_number='KL 10 CD 2222')
        self.assertEqual(first.estimate_number, 'EST-26-001')
        self.assertEqual(second.estimate_number, 'EST-26-002')

    def test_the_sequence_is_numeric_not_lexicographic(self):
        """
        A CharField sorts 'EST-26-999' above 'EST-26-1000', which is how the job
        card's numbering once looped back past 999 and collided on its unique
        constraint. Seeding 999 directly and asking for the next one is the
        cheapest way to prove this counts rather than sorts.
        """
        day = timezone.localdate().replace(year=2026, month=8, day=5)
        _estimate(date=day, estimate_number='EST-26-999')
        self.assertEqual(_estimate(date=day).estimate_number, 'EST-26-1000')

    def test_a_junk_suffix_does_not_stop_the_next_number(self):
        day = timezone.localdate().replace(year=2026, month=8, day=5)
        _estimate(date=day, estimate_number='EST-26-DRAFT')
        self.assertEqual(_estimate(date=day).estimate_number, 'EST-26-001')

    def test_the_registration_number_is_normalised_like_a_job_card(self):
        est = _estimate(registration_number='  kl 10 ab 1234 ', brand_name='  toyota ')
        self.assertEqual(est.registration_number, 'KL 10 AB 1234')
        self.assertEqual(est.brand_name, 'Toyota')

    def test_the_model_name_is_not_title_cased(self):
        """'i20' must not become 'I20' and 'CR-V' must not become 'Cr-V' — the
        same reason JobCard.clean leaves model_name alone."""
        self.assertEqual(_estimate(model_name='  i20  ').model_name, 'i20')
        self.assertEqual(_estimate(model_name='CR-V', registration_number='KL 1 A 1').model_name, 'CR-V')


# =============================================================================
# THE PRICE HINT
# =============================================================================

class ThePriceHintIsASuggestionTests(TestCase):
    """
    The one piece of cleverness in this section, and therefore the one most
    worth pinning down. It suggests what a part last sold for, in the Unit Price
    box's placeholder — it must be a CUSTOMER price, it must come from real
    sales, and it must never reach the database.
    """

    def setUp(self):
        self.user = _office_user()
        self.client.force_login(self.user)
        self.url = reverse('spare_price_hint')
        self.shop = SpareShop.objects.create(name='Al Ameen Spares')

    def _sale(self, name, total, quantity=None, cost=None):
        """One past sale of a part: what the customer paid, and what it cost."""
        job = JobCard.objects.create(
            admitted_date=timezone.localdate(),
            brand_name='Toyota', model_name='Corolla',
            registration_number=f'KL 10 XX {JobCard.objects.count():04d}',
        )
        return JobCardSpareItem.objects.create(
            job_card=job, spare_part_name=name, shop=self.shop,
            source=JobCardSpareItem.SOURCE_SHOP,
            quantity=quantity, unit_price=cost, total_price=total,
        )

    def test_the_hint_is_the_customer_price_never_the_cost(self):
        """
        `JobCardSpareItem.unit_price` is what the workshop PAID. Suggesting it
        would quote every part at cost — the single most expensive way this
        feature could be wrong.
        """
        self._sale('Air Filter', total=Decimal('1200'), quantity=Decimal('1'), cost=Decimal('700'))

        data = self.client.get(self.url, {'name': 'Air Filter'}).json()
        self.assertTrue(data['found'])
        self.assertEqual(data['average'], '1200.00')

    def test_it_averages_the_last_five_and_ignores_older_sales(self):
        # Oldest first: the ₹5,000 sale has the LOWEST pk, so the five-row
        # window starting from the newest leaves it out. If the ordering ever
        # flips to oldest-first this reads 1080.00 and fails, which is the
        # point — a stale price is worse than no suggestion.
        for total in ('5000', '100', '100', '100', '100', '100'):
            self._sale('Brake Pad', total=Decimal(total), quantity=Decimal('1'))

        data = self.client.get(self.url, {'name': 'Brake Pad'}).json()
        self.assertEqual(data['average'], '100.00')
        self.assertEqual(data['count'], 5)

    def test_fewer_than_five_sales_averages_what_exists(self):
        self._sale('Wiper Blade', total=Decimal('600'), quantity=Decimal('1'))
        self._sale('Wiper Blade', total=Decimal('800'), quantity=Decimal('1'))

        data = self.client.get(self.url, {'name': 'Wiper Blade'}).json()
        self.assertEqual(data['average'], '700.00')
        self.assertEqual(data['count'], 2)

    def test_a_blank_quantity_is_read_as_one_like_everywhere_else(self):
        """Same `effective_quantity` the printed documents use, so the
        suggestion means what the printed UNIT PRICE column means."""
        self._sale('Oil Filter', total=Decimal('450'), quantity=None)

        data = self.client.get(self.url, {'name': 'Oil Filter'}).json()
        self.assertEqual(data['average'], '450.00')

    def test_the_per_unit_price_is_derived_from_the_quantity(self):
        self._sale('Engine Oil', total=Decimal('4400'), quantity=Decimal('4'))

        data = self.client.get(self.url, {'name': 'Engine Oil'}).json()
        self.assertEqual(data['average'], '1100.00')

    def test_the_lookup_ignores_case(self):
        self._sale('Air Filter', total=Decimal('1200'), quantity=Decimal('1'))
        self.assertTrue(self.client.get(self.url, {'name': 'air FILTER'}).json()['found'])

    def test_a_part_never_sold_returns_not_found_rather_than_zero(self):
        """Zero would fill the placeholder with a price of nothing. 'No history'
        and 'it is free' are different answers."""
        data = self.client.get(self.url, {'name': 'Never Sold Before'}).json()
        self.assertFalse(data['found'])
        self.assertNotIn('average', data)

    def test_unpriced_past_rows_are_skipped_not_counted_as_zero(self):
        self._sale('Radiator Cap', total=None, quantity=Decimal('1'))
        self._sale('Radiator Cap', total=Decimal('300'), quantity=Decimal('1'))

        data = self.client.get(self.url, {'name': 'Radiator Cap'}).json()
        self.assertEqual(data['average'], '300.00')
        self.assertEqual(data['count'], 1)

    def test_past_estimates_never_feed_the_hint(self):
        """
        Only what was actually charged counts. Letting estimates feed each other
        would let one optimistic quote drift the suggestion upward forever with
        nothing real underneath it.
        """
        est = _estimate()
        EstimatePartLine.objects.create(
            estimate=est, name='Fuel Pump', quantity=Decimal('1'), amount=Decimal('9999')
        )
        self.assertFalse(self.client.get(self.url, {'name': 'Fuel Pump'}).json()['found'])

    def test_an_empty_name_asks_nothing_of_the_database(self):
        self.assertFalse(self.client.get(self.url, {'name': '   '}).json()['found'])
        self.assertFalse(self.client.get(self.url).json()['found'])

    def test_floor_cannot_read_prices_here(self):
        """Floor is shown no prices anywhere else in the app, which is why this
        endpoint is @office_required and not @staff_required like its
        neighbours in the same module."""
        floor = User.objects.create_user(username='floor_est', password='pw')
        floor.groups.add(Group.objects.get_or_create(name='Floor')[0])
        self.client.force_login(floor)

        self.assertEqual(self.client.get(self.url, {'name': 'Air Filter'}).status_code, 403)


# =============================================================================
# THE SCREENS
# =============================================================================

class EstimateScreensTests(TestCase):

    def setUp(self):
        self.user = _office_user()
        self.client.force_login(self.user)
        SparePart.objects.create(name='Air Filter')

    def _payload(self, **overrides):
        data = {
            'date': timezone.localdate().isoformat(),
            'customer_name': 'Mr Nadeem',
            'customer_contact': '9876543210',
            'brand_name': 'Toyota',
            'model_name': 'Corolla',
            'registration_number': 'KL 10 AB 1234',
            'mileage': '82000',
            'labour_amount': '8500',
            'notes': '',

            'jobs-TOTAL_FORMS': '2',
            'jobs-INITIAL_FORMS': '0',
            'jobs-MIN_NUM_FORMS': '0',
            'jobs-MAX_NUM_FORMS': '1000',
            'jobs-0-description': 'Timing chain replacement',
            'jobs-1-description': '',

            'parts-TOTAL_FORMS': '2',
            'parts-INITIAL_FORMS': '0',
            'parts-MIN_NUM_FORMS': '0',
            'parts-MAX_NUM_FORMS': '1000',
            'parts-0-name': 'Timing chain kit',
            'parts-0-quantity': '1',
            'parts-0-customer_rate': '',
            'parts-0-amount': '14000',
            'parts-1-name': '',
            'parts-1-quantity': '',
            'parts-1-customer_rate': '',
            'parts-1-amount': '',
        }
        data.update(overrides)
        return data

    def test_creating_an_estimate_saves_its_lines_and_its_total(self):
        response = self.client.post(reverse('estimate_create'), self._payload())

        est = Estimate.objects.get()
        self.assertRedirects(response, reverse('estimate_print', args=[est.pk]))
        self.assertEqual(est.job_lines.count(), 1)
        self.assertEqual(est.parts.count(), 1)
        self.assertEqual(est.total_amount, Decimal('22500'))
        self.assertEqual(est.created_by, self.user)

    def test_blank_rows_are_not_saved_as_empty_lines(self):
        self.client.post(reverse('estimate_create'), self._payload())
        est = Estimate.objects.get()
        self.assertEqual(EstimateJobLine.objects.filter(estimate=est).count(), 1)
        self.assertEqual(EstimatePartLine.objects.filter(estimate=est).count(), 1)

    def test_a_priced_row_with_no_name_is_refused(self):
        """It would print an amount beside a blank line and inflate the total by
        something the customer cannot identify."""
        response = self.client.post(reverse('estimate_create'), self._payload(**{
            'parts-1-amount': '2000',
        }))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Estimate.objects.count(), 0)

    def test_an_empty_labour_box_is_zero_not_an_error(self):
        """Plenty of estimates are parts only; required would refuse to save
        one, and the column is NOT NULL so cleaning to None would be an
        IntegrityError rather than a message."""
        self.client.post(reverse('estimate_create'), self._payload(labour_amount=''))
        est = Estimate.objects.get()
        self.assertEqual(est.labour_amount, Decimal('0'))
        self.assertEqual(est.total_amount, Decimal('14000'))

    def test_a_negative_labour_charge_is_refused_not_clamped(self):
        response = self.client.post(reverse('estimate_create'), self._payload(labour_amount='-500'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Estimate.objects.count(), 0)

    def test_editing_recomputes_the_stored_total(self):
        self.client.post(reverse('estimate_create'), self._payload())
        est = Estimate.objects.get()
        job_line = est.job_lines.get()
        part_line = est.parts.get()

        self.client.post(reverse('estimate_edit', args=[est.pk]), self._payload(**{
            'labour_amount': '1000',
            'jobs-INITIAL_FORMS': '1',
            'jobs-0-id': str(job_line.pk),
            'parts-INITIAL_FORMS': '1',
            'parts-0-id': str(part_line.pk),
            'parts-0-amount': '500',
        }))

        est.refresh_from_db()
        self.assertEqual(est.total_amount, Decimal('1500'))

    def test_the_print_page_renders_the_document(self):
        est = _estimate(labour_amount=Decimal('8500'))
        EstimateJobLine.objects.create(estimate=est, description='Timing chain replacement')
        EstimatePartLine.objects.create(estimate=est, name='Timing chain kit', amount=Decimal('14000'))
        est.update_totals()

        response = self.client.get(reverse('estimate_print', args=[est.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('ESTIMATE', content)
        self.assertIn('JOB NEEDS TO BE PERFORMED', content)
        self.assertIn('Timing chain kit', content)
        self.assertIn(est.estimate_number, content)

    def test_nothing_interactive_lives_on_the_paper(self):
        """
        The workshop's controls sit OUTSIDE the .sheet element, not merely
        hidden in print — a CSS-only rule is one stylesheet edit away from
        printing a button on a customer's quotation. Same assertion the invoice
        carries.
        """
        est = _estimate()
        content = self.client.get(reverse('estimate_print', args=[est.pk])).content.decode()

        start = content.index('<div class="sheet" id="sheet">')
        end = content.index('</div>\n    </div>', start)
        sheet = content[start:end]

        for tag in ('<button', '<a ', '<form', '<input', '<script', '<dialog'):
            self.assertNotIn(tag, sheet, f"{tag} found inside the printed sheet")

    def test_no_list_row_puts_a_button_inside_a_link(self):
        """
        An <a> may not contain interactive content, and a browser does not
        forgive it quietly: the parser closes the anchor and reopens it around
        whatever follows. Wrapping a row in <a> with the ⋮ dropdown inside it
        turned ONE estimate into FOUR anchor elements — three of them empty —
        and split the CSS grid row into four grid containers. Django renders the
        markup verbatim, so nothing server-side notices; only a parser does.

        The row now uses a `.stretched-link` inside a <div> instead. This
        asserts the invariant rather than the implementation, so any future row
        layout is held to it too.
        """
        from html.parser import HTMLParser

        _estimate()

        class NestedInteractiveFinder(HTMLParser):
            INTERACTIVE = {'button', 'a', 'input', 'select', 'textarea'}

            def __init__(self):
                super().__init__()
                self.depth = 0          # how many <a> elements we are inside
                self.offences = []

            def handle_starttag(self, tag, attrs):
                if self.depth and tag in self.INTERACTIVE:
                    self.offences.append(tag)
                if tag == 'a':
                    self.depth += 1

            def handle_endtag(self, tag):
                if tag == 'a' and self.depth:
                    self.depth -= 1

        parser = NestedInteractiveFinder()
        parser.feed(self.client.get(reverse('estimate_list'), {'filter': 'all'}).content.decode())
        self.assertEqual(
            parser.offences, [],
            f"interactive elements nested inside an <a>: {parser.offences} — "
            f"a browser will split that anchor and break the row"
        )

    def test_the_history_lists_estimates_and_finds_them_by_registration(self):
        _estimate(registration_number='KL 10 AB 1234')
        _estimate(registration_number='KL 55 ZZ 9999', customer_name='Someone Else')

        page = self.client.get(reverse('estimate_list'), {'filter': 'all'}).content.decode()
        self.assertIn('KL 10 AB 1234', page)
        self.assertIn('KL 55 ZZ 9999', page)

        filtered = self.client.get(
            reverse('estimate_list'), {'filter': 'all', 'q': 'KL 55'}
        ).content.decode()
        self.assertIn('KL 55 ZZ 9999', filtered)
        self.assertNotIn('KL 10 AB 1234', filtered)

    def test_floor_cannot_reach_any_estimate_screen(self):
        floor = User.objects.create_user(username='floor_screens', password='pw')
        floor.groups.add(Group.objects.get_or_create(name='Floor')[0])
        self.client.force_login(floor)

        est = _estimate()
        for url in (
            reverse('estimate_list'),
            reverse('estimate_create'),
            reverse('estimate_print', args=[est.pk]),
            reverse('estimate_edit', args=[est.pk]),
            reverse('estimate_delete', args=[est.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_the_delete_confirmation_does_not_delete_on_a_get(self):
        est = _estimate()
        self.client.get(reverse('estimate_delete', args=[est.pk]))
        self.assertTrue(Estimate.objects.filter(pk=est.pk).exists())

    def test_the_header_puts_the_action_beside_the_title_not_under_it(self):
        """
        Title + New Estimate share one row at every width, with the description
        below them — the mobile rules used to switch that row to a column, which
        gave a phone a full-width button on a line of its own and pushed the
        first card below the fold. Asserted on the markup because the ordering
        is what the CSS depends on: the subtitle must be OUTSIDE `.est-header-top`.
        """
        page = self.client.get(reverse('estimate_list')).content.decode()
        # Anchor on the MARKUP, not the first mention of the class — that one is
        # in the <style> block and slicing from it captures the whole stylesheet.
        start = page.index('<div class="est-header-top">')
        header = page[start:page.index('</div>', page.index('est-new-btn', start))]

        self.assertIn('est-page-title', header)
        self.assertIn('est-new-btn', header)
        self.assertNotIn('est-page-sub', header,
                         "the description belongs below the header row, not inside it")
        # The row must never become a column — that is what stacked the button.
        # Scoped to rules for THIS element: base.html uses column flex all over
        # the place for the drawer, so a document-wide search is meaningless.
        import re
        for rule in re.findall(r'\.est-header-top\s*\{[^}]*\}', page):
            self.assertNotIn('column', rule,
                             "the header row was switched back to a column")

    def test_the_count_reads_total_first(self):
        _estimate()
        page = self.client.get(reverse('estimate_list')).content.decode()
        self.assertIn('TOTAL 1', page)
        self.assertNotIn('1 total', page)

    def test_the_count_wording_survives_a_live_search(self):
        """The pill is rewritten by JS after a search, so the two places that
        produce it have to agree — otherwise it silently reverts to the old
        wording the moment someone types."""
        _estimate(registration_number='KL 10 AB 1234')
        page = self.client.get(reverse('estimate_list')).content.decode()
        self.assertIn("'TOTAL ' + freshCount.getAttribute('data-total-count')", page)

        partial = self.client.get(
            reverse('estimate_list'), {'q': 'KL'},
            headers={'x-requested-with': 'XMLHttpRequest'},
        ).content.decode()
        self.assertIn('data-total-count="1"', partial)

    def test_only_two_date_filters_are_offered(self):
        """
        Estimates are written a handful of times a month and looked up months
        later, so six of the day-to-day vocabulary's eight options would return
        an empty page most of the time — which reads as a broken screen, not an
        empty period.
        """
        page = self.client.get(reverse('estimate_list')).content.decode()
        self.assertIn('data-filter="this_year"', page)
        self.assertIn('data-filter="all"', page)
        for gone in ('today', 'this_week', 'this_month', 'last_week', 'last_month', 'last_year'):
            self.assertNotIn(f'data-filter="{gone}"', page)

    def test_an_unknown_filter_falls_back_instead_of_showing_everything(self):
        """`?filter=last_month` is a stale bookmark, not a request for all
        time — silently widening the window would be the wrong answer."""
        _estimate()
        response = self.client.get(reverse('estimate_list'), {'filter': 'last_month'})
        self.assertEqual(response.context['filter_type'], 'this_year')

    def test_search_returns_only_the_rows_to_an_ajax_request(self):
        """Live search replaces the rows, never the page — otherwise the search
        box loses its focus and its caret on every keystroke."""
        _estimate(registration_number='KL 10 AB 1234')
        response = self.client.get(
            reverse('estimate_list'), {'q': 'KL 10', 'filter': 'all'},
            headers={'x-requested-with': 'XMLHttpRequest'},
        )
        body = response.content.decode()
        self.assertIn('KL 10 AB 1234', body)
        self.assertNotIn('<html', body)
        self.assertNotIn('estimateSearch', body)


class TheFormNeverArguesWithABlankRowTests(TestCase):
    """
    Everything on an estimate is optional, so a row left empty — or emptied
    out — has to be a row that does not exist, not a validation error. The
    columns are still NOT NULL; a blank row is deleted rather than written empty.
    """

    def setUp(self):
        self.user = _office_user()
        self.client.force_login(self.user)

    def _payload(self, **overrides):
        data = {
            'date': timezone.localdate().isoformat(),
            'customer_name': '', 'customer_contact': '',
            'brand_name': '', 'model_name': '',
            'registration_number': '', 'mileage': '',
            'labour_amount': '', 'notes': '',
            'jobs-TOTAL_FORMS': '2', 'jobs-INITIAL_FORMS': '0',
            'jobs-MIN_NUM_FORMS': '0', 'jobs-MAX_NUM_FORMS': '1000',
            'jobs-0-description': '', 'jobs-1-description': '',
            'parts-TOTAL_FORMS': '2', 'parts-INITIAL_FORMS': '0',
            'parts-MIN_NUM_FORMS': '0', 'parts-MAX_NUM_FORMS': '1000',
            'parts-0-name': '', 'parts-0-quantity': '',
            'parts-0-customer_rate': '', 'parts-0-amount': '',
            'parts-1-name': '', 'parts-1-quantity': '',
            'parts-1-customer_rate': '', 'parts-1-amount': '',
        }
        data.update(overrides)
        return data

    def test_a_completely_empty_estimate_saves(self):
        """Office opens the screen and saves to reserve a number. Nothing is
        required, so nothing should stop that."""
        response = self.client.post(reverse('estimate_create'), self._payload())
        est = Estimate.objects.get()
        self.assertRedirects(response, reverse('estimate_print', args=[est.pk]))
        self.assertEqual(est.job_lines.count(), 0)
        self.assertEqual(est.parts.count(), 0)
        self.assertEqual(est.total_amount, Decimal('0'))

    def test_a_row_of_whitespace_is_not_saved_as_a_line(self):
        self.client.post(reverse('estimate_create'), self._payload(**{
            'jobs-0-description': '   ',
            'parts-0-name': '  ',
        }))
        est = Estimate.objects.get()
        self.assertEqual(est.job_lines.count(), 0)
        self.assertEqual(est.parts.count(), 0)

    def test_clearing_an_existing_line_removes_it_instead_of_erroring(self):
        """Clearing the text is how a line is deleted. Being told 'This field
        is required' for changing your mind is the form arguing back."""
        est = _estimate()
        job = EstimateJobLine.objects.create(estimate=est, description='Timing chain')
        part = EstimatePartLine.objects.create(estimate=est, name='Chain kit', amount=Decimal('14000'))
        est.update_totals()

        response = self.client.post(reverse('estimate_edit', args=[est.pk]), self._payload(**{
            'registration_number': est.registration_number,
            'jobs-INITIAL_FORMS': '1', 'jobs-0-id': str(job.pk),
            'parts-INITIAL_FORMS': '1', 'parts-0-id': str(part.pk),
        }))

        self.assertEqual(response.status_code, 302)
        est.refresh_from_db()
        self.assertEqual(est.job_lines.count(), 0)
        self.assertEqual(est.parts.count(), 0)
        self.assertEqual(est.total_amount, Decimal('0'))

    def test_a_priced_NEW_row_with_no_name_is_still_refused(self):
        """
        A new row is being filled in, so a missing name there is a slip, not an
        erasure — dropping it silently would throw away a price someone just
        typed. (A STORED row is the opposite case; see the next test.)
        """
        response = self.client.post(reverse('estimate_create'), self._payload(**{
            'parts-0-amount': '2000',
        }))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Estimate.objects.count(), 0)

    def test_clearing_the_name_deletes_a_PRICED_stored_line(self):
        """
        There is no ✕ on a row — clearing the name and saving IS the delete
        gesture. It therefore has to work on a row that still holds figures,
        which is exactly the kind people want to remove. Refusing here would
        make the only delete there is fail on most of its targets.
        """
        est = _estimate()
        part = EstimatePartLine.objects.create(
            estimate=est, name='Chain kit', quantity=Decimal('1'), amount=Decimal('14000')
        )
        est.update_totals()

        response = self.client.post(reverse('estimate_edit', args=[est.pk]), self._payload(**{
            'registration_number': est.registration_number,
            'parts-INITIAL_FORMS': '1',
            'parts-0-id': str(part.pk),
            'parts-0-name': '',                 # cleared
            'parts-0-quantity': '1',            # figures deliberately left behind
            'parts-0-amount': '14000',
        }))

        self.assertEqual(response.status_code, 302)
        est.refresh_from_db()
        self.assertEqual(est.parts.count(), 0)
        self.assertEqual(est.total_amount, Decimal('0'))

    def test_the_form_offers_no_per_row_delete_control(self):
        """A ✕ beside every line is a one-tap way to lose work on a tablet.
        Removing a line is clearing its name."""
        est = _estimate()
        EstimatePartLine.objects.create(estimate=est, name='Chain kit', amount=Decimal('100'))
        page = self.client.get(reverse('estimate_edit', args=[est.pk])).content.decode()
        self.assertNotIn('data-remove-row', page)


class TheEstimateCarriesTheCarsColourTests(TestCase):
    """
    Same picker and same palette as a Job Card, drawn as the stripe down each
    history row — the identity cue staff use to find a car before reading a word
    of the text. It is NOT printed on the quotation; the customer knows what
    colour their own car is.
    """

    def setUp(self):
        self.user = _office_user()
        self.client.force_login(self.user)

    def test_the_estimate_and_the_job_card_agree_on_every_colour(self):
        """One palette in models.py. Two copies would let a Grey job card and a
        Grey estimate print different greys — invisible until they are side by
        side, and then obviously wrong."""
        from workshop.models import CAR_COLOR_CHOICES

        for value, _label in CAR_COLOR_CHOICES:
            job = JobCard(admitted_date=timezone.localdate(), brand_name='T',
                          model_name='C', registration_number='KL 1 A 1', car_color=value)
            est = Estimate(date=timezone.localdate(), car_color=value)
            self.assertEqual(job.get_car_color_hex, est.get_car_color_hex, value)
            self.assertEqual(job.get_car_color_display, est.get_car_color_display, value)

    def test_no_colour_recorded_is_not_the_same_as_a_grey_car(self):
        est = _estimate(car_color=None)
        grey = _estimate(car_color='Grey', registration_number='KL 9 Z 9')
        self.assertNotEqual(est.get_car_color_hex, grey.get_car_color_hex)
        self.assertEqual(est.get_car_color_display, 'Unknown')

    def test_an_other_colour_keeps_the_hex_that_was_picked(self):
        est = _estimate(car_color='Other', car_color_other='#ff8800')
        self.assertEqual(est.get_car_color_hex, '#ff8800')

    def test_the_colour_is_saved_from_the_form(self):
        self.client.post(reverse('estimate_create'), {
            'date': timezone.localdate().isoformat(),
            'registration_number': 'KL 10 AB 1234',
            'car_color': 'Red', 'car_color_other': '',
            'labour_amount': '',
            'jobs-TOTAL_FORMS': '0', 'jobs-INITIAL_FORMS': '0',
            'jobs-MIN_NUM_FORMS': '0', 'jobs-MAX_NUM_FORMS': '1000',
            'parts-TOTAL_FORMS': '0', 'parts-INITIAL_FORMS': '0',
            'parts-MIN_NUM_FORMS': '0', 'parts-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(Estimate.objects.get().car_color, 'Red')

    def test_the_history_row_carries_the_colour_stripe(self):
        _estimate(car_color='Red')
        page = self.client.get(reverse('estimate_list')).content.decode()
        self.assertIn('est-stripe', page)
        self.assertIn('--stripe-color: #dc2626', page)

    def test_a_colourless_row_is_hatched_rather_than_painted_grey(self):
        _estimate(car_color=None)
        page = self.client.get(reverse('estimate_list')).content.decode()
        self.assertIn('est-stripe--unset', page)

    def test_the_colour_never_reaches_the_printed_quotation(self):
        """The customer knows what colour their car is; the sheet is for the
        work and the money."""
        est = _estimate(car_color='Red')
        sheet = self.client.get(reverse('estimate_print', args=[est.pk])).content.decode()
        start = sheet.index('<div class="sheet" id="sheet">')
        end = sheet.index('</div>\n    </div>', start)
        self.assertNotIn('#dc2626', sheet[start:end])

    def test_both_screens_use_the_one_picker(self):
        """A second copy of ~100 lines of markup, CSS and JS would be free to
        drift from this one."""
        est = _estimate()
        job = JobCard.objects.create(
            admitted_date=timezone.localdate(), brand_name='Toyota',
            model_name='Corolla', registration_number='KL 22 CC 2222',
        )
        for url in (reverse('estimate_edit', args=[est.pk]),
                    reverse('jobcard_edit', args=[job.pk])):
            page = self.client.get(url).content.decode()
            self.assertEqual(page.count('id="color-picker-trigger"'), 1, url)
            self.assertEqual(page.count('id="native-color-picker"'), 1, url)
            self.assertIn('color-mini-grid', page, url)


class MoneyBoxesDoNotFightTheTypistTests(TestCase):
    """
    A money box that arrives holding `0` turns the first keystroke into `08500`,
    and one holding `8500.00` puts two zeros and a point between the caret and
    the next digit. Display only — nothing is stored differently.
    """

    def setUp(self):
        self.user = _office_user()
        self.client.force_login(self.user)

    def _labour_value(self, response):
        import re
        match = re.search(r'name="labour_amount"[^>]*value="([^"]*)"', response.content.decode())
        return match.group(1) if match else None

    def test_a_new_estimate_opens_with_an_empty_labour_box_not_zero(self):
        response = self.client.get(reverse('estimate_create'))
        self.assertIn(self._labour_value(response), (None, ''))

    def test_a_whole_rupee_figure_renders_without_paise(self):
        est = _estimate(labour_amount=Decimal('8500.00'))
        response = self.client.get(reverse('estimate_edit', args=[est.pk]))
        self.assertEqual(self._labour_value(response), '8500')

    def test_real_paise_are_kept(self):
        """Dropping these would change the number, not tidy it."""
        est = _estimate(labour_amount=Decimal('1250.50'))
        response = self.client.get(reverse('estimate_edit', args=[est.pk]))
        self.assertEqual(self._labour_value(response), '1250.50')

    def test_a_rejected_post_shows_back_exactly_what_was_typed(self):
        """`BoundField.value()` reads submitted data, not initial — a rejected
        form must not reformat someone's half-finished input under them."""
        response = self.client.post(reverse('estimate_create'), {
            'date': timezone.localdate().isoformat(),
            'labour_amount': '-5',
            'jobs-TOTAL_FORMS': '0', 'jobs-INITIAL_FORMS': '0',
            'jobs-MIN_NUM_FORMS': '0', 'jobs-MAX_NUM_FORMS': '1000',
            'parts-TOTAL_FORMS': '0', 'parts-INITIAL_FORMS': '0',
            'parts-MIN_NUM_FORMS': '0', 'parts-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._labour_value(response), '-5')

    def test_quantities_and_rates_are_tidied_too(self):
        est = _estimate()
        EstimatePartLine.objects.create(
            estimate=est, name='Engine Oil',
            quantity=Decimal('7.00'), customer_rate=Decimal('550.00'),
        )
        body = self.client.get(reverse('estimate_edit', args=[est.pk])).content.decode()
        self.assertIn('value="7"', body)
        self.assertIn('value="550"', body)
        self.assertNotIn('value="7.00"', body)
        self.assertNotIn('value="550.00"', body)


class TheManageButtonLightsUpForEverySectionBehindItTests(TestCase):
    """
    The top bar's Manage pill highlights while the current page lives behind the
    drawer. It used to be a chain of ten `{% if %}` comparisons in base.html and
    had quietly fallen two sections behind — Salary & Advance and Estimates were
    both in the drawer and missing from the highlight, so Manage read as
    inactive on pages reachable only through it. A missing entry in a ten-clause
    boolean is invisible; this makes it a test failure.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='owner_nav', password='pw', is_superuser=True)
        self.user.groups.add(Group.objects.get_or_create(name='Owner')[0])
        self.client.force_login(self.user)

    def test_every_drawer_destination_lights_the_manage_button(self):
        import re
        from workshop.templatetags.custom_filters import is_drawer_section

        page = self.client.get(reverse('estimate_list')).content.decode()
        drawer = page.split('id="appDrawer"', 1)[-1]
        hrefs = set(re.findall(r'<a class="drawer-link[^"]*"\s+href="([^"]+)"', drawer))

        self.assertGreater(len(hrefs), 5, "drawer links not found — has base.html changed shape?")
        for href in hrefs:
            self.assertTrue(
                is_drawer_section(href),
                f"{href} is in the drawer but missing from DRAWER_SECTION_PREFIXES, "
                f"so Manage will not highlight on it",
            )

    def test_the_estimates_page_marks_manage_as_open(self):
        page = self.client.get(reverse('estimate_list')).content.decode()
        self.assertIn('nav-btn nav-btn--menu is-open', page)

    def test_a_page_outside_the_drawer_leaves_manage_alone(self):
        page = self.client.get(reverse('jobcard_create')).content.decode()
        self.assertNotIn('nav-btn nav-btn--menu is-open', page)

    def test_car_profiles_is_listed_before_estimates(self):
        """Car Profiles is the everyday lookup; Estimates is occasional. The
        order is the owner's, and a reshuffle should be deliberate."""
        page = self.client.get(reverse('estimate_list')).content.decode()
        drawer = page.split('id="appDrawer"', 1)[-1]
        self.assertLess(drawer.index('/car-profiles/'), drawer.index('/estimates/'))
