"""
The job card, READ ONLY — laid out to the owner's own sketch (2026-08-18).

Two rebuilds in two days, and the second is the one that matters. The first kept
the edit form's shape: a section per fact, a label over every value. The owner
read it back as "still useless because it's confusing" and drew what they wanted
instead — three unlabelled lines of identity, then the four lists as a 2×2 grid,
with nothing under a part but its dates and its figures.

So the rule this file exists to protect is: THERE ARE NO LABELS. Every caption
removed is one fewer thing to read on a page four people open twenty times a
day, and the owner's own reasoning is why that is safe — "few times repeatedly
see, humans will understand and adapt easily." A test that lets a "Qty:" or a
"Customer:" creep back is letting the page slide back to the version that was
rejected.

What else is pinned:
  · nothing on it posts — that is the whole argument for it existing at all;
  · Office and Owner only, which is what lets the layout be this dense;
  · the four sections are the dashboard drawer's, not an approximation of it;
  · and no figure is ever printed twice on the money line.
"""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from workshop.models import JobCard, JobCardSpareItem, Mechanic, SpareShop

DETAIL = 'workshop/templates/workshop/jobcard/jobcard_detail.html'
BOARD = 'workshop/templates/workshop/dashboard/dashboard_home.html'


class DetailBase(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.office = User.objects.create_user('dv_office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.floor = User.objects.create_user('dv_floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))

        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.mech = Mechanic.objects.create(name='Amlah')
        self.job = JobCard.objects.create(
            admitted_date=date(2026, 1, 1), brand_name='Audi', model_name='A4',
            registration_number='KL10AA1000', customer_name='Rahul Menon',
            customer_contact='9876500000', car_color='Red',
            lead_mechanic=self.mech, mileage=10021,
            notes='Noise only when cold.')
        self.spare = JobCardSpareItem.objects.create(
            job_card=self.job, shop=self.shop,
            source=JobCardSpareItem.SOURCE_SHOP,
            spare_part_name='Front Brake Pad Set', quantity=D('2'),
            unit_price=D('4200'), total_price=D('6000'), status='RECEIVED',
            ordered_date=date(2026, 1, 5), received_date=date(2026, 1, 20))

    def html(self, user=None):
        """
        The PAGE REGION only — `<main>` inward.

        base.html wraps every page in a nav bar, a drawer and a logout modal,
        and that modal is a real <form>. A whole-page `assertNotIn('<form')`
        fails on furniture this page does not own, and a whole-page `assertIn`
        can pass on it. Every assertion below is about this template.
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
        The argument for keeping this page rather than redirecting to the edit
        form is that a read cannot become a write by accident. One <form> here
        would end it.
        """
        body = self.html()
        for tag in ('<form', '<textarea', '<input', '<select'):
            self.assertNotIn(tag, body,
                             'a read-only page has grown a %s' % tag)

    def test_it_carries_no_table_and_needs_no_sideways_scroll(self):
        """
        It used to hold two: Inventory (4 columns) and Spare Parts (6). On the
        one page whose only job is to be read, that meant scrolling sideways to
        find out when a part arrived.
        """
        self.assertNotIn('<table', self.html())
        self.assertNotIn('table-responsive', self.html())


class ThePageIsDataWithNoLabelsTests(DetailBase):
    """
    The owner's design, and the reason for it: labels are what you need the
    FIRST time and what gets in the way every time after.
    """

    def test_the_three_identity_lines_carry_the_owners_facts_in_order(self):
        """
        Line 1 is the car, line 2 the four facts, line 3 the note. The
        SEPARATORS between the facts are drawn in CSS
        (`.dv-fact + .dv-fact::before`) rather than written into the markup, so
        a missing value takes its own separator with it and a stray dot is not
        expressible — which is why this asserts the values and their ORDER
        rather than one joined string.
        """
        import re
        body = self.html()

        line1 = body.split('dv-line1', 1)[1].split('</h1>', 1)[0]
        self.assertIn('Audi A4', line1)
        self.assertIn('KL10AA1000', line1)
        self.assertIn('01/01/2026', line1)

        line2 = body.split('dv-line2', 1)[1].split('</p>', 1)[0]
        self.assertIn('10,021 km', line2)
        self.assertIn('Amlah', line2)
        # The mechanic wears the dashboard car card's own glyph — it is the one
        # fact on the line that is a PERSON, and the board people arrive from
        # already marks it that way.
        self.assertIn('bi-person-gear', line2)

        # THE CUSTOMER IS ONE THING: name and number in one box, and the box is
        # transparent because it groups rather than emphasises.
        cust = line2.split('dv-cust"', 1)[1]
        self.assertIn('Rahul Menon', cust)
        self.assertIn('9876500000', cust)
        self.assertLess(line2.index('dv-fact'), line2.index('dv-cust'),
                        "the car's facts come before the customer's box")

        self.assertIn('Noise only when cold.', body)

    def test_a_card_with_no_customer_shows_no_empty_box(self):
        """
        The box is a grouping, so with nothing to group it is not drawn — an
        empty outline on the head would be the page announcing an absence,
        which is the one thing this layout never does.
        """
        bare = JobCard.objects.create(
            admitted_date=date(2026, 2, 2), registration_number='KL10AA3000',
            brand_name='Bmw', model_name='320d', mileage=500,
            lead_mechanic=self.mech)
        client = Client()
        client.force_login(self.office)
        body = client.get(reverse('jobcard_detail', args=[bare.pk])).content.decode()
        # `<main>` inward. `.dv-cust` is also a RULE in this page's inline
        # stylesheet, so a whole-page search finds it on every render — the trap
        # CLAUDE.md records for `.paid-box` and `.pf-critical`.
        body = body.split('<main', 1)[1].split('</main>', 1)[0]

        self.assertNotIn('dv-cust', body)
        self.assertIn('bi-person-gear', body)     # the mechanic is still there

    def test_a_missing_value_leaves_no_trace(self):
        """
        Absent is absent — no "Not recorded", no dash, and above all no stray
        comma from a separator that was written by hand. A row of grey apologies
        is what makes a page feel broken, which is the rule Car Profiles and the
        Estimate list already follow.
        """
        bare = JobCard.objects.create(
            admitted_date=date(2026, 2, 2), registration_number='KL10AA2000',
            brand_name='Bmw', model_name='320d')
        client = Client()
        client.force_login(self.office)
        body = client.get(reverse('jobcard_detail', args=[bare.pk])).content.decode()
        body = body.split('<main', 1)[1].split('</main>', 1)[0]

        for apology in ('Not recorded', 'Unassigned', 'No customer', 'None'):
            self.assertNotIn(apology, body)
        # The identity line is absent entirely rather than rendered empty.
        self.assertNotIn('dv-line2', body)
        self.assertNotIn('dv-line3', body)

    def test_a_part_carries_its_facts_and_its_figures_in_two_places(self):
        """
        The owner's list — dates, price, shop, quantity — and where each goes.
        The facts sit under the name; the two figures sit right-aligned in their
        own column so they form a line you can run an eye down, which is the
        crowding fix: joined, a row read
        "10/07/2026 – 10/07/2026 · ₹5,727 – ₹7,967".
        """
        body = self.html()
        row = body.split('Front Brake Pad Set', 1)[1]

        money = row.split('dv-money-col', 1)[1].split('</div>', 1)[0]
        self.assertIn('₹4,200', money)          # the workshop's cost, quieter
        self.assertIn('dv-cost', money)
        self.assertIn('₹6,000', money)          # what the customer pays

        meta = row.split('dv-row-meta', 1)[1].split('</div>', 1)[0]
        self.assertIn('05/01/2026 – 20/01/2026', meta)
        self.assertIn('Ajmal Auto Parts', meta)
        self.assertIn('× 2', meta)

        for caption in ('Ordered:', 'Received:', 'Qty:', 'Shop:', 'Price:',
                        'Status:', 'Shop Price', 'Customer Price'):
            self.assertNotIn(caption, body,
                             '%r is back on a page whose whole rule is that '
                             'position carries the meaning' % caption)

    def test_half_a_date_pair_still_prints_as_a_pair(self):
        """
        A spare is finished when it has been ordered AND received, so the pair
        is one item and the missing half is a dash rather than a silence — the
        rule the job card's own date chip follows.
        """
        self.spare.received_date = None
        self.spare.save()
        self.assertIn('05/01/2026 – —', self.html())

    def test_a_quantity_prints_only_above_one(self):
        """
        This workshop writes a number down only when there is more than one of
        something — the invoice and the Live Report both follow it. It is a
        figure, not a caption, so it belongs on the line of figures.
        """
        self.assertIn('× 2', self.html())
        self.spare.quantity = D('1')
        self.spare.save()
        self.assertNotIn('× 1', self.html())


class TheFourSectionsAreTheDashboardDrawersTests(DetailBase):
    """
    "These 4 exactly as Dashboard Card cards - View section" — so the values are
    copied, not approximated. The two screens show the same four lists about the
    same car, and a person moving between them should not have to re-learn
    anything.
    """

    #: Section title, and the glyph the dashboard drawer gives it.
    SECTIONS = [
        ('Customer Concerns', 'bi-exclamation-circle'),
        ('Job Performed', 'bi-tools'),
        ('Inventory Items', 'bi-box-seam'),
        ('Spare Parts', 'bi-gear-wide-connected'),
    ]

    def test_all_four_are_present_in_the_owners_order(self):
        import re
        body = self.html()
        found = re.findall(
            r'dv-sec-title">\s*<i class="bi ([a-z-]+)[^"]*"></i>\s*([A-Za-z ]+?)\s*\n',
            body)
        self.assertEqual([(n.strip(), i) for i, n in found],
                         [(n, i) for n, i in self.SECTIONS])

    def test_an_empty_section_is_still_drawn(self):
        """
        A DELIBERATE divergence from the drawer, which omits empty sections.
        The owner drew a fixed 2×2, and a fixed grid is what makes the page
        learnable — "in seconds human can get everything structure". Sections
        appearing and disappearing would move the other three every time.
        """
        body = self.html()
        self.assertIn('Customer Concerns', body)   # this card has none
        self.assertIn('Inventory Items', body)     # nor any of these

    def test_the_row_styling_is_the_drawers_own(self):
        """
        Copied value for value. If the drawer is restyled, restyle this with it;
        this test fails either way round.
        """
        import re

        def rule(path, selector):
            style = self.source(path).split('<style>', 1)[1].split('</style>', 1)[0]
            style = re.sub(r'/\*.*?\*/', '', style, flags=re.S)
            for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', style):
                if m.group(1).strip() == selector:
                    return {k.strip(): v.strip()
                            for k, _, v in (d.partition(':')
                                            for d in m.group(2).split(';')) if k.strip()}
            raise AssertionError('no %s in %s' % (selector, path))

        mine = rule(DETAIL, '.dv-sec-title')
        theirs = rule(BOARD, '.drawer-sec-title')
        for prop in ('font-size', 'font-weight', 'letter-spacing',
                     'text-transform', 'color', 'background', 'padding'):
            self.assertEqual(mine[prop], theirs[prop],
                             '%s has drifted from the dashboard drawer' % prop)

    def test_the_sections_are_one_column_at_every_width(self):
        """
        They sat 2×2 for half a day and the owner had them straightened out. A
        2×2 makes you read in a Z, and the two columns are unrelated lists of
        unrelated lengths, so the right-hand one starts wherever the left-hand
        one happened to end. One column also gives the rows the full width,
        which is what lets the money have its own right-hand column.
        """
        style = self.source(DETAIL)
        self.assertNotIn('grid-template-columns: repeat(2', style)
        self.assertIn('.dv-secs { display: flex; flex-direction: column;', style)

    def test_it_sheds_its_boxes_on_a_phone_like_the_drawer_does(self):
        """
        Below 640px the drawer drops the four boxes and keeps one hairline
        between sections — measured there, four boxes each drawing a border on
        all four sides spent 151.6px of a 375px screen on furniture.
        """
        phone = self.source(DETAIL).split('@media (max-width: 640px)', 1)[1]
        self.assertIn('.dv-sec + .dv-sec { border-top', phone)


class TheMoneyLineNeverPrintsAFigureTwiceTests(DetailBase):
    """
    Two ways one number can appear twice on a line of four, and both are
    guarded. A figure repeated in a second colour reads as a second fact and
    sends you looking for a difference that is not there.
    """

    def money(self):
        """
        The FOOTER, matched on its exact class attribute. Splitting on the bare
        string `dv-money` finds `dv-money-col` first — every part row carries one
        — and the test then asserts about a price instead of the bill. Same
        prefix trap the CSS-rule helpers in this file guard against.
        """
        body = self.html()
        return body.split('class="dv-money"', 1)[1].split('</div>', 1)[0]

    def settle(self, received, status='PAID', discount=D('0')):
        self.job.total_bill_amount = D('10000')
        self.job.received_amount = received
        self.job.discount_amount = discount
        self.job.payment_status = status
        self.job.save()

    def test_nothing_received_prints_the_bill_alone(self):
        """The balance IS the bill; the state chip carries the rest."""
        self.settle(D('0'), status='PENDING')
        line = self.money()
        self.assertEqual(line.count('10,000'), 1)
        self.assertIn('Pending', line)

    def test_paid_in_full_prints_the_bill_alone(self):
        """The receipt IS the bill."""
        self.settle(D('10000'))
        self.assertEqual(self.money().count('10,000'), 1)

    def test_a_part_payment_prints_all_three(self):
        self.settle(D('4000'), status='PARTIAL')
        line = self.money()
        self.assertIn('10,000', line)
        self.assertIn('4,000', line)
        self.assertIn('6,000', line)      # still owed

    def test_a_settled_shortfall_prints_as_the_discount(self):
        """
        On a settled walk-in the shortfall IS the discount and the balance is
        zero by construction — the deliberate rule at the top of CLAUDE.md.
        """
        self.settle(D('9000'), discount=D('1000'))
        line = self.money()
        self.assertIn('9,000', line)
        self.assertIn('1,000', line)
        self.assertIn('Paid', line)


class ItIsOfficeAndOwnerOnlyTests(DetailBase):
    """
    Changed 2026-08-18 on the owner's question — "this section only gets Office
    and Owners, right? No chance to get Floor, right?" It was `@staff_required`
    at the time, so the honest answer was no: Floor could reach it by URL and by
    the "View" button in the Vehicles-in-Workshop sidebar on the new-job-card
    screen, which is a Floor page. The customer and the money were gated inside
    the template.

    Closing the whole door is the layout's doing more than the secrecy's. Line 2
    runs mileage, mechanic, customer and phone number together with no captions,
    and every part sets the workshop's COST beside the customer's price.
    Removing two of four values from an unlabelled line does not produce a safe
    page, it produces a confusing one — so there is one audience.
    """

    def test_floor_cannot_open_it(self):
        client = Client()
        client.force_login(self.floor)
        self.assertEqual(
            client.get(reverse('jobcard_detail', args=[self.job.pk])).status_code,
            403)

    def test_office_can(self):
        self.assertIn('Front Brake Pad Set', self.html())

    def test_the_one_floor_visible_link_to_it_is_gated_too(self):
        """
        A gate must mirror its view's decorator in BOTH directions — a door
        Floor can see and not open is worse than no door
        (`InvoiceLinkVisibilityTests`).
        """
        client = Client()
        client.force_login(self.floor)
        body = client.get(reverse('jobcard_create')).content.decode()
        self.assertNotIn(reverse('jobcard_detail', args=[self.job.pk]), body)

    def test_floor_still_reads_the_same_four_lists_on_the_board(self):
        """
        What makes closing the door cheap: the dashboard car card's
        live-details drawer is these same four sections, on the screen Floor
        works from all day.
        """
        client = Client()
        client.force_login(self.floor)
        board = client.get(reverse('home')).content.decode()
        for section, _ in TheFourSectionsAreTheDashboardDrawersTests.SECTIONS:
            self.assertIn(section, board)


class SparePartsWearsOneGlyphTests(DetailBase):

    def test_it_is_the_same_glyph_everywhere_it_is_named(self):
        """
        The owner picked the dashboard's gear on 2026-08-18. Three glyphs had
        been meaning "spare parts": that gear, `bi-nut-fill` on the job-card
        form, and `bi-tools` on the Spare Shops pages — which is the JOB
        PERFORMED icon, so the section that buys parts wore the icon of the
        section that fits them.
        """
        import glob
        offenders = [path for path in glob.glob('workshop/templates/**/*.html',
                                                recursive=True)
                     if 'bi-nut' in self.source(path)]
        self.assertEqual(offenders, [],
                         'Spare Parts is wearing a second glyph again')
