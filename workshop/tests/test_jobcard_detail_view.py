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
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from workshop.models import (
    JobCard, JobCardConcern, JobCardLabourItem, JobCardSpareItem, Mechanic,
    SpareShop)

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

    def test_the_answer_card_carries_the_owners_rows_in_order(self):
        """
        The owner's own row order, given on 2026-08-28:

            1  the car, and the kebab
            2  the plate, and the job card number
            3  mileage, and the mechanic
            4  the customer
            5  the note
            6  the money, and the state
            7  admitted / completed / settled

        Three things moved and each was asked for. The CAR gets row 1 to itself
        — it used to share the line with the plate, the dates and two filled
        buttons, and at 375px it was the thing that lost. The JOB CARD NUMBER
        joined row 2, because `bill_number` is what the workshop reads out on
        the phone and the one screen dedicated to a single card never printed
        it. The CUSTOMER took a line of its own: the row above is about the car,
        this is about a person, and on a phone the two ran together and wrapped
        anyway.

        The SEPARATOR between mileage and mechanic is drawn in CSS
        (`.dv-fact + .dv-fact::before`), so a missing value takes its own
        separator with it and a stray dot is not expressible — which is why this
        asserts the values and their ORDER rather than one joined string.
        """
        body = self.html()

        row1 = body.split('dv-line1', 1)[1].split('</h1>', 1)[0]
        self.assertIn('Audi A4', row1)
        self.assertIn('dv-dots', row1)
        self.assertNotIn('KL10AA1000', row1)      # the plate moved down a row

        row2 = body.split('dv-ids', 1)[1].split('</p>', 1)[0]
        self.assertIn('KL10AA1000', row2)
        self.assertIn(self.job.bill_number, row2)

        row3 = body.split('dv-line2', 1)[1].split('</p>', 1)[0]
        self.assertIn('10,021 km', row3)
        self.assertIn('Amlah', row3)
        # The mechanic wears the dashboard car card's own glyph — it is the one
        # fact on the line that is a PERSON, and the board people arrive from
        # already marks it that way.
        self.assertIn('bi-person-gear', row3)
        self.assertNotIn('Rahul Menon', row3)     # the customer has its own row

        # THE CUSTOMER IS ONE THING: name and number in one box, and the box is
        # transparent because it groups rather than emphasises.
        row4 = body.split('dv-cust"', 1)[1].split('</p>', 1)[0]
        self.assertIn('Rahul Menon', row4)
        self.assertIn('9876500000', row4)

        self.assertIn('Noise only when cold.', body)

        # …and every one of them is in the ONE card, above the four lists.
        card = body.split('class="dv-head', 1)[1].split('<div class="dv-secs"', 1)[0]
        for value in ('Audi A4', 'KL10AA1000', self.job.bill_number, '10,021 km',
                      'Amlah', 'Rahul Menon', 'Noise only when cold.',
                      'dv-money', 'Admitted', 'Settled'):
            self.assertIn(value, card)

    def test_a_car_with_no_name_wears_its_plate_once_not_twice(self):
        """
        With no brand or model recorded there has to be something to call the
        car, so the registration becomes the headline — and then the chip on
        row 2 would be the page saying one thing twice, a line apart. It is
        dropped; the job card number still prints, because that is a different
        fact.

        This is the money line's own rule applied to the identity: with nothing
        received the balance IS the bill, so the bill is printed once.
        """
        nameless = JobCard.objects.create(
            admitted_date=date(2026, 2, 2), registration_number='KL10AA5000')
        client = Client()
        client.force_login(self.office)
        body = client.get(reverse('jobcard_detail', args=[nameless.pk])).content.decode()
        card = body.split('<main', 1)[1].split('</main>', 1)[0]
        card = card.split('class="dv-head', 1)[1].split('<div class="dv-secs"', 1)[0]

        self.assertEqual(card.count('KL10AA5000'), 1)
        self.assertIn('dv-car', card)
        self.assertNotIn('dv-reg', card)
        self.assertIn(nameless.bill_number, card)

    def test_the_money_is_answered_before_the_lists_not_after_them(self):
        """
        It was a card of its own at the very FOOT of the page — on a phone, past
        thirty rows of parts, which made the most important figure on the page
        the hardest one to reach. It is in the answer card now, so *which car,
        what it costs, where it is* are all answered without scrolling and the
        four lists below are pure detail.
        """
        body = self.html()
        self.assertLess(body.index('class="dv-money"'), body.index('dv-secs'),
                        'the money has fallen back below the four lists')

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

        THE PRICE IS ALONE ON THE ROW'S FIRST LINE, and it is GREEN. It used to
        sit beside the workshop's cost with a dash between them, and five rows
        of "₹1,000 – ₹1,500" read as five ranges rather than five prices. Green
        because this is money IN — the Profit page's own rule, and the same
        token. The COST drops to the second line, opposite the dates, where it
        costs no height at all on a row that already has a meta line.
        """
        body = self.html()
        row = body.split('Front Brake Pad Set', 1)[1]

        price = row.split('dv-money-col', 1)[1].split('</div>', 1)[0]
        self.assertIn('₹6,000', price)          # what the customer pays
        self.assertNotIn('₹4,200', price)       # the cost is NOT in this column
        self.assertNotIn('–', price)            # and there is no range to read

        cost = row.split('dv-cost-col', 1)[1].split('</div>', 1)[0]
        self.assertIn('₹4,200', cost)           # the workshop's own side, quieter

        meta = row.split('dv-row-meta', 1)[1].split('</div>', 1)[0]
        self.assertIn('05/01 – 20/01', meta)
        self.assertIn('Ajmal Auto Parts', meta)
        self.assertIn('× 2', meta)

        for caption in ('Ordered:', 'Received:', 'Qty:', 'Shop:', 'Price:',
                        'Status:', 'Shop Price', 'Customer Price'):
            self.assertNotIn(caption, body,
                             '%r is back on a page whose whole rule is that '
                             'position carries the meaning' % caption)

    def test_money_in_is_green_and_the_waypoint_between_is_not(self):
        """
        The Profit page's structure, mapped onto this one: the HERO is green,
        the individual amounts are green, and the intermediate waypoint between
        them is not — with green above and below it there would be nothing for
        the eye to land on.

        Here the hero is the BILL and the waypoints are the three section
        subtotals. It shipped inverted for a revision — a dark bill over green
        rows, on the mistaken reading that the bill was the waypoint — so a
        settled card printed the amount actually taken in the one colour on the
        page that does not mean money.
        """
        style = self.source(DETAIL)

        for amount in ('.dv-bill {', '.dv-money-col {'):
            rule = style.split(amount, 1)[1].split('}', 1)[0]
            self.assertIn('--color-success', rule,
                          '%s is money in and should be green' % amount)

        waypoint = style.split('.dv-sec-sum {', 1)[1].split('}', 1)[0]
        self.assertNotIn('--color-success', waypoint,
                         'the subtotal between the rows and the bill has gone '
                         'green too, so there is nothing left to land on')

    def test_every_row_in_all_four_lists_carries_a_status_mark(self):
        """
        THE MARK IS THE ROW'S LEFT ANCHOR AS MUCH AS ITS STATUS, and that is why
        all four lists carry one.

        It was pulled from Job Performed and Inventory Items for a revision, on
        the argument that a mark hard-coded to green says nothing — a job line
        has no state to be in, and a warehouse draw came off the shelf already
        fitted, so its `status` column is meaningless. The owner's call
        overruled it: without the mark, Job Performed read as a bare wall of
        sentences rather than a list of things that were done, and the two lists
        that kept theirs no longer lined up with the two that had lost them.

        What survived from that pass is the WEIGHT, not the removal — see
        `test_the_expected_mark_is_quieter_than_the_two_that_want_attention`.
        """
        from inventory.models import Category, Item

        JobCardConcern.objects.create(
            job_card=self.job, concern_text='Noise from the front', status='FIXED')
        JobCardLabourItem.objects.create(
            job_card=self.job, job_description='Engine Oil replaced')
        JobCardSpareItem.objects.create(
            job_card=self.job, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=Item.objects.create(
                category=Category.objects.create(name='Oils'),
                name='Engine Oil 5W30', average_stock=D('20'),
                current_stock=D('20'), avg_cost=D('400')),
            spare_part_name='Engine Oil 5W30', quantity=D('4'),
            total_price=D('2500'))

        body = self.html()

        def section(glyph):
            # Sliced from the GLYPH, not the section's name: the template
            # carries a rendered HTML comment naming each section above it, so
            # splitting on the name lands before the heading rather than on it.
            return body.split(glyph, 1)[1].split('dv-sec-title', 1)[0]

        for name, glyph in (('Customer Concerns', 'bi-exclamation-circle'),
                            ('Job Performed', 'bi-tools'),
                            ('Inventory Items', 'bi-box-seam'),
                            ('Spare Parts', 'bi-gear-wide-connected')):
            block = section(glyph)
            self.assertIn('dv-row', block)
            self.assertIn('dv-ico-', block,
                          '%s has lost its status mark, so its rows no longer '
                          'start where the other lists do' % name)

    def test_the_expected_mark_is_quieter_than_the_two_that_want_attention(self):
        """
        Nine saturated green ticks on an ordinary card were the loudest thing in
        every list, and a list of twenty read as twenty alarms. Done is the
        answer you EXPECT; not-started and under-way are the ones worth finding.
        So the traffic light keeps its meanings — it is the same one the Live
        Report and the dashboard drawer use — and only the expected one is
        drawn quietly.
        """
        import re

        style = self.source(DETAIL)

        def size(selector):
            rule = style.split(selector, 1)[1].split('}', 1)[0]
            return float(re.search(r'font-size:\s*([\d.]+)rem', rule).group(1))

        self.assertLess(size('.dv-ico-done '), size('.dv-ico-going '),
                        'the mark you expect to see is no longer the quiet one')


    def test_a_part_date_drops_the_cards_own_year_and_keeps_a_different_one(self):
        """
        A width fix with a measurement behind it, not a formatting preference:
        the full pair plus a shop name ("16/07/2026 – 17/07/2026 · Spare club")
        is 38 characters and wrapped to two lines on a 375px phone, so rows in
        one list came out different heights and the list read as broken.

        The year is KEPT the moment it differs, because then it is the whole
        point — a part ordered in December for a car admitted in January is the
        one case where the reader must not have to assume. Each half is compared
        on its own, so a pair straddling New Year prints one short and one long
        rather than hiding the crossing.
        """
        from workshop.views.jobcard import _short_date

        self.assertEqual(_short_date(date(2026, 7, 16), 2026), '16/07')
        self.assertEqual(_short_date(date(2025, 12, 30), 2026), '30/12/2025')
        self.assertEqual(_short_date(None, 2026), '—')
        # No card year to compare against — say it in full rather than guess.
        self.assertEqual(_short_date(date(2026, 7, 16), None), '16/07/2026')

    def test_half_a_date_pair_still_prints_as_a_pair(self):
        """
        A spare is finished when it has been ordered AND received, so the pair
        is one item and the missing half is a dash rather than a silence — the
        rule the job card's own date chip follows.
        """
        self.spare.received_date = None
        self.spare.save()
        self.assertIn('05/01 – —', self.html())

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



class TheThreeDatesAreLabelledTests(DetailBase):
    """
    ADMITTED, COMPLETED, SETTLED — the three moments a job card has, and the
    three date columns it stores (2026-08-28).

    LABELLED, while nothing else on the page is, and that is not an
    inconsistency. Every other unlabelled value here is unambiguous because it
    is the only one of its kind on its line; three dates of the same shape side
    by side are the one place position could not carry the meaning. They were a
    bare range in the heading before this, which said neither which was which
    nor that a third existed.
    """

    def card(self, job=None):
        """
        The answer card only. `.dv-dates` and friends are also rules in this
        page's inline stylesheet, so a whole-page search finds them on every
        render — the trap this file already guards for `.dv-cust`.
        """
        client = Client()
        client.force_login(self.office)
        body = client.get(
            reverse('jobcard_detail', args=[(job or self.job).pk])).content.decode()
        body = body.split('<main', 1)[1].split('</main>', 1)[0]
        return body.split('class="dv-head', 1)[1].split('<div class="dv-secs"', 1)[0]

    def strip(self, job=None):
        """
        The date strip alone. Scoped because "Settled" also appears in the
        menu's lock tooltip, higher up the same card — a whole-card search finds
        that one first and reports the three dates as being out of order.
        """
        return self.card(job).split('class="dv-dates"', 1)[1].split('</div>', 1)[0]

    def settle(self, when):
        from datetime import datetime

        from django.utils import timezone as tz

        self.job.completed = True
        self.job.completed_date = date(2026, 1, 20)
        self.job.payment_status = 'PAID'
        self.job.received_amount = D('6000')
        self.job.paid_date = tz.make_aware(datetime.combine(when, datetime.min.time().replace(hour=11)))
        self.job.save()

    def test_each_date_is_under_its_own_word(self):
        self.settle(date(2026, 3, 9))

        strip = self.strip()
        labels = ['Admitted', 'Completed', 'Settled']
        for word in labels:
            self.assertIn(word, strip)
        # In the order the three things happen, so the row reads left to right
        # like the life it describes.
        self.assertEqual(sorted(labels, key=strip.index), labels)

        self.assertIn('01/01/2026', strip)
        self.assertIn('20/01/2026', strip)
        self.assertIn('09/03/2026', strip)

    def test_the_settled_date_is_its_own_column_and_is_read_from_paid_date(self):
        """
        The third date earns its column on a FLEET card. A walk-in has exactly
        one payment event and it happens at pickup, so settled repeats the
        handover day — but a fleet collector comes round weeks or months later
        against several months of cars, and those are the largest single
        receipts the workshop takes.

        Dated well clear of the other two here on purpose: all three demo
        seeders write `paid_date` from `completed_date`, so a fixture that let
        them coincide would pass whether or not the column was being read.
        """
        self.settle(date(2026, 3, 9))
        card = self.card()
        self.assertIn('09/03/2026', card)
        # Read from `paid_date`, not inferred from the status.
        self.job.paid_date = None
        self.job.save()
        self.assertNotIn('09/03/2026', self.card())

    def test_an_unreached_date_prints_a_dash_and_the_column_stays(self):
        """
        A fixed structure is what makes this page learnable — the same rule that
        keeps an empty section below drawn rather than omitted — so the dash is
        structure, not an apology. A column that came and went would move the
        other two between one card and the next.
        """
        strip = self.strip()        # admitted only
        self.assertIn('Settled', strip)
        self.assertEqual(strip.count('dv-d-lab'), 3)
        self.assertEqual(strip.count('dv-d-val"'), 1)        # only admitted has one
        self.assertEqual(strip.count('dv-d-val--none'), 2)   # completed, settled

    def test_a_date_is_read_from_its_own_column_never_from_the_one_before(self):
        """
        A card that reached a state out of order — a fleet card settled before
        anybody pressed Completed, or an old row whose status moved without its
        date — still prints honestly, rather than the page inventing a sequence
        the data does not support.
        """
        from workshop.views.jobcard import _lifecycle

        self.job.completed = False
        self.job.completed_date = None
        self.job.payment_status = 'BULK_PAID'
        self.job.save()

        labels = [s['label'] for s in _lifecycle(self.job)]
        self.assertEqual(labels, ['Admitted', 'Completed', 'Settled'])
        self.assertIsNone(_lifecycle(self.job)[1]['date'])   # completed: nothing

    def test_the_counter_appears_only_while_the_car_is_still_here(self):
        """
        On a finished card both dates are printed an inch apart and the
        subtraction is trivial; on an open one there is no second date to
        subtract from, so the counter is the ONLY way to know. That is the whole
        rule — it is not decoration that happens to be hidden sometimes.
        """
        from django.utils import timezone as tz

        self.job.admitted_date = tz.localdate() - timedelta(days=12)
        self.job.completed = False
        self.job.completed_date = None
        self.job.save()
        self.assertIn('12 days in', self.card())

        self.job.completed = True
        self.job.completed_date = tz.localdate()
        self.job.save()
        self.assertNotIn('dv-d-open', self.card())

    def test_the_gap_is_worded_by_the_view_not_counted_in_the_template(self):
        """
        `_time_in_workshop()` owns the words. A template cannot get "Same day"
        and "1 day" right, and the singular is exactly the case a naive
        "{{ n }} days" gets wrong on the commonest short job there is.
        """
        from workshop.views.jobcard import _time_in_workshop

        def phrase(admitted, completed):
            job = JobCard(admitted_date=admitted, completed_date=completed,
                          completed=completed is not None)
            return _time_in_workshop(job)['text']

        self.assertEqual(phrase(date(2026, 1, 1), date(2026, 1, 1)), 'Same day')
        self.assertEqual(phrase(date(2026, 1, 1), date(2026, 1, 2)), '1 day')
        self.assertEqual(phrase(date(2026, 1, 1), date(2026, 1, 4)), '3 days')

    def test_a_completion_dated_before_the_admission_prints_no_gap(self):
        """
        A negative day count is a typo upstream, and "-3 days" would make this
        page look like the broken thing rather than the data. Nothing is printed
        at all — all three real dates still are, so the mistake stays visible.
        """
        from workshop.views.jobcard import _time_in_workshop

        job = JobCard(admitted_date=date(2026, 1, 10),
                      completed_date=date(2026, 1, 4), completed=True)
        self.assertIsNone(_time_in_workshop(job))

    def test_a_settled_card_says_it_is_locked_and_an_open_one_says_nothing(self):
        """
        The full-width state banner is gone: "Completed" in amber across the
        page said what a date under the word Completed says better. What
        survives is the LOCK, which is not derivable from a date, and it sits at
        the foot of the card the dates are in.
        """
        self.assertNotIn('dv-note', self.card())

        self.job.payment_status = 'PAID'
        self.job.received_amount = D('6000')
        self.job.save()
        self.assertIn('locked against editing', self.card())

    def test_a_car_on_hold_says_so(self):
        """
        It was nowhere. A paused car drew exactly the same page as a running
        one, on the one screen that claims to say where the car is.
        """
        self.job.on_hold = True
        self.job.save()
        self.assertIn('On hold', self.card())


class EverySectionSaysWhatItCameToTests(DetailBase):
    """
    The real optimisation of the 2026-08-28 pass: Job Performed + Inventory
    Items + Spare Parts total the figure on the money line EXACTLY, because
    `update_totals()` is `sum(spares.total_price) + labour_amount` over both
    routes.

    The bill stops being a number you take on trust. It costs no query — the two
    parts figures are summed off the very rows printed underneath — which is the
    point: a second aggregate could disagree with the rows above it.
    """

    def setUp(self):
        super().setUp()
        from inventory.models import Category, Item

        self.item = Item.objects.create(
            category=Category.objects.create(name='Oils'),
            name='Engine Oil 5W30', average_stock=D('20'),
            current_stock=D('20'), avg_cost=D('400'))
        JobCardSpareItem.objects.create(
            job_card=self.job, source=JobCardSpareItem.SOURCE_INVENTORY,
            item=self.item, spare_part_name='Engine Oil 5W30',
            quantity=D('4'), total_price=D('2500'))
        self.job.labour_amount = D('1500')
        self.job.save()
        self.job.update_totals()
        self.job.refresh_from_db()

    def heads(self):
        """Every section heading, keyed by its name."""
        body = self.html()
        out = {}
        for chunk in body.split('dv-sec-title')[1:]:
            head = chunk.split('</div>', 1)[0]
            name = head.split('</i>', 1)[1].split('\n', 1)[0].strip()
            out[name] = head
        return out

    def test_the_three_subtotals_add_up_to_the_bill(self):
        # 6,000 spare + 2,500 draw + 1,500 labour
        self.assertEqual(self.job.total_bill_amount, D('10000'))

        heads = self.heads()
        self.assertIn('6,000', heads['Spare Parts'])
        self.assertIn('2,500', heads['Inventory Items'])
        self.assertIn('1,500', heads['Job Performed'])
        self.assertIn('10,000', self.html().split('class="dv-money"', 1)[1])

    def test_labour_is_in_the_HEADING_and_never_on_a_job_line(self):
        """
        Labour is ONE charge on the card, never a price per line — the rule at
        the top of CLAUDE.md, and how the printed invoice sets it out. A figure
        beside each job description would invite a line-by-line negotiation
        about work that was quoted whole.
        """
        # Sliced from the GLYPH, not from the words "Job Performed" -- the
        # template carries an HTML comment naming the section above it, which is
        # rendered, so splitting on the name lands before the heading rather
        # than on it. `bi-tools` is the Job Performed icon and appears once.
        section = self.html().split('bi-tools', 1)[1].split('dv-sec-title', 1)[0]
        heading, rows = section.split('</div>', 1)
        self.assertIn('1,500', heading)
        self.assertNotIn('1,500', rows)

    def test_the_concerns_heading_carries_no_figure(self):
        """There is no money on a concern, so there is nothing to total."""
        self.assertNotIn('dv-sec-sum', self.heads()['Customer Concerns'])

    def test_a_section_worth_nothing_prints_no_zero(self):
        """
        A subtotal is only drawn when there is one. A zero beside an empty
        section is a figure nobody needs to read, on a page whose rule is that a
        missing value leaves no trace.
        """
        bare = JobCard.objects.create(
            admitted_date=date(2026, 2, 2), registration_number='KL10AA4000',
            brand_name='Bmw', model_name='320d')
        client = Client()
        client.force_login(self.office)
        body = client.get(reverse('jobcard_detail', args=[bare.pk])).content.decode()
        body = body.split('<main', 1)[1].split('</main>', 1)[0]
        self.assertNotIn('dv-sec-sum', body)

    def test_the_spares_subtotal_is_the_customer_side_not_the_cost(self):
        """
        `total_price`, the column the rows print on the right and the one
        `update_totals()` sums. Totalling `unit_price` instead would put a
        figure in the heading that the bill below it does not contain — the
        spare here costs 4,200 and sells for 6,000.
        """
        self.assertIn('6,000', self.heads()['Spare Parts'])
        self.assertNotIn('4,200', self.heads()['Spare Parts'])


class TheTwoActionsLiveInOneMenuTests(DetailBase):
    """
    They were filled buttons pinned to the head's top-right: the two loudest
    objects on a screen whose only job is to be read, both of them leaving it,
    and ~90px off the car's own name at 375px.
    """

    def test_both_actions_are_there_and_behind_the_dots(self):
        body = self.html()
        self.assertIn('bi-three-dots-vertical', body)
        self.assertIn('data-bs-toggle="dropdown"', body)

        menu = body.split('dv-menu', 1)[1].split('</ul>', 1)[0]
        self.assertIn(reverse('invoice_view', args=[self.job.pk]), menu)
        self.assertIn(reverse('jobcard_edit', args=[self.job.pk]), menu)
        self.assertIn('Invoice', menu)
        self.assertIn('Edit', menu)

    def test_the_menu_posts_nothing(self):
        """
        `ItIsStillOnlyAViewTests` covers the page; this covers the menu on its
        own, because a kebab menu elsewhere in this app routinely holds a POST
        form (the Completed list's Undo Completion is one) and copying that
        shape in here is the obvious way this page would stop being read-only.
        """
        menu = self.html().split('dv-menu', 1)[1].split('</ul>', 1)[0]
        for tag in ('<form', '<button', 'method="post"'):
            self.assertNotIn(tag, menu)

    def test_the_head_cannot_clip_the_menu(self):
        """
        `.dv-head` was `overflow: hidden` — a clipping ancestor, the one thing
        Popper cannot escape, and it fails invisibly and only sometimes. The
        clip was only ever rounding the colour rail, which now rounds itself.
        """
        style = self.source(DETAIL)
        self.assertNotIn('overflow', style.split('.dv-head {', 1)[1].split('}', 1)[0])
        self.assertIn('border-radius',
                      style.split('.dv-head::before {', 1)[1].split('}', 1)[0])

    def test_the_dots_wrapper_carries_no_line_box_of_its_own(self):
        """
        THE WRAPPER, NOT THE BUTTON, PUT THE KEBAB IN THE WRONG PLACE.

        Bootstrap's `.dropdown` is a plain inline box, so inside the <h1> it
        inherited the heading's 36.8px line-height and the inline-block button
        sat on THAT line box's baseline. Measured: a 40px wrapper around a 32px
        button, with the button 8px below the top of its row — and since the
        strut then set the row's height, the corner the button was meant to sit
        in carried 8px of empty space under it.

        `display: flex` takes the button off the baseline; `line-height: 0`
        removes the strut. CLAUDE.md records the identical fix for a table cell
        holding an inline-flex child. After it: wrapper 32px, button 32px, row
        32px, button top == row top, 12px from the card's top edge and 14px
        from its right.

        Nothing in the Django suite executes CSS, so this is asserted on the
        source.
        """
        rule = self.source(DETAIL).split('.dv-line1 .dropdown {', 1)[1].split('}', 1)[0]
        self.assertIn('line-height: 0', rule)
        self.assertIn('display: flex', rule)

    def test_the_colour_rail_stays_inside_the_head_at_every_width(self):
        """
        The other half of dropping `overflow: hidden`, and it cost a real
        defect before it was measured.

        The car's colour is an absolutely-positioned `::before` with
        `inset: 0 auto 0 0`, so it needs `.dv-head` to be its CONTAINING
        BLOCK. `position: sticky` used to provide that for free. The card
        is no longer pinned — it carries the money now, and pinning ~240px
        of a 667px phone spends a third of the screen on something already
        read — and a first attempt at unpinning it used `static`, which is
        not a positioned value: the rail then measured itself against the
        VIEWPORT and rendered 812px of car colour down the left edge of the
        whole page.

        Nothing in the Django suite executes CSS, so this is asserted on the
        source. Measured in a browser at 375, 320 and 1280: the rail stays
        inside the card at all three.
        """
        style = self.source(DETAIL)
        head = style.split('.dv-head {', 1)[1].split('}', 1)[0]
        self.assertIn('position: relative', head)
        self.assertNotIn('position: static', style)
        self.assertNotIn('position: sticky', style)

    def test_a_settled_card_marks_the_edit_item(self):
        """
        The door is still open — a settled card unlocks from the form itself —
        so this ANNOTATES rather than disables. Anyone tapping Edit on a settled
        card is bounced by `jobcard_edit`; one glyph removes the surprise.
        """
        self.assertNotIn('dv-menu-lock', self.html())
        self.job.payment_status = 'PAID'
        self.job.received_amount = D('6000')
        self.job.save()
        self.assertIn('dv-menu-lock', self.html())

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
