"""
The job card, READ ONLY (rebuilt 2026-08-18).

The owner reported the page as "not visually comfortable" and offered two ways
out: delete it and send everything that reaches it to the edit form, or rebuild
it against the dashboard car card's live-details drawer. The rebuild won, and
the reasoning is recorded at the head of the template. What is pinned here is
the part a future edit could quietly undo:

  · it is still READ ONLY — nothing on it posts;
  · it looks like the form it mirrors, because it uses the form's own band;
  · it needs no horizontal scroller, which is what the two tables cost;
  · and it withholds exactly what the form withholds from Floor.

That last one is the only one that is a security property rather than a
courtesy. The rest are why the page exists at all: if it stops being cheaper to
read than the edit form, delete it and take the owner's other option.
"""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from workshop.models import JobCard, JobCardSpareItem, Mechanic, SpareShop

DETAIL = 'workshop/templates/workshop/jobcard/jobcard_detail.html'
FORM = 'workshop/templates/workshop/jobcard/jobcard_form.html'


class DetailBase(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.office = User.objects.create_user('dv_office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.floor = User.objects.create_user('dv_floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))

        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.mech = Mechanic.objects.create(name='Lead Tech')
        self.job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Bmw', model_name='320d',
            registration_number='KL07CD7788', customer_name='Rahul Menon',
            customer_contact='9876500000', car_color='Red',
            lead_mechanic=self.mech, mileage=41000,
            notes='Noise only when cold.')
        JobCardSpareItem.objects.create(
            job_card=self.job, shop=self.shop,
            source=JobCardSpareItem.SOURCE_SHOP,
            spare_part_name='Front Brake Pad Set', quantity=D('2'),
            unit_price=D('3200'), total_price=D('4200'), status='RECEIVED')

    def html(self, user=None):
        """
        The PAGE REGION only — `<main>` inward.

        base.html wraps every page in a nav bar, a drawer and a logout modal,
        and that modal is a real <form>. A whole-page `assertNotIn('<form')`
        therefore fails on furniture this page does not own, and a whole-page
        `assertIn` can pass on it. Every assertion below is about this template.
        """
        client = Client()
        client.force_login(user or self.office)
        body = client.get(reverse('jobcard_detail', args=[self.job.pk])).content.decode()
        return body.split('<main', 1)[1].split('</main>', 1)[0]

    @staticmethod
    def source(path):
        with open(path, encoding='utf-8') as fh:
            return fh.read()


class ItIsStillOnlyAViewTests(DetailBase):

    def test_nothing_on_the_page_posts(self):
        """
        The whole argument for keeping this page rather than redirecting to the
        edit form is that a read cannot become a write by accident. A single
        <form> here would end that.
        """
        body = self.html()
        for tag in ('<form', '<textarea', '<input', '<select'):
            self.assertNotIn(tag, body,
                             'a read-only page has grown a %s — either remove '
                             'it or take the owner\'s other option and delete '
                             'the page' % tag)

    def test_it_carries_no_table_and_needs_no_sideways_scroll(self):
        """
        It used to hold two: Inventory (4 columns) and Spare Parts (6). On the
        one page whose only job is to be read, that meant scrolling sideways to
        find out when a part arrived. The drawer shape replaced them.
        """
        self.assertNotIn('<table', self.html())
        self.assertNotIn('table-responsive', self.html())


class ItLooksLikeTheFormItMirrorsTests(DetailBase):
    """
    These two are opened minutes apart on the same car, and the complaint that
    started this was that they had stopped looking related.
    """

    def test_the_section_band_is_the_forms_own_colour(self):
        """
        Note the EXACT selector match. `.jc-sec-head` is re-used further down
        the form's stylesheet by the locked-record palette, and an `endswith`
        finds that one first — which is the trap CLAUDE.md already records for
        this class, and it reads as the band having changed colour when it has
        not.
        """
        import re

        def band(path, selector):
            style = self.source(path).split('<style>', 1)[1].split('</style>', 1)[0]
            style = re.sub(r'/\*.*?\*/', '', style, flags=re.S)
            for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', style):
                if m.group(1).strip() == selector:
                    found = re.search(r'background:\s*(#[0-9a-fA-F]{6})', m.group(2))
                    if found:
                        return found.group(1).lower()
            raise AssertionError('no %s rule in %s' % (selector, path))

        self.assertEqual(
            band(DETAIL, '.dv-sec-head'), band(FORM, '.jc-sec-head'),
            'the read-only page and the form no longer agree on the band '
            'colour — they are the same card, seen twice')

    def test_the_car_wears_its_colour_the_same_way_on_both(self):
        """
        Both exceptions travel with the rail or they are not the same rail: a
        WHITE car outlined, and a car with NO colour recorded HATCHED rather
        than tinted slate — "nobody wrote it down" is a different fact from
        "this car is grey". The test reads the DETAIL page's own markup for the
        conditions, because getting these wrong shows up as one car looking
        like two different colours across two screens.
        """
        detail = self.source(DETAIL)
        self.assertIn("jobcard.car_color == 'White'", detail)
        self.assertIn('dv-head--white', detail)
        self.assertIn('dv-head--unset', detail)
        # …and tested against `car_color`, never the resolved hex, exactly as
        # the form tests it.
        self.assertIn("{% if not jobcard.car_color %}", detail)

    def test_spare_parts_wears_the_same_glyph_everywhere_it_is_named(self):
        """
        The owner picked the dashboard's gear for Spare Parts on 2026-08-18.
        Three glyphs had been meaning "spare parts": that gear, `bi-nut-fill` on
        the job-card form, and `bi-tools` on the Spare Shops pages — which is
        the JOB PERFORMED icon, so the section that buys parts was wearing the
        icon of the section that fits them.
        """
        import glob
        offenders = []
        for path in glob.glob('workshop/templates/**/*.html', recursive=True):
            text = self.source(path)
            if 'bi-nut' in text:
                offenders.append(path)
        self.assertEqual(offenders, [],
                         'Spare Parts is wearing a second glyph again')


class FloorReadsTheCardButNotTheCustomerOrTheMoneyTests(DetailBase):
    """
    The page is `@staff_required` because Floor legitimately reads a card here —
    and that is exactly what made it the one door left open when the customer
    gate went on the FORM. A card must never say more on the page that only
    reads it than on the page that writes it.
    """

    def test_floor_is_not_told_who_the_customer_is(self):
        body = self.html(self.floor)
        self.assertNotIn('Rahul Menon', body)
        self.assertNotIn('9876500000', body)

    def test_office_is(self):
        body = self.html()
        self.assertIn('Rahul Menon', body)
        self.assertIn('9876500000', body)

    def test_floor_is_shown_no_money_at_all(self):
        """
        Not the parts prices, not the labour charge, not the billing block.
        Floor is shown cost and price nowhere in this app.
        """
        body = self.html(self.floor)
        self.assertNotIn('4200', body)
        self.assertNotIn('Total Labour', body)
        self.assertNotIn('Billing', body)

    def test_floor_still_reads_the_workshop_note(self):
        """
        The note is about the CAR, not the customer — "noise only when cold" —
        and the mechanic is usually the one who found out. So it survives the
        gate, and the section is NAMED differently for them, because a heading
        reading "Customer & Notes" over a box with no customer in it is the page
        misdescribing itself.
        """
        body = self.html(self.floor)
        self.assertIn('Noise only when cold.', body)
        self.assertIn('Workshop Note', body)
        self.assertNotIn('Customer &amp; Notes', body)

    def test_floor_still_reads_the_part_itself(self):
        """The gate is about money and identity, not about the work."""
        self.assertIn('Front Brake Pad Set', self.html(self.floor))
