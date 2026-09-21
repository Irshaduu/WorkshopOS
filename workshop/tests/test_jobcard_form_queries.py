"""
AUD-0096 — the job card form must cost the same whatever the card carries.

Both parts sections scope their formset to one route in `get_queryset()`
(`SourceScopedSpareFormSet`). Django asks a formset for its queryset several
times per row — `initial_form_count()`, `_construct_form()`, `add_fields()` —
and relies on getting the SAME object back, so the first `len()` loads it and
every later `[i]` reads the loaded rows. The override built a fresh, unloaded
queryset on every call, so each of those questions went back to the database:
several queries per part, on the longest form in the app.

Written as INVARIANTS — fifteen parts cost what one costs — never as a page's
total query count, which would go stale on the next query somebody adds.
"""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from inventory.models import Category, Item
from workshop.forms import JobCardInventoryFormSet, JobCardSpareFormSet
from workshop.models import JobCard, JobCardPhoto, JobCardSpareItem, SpareShop

SHOP = JobCardSpareItem.SOURCE_SHOP
INVENTORY = JobCardSpareItem.SOURCE_INVENTORY


class TheJobCardFormCostsTheSameWhateverItCarriesTests(TestCase):

    def setUp(self):
        office, _ = Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='off', password='pw')
        user.groups.add(office)
        self.client = Client()
        self.client.login(username='off', password='pw')

        self.shop = SpareShop.objects.create(name='Spare club')
        self.item = Item.objects.create(
            category=Category.objects.create(name='Engine Oil'),
            name='Castrol 5W-30', average_stock=D('20'),
            current_stock=D('20'), avg_cost=D('400'))

    def _card(self, reg, parts):
        """A card carrying `parts` shop spares AND `parts` warehouse draws."""
        card = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota',
            model_name='Corolla', registration_number=reg)
        for n in range(parts):
            JobCardSpareItem.objects.create(
                job_card=card, source=SHOP, spare_part_name=f'Part {n}',
                quantity=D('1'), shop=self.shop, shop_name=str(self.shop.pk),
                unit_price=D('500'), total_price=D('700'))
            JobCardSpareItem.objects.create(
                job_card=card, source=INVENTORY, item=self.item,
                quantity=D('1'), total_price=D('600'))
        return card

    def _page_queries(self, card):
        with CaptureQueriesContext(connection) as ctx:
            page = self.client.get(reverse('jobcard_edit', args=[card.pk]))
        self.assertEqual(page.status_code, 200)
        return len(ctx.captured_queries)

    def _section_queries(self, formset_class, prefix, card):
        """Build one parts section and touch what the template reads per row."""
        with CaptureQueriesContext(connection) as ctx:
            formset = formset_class(instance=card, prefix=prefix)
            for form in formset.forms:
                form.instance.photo_count
                if prefix == 'inventory':
                    # the product box, and the category the Job Performed
                    # suggestions read off the row — the second one was a
                    # query per draw even after the rows were read once
                    form.search_value
                    form.part_category
        return len(ctx.captured_queries)

    def test_the_edit_page_costs_the_same_with_fifteen_parts_as_with_one(self):
        small = self._card('KL01A0001', parts=1)
        large = self._card('KL01A0002', parts=15)
        self._page_queries(small)       # warm-up: first-request caches
        one, fifteen = self._page_queries(small), self._page_queries(large)
        self.assertEqual(
            fifteen, one,
            f'1 part of each kind: {one} queries; 15 of each: {fifteen}')

    def test_each_parts_section_reads_its_rows_once(self):
        small = self._card('KL01A0003', parts=1)
        large = self._card('KL01A0004', parts=15)
        for formset_class, prefix in ((JobCardSpareFormSet, 'spares'),
                                      (JobCardInventoryFormSet, 'inventory')):
            with self.subTest(prefix):
                one = self._section_queries(formset_class, prefix, small)
                fifteen = self._section_queries(formset_class, prefix, large)
                self.assertEqual(
                    fifteen, one,
                    f'{prefix}: 1 row {one} queries, 15 rows {fifteen}')

    def test_each_section_still_shows_only_its_own_rows_with_their_photo_counts(self):
        """Reading the rows once must not change WHICH rows, or what they carry."""
        card = self._card('KL01A0005', parts=3)
        shop_rows = list(card.spares.filter(source=SHOP).order_by('pk'))
        draws = list(card.spares.filter(source=INVENTORY).order_by('pk'))
        JobCardPhoto.objects.create(spare=shop_rows[1])
        JobCardPhoto.objects.create(spare=shop_rows[1])
        JobCardPhoto.objects.create(spare=draws[2])

        spares = JobCardSpareFormSet(instance=card, prefix='spares')
        self.assertEqual([f.instance.pk for f in spares.forms if f.instance.pk],
                         [r.pk for r in shop_rows])
        self.assertEqual([f.instance.photo_count for f in spares.forms if f.instance.pk],
                         [0, 2, 0])

        inventory = JobCardInventoryFormSet(instance=card, prefix='inventory')
        self.assertEqual([f.instance.pk for f in inventory.forms if f.instance.pk],
                         [r.pk for r in draws])
        self.assertEqual([f.instance.photo_count for f in inventory.forms if f.instance.pk],
                         [0, 0, 1])
