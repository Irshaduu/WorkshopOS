"""
OLD BILLS — bills written in Excel before the system existed, typed in for a
car's history only.

The one rule that matters most is the isolation: an old bill must never reach
profit, cash, balances, stock or any ledger. These tests assert it in two ways —
by what the code is ALLOWED to mention, and by what the figures actually do.
"""

import json
import re
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.contrib.auth.models import Group, User
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.urls import reverse

from workshop import analysis_engine as engine
from workshop.old_bills import (
    bill_number_problem, format_bill_number, read_amount, read_date, read_old_bill,
    read_quantity,
    split_bill_number,
)
from workshop.models import (
    CarBrand, CarModel, JobCard, JobCardSpareItem,
    OldBill, OldBillJobLine, OldBillPartLine, SparePart,
)


def _bill(number='JB-26-097', **kwargs):
    fields = dict(
        bill_number=number,
        bill_date=date(2026, 4, 10),
        registration_number='HR 26 BQ 1572',
        brand_name='Porsche',
        model_name='Boxster',
        mileage='16797',
        labour_amount=Decimal('22300'),
    )
    fields.update(kwargs)
    return OldBill.objects.create(**fields)


def _the_sample_bill():
    """The real Running Invoice JB-26-097: ₹22,300 labour + ₹14,775 parts."""
    bill = _bill()
    for text in ('Drive belt tensioner replaced', 'Coolant replaced', 'Spark plugs replaced'):
        OldBillJobLine.objects.create(old_bill=bill, description=text)
    for name, quantity, amount in (
        ('Drive belt', None, None),
        ('Coolant', None, Decimal('2820')),
        ('Distilled water', None, Decimal('75')),
        ('Spark plugs', Decimal('6'), Decimal('11880')),
    ):
        OldBillPartLine.objects.create(old_bill=bill, name=name, quantity=quantity, amount=amount)
    bill.update_totals()
    bill.refresh_from_db()
    return bill


class OldBillsAreConnectedToNothingTests(TestCase):
    """
    ⚠ THE ISOLATION IS THE DESIGN. If any of these fail, old bills have started
    reaching figures for months that already happened outside the system.
    """

    #: The ONLY application files allowed to mention old bills. Every later
    #: step adds its own file here, on purpose — so wiring old bills into
    #: anything new is a decision somebody has to make, never an accident.
    #: The money code (analysis_engine, settlement, the ledgers, inventory,
    #: the dashboard and bill lists) must never appear on this list.
    ALLOWED = {
        'workshop/models.py',
        'workshop/old_bills.py',
        'workshop/views/old_bills.py',
        'workshop/views/car_profiles.py',
        'workshop/known_car.py',
        'workshop/master_data.py',
        'workshop/management/commands/purge_business_data.py',
    }

    # Class names and the table name. Deliberately NOT "old_bill": inventory's
    # signals already use that phrase for a Supplies Shop bill's old terms.
    MENTION = re.compile(r'OldBill|oldbill')

    def test_only_the_allowed_files_mention_old_bills(self):
        base = Path(settings.BASE_DIR)
        offenders = []
        for app in ('workshop', 'inventory', 'formulad_workshop'):
            for path in (base / app).rglob('*.py'):
                rel = path.relative_to(base).as_posix()
                if '/migrations/' in rel or '/tests/' in rel or path.name.startswith('test'):
                    continue
                if rel in self.ALLOWED:
                    continue
                if self.MENTION.search(path.read_text(encoding='utf-8', errors='replace')):
                    offenders.append(rel)
        self.assertEqual(offenders, [], "These files now read old bills — was that decided?")

    def test_an_old_bill_moves_no_reported_figure(self):
        # A real job card first, so the figures being compared are not all zero.
        card = JobCard.objects.create(
            admitted_date=date(2026, 4, 9), brand_name='Audi', model_name='A4',
            registration_number='KL 10 AA 1000', labour_amount=Decimal('5000'),
            completed=True, completed_date=date(2026, 4, 9),
        )
        card.update_totals()

        start, end = date(2024, 1, 1), date(2026, 12, 31)
        before = (
            engine.build_profit_report(start, end),
            engine.cash_position(start, end),
            engine.financial_position(),
        )

        _the_sample_bill()

        after = (
            engine.build_profit_report(start, end),
            engine.cash_position(start, end),
            engine.financial_position(),
        )
        self.assertEqual(after[0], before[0], "Profit page moved")
        self.assertEqual(after[1], before[1], "Cash Tracking moved")
        self.assertEqual(after[2], before[2], "Position Right Now moved")

    def test_a_part_named_like_a_stock_product_moves_no_stock(self):
        from inventory.models import Category, Item

        category = Category.objects.create(name='Coolant')
        item = Item.objects.create(
            category=category, name='Coolant',
            current_stock=Decimal('20'), average_stock=Decimal('20'),
        )
        bill = _bill()
        OldBillPartLine.objects.create(
            old_bill=bill, name='Coolant', quantity=Decimal('5'), amount=Decimal('2820'),
        )
        item.refresh_from_db()
        self.assertEqual(item.current_stock, Decimal('20'))

    def test_typing_an_old_bill_creates_no_job_card_and_no_spare(self):
        _the_sample_bill()
        self.assertEqual(JobCard.objects.count(), 0)
        self.assertEqual(JobCardSpareItem.objects.count(), 0)


class AnOldBillSpellsACarLikeAJobCardTests(TestCase):
    """
    An old bill and a live job card for one car must land on ONE Car Profile,
    so the same typing has to be stored the same way on both.
    """

    def test_the_same_typing_is_stored_the_same_way(self):
        brand = CarBrand.objects.create(name='BMW')
        CarModel.objects.create(brand=brand, name='320d')
        raw = dict(
            registration_number='  kl 11 aj 2266 ',
            brand_name='  bmw ',
            model_name=' 320D ',
            mileage='50,000 km',
        )
        card = JobCard(admitted_date=date(2026, 4, 10), **raw)
        card.clean()
        bill = _bill(**raw)

        self.assertEqual(
            (bill.registration_number, bill.brand_name, bill.model_name, bill.mileage),
            (card.registration_number, card.brand_name, card.model_name, card.mileage),
        )
        self.assertEqual(
            (bill.registration_number, bill.brand_name, bill.model_name, bill.mileage),
            ('KL 11 AJ 2266', 'BMW', '320d', '50000'),
        )

    def test_the_bill_number_is_tidied(self):
        bill = _bill(number=' jb-26-097 ')
        self.assertEqual(bill.bill_number, 'JB-26-097')


class AnOldBillsTotalIsLabourPlusPartsTests(TestCase):

    def test_the_sample_bill_adds_up_to_its_paper_total(self):
        self.assertEqual(_the_sample_bill().total_amount, Decimal('37075'))

    def test_a_part_with_no_amount_adds_nothing_and_stays_blank(self):
        bill = _the_sample_bill()
        belt = bill.part_lines.get(name='Drive belt')
        self.assertIsNone(belt.amount)

    def test_a_parts_only_bill_needs_no_labour(self):
        bill = _bill(labour_amount=Decimal('0'))
        OldBillPartLine.objects.create(old_bill=bill, name='Coolant', amount=Decimal('2820'))
        bill.update_totals()
        bill.refresh_from_db()
        self.assertEqual(bill.total_amount, Decimal('2820'))


class TheDatabaseRefusesWhatCannotBeTrueTests(TestCase):
    """Backstops. The form refuses all of these first; the database is the floor."""

    def test_one_bill_number_is_one_bill(self):
        _bill()
        with self.assertRaises(IntegrityError), transaction.atomic():
            _bill(bill_date=date(2026, 4, 11))

    def test_a_negative_labour_charge_is_refused(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            _bill(labour_amount=Decimal('-1'))

    def test_a_zero_quantity_is_refused(self):
        bill = _bill()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OldBillPartLine.objects.create(old_bill=bill, name='Coolant', quantity=Decimal('0'))

    def test_a_negative_part_amount_is_refused(self):
        bill = _bill()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OldBillPartLine.objects.create(old_bill=bill, name='Coolant', amount=Decimal('-5'))

    def test_deleting_a_bill_removes_its_lines(self):
        bill = _the_sample_bill()
        pk = bill.pk
        bill.delete()
        self.assertFalse(OldBillJobLine.objects.filter(old_bill_id=pk).exists())
        self.assertFalse(OldBillPartLine.objects.filter(old_bill_id=pk).exists())


class ThePreGoLivePurgeClearsOldBillsTests(TestCase):
    """
    Real old bills are typed in AFTER go-live, so anything in these tables when
    the go-live purge runs is test typing — and must not survive into the books.
    """

    def test_it_clears_old_bills_and_their_lines(self):
        _the_sample_bill()
        call_command('purge_business_data', yes=True, stdout=StringIO())
        self.assertEqual(OldBill.objects.count(), 0)
        self.assertEqual(OldBillJobLine.objects.count(), 0)
        self.assertEqual(OldBillPartLine.objects.count(), 0)

    def test_a_dry_run_deletes_nothing(self):
        _the_sample_bill()
        call_command('purge_business_data', stdout=StringIO())
        self.assertEqual(OldBill.objects.count(), 1)


def _card(admitted, reg='KL 10 AA 1000'):
    return JobCard.objects.create(
        admitted_date=admitted, brand_name='Audi', model_name='A4', registration_number=reg,
    )


class OneJBNumberExistsOnceTests(TestCase):
    """
    The Excel bills were numbered JB-YY-NNN from 001 every January — the same
    shape the system uses. A customer must never hold two different bills with
    one number, so paper and system share ONE sequence per year.
    """

    def test_with_no_setting_the_numbering_is_unchanged(self):
        self.assertEqual(_card(date(2026, 9, 1)).bill_number, 'JB-26-001')

    @override_settings(LAST_EXCEL_BILL_NUMBER='JB-26-245')
    def test_the_first_live_card_follows_the_last_excel_bill(self):
        self.assertEqual(_card(date(2026, 9, 1), 'A').bill_number, 'JB-26-246')
        self.assertEqual(_card(date(2026, 9, 2), 'B').bill_number, 'JB-26-247')

    @override_settings(LAST_EXCEL_BILL_NUMBER='JB-26-245')
    def test_the_next_year_starts_at_001(self):
        self.assertEqual(_card(date(2027, 1, 1)).bill_number, 'JB-27-001')

    def test_a_live_card_skips_a_number_an_old_bill_holds(self):
        _bill(number='JB-25-120', bill_date=date(2025, 6, 1))
        self.assertEqual(_card(date(2025, 6, 2)).bill_number, 'JB-25-121')

    def test_leading_zeros_do_not_make_a_second_number(self):
        self.assertEqual(split_bill_number('JB-26-97'), split_bill_number(' jb-26-097 '))
        self.assertEqual(format_bill_number(26, 97), 'JB-26-097')
        self.assertEqual(format_bill_number(26, 1000), 'JB-26-1000')


@override_settings(LAST_EXCEL_BILL_NUMBER='JB-26-245')
class AnOldBillNumberIsCheckedTests(TestCase):

    def test_a_good_number_passes(self):
        self.assertIsNone(bill_number_problem('JB-26-097', date(2026, 4, 10)))

    def test_it_must_look_like_a_bill_number(self):
        self.assertIn('like JB-26-097', bill_number_problem('26-097', date(2026, 4, 10)))
        self.assertIn('like JB-26-097', bill_number_problem('JB-26-000', date(2026, 4, 10)))

    def test_its_year_must_be_the_dates_year(self):
        problem = bill_number_problem('JB-26-097', date(2025, 4, 10))
        self.assertIn('2026 bill', problem)
        self.assertIn('2025', problem)

    def test_a_number_after_the_last_excel_bill_belongs_to_the_system(self):
        self.assertIn('belong to the system', bill_number_problem('JB-26-246', date(2026, 4, 10)))
        self.assertIn('belong to the system', bill_number_problem('JB-27-001', date(2027, 1, 1)))

    def test_a_number_a_job_card_holds_is_refused(self):
        card = _card(date(2025, 3, 1))
        self.assertIn('already a job card', bill_number_problem(card.bill_number, date(2025, 3, 1)))

    def test_a_number_already_typed_in_is_refused_and_named(self):
        _bill(number='JB-26-097')
        problem = bill_number_problem('JB-26-97', date(2026, 4, 10))
        self.assertIn('already in', problem)
        self.assertIn('Porsche Boxster', problem)

    def test_an_edit_does_not_collide_with_itself(self):
        bill = _bill(number='JB-26-097')
        self.assertIsNone(bill_number_problem('JB-26-097', date(2026, 4, 10), exclude_pk=bill.pk))


class TheBrowserAndTheServerReadTypingTheSameWayTests(TestCase):
    """
    `static/js/old-bill-core.js` shows what the form understood while somebody
    types; `old_bills.py` decides on save. Both suites read ONE case file, so a
    date or an amount can never be "✓" on screen and refused on save.
    """

    CASES = Path(settings.BASE_DIR) / 'workshop' / 'tests' / 'js' / 'old-bill-cases.json'
    COLUMNS = {
        'labour': (OldBill, 'labour_amount'),
        'part': (OldBillPartLine, 'amount'),
        'total': (OldBill, 'total_amount'),
    }

    def setUp(self):
        self.cases = json.loads(self.CASES.read_text(encoding='utf-8'))

    def test_every_shared_date_case(self):
        today = date.fromisoformat(self.cases['today'])
        for case in self.cases['dates']:
            with self.subTest(case=case):
                value, problem = read_date(case['day'], case['month'], case['year'], today)
                if 'error' in case:
                    self.assertEqual((value, problem), (None, case['error']))
                else:
                    self.assertEqual((value, problem), (date.fromisoformat(case['iso']), None))

    def test_every_shared_amount_case(self):
        for case in self.cases['amounts']:
            with self.subTest(case=case):
                value, problem = read_amount(case['text'], *self.COLUMNS[case['kind']])
                if 'error' in case:
                    self.assertEqual((value, problem), (None, case['error']))
                elif case.get('blank'):
                    self.assertEqual((value, problem), (None, None))
                else:
                    self.assertEqual((value, problem), (Decimal(case['paise']) / 100, None))


class APartQuantityIsReadTests(TestCase):

    def test_blank_is_no_quantity(self):
        self.assertEqual(read_quantity('  '), (None, None))

    def test_a_plain_or_decimal_quantity(self):
        self.assertEqual(read_quantity('6'), (Decimal('6.00'), None))
        self.assertEqual(read_quantity('1.5'), (Decimal('1.50'), None))

    def test_zero_is_refused(self):
        self.assertEqual(read_quantity('0'), (None, 'zero'))

    def test_junk_and_overflow_are_refused(self):
        self.assertEqual(read_quantity('six'), (None, 'not a number'))
        self.assertEqual(read_quantity('1000000'), (None, 'too large'))


# -----------------------------------------------------------------------------
# THE FORM
# -----------------------------------------------------------------------------

def _user(role):
    user = User.objects.create_user(username=f'ob_{role.lower()}', password='pw')
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


def _sample_post(**changes):
    """The real Running Invoice JB-26-097, typed the way the paper prints it."""
    data = {
        'day': '10', 'month': 'apr', 'year': '2026', 'num_year': '26', 'num_seq': '97',
        'customer_name': '', 'registration_number': 'hr 26 bq 1572',
        'brand_name': 'Porsche', 'model_name': 'Boxster', 'mileage': '16797',
        'job': ['Drive belt tensioner replaced', 'Coolant replaced', 'Spark plugs replaced', ''],
        'labour_amount': '22,300.00',
        'part_name': ['Drive belt', 'Coolant', 'Distilled water', 'Spark plugs', ''],
        'part_qty': ['', '', '', '6', ''],
        'part_amount': ['', '2,820.00', '75.00', '11,880.00', ''],
    }
    data.update(changes)
    return data


def _query(data):
    query = QueryDict(mutable=True)
    for key, value in data.items():
        if isinstance(value, list):
            query.setlist(key, value)
        else:
            query[key] = value
    return query


TODAY = date(2026, 9, 17)


class ReadingTheWholeBillTests(TestCase):

    def test_the_sample_bill_reads_clean(self):
        values, problems = read_old_bill(_query(_sample_post()), TODAY)
        self.assertEqual(problems, [])
        self.assertEqual(values['bill_number'], 'JB-26-097')
        self.assertEqual(values['bill_date'], date(2026, 4, 10))
        self.assertEqual(values['labour_amount'], Decimal('22300.00'))
        self.assertEqual(values['jobs'], ['Drive belt tensioner replaced', 'Coolant replaced', 'Spark plugs replaced'])
        self.assertEqual(values['parts'][0], ('Drive belt', None, None))
        self.assertEqual(values['parts'][3], ('Spark plugs', Decimal('6.00'), Decimal('11880.00')))

    def test_the_total_is_worked_out_and_nothing_typed_can_change_it(self):
        # The owners' call (2026-09-17): the TOTAL is labour + every part
        # amount, checked against the XL bill by eye. A total posted by a page
        # opened before the change is not read at all.
        self.client.force_login(_user('Office'))
        response = self.client.post(reverse('old_bill_add'), _sample_post(paper_total='1'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OldBill.objects.get().total_amount, Decimal('37075.00'))

    def test_a_total_too_large_for_its_column_is_refused(self):
        # Each amount fits its own column; together they do not fit the
        # total's. Refused, or PostgreSQL would 500 on save.
        _, problems = read_old_bill(_query(_sample_post(
            labour_amount='9,999,999,999', part_amount=['', '99,999,999', '', '', ''])), TODAY)
        self.assertEqual(problems, ['The TOTAL is too large.'])

    def test_an_amount_with_no_part_name_is_refused_never_dropped(self):
        _, problems = read_old_bill(
            _query(_sample_post(part_name=['Drive belt', '', 'Distilled water', 'Spark plugs', ''])), TODAY)
        self.assertIn('Part row 2 has a quantity or amount but no part name.', problems)

    def test_the_number_and_the_date_must_agree(self):
        _, problems = read_old_bill(_query(_sample_post(year='25')), TODAY)
        self.assertIn('2026 bill', ' '.join(problems))

    def test_a_blank_form_says_what_is_missing(self):
        blank = {key: ([''] * len(value) if isinstance(value, list) else '')
                 for key, value in _sample_post().items()}
        _, problems = read_old_bill(_query(blank), TODAY)
        for expected in ('Enter the date on the bill.', 'Enter the bill number.',
                         'Enter the registration number.'):
            self.assertIn(expected, problems)


class TheOldBillScreensTests(TestCase):

    def setUp(self):
        self.office = _user('Office')
        self.client.force_login(self.office)

    def test_floor_cannot_open_any_of_them(self):
        self.client.force_login(_user('Floor'))
        bill = _the_sample_bill()
        for url in (reverse('old_bill_list'), reverse('old_bill_add'),
                    reverse('old_bill_edit', args=[bill.pk])):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(reverse('old_bill_delete', args=[bill.pk])).status_code, 403)
        self.assertTrue(OldBill.objects.filter(pk=bill.pk).exists())

    def test_the_add_page_opens_ONE_row_of_each(self):
        # The next row opens as the last one is typed into, so nobody scrolls
        # past empty boxes. (+ 1: the row template the script copies.)
        html = self.client.get(reverse('old_bill_add')).content.decode()
        self.assertEqual(html.count('<input name="job"'), 1 + 1)
        self.assertEqual(html.count('<input name="part_name"'), 1 + 1)

    def test_an_edit_and_a_refusal_carry_their_lines_and_one_open_row(self):
        bill = _the_sample_bill()
        html = self.client.get(reverse('old_bill_edit', args=[bill.pk])).content.decode()
        self.assertEqual(html.count('<input name="job"'), 3 + 1 + 1)
        self.assertEqual(html.count('<input name="part_name"'), 4 + 1 + 1)

        # Blank rows left at the end of a refused form come back as ONE.
        refused = self.client.post(reverse('old_bill_add'), _sample_post(
            registration_number='',
            job=['Coolant replaced', '', '', ''],
            part_name=['Coolant', '', ''], part_qty=['', '', ''], part_amount=['2,820.00', '', ''],
        )).content.decode()
        self.assertEqual(refused.count('<input name="job"'), 1 + 1 + 1)
        self.assertEqual(refused.count('<input name="part_name"'), 1 + 1 + 1)

    def test_on_a_phone_the_part_rows_scroll_sideways_like_the_job_cards(self):
        # Nothing in this suite executes CSS, so the declarations are read.
        html = self.client.get(reverse('old_bill_add')).content.decode()
        phone = html.split('@media (max-width: 575.98px)', 1)[1].split('</style>', 1)[0]
        self.assertIn('#obParts { overflow-x: auto; scroll-padding-left: 40px; }', phone)
        # The name keeps a real width, and the row number stays pinned.
        self.assertIn('grid-template-columns: 20px 240px 64px 120px', phone)
        self.assertIn('position: sticky; left: 0;', phone.split('.ob-row--part > :first-child', 1)[1][:60])
        # Nothing restacks the row: every piece stays on the one line.
        self.assertNotIn('grid-row: 3', phone)

    def test_the_total_is_a_figure_on_the_right_with_nothing_to_type(self):
        html = self.client.get(reverse('old_bill_add')).content.decode()
        self.assertNotIn('name="paper_total"', html)
        self.assertRegex(html, r'<output id="obTotal" class="ob-total-figure">')
        self.assertIn('Verify this total with the XL bill.', html)
        # The sentence that printed the running sum is gone: with the sum on
        # screen there is nothing left for it to say.
        self.assertNotIn('so far', html)

    def test_the_vehicle_boxes_work_the_way_the_job_cards_do(self):
        html = self.client.get(reverse('old_bill_add')).content.decode()
        # The plate is typed in capitals.
        self.assertRegex(html, r'<input id="obReg"[^>]*autocapitalize="characters"')
        self.assertIn('#obReg { text-transform: uppercase; }', html)
        # MAKE and MODEL use the Job Card's own dropdown (script.js), not a datalist.
        self.assertRegex(html, r'<input id="obMake"[^>]*class="form-control autocomplete-brand"')
        self.assertRegex(html, r'<input id="obModel"[^>]*class="form-control autocomplete-model"')
        self.assertNotIn('<datalist', html)   # a phone shows one above the keyboard
        # A known plate is asked about, as on the Job Card.
        self.assertIn(reverse('known_car_lookup'), html)

    def test_job_and_part_suggestions_drop_down_under_the_box_like_the_job_cards(self):
        # The Job Card's own markup: the box, then an empty Bootstrap list-group
        # that the script fills. Never a datalist, which a phone or tablet shows
        # as a strip above the keyboard. Once for the open row, once for the
        # row template the script copies.
        html = self.client.get(reverse('old_bill_add')).content.decode()
        for name in ('job', 'part_name'):
            pattern = (r'<input name="' + name + r'"[^>]*>\s*'
                       r'<div class="list-group autocomplete-suggestions"></div>')
            self.assertEqual(len(re.findall(pattern, html)), 2, name)
        self.assertNotIn(' list="', html)
        # The rows are added after the page loads, so the script must not use
        # the Job Card's per-box classes, which script.js wires only on load.
        self.assertNotIn('autocomplete-spare', html)
        # An edit carries the same box on every saved line.
        bill = _the_sample_bill()
        edit = self.client.get(reverse('old_bill_edit', args=[bill.pk])).content.decode()
        self.assertEqual(len(re.findall(
            r'<input name="part_name"[^>]*>\s*<div class="list-group autocomplete-suggestions"></div>', edit)),
            4 + 1 + 1)

    def test_the_form_takes_no_customer_name(self):
        # Not needed on these bills (the owners' call, 2026-09-17).
        html = self.client.get(reverse('old_bill_add')).content.decode()
        self.assertNotIn('name="customer_name"', html)
        self.assertNotIn('Customer name', html)
        # A name already stored is left alone by an edit — never wiped, and a
        # posted one is not read.
        bill = _the_sample_bill()
        OldBill.objects.filter(pk=bill.pk).update(customer_name='Anwar')
        response = self.client.post(reverse('old_bill_edit', args=[bill.pk]),
                                    _sample_post(customer_name='Someone else'))
        self.assertEqual(response.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.customer_name, 'Anwar')

    def test_part_name_suggests_the_categories_and_the_master_list(self):
        # A live bill prints a stock part under its CATEGORY ("Engine Oil"), and
        # the Excel bills were written the same way, so both lists are offered.
        from inventory.models import Category
        Category.objects.create(name='Engine Oil')
        SparePart.objects.create(name='Drive belt tensioner')
        response = self.client.get(reverse('old_bill_add'))
        known = response.context['known_parts']
        self.assertIn('Engine Oil', known['categories'])
        self.assertIn('Drive belt tensioner', known['spares'])
        html = response.content.decode()
        self.assertIn('<script id="obKnownParts" type="application/json">', html)   # json_script, never interpolated
        self.assertIn('Drive belt tensioner', html.split('id="obKnownParts"', 1)[1].split('</script>', 1)[0])

    def test_saving_an_old_bill_adds_nothing_to_either_list(self):
        # Suggestions are READ ONLY: typing 800 Excel bills must not fill the
        # master lists with every slip in them.
        from inventory.models import Category
        spares, categories = SparePart.objects.count(), Category.objects.count()
        self.client.post(reverse('old_bill_add'), _sample_post(
            part_name=['A part nobody has heard of', 'Coolant', '', '', ''],
            part_qty=['', '', '', '', ''], part_amount=['14,700', '75', '', '', '']))
        self.assertEqual(OldBill.objects.count(), 1)
        self.assertEqual(SparePart.objects.count(), spares)
        self.assertEqual(Category.objects.count(), categories)

    def test_the_part_suggestions_strip_the_job_cards_own_verbs(self):
        # old-bill-core.js takes a part name off a job line by removing one of
        # these verbs. They are the Job Card's list, word for word and in order.
        base = Path(settings.BASE_DIR) / 'workshop'
        def verbs(path, name):
            source = (base / path).read_text(encoding='utf-8')
            block = re.search(name + r'\s*=\s*\[(.*?)\]', source, re.S).group(1)
            return re.findall(r"'([^']+)'", block)
        job_card = verbs('templates/workshop/jobcard/jobcard_form.html', 'const VERBS')
        old_bill = verbs('static/js/old-bill-core.js', 'var JOB_VERBS')
        self.assertEqual(len(job_card), 5)
        self.assertEqual(old_bill, job_card)

    def test_save_and_add_next_keeps_the_month_and_year(self):
        response = self.client.post(reverse('old_bill_add'), _sample_post(after='next'))
        bill = OldBill.objects.get()
        self.assertRedirects(
            response, f"{reverse('old_bill_add')}?month=4&year=26&saved={bill.pk}",
            fetch_redirect_response=False,
        )
        self.assertEqual(bill.total_amount, Decimal('37075.00'))
        self.assertEqual(bill.registration_number, 'HR 26 BQ 1572')
        self.assertEqual(bill.created_by, self.office)
        self.assertEqual(bill.part_lines.count(), 4)

        page = self.client.get(response['Location']).content.decode()
        self.assertIn('Last saved: <strong>JB-26-097</strong>', page)
        self.assertIn('value="4"', page)

    def test_save_and_close_lands_on_that_month(self):
        response = self.client.post(reverse('old_bill_add'), _sample_post(after='close'))
        self.assertRedirects(response, f"{reverse('old_bill_list')}?month=2026-04",
                             fetch_redirect_response=False)

    def test_a_refusal_keeps_everything_typed(self):
        response = self.client.post(reverse('old_bill_add'), _sample_post(num_seq=''))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OldBill.objects.count(), 0)
        html = response.content.decode()
        self.assertIn('The bill number must be like 097.', html)
        for typed in ('value="apr"', 'value="hr 26 bq 1572"', 'value="2,820.00"', 'value="Drive belt"'):
            self.assertIn(typed, html)

    def test_the_same_number_twice_is_refused_and_named(self):
        self.client.post(reverse('old_bill_add'), _sample_post())
        response = self.client.post(reverse('old_bill_add'), _sample_post())
        self.assertEqual(OldBill.objects.count(), 1)
        self.assertIn('JB-26-097 is already in', response.content.decode())

    def test_an_edit_replaces_the_lines_and_keeps_the_bill(self):
        bill = _the_sample_bill()
        response = self.client.post(
            reverse('old_bill_edit', args=[bill.pk]),
            _sample_post(part_name=['Coolant', ''], part_qty=['', ''],
                         part_amount=['14,775', ''], mileage='16,800 km'),
        )
        self.assertEqual(response.status_code, 302)
        bill.refresh_from_db()
        self.assertEqual(bill.mileage, '16800')
        self.assertEqual(list(bill.part_lines.values_list('name', flat=True)), ['Coolant'])
        self.assertEqual(bill.total_amount, Decimal('37075.00'))

    def test_delete_is_a_post_and_removes_the_bill(self):
        bill = _the_sample_bill()
        self.assertEqual(self.client.get(reverse('old_bill_delete', args=[bill.pk])).status_code, 405)
        self.client.post(reverse('old_bill_delete', args=[bill.pk]))
        self.assertFalse(OldBill.objects.exists())

    def test_the_delete_waits_in_the_menu_and_asks_first(self):
        bill = _the_sample_bill()
        html = self.client.get(reverse('old_bill_edit', args=[bill.pk])).content.decode()
        self.assertIn('data-confirm-title="Delete this old bill?"', html)
        # Inside the ⋮ at the top — before the bill's own form, so never
        # beside the Save buttons and never nested in the form it would delete.
        menu_at = html.index('class="dropdown-menu dropdown-menu-end ob-menu"')
        delete_at = html.index(f'action="{reverse("old_bill_delete", args=[bill.pk])}"')
        self.assertLess(menu_at, delete_at)
        self.assertLess(delete_at, html.index('<form method="post" id="obForm"'))
        self.assertEqual(html.count(reverse('old_bill_delete', args=[bill.pk])), 1)
        # A bill not saved yet has nothing to delete.
        add = self.client.get(reverse('old_bill_add')).content.decode()
        self.assertNotIn('ob-menu"', add)

    def test_the_drawer_offers_old_bills_to_office(self):
        html = self.client.get(reverse('old_bill_list')).content.decode()
        self.assertIn(f'href="{reverse("old_bill_list")}"', html)


class TheOldBillsPageTests(TestCase):
    """The page three people split the paper pile on."""

    def setUp(self):
        self.client.force_login(_user('Office'))
        for number, day in (('JB-25-001', date(2025, 3, 4)), ('JB-25-003', date(2025, 3, 2)),
                            ('JB-25-004', date(2025, 5, 1)), ('JB-26-002', date(2026, 4, 10)),
                            ('JB-26-001', date(2026, 4, 10))):
            _bill(number=number, bill_date=day, registration_number=f'KL {number[-3:]}')

    def get(self, **params):
        return self.client.get(reverse('old_bill_list'), params)

    def test_it_opens_on_the_newest_month(self):
        response = self.get()
        self.assertEqual(response.context['month_label'], 'Apr 2026')
        self.assertEqual([b.bill_number for b in response.context['rows']], ['JB-26-001', 'JB-26-002'])

    def test_a_month_lists_in_the_paper_files_order(self):
        rows = self.get(month='2025-03').context['rows']
        # By date first — then by NUMBER, never by the order they were typed.
        self.assertEqual([b.bill_number for b in rows], ['JB-25-003', 'JB-25-001'])

    def test_every_month_carries_its_count(self):
        years = {y['year']: y for y in self.get().context['years']}
        self.assertEqual(list(years), [2026, 2025])
        months_2025 = {m['label']: m['count'] for m in years[2025]['months']}
        self.assertEqual((months_2025['Mar'], months_2025['May'], months_2025['Apr']), (2, 1, 0))

    def test_a_number_not_typed_yet_is_named(self):
        years = {y['year']: y for y in self.get().context['years']}
        self.assertEqual(years[2025]['missing_named'], ['JB-25-002'])
        self.assertEqual(years[2026]['missing_count'], 0)

    @override_settings(LAST_EXCEL_BILL_NUMBER='JB-26-005')
    def test_in_the_go_live_year_the_gap_runs_to_the_last_excel_bill(self):
        years = {y['year']: y for y in self.get().context['years']}
        self.assertEqual(years[2026]['missing_named'], ['JB-26-003', 'JB-26-004', 'JB-26-005'])
        self.assertContains(self.get(), 'The last Excel bill is JB-26-005')

    def test_many_missing_numbers_are_counted_not_listed(self):
        _bill(number='JB-25-400', bill_date=date(2025, 12, 1), registration_number='KL 400')
        years = {y['year']: y for y in self.get().context['years']}
        self.assertEqual(years[2025]['missing_count'], 396)
        self.assertEqual(years[2025]['missing_named'], [])

    def test_a_search_reaches_across_every_month(self):
        response = self.get(q='kl 00')
        self.assertEqual(response.context['page_obj'].paginator.count, 5)
        self.assertNotIn('years', response.context)

    def test_an_unreadable_month_falls_back_to_the_newest(self):
        self.assertEqual(self.get(month='2025-13').context['month_label'], 'Apr 2026')
        self.assertEqual(self.get(month='junk').context['month_label'], 'Apr 2026')

    def test_the_add_button_keeps_its_name_when_it_goes_icon_only(self):
        # Below 576px the button shows only its + so the title is not cut to
        # "Old B…"; the name rides in aria-label.
        self.assertContains(self.get(), 'class="obl-new" aria-label="Add Old Bill"')

    def test_nobody_is_shown_the_unset_setting(self):
        # Removed on the owner's call (2026-09-17): nobody who opens this page
        # can set it — it is a Railway setting, set on go-live day (runbook
        # §3.5b) — and a paragraph naming a code setting was weight on every
        # visit. Once set, the page says where the system's numbers start.
        self.assertNotContains(self.get(), 'LAST_EXCEL_BILL_NUMBER')
        self.client.force_login(_user('Owner'))
        self.assertNotContains(self.get(), 'LAST_EXCEL_BILL_NUMBER')
        with override_settings(LAST_EXCEL_BILL_NUMBER='JB-26-245'):
            self.assertContains(self.get(), 'The last Excel bill is JB-26-245')


class OldBillsOnACarProfileTests(TestCase):
    """
    Old bills complete a car's history on its profile — yellow, at the end,
    numbered on their own — and never touch the money tiles or the visits.
    """

    def setUp(self):
        self.client.force_login(_user('Office'))
        self.reg = 'KL 07 CD 4321'
        for day, amount in ((date(2026, 9, 1), '5000'), (date(2026, 9, 5), '7000')):
            card = JobCard.objects.create(
                admitted_date=day, brand_name='Mercedes-Benz', model_name='C220d',
                registration_number=self.reg, labour_amount=Decimal(amount),
                completed=True, completed_date=day,
            )
            card.update_totals()

    def profile(self, reg=None, **params):
        return self.client.get(reverse('car_profile_detail', args=[reg or self.reg]), params)

    def listing(self, **params):
        return self.client.get(reverse('car_profile_list'), params)

    def test_the_money_tiles_and_visit_numbers_do_not_move(self):
        before = self.profile().context
        before_info = {k: before['car_info'][k] for k in ('billed', 'discount', 'paid', 'outstanding', 'visits')}
        before_numbers = [bill.visit_number for bill in before['bills']]

        _bill(number='JB-25-120', bill_date=date(2025, 2, 14), registration_number=self.reg)

        after = self.profile().context
        self.assertEqual({k: after['car_info'][k] for k in before_info}, before_info)
        self.assertEqual([bill.visit_number for bill in after['bills']], before_numbers)
        self.assertEqual(after['car_info']['old_bills'], 1)

    def test_old_bills_are_numbered_on_their_own_from_the_oldest(self):
        _bill(number='JB-24-050', bill_date=date(2024, 11, 5), registration_number=self.reg)
        _bill(number='JB-25-120', bill_date=date(2025, 2, 14), registration_number=self.reg)
        rows = self.profile().context['old_bills']
        self.assertEqual([(b.bill_number, b.old_number) for b in rows],
                         [('JB-25-120', 2), ('JB-24-050', 1)])
        page = self.profile().content.decode()
        self.assertIn('class="cd-list cd-list-old"', page)   # the element, not the CSS rule
        self.assertIn('Old bills: 2', page)

    def test_a_car_known_only_from_old_bills_has_a_profile_with_no_money_tiles(self):
        _bill(number='JB-25-121', bill_date=date(2025, 3, 1), registration_number='HR 26 BQ 1572',
              customer_name='Anwar')
        response = self.profile('HR 26 BQ 1572')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('class="cd-stats"', html)   # the element; the rule is in the page's CSS
        self.assertNotIn('Visit history', html)
        self.assertIn('Old bills: 1', html)
        self.assertEqual(response.context['car_info']['brand'], 'Porsche')
        self.assertEqual(response.context['car_info']['customer'], 'Anwar')
        self.assertFalse(response.context['car_info']['has_color'])

    def test_a_plate_with_nothing_at_all_is_still_a_404(self):
        self.assertEqual(self.profile('NOSUCHCAR').status_code, 404)

    def test_the_list_carries_a_car_known_only_from_old_bills(self):
        _bill(number='JB-25-121', bill_date=date(2025, 3, 1), registration_number='HR 26 BQ 1572',
              customer_name='Anwar')
        rows = {row['registration']: row for row in self.listing().context['car_profiles']}
        self.assertEqual(rows['HR 26 BQ 1572']['old_bills'], 1)
        self.assertEqual(rows['HR 26 BQ 1572']['total_visits'], 0)
        self.assertEqual(rows['HR 26 BQ 1572']['last_activity'], date(2025, 3, 1))
        self.assertEqual(rows[self.reg]['total_visits'], 2)
        # ...and a search by the name on the old bill finds it.
        found = [row['registration'] for row in self.listing(q='anwar').context['car_profiles']]
        self.assertEqual(found, ['HR 26 BQ 1572'])

    def test_a_car_with_both_counts_both_separately(self):
        _bill(number='JB-25-120', bill_date=date(2025, 2, 14), registration_number=self.reg)
        row = next(r for r in self.listing().context['car_profiles'] if r['registration'] == self.reg)
        self.assertEqual((row['total_visits'], row['old_bills']), (2, 1))
        self.assertIn('2 visits', self.listing().content.decode())

    def test_an_old_bill_never_reorders_the_cars_that_have_job_cards(self):
        other = JobCard.objects.create(
            admitted_date=date(2026, 9, 3), brand_name='Audi', model_name='A4',
            registration_number='KL 01 AA 0001',
        )
        before = [r['registration'] for r in self.listing().context['car_profiles']]
        _bill(number='JB-25-120', bill_date=date(2025, 2, 14), registration_number=other.registration_number)
        after = [r['registration'] for r in self.listing().context['car_profiles']]
        self.assertEqual(after, before)

    def test_the_old_bill_tile_outranks_the_latest_visit_tile(self):
        # `:first-child` counts as a class, so a three-class rule loses to the
        # later `.cd-visit:first-child .cd-visit-no` — measured blue once.
        from django.template.loader import get_template
        source = get_template('workshop/car_profiles/car_profile_detail.html').template.source
        self.assertIn('.cd-list-old .cd-visit.cd-visit-old .cd-visit-no', source)


# -----------------------------------------------------------------------------
# THE DOCUMENTS
# -----------------------------------------------------------------------------

class AnOldBillReprintsLikeTheBillTests(TestCase):
    """`build_old_bill` fills the same sheet `build_invoice` does, row for row."""

    def test_it_has_every_key_a_job_card_bill_has(self):
        from workshop.invoice import build_invoice, build_old_bill

        card = _card(date(2026, 9, 1))
        card_keys = set(build_invoice(card))
        self.assertEqual(set(build_old_bill(_the_sample_bill())), card_keys)

    def test_the_sample_bill_prints_the_way_its_paper_does(self):
        from workshop.invoice import build_old_bill

        doc = build_old_bill(_the_sample_bill())
        self.assertEqual(doc['date'], date(2026, 4, 10))
        self.assertEqual(doc['job_subtotal'], Decimal('22300'))
        self.assertEqual(doc['part_subtotal'], Decimal('14775'))
        self.assertEqual(doc['grand_total'], Decimal('37075'))
        self.assertIsNone(doc['settlement'])            # no PAID stamp: the paper had none
        rows = {line.name: line for line in doc['part_lines']}
        self.assertFalse(rows['Drive belt'].priced)      # blank on paper, blank here
        self.assertIsNone(rows['Coolant'].display_quantity)
        self.assertEqual(rows['Spark plugs'].display_quantity, Decimal('6'))
        self.assertEqual(rows['Spark plugs'].unit_price, Decimal('1980.00'))

    def test_a_job_card_bill_still_carries_its_admitted_date(self):
        from workshop.invoice import build_invoice

        self.assertEqual(build_invoice(_card(date(2026, 9, 1)))['date'], date(2026, 9, 1))


class OldBillsOnTheCarDocumentsTests(TestCase):

    REG = 'KL 07 CD 4321'

    def setUp(self):
        self.client.force_login(_user('Office'))
        self.card = JobCard.objects.create(
            admitted_date=date(2026, 9, 5), brand_name='Mercedes-Benz', model_name='C220d',
            registration_number=self.REG, mileage='60000', labour_amount=Decimal('5000'),
            completed=True, completed_date=date(2026, 9, 5),
        )
        self.card.update_totals()
        self.old = _bill(number='JB-25-120', bill_date=date(2025, 2, 14), registration_number=self.REG,
                         brand_name='Mercedes-Benz', model_name='C220d', mileage='40000',
                         labour_amount=Decimal('3000'))
        OldBillPartLine.objects.create(old_bill=self.old, name='Engine Oil', quantity=Decimal('5'),
                                       amount=Decimal('4500'))
        self.old.update_totals()

    def history(self):
        from workshop.service_history import build_service_history

        cards = JobCard.objects.filter(registration_number=self.REG).prefetch_related(
            'labours', 'concerns', 'spares__item__category')
        olds = OldBill.objects.filter(registration_number=self.REG).prefetch_related(
            'job_lines', 'part_lines')
        return build_service_history(list(cards), old_bills=list(olds))

    def test_old_bills_are_numbered_apart_and_no_visit_number_moves(self):
        visits = self.history()['visits']
        self.assertEqual([(v.is_old_bill, v.number) for v in visits], [(False, 1), (True, 1)])

    def test_they_join_the_history_but_not_its_money(self):
        summary = self.history()['summary']
        self.assertEqual(summary.visits, 1)
        self.assertEqual(summary.old_bills, 1)
        self.assertEqual(summary.old_bills_total, Decimal('7500'))
        self.assertEqual(summary.total_billed, Decimal('5000'))      # the Car Profile's figure
        self.assertEqual(summary.first_date, date(2025, 2, 14))      # the car's whole life here
        self.assertEqual(summary.distance, 20000)

    def test_a_part_on_an_old_bill_starts_its_chain(self):
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Engine Oil', source=JobCardSpareItem.SOURCE_SHOP,
            quantity=Decimal('5'), total_price=Decimal('5000'),
        )
        chain = next(c for c in self.history()['chains'] if c.key == 'engine oil')
        self.assertEqual([i.number for i in chain.instances], [2, 1])
        self.assertEqual(chain.instances[1].life_km, 20000)          # old bill → next change

    def test_the_sheet_prints_them_as_old_bills(self):
        url = reverse('car_service_history_sheet', args=[self.REG])
        html = self.client.get(url, {'amount': '1', 'work': '1', 'concerns': '1'}).content.decode()
        self.assertIn('OLD BILL 1', html)
        self.assertIn('VISIT 1', html)
        self.assertIn('OLD BILLS: 1', html)
        self.assertIn('not in the total', html)

    def test_all_invoices_carries_the_old_bills_after_the_job_cards(self):
        response = self.client.get(reverse('car_all_invoices', args=[self.REG]))
        self.assertEqual(response.context['count'], 2)
        self.assertEqual([item['jobcard'].bill_number for item in response.context['sheets']],
                         [self.card.bill_number, 'JB-25-120'])

    def test_one_old_bill_prints_on_its_own_with_an_edit(self):
        response = self.client.get(reverse('old_bill_invoice', args=[self.old.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('JB-25-120', html)
        self.assertIn(reverse('old_bill_edit', args=[self.old.pk]), html)
        self.assertEqual(response.context['document_title'], 'Mercedes-Benz C220d KL 07 CD 4321 (JB-25-120)')

    def test_a_car_known_only_from_old_bills_has_both_documents(self):
        _bill(number='JB-25-121', bill_date=date(2025, 3, 1), registration_number='HR 26 BQ 1572')
        for name in ('car_service_history', 'car_service_history_sheet', 'car_all_invoices'):
            response = self.client.get(reverse(name, args=['HR 26 BQ 1572']), {'amount': '1'})
            self.assertEqual(response.status_code, 200, name)
        sheet = self.client.get(reverse('car_service_history_sheet', args=['HR 26 BQ 1572']),
                                {'amount': '1'}).content.decode()
        # No system visit to total, so no "TOTAL BILLED ₹0.00".
        self.assertNotIn('TOTAL BILLED', sheet)

    def test_a_plate_with_nothing_is_still_a_404_on_every_document(self):
        for name in ('car_service_history', 'car_service_history_sheet', 'car_all_invoices'):
            self.assertEqual(self.client.get(reverse(name, args=['NOSUCHCAR'])).status_code, 404, name)

    def test_the_profile_row_opens_the_printed_bill(self):
        html = self.client.get(reverse('car_profile_detail', args=[self.REG])).content.decode()
        self.assertIn(reverse('old_bill_invoice', args=[self.old.pk]), html)


class AReturningCarFromTheExcelYearsIsRecognisedTests(TestCase):
    """The known-plate lookup reads old bills — for what their paper carries."""

    def test_a_plate_known_only_from_an_old_bill_fills_the_car_and_offers_the_name(self):
        from workshop.known_car import known_car

        _bill(customer_name='Anwar')
        answer = known_car('hr 26 bq 1572', include_customer=True)
        self.assertTrue(answer['found'])
        self.assertEqual((answer['brand_name'], answer['model_name']), ('Porsche', 'Boxster'))
        self.assertEqual(answer['customer_name'], 'Anwar')
        # Never a phone number or a colour: the paper has neither.
        self.assertEqual((answer['customer_contact'], answer['car_color']), ('', ''))

    def test_floor_still_gets_no_customer_keys(self):
        from workshop.known_car import known_car

        _bill(customer_name='Anwar')
        self.assertNotIn('customer_name', known_car('HR 26 BQ 1572'))

    def test_a_newer_job_card_answers_before_an_old_bill(self):
        from workshop.known_car import known_car

        _bill(customer_name='Old Owner')
        JobCard.objects.create(
            admitted_date=date(2026, 9, 1), brand_name='Porsche', model_name='Cayenne',
            registration_number='HR 26 BQ 1572', customer_name='New Owner',
            customer_contact='9207217978', car_color='White',
        )
        answer = known_car('HR 26 BQ 1572', include_customer=True)
        self.assertEqual(answer['model_name'], 'Cayenne')
        self.assertEqual((answer['customer_name'], answer['customer_contact']), ('New Owner', '9207217978'))
        self.assertEqual(answer['car_color'], 'White')


class AMasterListRenameReachesOldBillsTests(TestCase):
    """One car, one spelling — on its old bills as on its job cards."""

    def test_a_spare_rename_relabels_old_bill_parts(self):
        from workshop.master_data import rename_spare
        from workshop.models import SparePart

        bill = _the_sample_bill()
        rename_spare(SparePart.objects.create(name='Coolant'), 'Engine Coolant')
        self.assertTrue(bill.part_lines.filter(name='Engine Coolant').exists())

    def test_a_brand_and_model_rename_relabel_old_bills(self):
        from workshop.master_data import rename_brand, rename_model

        brand = CarBrand.objects.create(name='Porsche')
        model = CarModel.objects.create(brand=brand, name='Boxster')
        bill = _bill()
        rename_model(model, 'Boxster 718')
        rename_brand(brand, 'Porsche AG')
        bill.refresh_from_db()
        self.assertEqual((bill.brand_name, bill.model_name), ('Porsche AG', 'Boxster 718'))

    def test_a_rename_moves_no_money_on_the_old_bill(self):
        from workshop.master_data import rename_spare
        from workshop.models import SparePart

        bill = _the_sample_bill()
        rename_spare(SparePart.objects.create(name='Spark plugs'), 'Spark Plug Set')
        bill.refresh_from_db()
        self.assertEqual(bill.total_amount, Decimal('37075'))
