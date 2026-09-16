"""
Suggested customer prices on the job card, and the markup behind them (2026-09-16).

What the owners asked for: a customer price that fills itself from the shop
price or the warehouse cost at a markup — 40% for every spare part, a markup of
its own for each stock product — editable, with a badge saying what markup the
price actually carries.

THE SAFETY OF IT IS WHERE THE ARITHMETIC LIVES, so that is what these tests pin
first. The suggestion is worked out in the BROWSER (`pricing-core.js`, tested by
`node --test`) and reaches the database only when a person saves it. The server
holds no copy of the arithmetic and prices nothing on its own — see
`workshop/pricing.py` for the four ways a server-side price would move money
nobody decided to move.

Nothing in this suite executes JavaScript, so where a rule lives in the page
these assert the contract the script relies on — what the server renders, for
whom, and in what shape — rather than pretending to run it. The filling itself
was measured in the browser.
"""
import json
import re
from datetime import date
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from inventory.models import Category, Item, ShopCatalogItem, SupplierShop
from workshop import settlement
from workshop.models import JobCardSpareItem
from workshop.pricing import (DEFAULT_MARKUP_PERCENT, LOW_MARKUP_PERCENT,
                              MAX_MARKUP_PERCENT, parse_markup)
from workshop.tests.test_jobcard_form_ux import JobCardFormBase

INVENTORY = JobCardSpareItem.SOURCE_INVENTORY
SHOP = JobCardSpareItem.SOURCE_SHOP


class ReadingAMarkupTests(SimpleTestCase):
    """`parse_markup` — refused, never clamped or defaulted."""

    def test_the_numbers_the_owners_chose(self):
        self.assertEqual(DEFAULT_MARKUP_PERCENT, 40)
        self.assertEqual(LOW_MARKUP_PERCENT, 20)
        self.assertEqual(MAX_MARKUP_PERCENT, 999)

    def test_a_whole_percent_from_0_to_999_is_read(self):
        for raw, want in (('40', 40), (' 40 ', 40), ('0', 0), ('999', 999), ('040', 40), (40, 40)):
            self.assertEqual(parse_markup(raw), want, repr(raw))

    def test_everything_else_is_refused(self):
        for raw in ('40.5', '-5', '1000', '40%', 'abc', '', '  ', None, '4,0',
                    '1e2', 'NaN', 'Infinity',
                    # Python's int() reads these as 40 — which is why the parser
                    # matches ASCII digits rather than `\d`.
                    '٤٠', '४०'):
            self.assertIsNone(parse_markup(raw), repr(raw))


class PricingBase(JobCardFormBase):
    def setUp(self):
        super().setUp()
        self.category = Category.objects.create(name='Engine Oil')
        self.item = Item.objects.create(
            category=self.category, name='Castrol 5W-30',
            average_stock=D('20'), current_stock=D('20'), avg_cost=D('400'))

    def pricing_config(self, html):
        m = re.search(r'<script id="jcPricing" type="application/json">(.*?)</script>', html, re.S)
        return json.loads(m.group(1)) if m else None

    def inventory_rows(self, html):
        """The live inventory rows' opening tags, attributes and all."""
        body = html.split('<tbody id="inventory-list">', 1)[1].split('</tbody>', 1)[0]
        return re.findall(r'<tr class="inventory-row[^>]*>', body)


class TheServerNeverPricesAPartTests(PricingBase):
    """
    The load-bearing rule. If one of these fails, the server has started working
    out prices by itself — which would bill Floor's unpriced parts, silence the
    settle check, and reprice paid bills. Put the arithmetic back in the browser.
    """

    def test_a_shop_price_with_no_customer_price_saves_no_customer_price(self):
        resp = self.edit(**{
            'spares-TOTAL_FORMS': '1',
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '1',
            'spares-0-shop_name': str(self.shop.pk),
            'spares-0-status': 'PENDING',
            'spares-0-unit_price': '1000',
            'spares-0-total_price': '',
        })
        self.assertEqual(resp.status_code, 302)
        spare = JobCardSpareItem.objects.get(job_card=self.job, source=SHOP)
        self.assertEqual(spare.unit_price, D('1000'))
        self.assertIsNone(spare.total_price)
        # …so the settle check still chases it, exactly as before.
        gaps = settlement.unfilled(self.job)
        self.assertIn(settlement.CUSTOMER_PRICE, gaps.spares[0].tags)

    def test_a_warehouse_draw_with_no_customer_price_saves_none_either(self):
        # The product has a known cost AND a markup — everything a price could
        # be worked out from — and the server still must not do it.
        self.item.markup_percent = 40
        self.item.save(update_fields=['markup_percent'])
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '2',
            'inventory-0-customer_rate': '',
            'inventory-0-total_price': '',
        })
        self.assertEqual(resp.status_code, 302)
        draw = JobCardSpareItem.objects.get(job_card=self.job, source=INVENTORY)
        self.assertEqual(draw.unit_price, D('400'))       # the cost is taken, as always
        self.assertIsNone(draw.customer_rate)
        self.assertIsNone(draw.total_price)

    def test_a_typed_total_with_no_unit_price_is_saved_exactly(self):
        """
        What the grey unit price protects. The page shows total ÷ quantity in the
        Unit Price box as a placeholder and posts the box EMPTY — measured in the
        browser — so the typed total is the saved total. Were 142.86 posted, the
        server would save 142.86 × 7 = ₹1,000.02 instead.
        """
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '7',
            'inventory-0-customer_rate': '',
            'inventory-0-total_price': '1000',
        })
        self.assertEqual(resp.status_code, 302)
        draw = JobCardSpareItem.objects.get(job_card=self.job, source=INVENTORY)
        self.assertIsNone(draw.customer_rate)
        self.assertEqual(draw.total_price, D('1000.00'))

    def test_a_divided_unit_price_WOULD_move_the_bill_which_is_why_it_is_never_posted(self):
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '7',
            'inventory-0-customer_rate': '142.86',
            'inventory-0-total_price': '1000',
        })
        self.assertEqual(resp.status_code, 302)
        draw = JobCardSpareItem.objects.get(job_card=self.job, source=INVENTORY)
        self.assertEqual(draw.total_price, D('1000.02'))

    def test_the_pricing_module_holds_no_price_function(self):
        import workshop.pricing as pricing
        public = {n for n in dir(pricing) if not n.startswith('_')}
        self.assertFalse({n for n in public if 'price' in n.lower() or 'suggest' in n.lower()},
                         'workshop/pricing.py has grown a price function — the '
                         'suggestion belongs in the browser, see its docstring')


class CostAndMarkupAreOfficeAndOwnerOnlyTests(PricingBase):

    def test_the_product_search_sends_cost_and_markup_to_office(self):
        rows = self.client.get(reverse('autocomplete_inventory_items'), {'q': 'Castrol'}).json()
        self.assertEqual(rows[0]['cost'], '400.00')
        self.assertEqual(rows[0]['markup'], 40)

    def test_the_product_search_sends_floor_NEITHER_key(self):
        """It sent `cost` to every role before this change. Absent, not blank."""
        rows = self.floor_client().get(
            reverse('autocomplete_inventory_items'), {'q': 'Castrol'}).json()
        self.assertEqual(len(rows), 1)
        self.assertNotIn('cost', rows[0])
        self.assertNotIn('markup', rows[0])
        self.assertEqual(rows[0]['id'], self.item.pk)     # the pick still works

    def test_floor_gets_no_config_no_cost_and_no_badge(self):
        JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                        quantity=D('2'), total_price=D('1200'))
        html = self.rendered_as_floor()
        self.assertIsNone(self.pricing_config(html))
        self.assertNotIn('data-cost=', html)
        self.assertNotIn('data-markup=', html)
        self.assertNotIn('Cost / Unit (₹)</th>', html)
        self.assertNotIn('class="inventory-cost-val"', html)
        self.assertNotIn('class="jc-mk ', html)
        # The definition, not the name: `importSpare()` asks
        # `if (window.jcPriceFill)` on every role's page.
        self.assertNotIn('window.jcPriceFill = function', html)
        # …while the hidden price inputs Floor must still post are all there.
        self.assertIn('inventory-0-customer_rate', html)
        self.assertIn('inventory-0-total_price', html)

    def test_office_gets_the_config_the_column_and_the_badges(self):
        JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                        quantity=D('2'), total_price=D('1200'))
        html = self.rendered()
        self.assertEqual(self.pricing_config(html),
                         {'spare_markup': 40, 'low_markup': 20, 'fill': True})
        self.assertIn('>Cost / Unit (₹)</th>', html)
        self.assertIn('class="jc-mk ', html)


class ASettledCardIsNeverFilledTests(PricingBase):
    """Unlocking a settled card is for correcting it — a filled price there would
    change a bill the customer already paid. The badges still read."""

    def test_fill_is_off_on_a_paid_card(self):
        for status in ('PAID', 'BULK_PAID'):
            type(self.job).objects.filter(pk=self.job.pk).update(payment_status=status)
            config = self.pricing_config(self.rendered())
            self.assertIs(config['fill'], False, status)

    def test_fill_is_on_while_money_is_still_owed(self):
        for status in ('PENDING', 'PARTIAL'):
            type(self.job).objects.filter(pk=self.job.pk).update(payment_status=status)
            self.assertIs(self.pricing_config(self.rendered())['fill'], True, status)

    def test_a_new_card_fills(self):
        html = self.client.get(reverse('jobcard_create')).content.decode()
        self.assertIs(self.pricing_config(html)['fill'], True)


class TheCostColumnReadsTheRightFigureTests(PricingBase):

    def test_a_saved_draw_shows_ITS_OWN_cost_not_todays_average(self):
        JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                        quantity=D('2'), total_price=D('1200'))
        Item.objects.filter(pk=self.item.pk).update(avg_cost=D('900'), markup_percent=25)
        row = self.inventory_rows(self.rendered())[0]
        self.assertIn('data-cost="400.00"', row)     # what the Profit page charges
        self.assertIn('data-markup="25"', row)       # the product's markup today

    def test_an_unknown_cost_is_blank_and_drawn_as_a_dash(self):
        Item.objects.filter(pk=self.item.pk).update(avg_cost=D('0'))
        JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                        quantity=D('2'), total_price=D('1200'))
        html = self.rendered()
        self.assertIn('data-cost=""', self.inventory_rows(html)[0])
        body = html.split('<tbody id="inventory-list">', 1)[1].split('</tbody>', 1)[0]
        self.assertIn('title="No Supplies Shop bill has costed this product yet">—</div>', body)

    def test_the_figure_is_never_localised_into_a_comma(self):
        Item.objects.filter(pk=self.item.pk).update(avg_cost=D('105714.50'))
        JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                        quantity=D('1'), total_price=D('150000'))
        html = self.rendered()
        self.assertIn('data-cost="105714.50"', self.inventory_rows(html)[0])
        # …while the figure a person READS is grouped.
        self.assertIn('>1,05,714.50</div>', html)


class ADrawCorrectedToAnotherProductIsRecostedTests(PricingBase):
    """It kept the FIRST product's cost, because the snapshot only ran on
    create — so the Profit page and the Cost / Unit column read the wrong oil."""

    def setUp(self):
        super().setUp()
        self.other = Item.objects.create(category=self.category, name='Castrol 5W-40',
                                         average_stock=D('5'), avg_cost=D('650'))

    def test_the_new_products_cost_is_taken(self):
        draw = JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                               quantity=D('2'), customer_rate=D('560'))
        draw.item = self.other
        draw.save()
        draw.refresh_from_db()
        self.assertEqual(draw.unit_price, D('650'))
        # The customer's figure is never touched by a cost.
        self.assertEqual(draw.total_price, D('1120'))

    def test_an_unknown_cost_clears_rather_than_keeping_the_old_products(self):
        Item.objects.filter(pk=self.other.pk).update(avg_cost=D('0'))
        draw = JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                               quantity=D('2'), total_price=D('1200'))
        draw.item = self.other
        draw.save()
        draw.refresh_from_db()
        self.assertIsNone(draw.unit_price)

    def test_an_ordinary_edit_of_the_same_product_keeps_its_cost(self):
        draw = JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                               quantity=D('2'), total_price=D('1200'))
        Item.objects.filter(pk=self.item.pk).update(avg_cost=D('999'))
        draw.quantity = D('3')
        draw.save()
        draw.refresh_from_db()
        self.assertEqual(draw.unit_price, D('400'))

    def test_through_the_job_card_form(self):
        draw = JobCardSpareItem.objects.create(job_card=self.job, source=INVENTORY, item=self.item,
                                               quantity=D('2'), total_price=D('1200'))
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1', 'inventory-INITIAL_FORMS': '1',
            'inventory-0-id': str(draw.pk),
            'inventory-0-item': str(self.other.pk),
            'inventory-0-quantity': '2',
            'inventory-0-total_price': '1200',
        })
        self.assertEqual(resp.status_code, 302)
        draw.refresh_from_db()
        self.assertEqual(draw.unit_price, D('650'))


class ALineTooLargeForItsColumnIsRefusedTests(PricingBase):
    """Unit price × quantity overflowed `numeric(10,2)` on save — a 500 on
    PostgreSQL. Each box passed its own check; the product did not."""

    def test_it_is_refused_with_a_message_not_a_crash(self):
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '1000',
            'inventory-0-customer_rate': '140000',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'too large for one line')
        self.assertFalse(JobCardSpareItem.objects.filter(job_card=self.job).exists())

    def test_the_largest_line_that_fits_is_accepted(self):
        resp = self.edit(**{
            'inventory-TOTAL_FORMS': '1',
            'inventory-0-item': str(self.item.pk),
            'inventory-0-quantity': '1',
            'inventory-0-customer_rate': '99999999.99',
        })
        self.assertEqual(resp.status_code, 302)
        draw = JobCardSpareItem.objects.get(job_card=self.job)
        self.assertEqual(draw.total_price, D('99999999.99'))


class TheBadgeCannotEscapeItsTableTests(JobCardFormBase):
    """
    Nothing here executes CSS, so the two declarations that decide the layout are
    asserted directly — both were measured in a browser before being written.
    """

    def test_the_badge_is_positioned_so_its_hidden_label_stays_in_the_scroller(self):
        # Without it the `visually-hidden` label (position: absolute) escaped
        # the table's sideways scroller and widened the job card to 1,273px on a
        # 375px phone.
        self.assertIn('position: relative', self.css_rule('.jc-mk'))

    def test_an_empty_badge_keeps_its_width(self):
        # `visibility`, never `display: none` — a badge appearing as a price is
        # typed must not shift every column to its right.
        self.assertIn('visibility: hidden', self.css_rule('.jc-mk.is-empty'))

    def test_its_colour_never_animates(self):
        self.assertIn('transition: none', self.css_rule('.jc-mk'))

    def test_the_arithmetic_loads_before_the_script_that_uses_it(self):
        source = self.source()
        self.assertLess(source.index("js/pricing-core.js"),
                        source.index('json_script:"jcPricing"'))

    def test_the_row_templates_carry_the_same_cells_as_the_live_rows(self):
        source = self.source()

        def chunk(start, end_marker='</tbody>'):
            return source.split(start, 1)[1].split(end_marker, 1)[0]

        for label, live, template in (
            ('inventory', chunk('<tbody id="inventory-list">'), chunk('id="empty-inventory-form"')),
            ('spare', chunk('<tbody id="spare-list">'), chunk('id="empty-spare-form"')),
        ):
            for token in ('class="jc-mk ', 'jc-mk-n'):
                self.assertIn(token, live, '%s live rows' % label)
                self.assertIn(token, template, '%s row template' % label)

        def order(text):
            found = sorted((text.find(t), t) for t in
                           ('quantity', 'inventory-cost-val', 'customer_rate', 'total_price')
                           if text.find(t) != -1)
            return [t for _, t in found]

        self.assertEqual(order(chunk('id="empty-inventory-form"')),
                         order(chunk('<tbody id="inventory-list">')))

    def test_the_inventory_total_is_headed_total_price_and_spares_keep_customer_price(self):
        source = self.source()

        def headings(tbody_marker):
            # The <th> texts only — the head also carries a {% comment %} that
            # explains the rename by naming the old word.
            head = source.split(tbody_marker, 1)[0].rsplit('<thead', 1)[1]
            return [t.strip() for t in re.findall(r'<th[^>]*>([^<]*)</th>', head)]

        inventory = headings('<tbody id="inventory-list">')
        spares = headings('<tbody id="spare-list">')
        self.assertEqual(inventory[-3:], ['Cost / Unit (₹)', 'Unit Price (₹)', 'Total Price (₹)'])
        self.assertNotIn('Customer Price (₹)', inventory)
        self.assertEqual(spares[-2:], ['Shop Price (₹)', 'Customer Price (₹)'])

    def test_the_grey_unit_price_is_a_placeholder_never_a_value(self):
        """Nothing in this suite runs the script, so the contract it relies on is
        pinned: it writes the box's PLACEHOLDER attribute, and its style is the
        `.jc-derived` placeholder rule — never `.value`."""
        source = self.source()
        body = source.split('function showDerivedRate(b)', 1)[1].split('\n    }', 1)[0]
        self.assertIn("setAttribute('placeholder'", body)
        self.assertNotIn('.value =', body)
        self.assertIn('font-style: italic', self.css_rule('.form-control.jc-derived:not(:focus)::placeholder'))

    def test_the_badge_closes_the_line_in_both_sections(self):
        """
        After Customer Price, not beside the inventory Unit Price — the owner's
        instruction. A row whose total was typed by hand has an EMPTY unit
        price, so a badge there sat beside a blank box describing a figure two
        columns away. It is the markup of the whole line.
        """
        source = self.source()
        for start in ('<tbody id="inventory-list">', 'id="empty-inventory-form"',
                      '<tbody id="spare-list">', 'id="empty-spare-form"'):
            # The FIRST `total_price` in each chunk is the Office cell's box —
            # Floor's hidden copy comes after it — and the one badge follows it.
            chunk = source.split(start, 1)[1].split('</tbody>', 1)[0]
            self.assertGreater(chunk.index('class="jc-mk '), chunk.index('total_price'), start)

    def test_the_cost_is_drawn_as_a_read_only_figure_the_boxes_height(self):
        rule = self.css_rule('.inventory-cost-val')
        # No fill and a dashed outline where every input is filled and solid.
        self.assertIn('dashed', rule)
        self.assertIn('background: transparent', rule)
        # Sized like an input sizes itself, so it cannot round to a different
        # height than the boxes beside it (a fixed calc measured 0.7px taller).
        self.assertIn('box-sizing: content-box', rule)
        self.assertIn('padding: 6px 12px', rule)
        self.assertIn('line-height: 1.5', rule)


class AProductCarriesItsOwnMarkupTests(TestCase):

    def setUp(self):
        Group.objects.get_or_create(name='Office')
        user = User.objects.create_user(username='off', password='pw')
        user.groups.add(Group.objects.get(name='Office'))
        self.client = Client()
        self.client.login(username='off', password='pw')
        self.shop = SupplierShop.objects.create(name='Fluid Supplies')

    def add(self, **fields):
        data = {'category_name': 'Engine Oil', 'item_name': 'Castrol 5W-30',
                'average_stock': '10', 'markup_percent': '40'}
        data.update(fields)
        return self.client.post(reverse('add_shop_catalog_item', args=[self.shop.pk]), data)

    def test_a_new_product_starts_at_40(self):
        item = Item.objects.create(category=Category.objects.create(name='X'), name='Y')
        self.assertEqual(item.markup_percent, 40)

    def test_add_product_offers_40_and_stores_what_was_typed(self):
        page = self.client.get(reverse('add_shop_catalog_item', args=[self.shop.pk]))
        self.assertContains(page, 'name="markup_percent"')
        self.assertContains(page, 'value="40"')

        resp = self.add(markup_percent='35')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Item.objects.get(name='Castrol 5W-30').markup_percent, 35)

    def test_an_unusable_markup_is_refused_and_creates_nothing(self):
        for bad in ('40.5', '-5', '1000', '', 'abc', '४०'):
            resp = self.add(markup_percent=bad)
            self.assertEqual(resp.status_code, 200, repr(bad))
            self.assertContains(resp, 'Markup must be a whole number from 0 to 999')
            self.assertFalse(Item.objects.exists(), repr(bad))

    def test_a_form_with_NO_markup_box_gets_the_default_40(self):
        """
        An Add Product page opened before this field existed, submitted after
        the deploy, carries no `markup_percent` key at all. Refusing it would
        bounce somebody for a box they were never shown, so it takes the
        column's own default — the split Edit Product already makes. A key that
        IS there but blank is still refused (the test above).
        """
        resp = self.client.post(
            reverse('add_shop_catalog_item', args=[self.shop.pk]),
            {'category_name': 'Engine Oil', 'item_name': 'Castrol 5W-30', 'average_stock': '10'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Item.objects.get(name='Castrol 5W-30').markup_percent, 40)

    def test_both_mistakes_are_reported_in_one_pass(self):
        resp = self.add(markup_percent='abc', average_stock='0')
        self.assertContains(resp, 'Average Stock is required')
        self.assertContains(resp, 'Markup must be a whole number')

    def test_linking_an_existing_product_never_changes_its_markup(self):
        existing = Item.objects.create(category=Category.objects.create(name='Engine Oil'),
                                       name='Castrol 5W-30', average_stock=D('10'),
                                       markup_percent=25)
        resp = self.add(confirm_existing='1', markup_percent='90')
        self.assertEqual(resp.status_code, 302)
        existing.refresh_from_db()
        self.assertEqual(existing.markup_percent, 25)

    def _catalog_item(self, markup=40):
        item = Item.objects.create(category=Category.objects.create(name='Engine Oil'),
                                   name='Castrol 5W-30', average_stock=D('10'),
                                   current_stock=D('7'), avg_cost=D('400'),
                                   markup_percent=markup)
        return item, ShopCatalogItem.objects.create(shop=self.shop, item=item)

    def edit(self, ci, **fields):
        return self.client.post(reverse('edit_catalog_item', args=[self.shop.pk, ci.pk]), fields)

    def test_edit_product_changes_the_markup(self):
        item, ci = self._catalog_item()
        self.edit(ci, item_name='Castrol 5W-30', average_stock='10', markup_percent='55')
        item.refresh_from_db()
        self.assertEqual(item.markup_percent, 55)

    def test_a_bad_markup_on_edit_changes_NOTHING_not_even_the_name(self):
        item, ci = self._catalog_item()
        self.edit(ci, item_name='Renamed', average_stock='12', markup_percent='abc')
        item.refresh_from_db()
        self.assertEqual((item.name, item.average_stock, item.markup_percent),
                         ('Castrol 5W-30', D('10'), 40))

    def test_a_page_without_the_markup_box_leaves_the_markup_alone(self):
        item, ci = self._catalog_item(markup=25)
        self.edit(ci, item_name='Castrol 5W-30', average_stock='10')
        item.refresh_from_db()
        self.assertEqual(item.markup_percent, 25)

    def test_edit_writes_only_its_own_three_fields(self):
        """A plain save() rewrote `current_stock` and `avg_cost` from a stale
        read, overwriting any draw that landed in between."""
        item, ci = self._catalog_item()
        with mock.patch.object(Item, 'save', autospec=True) as save:
            self.edit(ci, item_name='Castrol 5W-30', average_stock='10', markup_percent='40')
        self.assertEqual(sorted(save.call_args.kwargs['update_fields']),
                         ['average_stock', 'markup_percent', 'name'])

    def test_changing_a_markup_moves_no_saved_price(self):
        from workshop.models import JobCard
        item, ci = self._catalog_item()
        job = JobCard.objects.create(admitted_date=date.today(), registration_number='KL01A1',
                                     brand_name='Toyota', model_name='Corolla')
        draw = JobCardSpareItem.objects.create(job_card=job, source=INVENTORY, item=item,
                                               quantity=D('2'), customer_rate=D('560'))
        self.edit(ci, item_name='Castrol 5W-30', average_stock='10', markup_percent='90')
        draw.refresh_from_db()
        self.assertEqual((draw.customer_rate, draw.total_price), (D('560'), D('1120')))
