"""
The chassis code and the VIN (2026-09, the owners' request).

Two optional boxes on the Job Card and the Estimate, and every rule about them
lives in `workshop/vehicle_ids.py`. What these tests pin, in the order a car
meets them:

  * the rules — tidied, refused with a reason, never corrected, no check digit;
  * the forms — a VIN typed in groups survives the browser, both forms agree;
  * the Job Card — the row sits under the plate, the red hairline stays, Floor
    may record both;
  * the searches — all six screens that search cars find one by either;
  * the Car Profile — the latest RECORDED value, and a search that no longer
    shrinks a car's visit count;
  * (the lookup that fills them from a known plate is `test_known_car.py`);
  * what must NOT happen — printed on a customer document, chased at settlement.

The lookup script and the hairline are JavaScript, which nothing in this suite
executes, so those are pinned by the contract the script relies on.
"""
import re
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop import settlement
from workshop.forms import EstimateForm, JobCardForm
from workshop.models import Estimate, JobCard, Mechanic
from workshop.vehicle_ids import (
    VIN_LENGTH, latest_recorded, normalise_chassis_code, normalise_vin, vin_problem,
)

# A Mercedes-shaped VIN. Its ninth character is not a valid North American
# check digit, and that is the point: European makers do not have to use one.
EURO_VIN = 'WDD2050041F123456'
BMW_VIN = 'WBA8E9C50GK123456'


def _input_tag(html, name):
    match = re.search(r'<input[^>]*name="%s"[^>]*>' % re.escape(name), html)
    return match.group(0) if match else ''


class TheRulesTests(SimpleTestCase):

    def test_a_chassis_code_loses_its_spaces_and_its_case(self):
        self.assertEqual(normalise_chassis_code(' f 30 '), 'F30')
        self.assertEqual(normalise_chassis_code('w205'), 'W205')

    def test_a_chassis_code_keeps_the_characters_real_codes_carry(self):
        """Porsche writes 991.2, Audi 8W — a chassis code is never refused
        for its shape."""
        self.assertEqual(normalise_chassis_code('991.2'), '991.2')
        self.assertEqual(normalise_chassis_code('8w'), '8W')

    def test_a_vin_loses_spaces_dashes_and_case(self):
        self.assertEqual(normalise_vin('wdd 2050-041f 123456'), EURO_VIN)

    def test_a_blank_box_is_nothing_recorded(self):
        for blank in (None, '', '   '):
            with self.subTest(blank=blank):
                self.assertIsNone(normalise_chassis_code(blank))
                self.assertIsNone(normalise_vin(blank))
                self.assertEqual(vin_problem(blank), '')

    def test_a_real_european_vin_passes_because_there_is_no_check_digit_test(self):
        self.assertEqual(vin_problem(EURO_VIN), '')
        self.assertEqual(vin_problem(BMW_VIN), '')

    def test_a_vin_is_judged_after_it_is_tidied(self):
        self.assertEqual(vin_problem('wba 8e9c 50gk 123456'), '')

    def test_the_wrong_length_is_refused_and_says_how_long_it_is(self):
        short = vin_problem(EURO_VIN[:-1])
        self.assertIn(str(VIN_LENGTH), short)
        self.assertIn('16', short)
        self.assertIn('18', vin_problem(EURO_VIN + '7'))

    def test_i_o_and_q_are_refused_never_corrected(self):
        for letter in 'IOQ':
            with self.subTest(letter=letter):
                problem = vin_problem(EURO_VIN[:-1] + letter)
                self.assertIn('I, O or Q', problem)

    def test_anything_but_letters_and_numbers_is_refused(self):
        self.assertIn('letters and numbers', vin_problem(EURO_VIN[:-1] + '.'))


class TheModelTidiesBothTests(TestCase):

    def test_a_job_card_stores_one_spelling(self):
        card = JobCard.objects.create(
            admitted_date=timezone.localdate(), brand_name='BMW', model_name='320d',
            registration_number='KL01A1', chassis_code=' f 30', vin='wba 8e9c-50gk 123456')
        card.refresh_from_db()
        self.assertEqual(card.chassis_code, 'F30')
        self.assertEqual(card.vin, BMW_VIN)

    def test_a_blank_box_stores_null_not_an_empty_string(self):
        card = JobCard.objects.create(
            admitted_date=timezone.localdate(), brand_name='BMW', model_name='320d',
            registration_number='KL01A2', chassis_code='  ', vin='')
        card.refresh_from_db()
        self.assertIsNone(card.chassis_code)
        self.assertIsNone(card.vin)

    def test_an_estimate_stores_the_same_spelling_as_the_job_card(self):
        est = Estimate.objects.create(chassis_code='w 205', vin='wdd2050041f123456')
        est.refresh_from_db()
        self.assertEqual(est.chassis_code, 'W205')
        self.assertEqual(est.vin, EURO_VIN)


class TheFormsTakeBothTests(TestCase):

    def job_form(self, **extra):
        data = {
            'admitted_date': timezone.localdate().isoformat(),
            'brand_name': 'BMW', 'model_name': '320d', 'registration_number': 'KL01B1',
        }
        data.update(extra)
        return JobCardForm(data=data)

    def estimate_form(self, **extra):
        data = {'date': timezone.localdate().isoformat()}
        data.update(extra)
        return EstimateForm(data=data)

    def test_a_vin_typed_in_groups_is_saved_whole(self):
        form = self.job_form(vin='wba 8e9c 50gk 123456', chassis_code='f 30')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['vin'], BMW_VIN)
        self.assertEqual(form.cleaned_data['chassis_code'], 'F30')

    def test_the_browser_is_never_told_to_cut_a_vin_off(self):
        """A `maxlength` of 17 stops the browser taking the 18th keystroke, so a
        VIN typed with its spaces would be truncated with nothing said."""
        for form in (JobCardForm(), EstimateForm()):
            with self.subTest(form=type(form).__name__):
                self.assertNotIn('maxlength', form.fields['vin'].widget.attrs)

    def test_a_wrong_vin_is_refused_with_the_rule(self):
        form = self.job_form(vin=BMW_VIN[:-1])
        self.assertFalse(form.is_valid())
        self.assertIn('17', ' '.join(form.errors['vin']))

    def test_both_boxes_may_be_left_empty(self):
        form = self.job_form(vin='', chassis_code='')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['vin'])
        self.assertIsNone(form.cleaned_data['chassis_code'])

    def test_the_estimate_answers_exactly_as_the_job_card_does(self):
        for typed in ('wba 8e9c 50gk 123456', BMW_VIN[:-1], BMW_VIN[:-1] + 'O', ''):
            with self.subTest(vin=typed):
                job, est = self.job_form(vin=typed), self.estimate_form(vin=typed)
                self.assertEqual(job.is_valid(), est.is_valid())
                self.assertEqual(job.errors.get('vin'), est.errors.get('vin'))
                if job.is_valid():
                    self.assertEqual(job.cleaned_data['vin'], est.cleaned_data['vin'])

    def test_neither_box_carries_a_placeholder(self):
        for form in (JobCardForm(), EstimateForm()):
            for name in ('chassis_code', 'vin'):
                with self.subTest(form=type(form).__name__, field=name):
                    self.assertNotIn('placeholder', form.fields[name].widget.attrs)

    def test_neither_box_is_exempt_from_the_empty_box_hairline(self):
        """The owners asked for the red mark on both. The script marks every box
        EXCEPT one carrying `jc-optional`, so its absence is the whole rule."""
        form = JobCardForm()
        for name in ('chassis_code', 'vin'):
            with self.subTest(field=name):
                self.assertNotIn('jc-optional', form.fields[name].widget.attrs.get('class', ''))


class _SignedIn(TestCase):
    """An owner, an office login and a floor login, each with a client."""

    def setUp(self):
        self.owner = User.objects.create_superuser('owner-v', 'o@example.com', 'pw')
        self.owner_client = Client()
        self.owner_client.login(username='owner-v', password='pw')

        floor_group, _ = Group.objects.get_or_create(name='Floor')
        floor = User.objects.create_user('floor-v', password='pw')
        floor.groups.add(floor_group)
        self.floor_client = Client()
        self.floor_client.login(username='floor-v', password='pw')

    def card(self, plate, chassis_code=None, vin=None, days_ago=0, **extra):
        today = timezone.localdate()
        fields = dict(
            admitted_date=today - timedelta(days=days_ago), brand_name='BMW',
            model_name='320d', registration_number=plate,
            chassis_code=chassis_code, vin=vin,
        )
        fields.update(extra)
        return JobCard.objects.create(**fields)


class TheJobCardRowTests(_SignedIn):

    def payload(self, **overrides):
        data = {
            'registration_number': 'KL05C1', 'admitted_date': timezone.localdate().isoformat(),
            'brand_name': 'BMW', 'model_name': '320d', 'mileage': '10000',
            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'spares-TOTAL_FORMS': '0', 'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '0', 'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',
        }
        data.update(overrides)
        return data

    def test_the_row_sits_between_the_plate_and_the_mileage(self):
        html = self.owner_client.get(reverse('jobcard_create')).content.decode()
        positions = [html.find('name="%s"' % n)
                     for n in ('registration_number', 'chassis_code', 'vin', 'mileage')]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_the_vin_takes_two_columns_and_the_chassis_code_one(self):
        html = self.owner_client.get(reverse('jobcard_create')).content.decode()
        chassis_at, vin_at = html.find('name="chassis_code"'), html.find('name="vin"')
        self.assertIn('col-sm-8', html[chassis_at:vin_at])

    def test_the_rendered_boxes_carry_no_opt_out_and_no_maxlength_on_the_vin(self):
        html = self.owner_client.get(reverse('jobcard_create')).content.decode()
        self.assertNotIn('jc-optional', _input_tag(html, 'chassis_code'))
        self.assertNotIn('jc-optional', _input_tag(html, 'vin'))
        self.assertNotIn('maxlength', _input_tag(html, 'vin'))

    def test_floor_can_record_both_because_the_mechanic_reads_them_off_the_car(self):
        card = self.card('KL05C1')
        response = self.floor_client.post(
            reverse('jobcard_edit', args=[card.pk]),
            self.payload(chassis_code='f30', vin='wba 8e9c 50gk 123456'))
        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.chassis_code, 'F30')
        self.assertEqual(card.vin, BMW_VIN)

    def test_a_refused_vin_names_the_box_and_saves_nothing(self):
        card = self.card('KL05C1', vin=BMW_VIN)
        response = self.owner_client.post(
            reverse('jobcard_edit', args=[card.pk]), self.payload(vin='WBA8E9C50GK12345'))
        self.assertEqual(response.status_code, 200)
        # The rule's own sentence, not the word "VIN" — the label says that on
        # every render, so finding it would prove nothing.
        self.assertIn('A VIN has 17 characters', response.content.decode())
        card.refresh_from_db()
        self.assertEqual(card.vin, BMW_VIN)

    def test_the_estimate_form_carries_the_same_row(self):
        html = self.owner_client.get(reverse('estimate_create')).content.decode()
        positions = [html.find('name="%s"' % n)
                     for n in ('registration_number', 'chassis_code', 'vin', 'mileage')]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))


class EverySearchFindsTheCarByEitherTests(_SignedIn):
    """Six screens search cars, and all six must answer to both. Each term is
    found ONLY in the new field, so a hit proves the field is searched."""

    AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    def setUp(self):
        super().setUp()
        now = timezone.now()
        common = dict(completed=True, completed_date=timezone.localdate(),
                      payment_status='PAID', paid_date=now)
        self.bmw = self.card('KL07AA1111', chassis_code='F30', vin=BMW_VIN, **common)
        self.merc = self.card('KL07BB2222', chassis_code='W205', vin=EURO_VIN,
                              brand_name='Mercedes-Benz', model_name='C220d', **common)

    def assertFinds(self, response, plate, other):
        html = response.content.decode()
        self.assertIn(plate, html)
        self.assertNotIn(other, html)

    def test_the_job_cards_list(self):
        url = reverse('jobcard_list')
        self.assertFinds(self.owner_client.get(url, {'q': 'f30'}, **self.AJAX), 'KL07AA1111', 'KL07BB2222')
        self.assertFinds(self.owner_client.get(url, {'q': '41F123'}, **self.AJAX), 'KL07BB2222', 'KL07AA1111')

    def test_completed(self):
        url = reverse('completed_list')
        self.assertFinds(self.owner_client.get(url, {'q': 'w205'}), 'KL07BB2222', 'KL07AA1111')
        self.assertFinds(self.owner_client.get(url, {'q': 'GK1234'}), 'KL07AA1111', 'KL07BB2222')

    def test_paid_bills(self):
        url = reverse('paid_bills_list')
        self.assertFinds(self.owner_client.get(url, {'q': 'f30'}), 'KL07AA1111', 'KL07BB2222')
        self.assertFinds(self.owner_client.get(url, {'q': '41F123'}), 'KL07BB2222', 'KL07AA1111')

    def test_pending_bills(self):
        JobCard.objects.update(payment_status='PENDING', paid_date=None)
        url = reverse('pending_payments_list')
        self.assertFinds(self.owner_client.get(url, {'q': 'w205'}, **self.AJAX), 'KL07BB2222', 'KL07AA1111')
        self.assertFinds(self.owner_client.get(url, {'q': 'GK1234'}, **self.AJAX), 'KL07AA1111', 'KL07BB2222')

    def test_car_profiles(self):
        url = reverse('car_profile_list')
        self.assertFinds(self.owner_client.get(url, {'q': 'f30'}), 'KL07AA1111', 'KL07BB2222')
        self.assertFinds(self.owner_client.get(url, {'q': '41F123'}), 'KL07BB2222', 'KL07AA1111')

    def test_estimates(self):
        Estimate.objects.create(registration_number='KL07AA1111', chassis_code='F30', vin=BMW_VIN)
        Estimate.objects.create(registration_number='KL07BB2222', chassis_code='W205', vin=EURO_VIN)
        url = reverse('estimate_list')
        self.assertFinds(self.owner_client.get(url, {'q': 'f30'}), 'KL07AA1111', 'KL07BB2222')
        self.assertFinds(self.owner_client.get(url, {'q': '41F123'}), 'KL07BB2222', 'KL07AA1111')


class ACarProfileSearchCountsEveryVisitTests(_SignedIn):
    """The search chooses which CARS; it must never choose which VISITS are
    counted. Filtering the grouped query directly did both."""

    def setUp(self):
        super().setUp()
        self.card('KL09CC3333', days_ago=30, completed=True, customer_name='Anwar')
        self.card('KL09CC3333', days_ago=20, completed=True)
        self.card('KL09CC3333', days_ago=10, vin=BMW_VIN)

    def visits_found_by(self, q):
        response = self.owner_client.get(reverse('car_profile_list'), {'q': q})
        cars = response.context['car_profiles']
        self.assertEqual(len(cars), 1)
        return cars[0]['total_visits']

    def test_a_vin_on_the_newest_visit_still_counts_all_three(self):
        self.assertEqual(self.visits_found_by('GK1234'), 3)

    def test_a_name_on_the_oldest_visit_still_counts_all_three(self):
        self.assertEqual(self.visits_found_by('anwar'), 3)


class TheLatestRecordedValueTests(_SignedIn):

    def test_a_newer_visit_saved_without_a_vin_does_not_hide_the_older_one(self):
        self.card('KL11D1', vin=BMW_VIN, chassis_code='F30', days_ago=40, completed=True)
        self.card('KL11D1', days_ago=2)
        self.assertEqual(latest_recorded('KL11D1'), {'chassis_code': 'F30', 'vin': BMW_VIN})

    def test_each_field_comes_from_its_own_newest_record(self):
        self.card('KL11D1', vin=BMW_VIN, days_ago=40, completed=True)
        self.card('KL11D1', chassis_code='F30', days_ago=2)
        self.assertEqual(latest_recorded('KL11D1'), {'chassis_code': 'F30', 'vin': BMW_VIN})

    def test_a_newer_value_wins_over_an_older_one(self):
        self.card('KL11D1', chassis_code='E90', days_ago=40, completed=True)
        self.card('KL11D1', chassis_code='F30', days_ago=2)
        self.assertEqual(latest_recorded('KL11D1')['chassis_code'], 'F30')

    def test_the_plate_is_read_the_way_it_is_stored(self):
        self.card('KL11D1', vin=BMW_VIN)
        self.assertEqual(latest_recorded('  kl11d1 ')['vin'], BMW_VIN)

    def test_nothing_recorded_is_none_not_an_empty_string(self):
        self.assertEqual(latest_recorded(''), {'chassis_code': None, 'vin': None})
        self.assertEqual(latest_recorded('KL99Z9'), {'chassis_code': None, 'vin': None})

    def test_the_car_profile_header_shows_the_latest_recorded_values(self):
        self.card('KL11D1', vin=BMW_VIN, chassis_code='F30', days_ago=40, completed=True)
        self.card('KL11D1', days_ago=2)
        html = self.owner_client.get(reverse('car_profile_detail', args=['KL11D1'])).content.decode()
        self.assertIn('<span class="cd-chip cd-chip-id">F30</span>', html)
        self.assertIn('<span class="cd-chip cd-chip-id">%s</span>' % BMW_VIN, html)


class TheReadOnlyCardTests(_SignedIn):

    def test_the_card_prints_both_under_the_plate(self):
        card = self.card('KL12E1', chassis_code='F30', vin=BMW_VIN)
        html = self.owner_client.get(reverse('jobcard_detail', args=[card.pk])).content.decode()
        line = html.split('class="dv-line2 dv-vids"', 1)
        self.assertEqual(len(line), 2)
        self.assertIn('F30', line[1].split('</p>', 1)[0])
        self.assertIn(BMW_VIN, line[1].split('</p>', 1)[0])

    def test_a_card_with_neither_draws_no_line(self):
        card = self.card('KL12E2')
        html = self.owner_client.get(reverse('jobcard_detail', args=[card.pk])).content.decode()
        self.assertNotIn('class="dv-line2 dv-vids"', html)


class NeitherIsPrintedTests(_SignedIn):
    """The owners' call: on the forms, the read-only card and the Car Profile,
    and on no document handed to a customer."""

    CODE = 'ZZCODE42'

    def test_not_on_the_bill_or_all_invoices(self):
        card = self.card('KL14G1', chassis_code=self.CODE, vin=BMW_VIN,
                         completed=True, completed_date=timezone.localdate())
        for url in (reverse('invoice_view', args=[card.pk]),
                    reverse('car_all_invoices', args=['KL14G1'])):
            with self.subTest(url=url):
                html = self.owner_client.get(url).content.decode()
                self.assertNotIn(BMW_VIN, html)
                self.assertNotIn(self.CODE, html)

    def test_not_on_the_service_history_sheet(self):
        self.card('KL14G1', chassis_code=self.CODE, vin=BMW_VIN,
                  completed=True, completed_date=timezone.localdate())
        response = self.owner_client.get(reverse('car_service_history_sheet', args=['KL14G1']))
        html = response.content.decode()
        self.assertNotIn(BMW_VIN, html)
        self.assertNotIn(self.CODE, html)

    def test_not_on_the_printed_estimate(self):
        est = Estimate.objects.create(registration_number='KL14G1', chassis_code=self.CODE, vin=BMW_VIN)
        html = self.owner_client.get(reverse('estimate_print', args=[est.pk])).content.decode()
        self.assertNotIn(BMW_VIN, html)
        self.assertNotIn(self.CODE, html)


class NeitherIsChasedAtSettlementTests(TestCase):
    """Most cards will carry neither for a long time, so a missing one must
    never paint a card red in the settle dialog or on the Live Report."""

    def test_a_card_missing_both_has_nothing_unfilled(self):
        card = JobCard.objects.create(
            admitted_date=timezone.localdate(), brand_name='BMW', model_name='320d',
            registration_number='KL15H1', mileage='40000',
            lead_mechanic=Mechanic.objects.create(name='Hijaz'))
        self.assertFalse(settlement.unfilled(card))
