"""
A known plate fills the car (2026-09-15, the owners' request).

Typing a plate the workshop has seen before fills the brand, the model, the
colour and both codes by themselves, and OFFERS the last customer's name and
number as greyed placeholders with one "Use last visit" button — Office and
Owner only. Every rule about the
answer is `workshop/known_car.py`.

The form's script decides only which boxes it may write into, and nothing in
this suite executes JavaScript, so the script is pinned here by the contract it
relies on and its behaviour was measured in a browser.
"""
import re
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.known_car import known_car
from workshop.models import CAR_COLOR_HEX, CAR_COLOR_UNSET_HEX, JobCard
from workshop.vehicle_ids import latest_recorded

BMW_VIN = 'WBA8E9C50GK123456'


def _card(plate, days_ago=0, **fields):
    values = dict(
        admitted_date=timezone.localdate() - timedelta(days=days_ago),
        brand_name='BMW', model_name='320d', registration_number=plate,
    )
    values.update(fields)
    return JobCard.objects.create(**values)


class WhatAPlateAnswersTests(TestCase):

    def test_an_unknown_plate_answers_blank_and_not_found(self):
        answer = known_car('KL99Z9')
        self.assertFalse(answer['found'])
        for name in ('brand_name', 'model_name', 'car_color', 'car_color_other', 'chassis_code', 'vin'):
            with self.subTest(field=name):
                self.assertEqual(answer[name], '')
        self.assertEqual(answer['car_color_hex'], CAR_COLOR_UNSET_HEX)

    def test_an_empty_plate_answers_blank_too(self):
        """The form asks about an emptied plate as well, to take back what it
        filled — so blank must be a normal answer, not an error."""
        _card('KL20A1')
        self.assertFalse(known_car('   ')['found'])

    def test_the_car_comes_from_the_newest_visit(self):
        _card('KL20A1', days_ago=40, brand_name='Toyota', model_name='Corolla')
        newest = _card('KL20A1', days_ago=2, brand_name='Audi', model_name='A4')
        answer = known_car('KL20A1')
        self.assertTrue(answer['found'])
        self.assertEqual((answer['brand_name'], answer['model_name']),
                         (newest.brand_name, newest.model_name))

    def test_the_typed_plate_is_read_the_way_it_is_stored(self):
        card = _card('KL20A1')
        self.assertEqual(known_car('  kl20a1 ')['model_name'], card.model_name)

    def test_the_colour_comes_from_the_newest_visit_that_recorded_one(self):
        _card('KL20A1', days_ago=40, car_color='Red')
        _card('KL20A1', days_ago=2)
        answer = known_car('KL20A1')
        self.assertEqual(answer['car_color'], 'Red')
        self.assertEqual(answer['car_color_hex'], CAR_COLOR_HEX['Red'])

    def test_an_other_colour_carries_its_own_value(self):
        _card('KL20A1', car_color='Other', car_color_other='#7a1f3d')
        answer = known_car('KL20A1')
        self.assertEqual(
            (answer['car_color'], answer['car_color_other'], answer['car_color_hex']),
            ('Other', '#7a1f3d', '#7a1f3d'))

    def test_a_colour_is_never_stitched_from_two_visits(self):
        _card('KL20A1', days_ago=40, car_color='Other', car_color_other='#7a1f3d')
        _card('KL20A1', days_ago=2, car_color='Red')
        answer = known_car('KL20A1')
        self.assertEqual((answer['car_color'], answer['car_color_other']), ('Red', ''))

    def test_the_two_codes_are_the_car_profiles_own_answer(self):
        _card('KL20A1', days_ago=40, chassis_code='F30', vin=BMW_VIN)
        _card('KL20A1', days_ago=2)
        answer = known_car('KL20A1')
        self.assertEqual({'chassis_code': answer['chassis_code'], 'vin': answer['vin']},
                         latest_recorded('KL20A1'))

    def test_the_customer_is_absent_unless_asked_for(self):
        _card('KL20A1', customer_name='Anwar', customer_contact='9876543210')
        answer = known_car('KL20A1')
        self.assertNotIn('customer_name', answer)
        self.assertNotIn('customer_contact', answer)

    def test_the_customer_is_one_visits_name_and_number_never_two(self):
        """Sold on: the new owner's name on the newest card, the old owner's
        number on an older one. That number must never land under the new name."""
        _card('KL20A1', days_ago=40, customer_name='Anwar', customer_contact='9876543210')
        newest = _card('KL20A1', days_ago=2, customer_name='Rahul')
        answer = known_car('KL20A1', include_customer=True)
        self.assertEqual((answer['customer_name'], answer['customer_contact']),
                         (newest.customer_name, ''))

    def test_a_visit_with_no_customer_does_not_hide_the_last_one(self):
        older = _card('KL20A1', days_ago=40, customer_name='Anwar', customer_contact='9876543210')
        _card('KL20A1', days_ago=2)
        answer = known_car('KL20A1', include_customer=True)
        self.assertEqual((answer['customer_name'], answer['customer_contact']),
                         (older.customer_name, older.customer_contact))


class WhoIsAnsweredTests(TestCase):

    def setUp(self):
        self.card = _card('KL21B1', chassis_code='F30', vin=BMW_VIN, car_color='Red',
                          customer_name='Anwar', customer_contact='9876543210')
        User.objects.create_superuser('owner-k', 'owner-k@example.com', 'pw')
        for role in ('Office', 'Floor'):
            user = User.objects.create_user(f'{role.lower()}-k', password='pw')
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        self.clients = {}
        for username in ('owner-k', 'office-k', 'floor-k'):
            client = Client()
            client.login(username=username, password='pw')
            self.clients[username] = client

    def ask(self, who):
        return self.clients[who].get(reverse('known_car_lookup'), {'registration': 'KL21B1'})

    def page(self, who):
        return self.clients[who].get(reverse('jobcard_create')).content.decode()

    def test_it_needs_a_login(self):
        response = Client().get(reverse('known_car_lookup'), {'registration': 'KL21B1'})
        self.assertEqual(response.status_code, 302)

    def test_floor_gets_the_car_and_nothing_about_the_customer(self):
        response = self.ask('floor-k')
        data = response.json()
        self.assertEqual((data['vin'], data['chassis_code'], data['car_color']), (BMW_VIN, 'F30', 'Red'))
        self.assertNotIn('customer_name', data)
        self.assertNotIn('customer_contact', data)
        body = response.content.decode()
        self.assertNotIn(self.card.customer_name, body)
        self.assertNotIn(self.card.customer_contact, body)

    def test_office_and_owner_get_the_customer_too(self):
        for who in ('office-k', 'owner-k'):
            with self.subTest(who=who):
                data = self.ask(who).json()
                self.assertEqual((data['customer_name'], data['customer_contact']),
                                 (self.card.customer_name, self.card.customer_contact))

    def test_every_role_s_form_calls_the_lookup(self):
        for who in ('owner-k', 'office-k', 'floor-k'):
            with self.subTest(who=who):
                self.assertIn(reverse('known_car_lookup'), self.page(who))

    def test_the_use_button_ships_hidden_and_only_to_office_and_owner(self):
        for who in ('office-k', 'owner-k'):
            with self.subTest(who=who):
                # `type="button"`: a bare <button> inside the form would SUBMIT it.
                self.assertIn('<button type="button" class="jc-use-last" id="jcUseLast" hidden',
                              self.page(who))
        self.assertNotIn('id="jcUseLast"', self.page('floor-k'))

    def test_the_offer_is_never_sent_as_a_placeholder_by_the_server(self):
        """The greyed-in name and number are set in SCRIPT while the offer shows.
        The page itself carries no placeholder on either box, so there is nothing
        a person could mistake for a stored value or a form could post."""
        html = self.page('owner-k')
        for name in ('customer_name', 'customer_contact'):
            with self.subTest(field=name):
                tag = re.search(r'<input[^>]*name="%s"[^>]*>' % name, html).group(0)
                self.assertNotIn('placeholder', tag)

    def test_the_colour_picker_lets_the_lookup_paint_through_it(self):
        self.assertIn('window.carColourSelect = updateColorSelection', self.page('owner-k'))
