"""
A free-text odometer box, read as a number.

`JobCard.mileage` is a `CharField` and has to stay one — a car sometimes
arrives with a dead cluster, and a required integer would stop a mechanic
mid-shift over a figure that is context rather than money. Everything the
service history computes, though, is a SUBTRACTION of two of these, so the
column has to become an integer somewhere, once, with the same answer
everywhere. That is `workshop/mileage.py`.

The tests are in three parts, and the middle one is the point:

  * what the parser accepts, which is the ordinary day;
  * **what it REFUSES**, because a scrub-and-hope parser would return a wrong
    number for every one of those inputs rather than no number — and a wrong
    reading is what puts a fictional service interval on a customer's document;
  * that normalising on save tidies without ever losing what somebody typed.
"""

from datetime import date

from django.test import TestCase

from workshop.mileage import MAX_KM, normalise, parse_km
from workshop.models import Estimate, JobCard


class WhatCanBeReadTests(TestCase):
    """The shapes this workshop actually types."""

    def test_plain_digits(self):
        self.assertEqual(parse_km('50000'), 50000)

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(parse_km('  50000  '), 50000)

    def test_western_grouping(self):
        """Pasted, or typed out of habit."""
        self.assertEqual(parse_km('50,000'), 50000)

    def test_indian_grouping(self):
        """`1,02,340` is how a reading is read aloud here — see `inr`."""
        self.assertEqual(parse_km('1,02,340'), 102340)

    def test_the_unit_typed_out(self):
        for text in ('50000 km', '50000km', '50,000 kms', '50000 KM',
                     '50000 kilometres', '50000 kilometers', '50000 k.m.'):
            with self.subTest(text=text):
                self.assertEqual(parse_km(text), 50000)

    def test_the_k_shorthand_the_help_text_invites(self):
        """The field's own help_text says 'e.g. 50000 or 50k'."""
        self.assertEqual(parse_km('50k'), 50000)
        self.assertEqual(parse_km('50K'), 50000)

    def test_the_k_shorthand_with_a_half(self):
        self.assertEqual(parse_km('50.5k'), 50500)

    def test_a_reading_showing_tenths_rounds_to_the_kilometre(self):
        """
        Some clusters show a decimal. The whole kilometre is the reading; the
        tenths are the trip meter.
        """
        self.assertEqual(parse_km('50000.4'), 50000)
        self.assertEqual(parse_km('50000.6'), 50001)

    def test_a_half_rounds_up_not_to_even(self):
        """
        ROUND_HALF_UP, the same rule `invoice.derive_unit_price` uses. Python's
        own round() is banker's rounding and would answer 50000 here.
        """
        self.assertEqual(parse_km('50000.5'), 50001)

    def test_an_integer_is_accepted_as_well_as_a_string(self):
        """A seeder or a shell session passes the number itself."""
        self.assertEqual(parse_km(50000), 50000)

    def test_the_ceiling_itself_is_allowed(self):
        self.assertEqual(parse_km(str(MAX_KM)), MAX_KM)


class WhatIsRefusedTests(TestCase):
    """
    The half that decides whether this feature can be trusted.

    Every input here is one a strip-the-non-digits parser would answer with a
    number. None of those numbers is a reading, and a document that prints a
    fictional service interval is worse than one that says nothing.
    """

    def test_nothing_at_all(self):
        for value in (None, '', '   '):
            with self.subTest(value=repr(value)):
                self.assertIsNone(parse_km(value))

    def test_somebody_saying_they_do_not_know(self):
        for text in ('-', 'n/a', 'na', 'unknown', '?', 'not working'):
            with self.subTest(text=text):
                self.assertIsNone(parse_km(text))

    def test_a_note_about_a_reading_is_not_a_reading(self):
        """Scrubbing non-digits would read all three of these as 50000."""
        for text in ('approx 50000', '50000 (approx)', 'about 50000'):
            with self.subTest(text=text):
                self.assertIsNone(parse_km(text))

    def test_miles_are_refused_never_converted(self):
        """
        An imported car showing miles is real, and 1.609 is not a secret. A
        column that silently mixes two units is how an interval comes out 60%
        short with nothing on screen saying so, so this refuses and puts the
        decision in front of a person.
        """
        for text in ('50000 miles', '50000 mi', '50000 mile'):
            with self.subTest(text=text):
                self.assertIsNone(parse_km(text))

    def test_two_numbers_in_the_box(self):
        """`85000 2` would scrub to 850002 — a reading ten times the real one."""
        self.assertIsNone(parse_km('85000 2'))

    def test_zero_is_not_a_reading(self):
        """
        The expensive refusal, and the one that looks wrong at first. A car at
        0 km does not reach a workshop servicing used premium cars; what
        reaches the box is somebody filling it in to get past it. Taken
        literally, the NEXT visit reports the car's whole lifetime distance as
        one service interval.
        """
        self.assertIsNone(parse_km('0'))
        self.assertIsNone(parse_km('0 km'))

    def test_a_negative_reading(self):
        """An odometer has no direction."""
        self.assertIsNone(parse_km('-100'))

    def test_past_the_ceiling(self):
        """
        A slipped keypress or a pasted phone number. The column allows twenty
        characters, so without this a reading of 99999999999999999999 would be
        subtracted from the one before it.
        """
        self.assertIsNone(parse_km(str(MAX_KM + 1)))
        self.assertIsNone(parse_km('99999999999999999999'))

    def test_commas_that_are_not_a_number(self):
        for text in ('50,0', '1,2,3', ',500', '50,'):
            with self.subTest(text=text):
                self.assertIsNone(parse_km(text))

    def test_the_ceiling_catches_garbage_and_NOT_a_slipped_digit(self):
        """
        Recorded so nobody mistakes what this guard is for. An extra zero on
        85,000 gives 850,000 — a plausible-looking reading, well under the
        ceiling, and accepted here. Catching THAT is the job of the document,
        which compares a reading against the one before it. Two guards, and
        neither replaces the other.
        """
        self.assertEqual(parse_km('850000'), 850000)


class NormalisingNeverLosesWhatWasTypedTests(TestCase):
    """
    `normalise` runs inside `clean()`, which runs on every save — including
    saves that touched nothing near this box. Tidying may not discard.
    """

    def test_a_readable_value_is_stored_as_plain_digits(self):
        for text in ('50k', '50,000', '50000 km', ' 50000 '):
            with self.subTest(text=text):
                self.assertEqual(normalise(text), '50000')

    def test_an_unreadable_value_is_kept_exactly_as_typed(self):
        self.assertEqual(normalise('approx 50000'), 'approx 50000')
        self.assertEqual(normalise('odometer dead'), 'odometer dead')

    def test_an_unreadable_value_is_still_trimmed(self):
        self.assertEqual(normalise('  odometer dead  '), 'odometer dead')

    def test_an_empty_box_stays_empty(self):
        self.assertIsNone(normalise(None))
        self.assertEqual(normalise(''), '')

    def test_a_normalised_reading_always_fits_the_column(self):
        """
        `mileage` is 20 characters. The ceiling is seven digits, so a value
        this function produces can never overflow it — asserted rather than
        assumed, because a wider ceiling later would break the column silently.
        """
        column = JobCard._meta.get_field('mileage').max_length
        self.assertLessEqual(len(str(MAX_KM)), column)


class TheModelsTidyItOnSaveTests(TestCase):
    """
    Both write paths, because both models carry the column and the two
    documents are opened for the same car days apart.
    """

    def test_a_job_card_stores_the_tidied_reading(self):
        card = JobCard.objects.create(
            admitted_date=date(2026, 3, 1),
            brand_name='Audi', model_name='A4',
            registration_number='KL 10 AA 1000',
            mileage='50k',
        )
        card.refresh_from_db()
        self.assertEqual(card.mileage, '50000')

    def test_an_estimate_stores_the_tidied_reading(self):
        estimate = Estimate.objects.create(
            date=date(2026, 3, 1),
            brand_name='Audi', model_name='A4',
            registration_number='KL 10 AA 1000',
            mileage='1,02,340',
        )
        estimate.refresh_from_db()
        self.assertEqual(estimate.mileage, '102340')

    def test_the_two_documents_cannot_spell_one_reading_two_ways(self):
        """
        The reason `Estimate.clean` carries this at all. A quotation and the
        bill that follows it are written for the same car in the same week; one
        reading '50k' and the other '50000' is one document contradicting the
        other.
        """
        card = JobCard.objects.create(
            admitted_date=date(2026, 3, 1),
            brand_name='Audi', model_name='A4',
            registration_number='KL 10 AA 1001', mileage='50,000',
        )
        estimate = Estimate.objects.create(
            date=date(2026, 3, 1),
            brand_name='Audi', model_name='A4',
            registration_number='KL 10 AA 1001', mileage='50k',
        )
        self.assertEqual(card.mileage, estimate.mileage)

    def test_an_unreadable_reading_survives_an_unrelated_edit(self):
        """
        The defect `normalise` is written to avoid: a mechanic's note about a
        broken odometer being swept away the next time somebody corrects the
        registration on that card.
        """
        card = JobCard.objects.create(
            admitted_date=date(2026, 3, 1),
            brand_name='Audi', model_name='A4',
            registration_number='KL 10 AA 1002',
            mileage='cluster not working',
        )
        card.registration_number = 'KL 10 AA 1003'
        card.save()
        card.refresh_from_db()
        self.assertEqual(card.mileage, 'cluster not working')

    def test_a_card_with_no_reading_saves_normally(self):
        card = JobCard.objects.create(
            admitted_date=date(2026, 3, 1),
            brand_name='Audi', model_name='A4',
            registration_number='KL 10 AA 1004',
        )
        card.refresh_from_db()
        self.assertIsNone(card.mileage)
