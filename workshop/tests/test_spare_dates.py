"""
A part cannot arrive before it was ordered.

Two dates on one row, and exactly one mistake the pair can express that neither
date can express alone. Nothing in the schema stops it and both boxes are
independent `<input type="date">`, so "ordered 2026, received 2025" saved
happily and then read as time travel on the shop's ledger and in the printed
history.

The rule lived only in the Unassigned Spares hub. The JOB CARD — where most
spares are actually entered — had no pair check at all, which is the gap these
tests close. `workshop/spare_dates.pair_problem` is the one implementation, and
the last class here is what stops it becoming two.
"""

from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.models import JobCard, JobCardSpareItem, Mechanic, SpareShop
from workshop.spare_dates import pair_problem

SHOP = JobCardSpareItem.SOURCE_SHOP


class ThePairRuleItselfTests(TestCase):
    """Pure, no request — the rule as a function."""

    def setUp(self):
        self.today = timezone.localdate()

    def test_ordered_then_received_is_fine(self):
        self.assertIsNone(
            pair_problem(self.today - timedelta(days=3), self.today))

    def test_same_day_is_fine(self):
        """Plenty of parts are fetched from the shop the same morning."""
        self.assertIsNone(pair_problem(self.today, self.today))

    def test_received_before_ordered_is_refused(self):
        problem = pair_problem(self.today, self.today - timedelta(days=1))
        self.assertIsNotNone(problem)
        self.assertIn('before it was ordered', problem)

    def test_a_year_typed_wrong_is_the_case_that_matters(self):
        """The owner's own example: ordered 2026, received 2025."""
        self.assertIsNotNone(pair_problem(date(2026, 3, 1), date(2025, 3, 1)))

    def test_half_a_pair_is_never_wrong(self):
        """
        A part ordered and not yet arrived is the normal mid-workflow state, and
        a row with neither date is simply unfilled — which the "Billed but not
        filled" container chases separately. Only a pair where BOTH are present
        can be the wrong way round.
        """
        past = self.today - timedelta(days=2)
        self.assertIsNone(pair_problem(past, None))
        self.assertIsNone(pair_problem(None, past))
        self.assertIsNone(pair_problem(None, None))

    def test_a_future_date_is_refused_on_either_box(self):
        """
        Far more often a mistyped year than a plan — this workshop has no
        forward-ordering workflow for the refusal to get in the way of.
        """
        tomorrow = self.today + timedelta(days=1)
        self.assertIn('future', pair_problem(tomorrow, None))
        self.assertIn('future', pair_problem(None, tomorrow))


class TheJobCardRefusesItTests(TestCase):
    """
    The screen the rule was missing from. A spare's two dates live behind one
    chip in the Dates column, and both boxes inside that panel post on every
    save.
    """

    def setUp(self):
        self.office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='off', password='pw')
        self.user.groups.add(self.office)
        self.client = Client()
        self.client.login(username='off', password='pw')

        self.mechanic = Mechanic.objects.create(name='Lead Tech')
        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01A1234')
        self.spare = JobCardSpareItem.objects.create(
            job_card=self.job, source=SHOP, spare_part_name='Wheel Bearing',
            shop=self.shop, status='RECEIVED')

    def payload(self, ordered, received):
        return {
            'registration_number': 'KL01A1234',
            'admitted_date': str(date.today()),
            'brand_name': 'Toyota',
            'model_name': 'Corolla',
            'lead_mechanic': self.mechanic.id,

            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '0', 'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',

            'spares-TOTAL_FORMS': '1', 'spares-INITIAL_FORMS': '1',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'spares-0-id': str(self.spare.pk),
            'spares-0-spare_part_name': 'Wheel Bearing',
            'spares-0-status': 'RECEIVED',
            'spares-0-shop_name': str(self.shop.pk),
            'spares-0-ordered_date': ordered,
            'spares-0-received_date': received,
        }

    def post(self, ordered, received):
        return self.client.post(
            reverse('jobcard_edit', args=[self.job.pk]),
            self.payload(ordered, received))

    def test_a_pair_the_right_way_round_saves(self):
        resp = self.post('2026-07-22', '2026-07-29')
        self.assertEqual(resp.status_code, 302)

        self.spare.refresh_from_db()
        self.assertEqual(self.spare.ordered_date, date(2026, 7, 22))
        self.assertEqual(self.spare.received_date, date(2026, 7, 29))

    def test_time_travel_is_refused_and_nothing_is_written(self):
        resp = self.post('2026-07-29', '2026-07-22')

        self.assertEqual(resp.status_code, 200)   # re-rendered, not redirected
        self.spare.refresh_from_db()
        self.assertIsNone(self.spare.ordered_date)
        self.assertIsNone(self.spare.received_date)

    def test_the_refusal_says_which_part_and_what_is_wrong(self):
        """
        The error summary at the top of the page is the only thing read before
        scrolling, and "Spare 1" means counting rows on a card with eleven of
        them. `ShopSpareRowForm.row_label` names the PART, the same contract
        `InventoryDrawForm` already follows.
        """
        body = self.post('2026-07-29', '2026-07-22').content.decode()

        self.assertIn('Wheel Bearing', body)
        self.assertIn('before it was ordered', body)

    def test_an_unnamed_row_falls_back_to_its_position(self):
        payload = self.payload('2026-07-29', '2026-07-22')
        payload['spares-0-spare_part_name'] = ''
        body = self.client.post(
            reverse('jobcard_edit', args=[self.job.pk]), payload).content.decode()

        self.assertIn('row 1', body)

    def test_a_blank_row_being_deleted_is_not_argued_with(self):
        """
        The form marks rows nobody filled in for deletion, and refusing one of
        those would block a save over a row that is on its way out.
        """
        payload = self.payload('2026-07-29', '2026-07-22')
        payload['spares-0-DELETE'] = 'on'

        resp = self.client.post(reverse('jobcard_edit', args=[self.job.pk]), payload)

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(JobCardSpareItem.objects.filter(pk=self.spare.pk).exists())


class BothScreensAskTheSameQuestionTests(TestCase):
    """
    The Unassigned Spares hub has refused this since it was built; the job card
    now does too. Two answers to "is this pair the right way round" would
    disagree exactly where it matters — on the shop's ledger — so there is one
    implementation and this is what keeps it that way.
    """

    def test_the_hub_reads_the_shared_rule(self):
        import workshop.spare_dates as spare_dates
        import workshop.views.spare_shop as hub

        self.assertIs(hub.pair_problem, spare_dates.pair_problem)

    def test_the_job_card_reads_the_shared_rule(self):
        import workshop.forms as forms
        import workshop.spare_dates as spare_dates

        self.assertIs(forms.pair_problem, spare_dates.pair_problem)

    def test_the_hub_still_refuses_it_too(self):
        from workshop.views.spare_shop import _clean_spare_dates

        _, _, problem = _clean_spare_dates('2026-07-29', '2026-07-22',
                                           blank_is_today=False)
        self.assertIsNotNone(problem)
        self.assertIn('before it was ordered', problem)
