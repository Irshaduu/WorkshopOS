"""
"Job Performed", suggested from the parts already on this card.

Nearly every job line in this workshop is a part on the same card plus a verb —
"Engine Oil replaced", "Wheel Bearing replaced", "Brake Disc refurbished". So
the suggestions are built from the card's OWN two parts sections rather than
from a master list: the mechanic fitted these exact things, the whole line
arrives in one pick, and there is no separate taxonomy to keep in step.

Two halves, and only one of them is testable from here. The list is assembled by
`buildJobLineOptions()` in the template — nothing in this suite executes a line
of JavaScript, which is a known and accepted limitation (see CLAUDE.md). What
IS asserted is everything the server owes that script: the datalist to fill, the
`list=` on every box, and the part NAME on every row — including the rule that a
warehouse draw is named by its category and never by its branded SKU.
"""

from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Category, Item
from workshop.models import JobCard, JobCardSpareItem, Mechanic, SpareShop

SHOP = JobCardSpareItem.SOURCE_SHOP
INVENTORY = JobCardSpareItem.SOURCE_INVENTORY


class JobLineSuggestionBase(TestCase):
    def setUp(self):
        self.office, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='off', password='pw')
        self.user.groups.add(self.office)
        self.client = Client()
        self.client.login(username='off', password='pw')

        self.mechanic = Mechanic.objects.create(name='Lead Tech')
        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')

        # Category = the generic part the customer reads; Item = the branded
        # SKU the workshop buys. That is the taxonomy the printed bill depends
        # on, and it is what makes the category the right word for a job line.
        self.category = Category.objects.create(name='Engine Oil')
        self.item = Item.objects.create(
            category=self.category, name='Castrol Edge 5W-30',
            average_stock=D('20'), current_stock=D('20'), avg_cost=D('400'))

        self.job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Audi', model_name='A4',
            registration_number='KL01A1234', lead_mechanic=self.mechanic)

    def rendered(self):
        return self.client.get(
            reverse('jobcard_edit', args=[self.job.pk])).content.decode()


class TheBoxOffersTheCardsOwnPartsTests(JobLineSuggestionBase):

    def test_there_is_a_datalist_for_the_script_to_fill(self):
        self.assertIn('<datalist id="jobLineOptions">', self.rendered())

    def test_every_job_box_points_at_it(self):
        """
        Including the hidden `#empty-labour-form` template that "+ Add Job"
        clones — a native datalist needs no wiring, so a row added after page
        load gets the same list with nothing re-initialised. That is exactly why
        it is a datalist and not a fetch autocomplete: all three of `script.js`'s
        documented cloning traps live in per-element wiring.
        """
        html = self.rendered()
        boxes = html.count('class="form-control job-desc"')
        self.assertGreaterEqual(boxes, 1)
        self.assertEqual(html.count('list="jobLineOptions"'), boxes)

    def test_a_shop_spare_offers_the_name_office_typed(self):
        JobCardSpareItem.objects.create(
            job_card=self.job, source=SHOP, spare_part_name='Wheel Bearing',
            shop=self.shop)

        self.assertIn('value="Wheel Bearing"', self.rendered())

    def test_a_warehouse_draw_offers_its_CATEGORY_not_its_brand(self):
        """
        The invoice rule, reaching the job line. `Item.name` is the branded SKU
        ("Castrol Edge 5W-30") and `Category.name` is what it is ("Engine Oil");
        the printed bill names a draw by its category, so a job line reading
        "Castrol Edge 5W-30 replaced" beside a part line reading "Engine Oil"
        would be one document contradicting itself — and it would publish the
        workshop's supply chain into the bargain.
        """
        JobCardSpareItem.objects.create(
            job_card=self.job, source=INVENTORY, item=self.item,
            spare_part_name='Castrol Edge 5W-30', quantity=D('4'),
            total_price=D('3200'))

        html = self.rendered()
        row = html[html.index('class="inventory-row'):][:400]

        self.assertIn('data-category="Engine Oil"', row)

    def test_the_rule_is_the_invoices_own(self):
        """
        Not a style check. `part_category` goes through
        `invoice.item_display_name`, which `part_display_name` also calls — so
        the job line and the part line on one bill cannot come to disagree about
        what a draw is called.
        """
        import workshop.invoice as invoice

        spare = JobCardSpareItem.objects.create(
            job_card=self.job, source=INVENTORY, item=self.item,
            spare_part_name='Castrol Edge 5W-30', quantity=D('4'),
            total_price=D('3200'))

        self.assertEqual(invoice.item_display_name(self.item), 'Engine Oil')
        self.assertEqual(invoice.part_display_name(spare), 'Engine Oil')

    def test_a_draw_with_no_category_leaves_the_attribute_empty(self):
        """
        Empty rather than falling back to the SKU: the branded name must not
        reach a customer-facing box by a side door. An empty attribute simply
        contributes nothing to the list, and the line is typed as it always was.
        """
        html = self.rendered()          # a card with no draws at all
        self.assertIn('data-category=""', html)


class TheVerbsAreDeclaredOnceTests(JobLineSuggestionBase):
    """
    Replaced ~70%, then removed-and-installed, refurbished, inspected and
    repaired at 7-8% each — the owner's own measurement of the workshop. The
    order matters: a datalist keeps document order for whatever survives
    filtering, so opening the list cold shows one "replaced" line per part
    before any variant of anything.
    """

    #: In the order they are offered.
    VERBS = ['replaced', 'removed and installed', 'refurbished',
             'inspected', 'repaired']

    def test_every_verb_is_offered_and_replaced_leads(self):
        html = self.rendered()
        block = html[html.index('const VERBS = ['):][:400]

        for verb in self.VERBS:
            self.assertIn("'%s'" % verb, block)

        positions = [block.index("'%s'" % v) for v in self.VERBS]
        self.assertEqual(positions, sorted(positions),
                         'the verbs are no longer in frequency order')

    def test_they_exist_in_exactly_one_place(self):
        """
        Reword them there and every row follows. A second copy — a server-side
        list, a per-row attribute — is how "replaced" and "Replaced" end up in
        one workshop's history.
        """
        html = self.rendered()
        self.assertEqual(html.count('const VERBS = ['), 1)
