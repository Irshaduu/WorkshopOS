"""
FILL FROM PDF — the Add Old Bill form, filled from the bill's own PDF.

It fills boxes and nothing else: nothing is saved and no file is kept, and the
filled form is saved through the ordinary Add, so every rule still applies.

The bills are built here as real PDFs (Helvetica, laid out on the Excel
template's own lines) rather than committed: the owners' samples are customers'
bills. `_bill_pdf()` is the four real samples' shape — a two-page bill, a job
list with a double space in it, a part with no amount, a quantity with no unit
price, all three figures on one row, and Indian commas.
"""

import io
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from workshop.models import CarBrand, CarModel, OldBill
from workshop.old_bill_pdf import (
    MAX_BYTES, UnreadablePdf, fit_to_master_list, read_bill_pdf, read_bill_text,
)


def _make_pdf(pages):
    """A real PDF: each page a list of (x, y, text), Helvetica 10pt on A4-ish."""
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    catalog, pages_ref = add(None), add(None)
    font = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    kids = []
    for items in pages:
        ops = []
        for x, y, text in items:
            safe = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            ops.append(f'BT /F1 10 Tf 1 0 0 1 {x} {y} Tm ({safe}) Tj ET')
        data = '\n'.join(ops).encode('latin-1')
        content = add(b'<< /Length %d >>\nstream\n' % len(data) + data + b'\nendstream')
        kids.append(add(
            b'<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] '
            b'/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>' % (pages_ref, font, content)
        ))
    objects[catalog - 1] = b'<< /Type /Catalog /Pages %d 0 R >>' % pages_ref
    objects[pages_ref - 1] = (b'<< /Type /Pages /Kids [' + b' '.join(b'%d 0 R' % k for k in kids)
                              + b'] /Count %d >>' % len(kids))
    out = io.BytesIO()
    out.write(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b'%d 0 obj\n' % number + body + b'\nendobj\n')
    xref = out.tell()
    out.write(b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1))
    for offset in offsets:
        out.write(b'%010d 00000 n \n' % offset)
    out.write(b'trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n'
              % (len(objects) + 1, catalog, xref))
    return out.getvalue()


_PAGE_ONE = [
    (41, 687, 'Calicut Road,Pullara'),
    (356, 664, 'DATE:'), (388, 664, '9-Feb-2026'), (465, 664, '#: JB-26-042'),
    (47, 621, 'NAME:'), (82, 621, 'Ajmal'), (348, 621, 'REG NO:'), (388, 621, 'KL 01 AB 1234'),
    (46, 607, 'MAKE :'), (82, 607, 'Mercedes Benz'), (348, 607, 'MODEL:'), (388, 607, 'E250'),
    (348, 593, 'MILAGE:'), (388, 593, '91659'),
    (51, 576, 'JOB PERFOMED'), (520, 576, 'AMOUNT'),
    (41, 558, 'Oil filter replaced'), (41, 543, 'Lower arm  replaced'),
    (391, 427, 'SUBTOTAL'), (520, 427, '2,000.00'),
    (51, 395, 'PART NAME'), (365, 395, 'QTY'), (410, 395, 'UNIT PRICE'), (520, 395, 'AMOUNT'),
    (41, 376, 'Oil filter'), (520, 376, '2,200.00'),
    (41, 356, 'Steering ball joint'),
    (41, 336, 'Distilled water'), (370, 336, '2'), (520, 336, '50.00'),
]
# Page two carries on with parts and no heading, exactly as Excel printed it.
_PAGE_TWO = [
    (41, 740, 'Engine oil'), (370, 740, '8'), (420, 740, '1200.00'), (520, 740, '9,600.00'),
    (41, 720, 'Flex disc'), (520, 720, '1,00,000.00'),
    (388, 600, 'SUBTOTAL'), (517, 600, '1,11,850.00'),
    (82, 580, 'Thank you for your business!'), (388, 580, 'TOTAL'), (509, 580, '1,13,850.00'),
    (222, 540, 'Rijas Mohd, +91 92 07 21 79 78'),
]


def _bill_pdf():
    return _make_pdf([_PAGE_ONE, _PAGE_TWO])


def _upload(data, name='Mercedes Benz E250 KL 01 AB 1234.pdf'):
    return SimpleUploadedFile(name, data, content_type='application/pdf')


class ReadingTheBillTests(SimpleTestCase):

    def test_a_two_page_bill_is_read_whole(self):
        got = read_bill_pdf(_upload(_bill_pdf()))
        self.assertEqual(got['fields'], {
            'day': '9', 'month': '2', 'year': '26', 'num_year': '26', 'num_seq': '042',
            'registration_number': 'KL 01 AB 1234', 'brand_name': 'Mercedes Benz',
            'model_name': 'E250', 'mileage': '91659', 'labour_amount': '2000',
        })
        # A double space inside a job line is not two columns.
        self.assertEqual(got['jobs'], ['Oil filter replaced', 'Lower arm replaced'])
        self.assertEqual(got['parts'], [
            {'name': 'Oil filter', 'qty': '', 'amount': '2200'},
            {'name': 'Steering ball joint', 'qty': '', 'amount': ''},   # blank, never 0
            {'name': 'Distilled water', 'qty': '2', 'amount': '50'},
            {'name': 'Engine oil', 'qty': '8', 'amount': '9600'},       # unit price skipped
            {'name': 'Flex disc', 'qty': '', 'amount': '100000'},
        ])
        self.assertEqual(got['total'], '113850')
        self.assertEqual(got['missing'], [])

    def test_the_parts_and_labour_add_up_to_the_printed_total(self):
        got = read_bill_pdf(_upload(_bill_pdf()))
        worked_out = Decimal(got['fields']['labour_amount']) + sum(
            Decimal(p['amount']) for p in got['parts'] if p['amount'])
        self.assertEqual(worked_out, Decimal(got['total']))

    def test_the_name_on_the_bill_is_not_taken(self):
        # The form has no customer name box (the owners' call, 2026-09-17).
        got = read_bill_pdf(_upload(_bill_pdf()))
        self.assertNotIn('customer_name', got['fields'])
        self.assertNotIn('Ajmal', str(got))

    def test_the_figures_on_a_row_are_told_apart_by_their_shape(self):
        text = '\n'.join([
            '  JOB PERFORMED                                   AMOUNT',
            'Spark plugs replaced',
            '                              SUBTOTAL        22,300.00₹',
            '  PART NAME                QTY     UNIT PRICE      AMOUNT',
            'Spark plugs                 6       1,980.00       11,880.00₹',
            'Distilled water             2                          50.00',
            'Wiper blade                         1,200.00        1,200.00',
            'Engine oil                 4.5                      4,500.00',
            'Drive belt',
            '                              SUBTOTAL        17,580.00',
            '  Thank you for your business!      TOTAL  ₹  39,880.00',
        ])
        got = read_bill_text(text)
        self.assertEqual(got['parts'], [
            {'name': 'Spark plugs', 'qty': '6', 'amount': '11880'},
            {'name': 'Distilled water', 'qty': '2', 'amount': '50'},
            {'name': 'Wiper blade', 'qty': '', 'amount': '1200'},     # a unit price, no qty
            {'name': 'Engine oil', 'qty': '4.5', 'amount': '4500'},
            {'name': 'Drive belt', 'qty': '', 'amount': ''},
        ])
        self.assertEqual(got['fields']['labour_amount'], '22300')
        self.assertEqual(got['total'], '39880')

    def test_the_earliest_bills_numeric_date_is_read_day_first(self):
        # The first bills print DATE: 09-07-2024, and their PDFs were created
        # on 9 July 2024 — so a numeric date is day, month, year.
        top = '  DATE:       09-07-2024          #: JB-24-03\n  JOB PERFOMED    AMOUNT\n'
        got = read_bill_text(top)
        self.assertEqual((got['fields']['day'], got['fields']['month'], got['fields']['year']),
                         ('9', '7', '24'))
        self.assertNotIn('the date', got['missing'])
        # Month first would make this the 13th month: not read, and said so.
        got = read_bill_text(top.replace('09-07-2024', '07-13-2024'))
        self.assertEqual(got['fields']['month'], '')
        self.assertIn('the date', got['missing'])

    def test_the_earliest_bills_make_and_model_on_one_line_are_split(self):
        # No MAKE line on those bills: "MODEL: LEXUS, LS430" carries both.
        got = read_bill_text('  NAME:   MUFEED        MODEL:     LEXUS, LS430\n  JOB PERFOMED    AMOUNT\n')
        self.assertEqual((got['fields']['brand_name'], got['fields']['model_name']), ('LEXUS', 'LS430'))
        # With a MAKE line, a comma in the model is left alone.
        got = read_bill_text('  MAKE :  BMW       MODEL:  X5, M Sport\n  JOB PERFOMED    AMOUNT\n')
        self.assertEqual((got['fields']['brand_name'], got['fields']['model_name']), ('BMW', 'X5, M Sport'))

    def test_both_spellings_of_the_heading_are_read(self):
        for heading in ('JOB PERFOMED', 'JOB PERFORMED'):
            got = read_bill_text(f'  {heading}    AMOUNT\nCoolant replaced\n  SUBTOTAL\n')
            self.assertEqual(got['jobs'], ['Coolant replaced'], heading)

    def test_a_bill_with_no_labour_leaves_the_box_empty(self):
        got = read_bill_text('  JOB PERFOMED    AMOUNT\nCoolant replaced\n     SUBTOTAL\n')
        self.assertEqual(got['fields']['labour_amount'], '')

    def test_what_the_pdf_does_not_give_is_named(self):
        got = read_bill_text('  JOB PERFORMED    AMOUNT\nCoolant replaced\n  SUBTOTAL\n')
        self.assertEqual(got['missing'], ['the date', 'the bill number', 'the registration', 'the TOTAL'])

    def test_a_file_that_is_not_a_bill_is_refused(self):
        with self.assertRaises(UnreadablePdf):
            read_bill_pdf(_upload(b'this is not a pdf'))
        with self.assertRaises(UnreadablePdf):
            read_bill_pdf(_upload(_make_pdf([[(41, 700, 'A letter, not a bill')]])))

    def test_a_file_far_bigger_than_a_bill_is_refused_unread(self):
        upload = _upload(_bill_pdf())
        upload.size = MAX_BYTES + 1
        with self.assertRaises(UnreadablePdf):
            read_bill_pdf(upload)


class TheMasterListSpellingTests(TestCase):

    def test_the_make_and_model_take_the_lists_spelling(self):
        brand = CarBrand.objects.create(name='Mercedes-Benz')
        CarModel.objects.create(brand=brand, name='GLC 220')
        self.assertEqual(fit_to_master_list('Mercedes Benz', 'glc220'), ('Mercedes-Benz', 'GLC 220'))

    def test_what_the_list_does_not_hold_stays_as_printed(self):
        CarBrand.objects.create(name='Mercedes-Benz')
        self.assertEqual(fit_to_master_list('Mercedes Benz', 'E 220'), ('Mercedes-Benz', 'E 220'))
        self.assertEqual(fit_to_master_list('Koenigsegg', 'Jesko'), ('Koenigsegg', 'Jesko'))
        self.assertEqual(fit_to_master_list('', ''), ('', ''))


def _user(role):
    user = User.objects.create_user(username=f'pdf_{role.lower()}', password='pw')
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


class FillFromPdfTests(TestCase):

    def setUp(self):
        self.client.force_login(_user('Office'))
        self.url = reverse('old_bill_from_pdf')

    def fill(self, data=None, follow=False):
        return self.client.post(self.url, {'pdf': _upload(data or _bill_pdf())}, follow=follow)

    def test_the_add_page_offers_it_and_the_edit_page_does_not(self):
        # By the control's id: the words are also in the page's own script.
        self.assertContains(self.client.get(reverse('old_bill_add')), 'id="obPdfForm"')
        bill = OldBill.objects.create(
            bill_number='JB-26-001', bill_date=date(2026, 1, 5), registration_number='KL 01 A 1')
        self.assertNotContains(self.client.get(reverse('old_bill_edit', args=[bill.pk])), 'id="obPdfForm"')

    def test_a_pdf_fills_the_form_and_saves_nothing(self):
        response = self.fill()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OldBill.objects.count(), 0)
        html = response.content.decode()
        for value in ('9', '26', '042', 'KL 01 AB 1234', 'E250', '91659', '2000',
                      'Oil filter replaced', 'Lower arm replaced', 'Distilled water', '100000'):
            self.assertIn(f'value="{value}"', html)
        # The printed TOTAL rides along for the check under the form's own total.
        self.assertIn('name="pdf_total" id="obPdfTotal" value="113850"', html)
        # Drawn at this address, the bill must still be saved by the Add page.
        self.assertIn(f'id="obForm" autocomplete="off" novalidate action="{reverse("old_bill_add")}"', html)
        self.assertContains(response, 'Check every line against the PDF')

    def test_the_filled_form_saves_through_the_ordinary_add(self):
        got = read_bill_pdf(_upload(_bill_pdf()))
        post = dict(got['fields'], after='close', pdf_total=got['total'])
        post['job'] = got['jobs']
        post['part_name'] = [p['name'] for p in got['parts']]
        post['part_qty'] = [p['qty'] for p in got['parts']]
        post['part_amount'] = [p['amount'] for p in got['parts']]
        self.client.post(reverse('old_bill_add'), post)
        bill = OldBill.objects.get()
        self.assertEqual(bill.bill_number, 'JB-26-042')
        self.assertEqual(bill.bill_date, date(2026, 2, 9))
        self.assertEqual(bill.total_amount, Decimal('113850'))
        self.assertEqual(bill.part_lines.count(), 5)

    def test_the_make_is_filled_in_the_master_lists_spelling(self):
        CarBrand.objects.create(name='Mercedes-Benz')
        self.assertContains(self.fill(), 'value="Mercedes-Benz"')

    def test_a_number_already_in_is_said_straight_away(self):
        OldBill.objects.create(bill_number='JB-26-042', bill_date=date(2026, 2, 9),
                               registration_number='KL 01 AB 1234', brand_name='BMW', model_name='X5')
        self.assertContains(self.fill(), 'JB-26-042 is already in')

    def test_an_unreadable_file_leaves_the_form_to_be_typed(self):
        response = self.fill(b'not a pdf at all', follow=True)
        self.assertRedirects(response, reverse('old_bill_add'))
        self.assertContains(response, 'type this bill in by hand')
        self.assertEqual(OldBill.objects.count(), 0)

    def test_no_file_is_asked_for(self):
        response = self.client.post(self.url, follow=True)
        self.assertRedirects(response, reverse('old_bill_add'))
        self.assertContains(response, "Choose the bill")

    def test_it_is_a_post_and_office_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.force_login(_user('Floor'))
        self.assertEqual(self.fill().status_code, 403)

    def test_a_refusal_after_a_fill_keeps_comparing_against_the_pdf(self):
        response = self.client.post(reverse('old_bill_add'), {
            'day': '9', 'month': '2', 'year': '26', 'num_year': '26', 'num_seq': '042',
            'registration_number': '', 'job': ['Coolant replaced'], 'labour_amount': '2000',
            'pdf_total': '113850',
        })
        self.assertContains(response, 'Not saved yet')
        self.assertContains(response, 'name="pdf_total" id="obPdfTotal" value="113850"')
