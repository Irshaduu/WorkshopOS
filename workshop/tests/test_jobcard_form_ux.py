"""
The Job Card form's own shape (reworked 2026-08-13, on the owner's list).

Everything here is about the screen Office and Floor spend their day on, not
about money — but two of these guard rules that *become* money if they slip:
the internal note must never reach a customer, and the Spare Parts row must keep
posting every field it posts today whichever order the columns are in (a field
that stops being rendered saves as blank, which is how a purchase disappears off
a shop's ledger).

The visual half — the hairline on an empty box, the unsaved-changes pill, the
car-colour wash — is JavaScript and CSS, which nothing in this suite executes.
So these tests assert the things the SERVER decides: what is rendered, in what
order, and with what hooks for the script to find. Where a rule lives only in
script, the test pins the contract the script relies on rather than pretending to
run it.
"""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from workshop.models import JobCard, JobCardSpareItem, Mechanic, SpareShop

FORM_TEMPLATE = 'workshop/templates/workshop/jobcard/jobcard_form.html'


class JobCardFormBase(TestCase):
    def setUp(self):
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')

        self.user = User.objects.create_user(username='off', password='pw')
        self.user.groups.add(self.office_group)
        self.client = Client()
        self.client.login(username='off', password='pw')

        self.mechanic = Mechanic.objects.create(name='Lead Tech')
        self.shop = SpareShop.objects.create(name='Ajmal Auto Parts')
        self.job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01A1234', customer_name='John',
            customer_contact='1234567890', car_color='Red')

    def payload(self, **overrides):
        data = {
            'registration_number': 'KL01A1234',
            'admitted_date': str(date.today()),
            'customer_name': 'Alice',
            'customer_contact': '9876543210',
            'brand_name': 'Toyota',
            'model_name': 'Corolla',
            'mileage': '10000',
            'lead_mechanic': self.mechanic.id,
            'car_color': 'Red',

            'concerns-TOTAL_FORMS': '0', 'concerns-INITIAL_FORMS': '0',
            'concerns-MIN_NUM_FORMS': '0', 'concerns-MAX_NUM_FORMS': '1000',
            'spares-TOTAL_FORMS': '0', 'spares-INITIAL_FORMS': '0',
            'spares-MIN_NUM_FORMS': '0', 'spares-MAX_NUM_FORMS': '1000',
            'inventory-TOTAL_FORMS': '0', 'inventory-INITIAL_FORMS': '0',
            'inventory-MIN_NUM_FORMS': '0', 'inventory-MAX_NUM_FORMS': '1000',
            'labours-TOTAL_FORMS': '0', 'labours-INITIAL_FORMS': '0',
            'labours-MIN_NUM_FORMS': '0', 'labours-MAX_NUM_FORMS': '1000',
        }
        data.update(overrides)
        return data

    def edit(self, **overrides):
        return self.client.post(
            reverse('jobcard_edit', args=[self.job.pk]), self.payload(**overrides))

    def rendered(self):
        return self.client.get(reverse('jobcard_edit', args=[self.job.pk])).content.decode()

    def floor_client(self):
        """A signed-in Floor client — the role several sections render
        differently for."""
        floor = User.objects.create_user(username='flr-view', password='pw')
        floor.groups.add(self.floor_group)
        client = Client()
        client.login(username='flr-view', password='pw')
        return client

    def rendered_as_floor(self):
        return self.floor_client().get(
            reverse('jobcard_edit', args=[self.job.pk])).content.decode()

    @staticmethod
    def source():
        with open(FORM_TEMPLATE, encoding='utf-8') as fh:
            return fh.read()

    # ---- scoping helpers -------------------------------------------------
    #
    # Every class and custom property on this page is DECLARED in the inline
    # stylesheet as well as used in the markup, so a bare `assertIn` /
    # `assertNotIn` over the whole page answers the wrong question — it finds
    # `.jc-head--unset` in a CSS rule and calls that a hatched rail. These pull
    # out the one element or region being asserted about.

    @classmethod
    def css_rules(cls):
        """
        Every `(selector, body)` in the template's inline stylesheet.

        Worth the twenty lines: splitting the source on a selector string is
        wrong the moment two selectors share a suffix, and this file now has
        `.jc-submit,\\n    .jc-fab {` sitting above `.jc-fab {` — so
        `split('.jc-fab {')` silently returns the wrong rule and the test passes
        or fails for a reason unrelated to what it is checking. That cost five
        false failures once; it should not cost them twice.

        The regex matches only brace-free bodies, so nested `@media` wrappers
        are skipped and the rules inside them are returned on their own.
        """
        import re
        style = cls.source().split('<style>', 1)[1].split('</style>', 1)[0]
        style = re.sub(r'/\*.*?\*/', '', style, flags=re.S)
        return [(' '.join(m.group(1).split()), m.group(2))
                for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', style)]

    @classmethod
    def css_rule(cls, selector):
        """The body of exactly one rule, by its full selector."""
        for sel, body in cls.css_rules():
            if sel == selector:
                return body
        raise AssertionError('no rule in jobcard_form.html for %r' % selector)

    @staticmethod
    def open_tag(html, element_id):
        """The opening tag of `id="…"`, attributes and all."""
        at = html.find('id="%s"' % element_id)
        assert at != -1, 'no element with id=%s on the page' % element_id
        return html[html.rfind('<', 0, at):html.index('>', at) + 1]

    def spare_table(self, html=None):
        """
        Just the Spare Parts table — no stylesheet, and no Inventory table,
        whose headings ("Qty", "Customer Price (₹)") are the same words higher
        up the same page.
        """
        html = html or self.rendered()
        before, _, after = html.partition('<tbody id="spare-list">')
        assert after, 'the Spare Parts tbody has been renamed'
        return {
            # The last <thead> before that tbody is this table's own.
            'thead': before[before.rindex('<thead'):],
            'tbody': after.split('</tbody>', 1)[0],
        }


class TheInternalNoteIsForTheWorkshopOnlyTests(JobCardFormBase):
    """
    A line the workshop writes to itself. The whole value of it is that people
    will write frankly in it, and they will only do that while it is certain the
    customer never sees it.
    """

    def test_it_saves_and_comes_back(self):
        self.edit(notes='Owner is fussy — do not wash')
        self.job.refresh_from_db()
        self.assertEqual(self.job.notes, 'Owner is fussy — do not wash')
        self.assertIn('Owner is fussy', self.rendered())

    def test_it_starts_empty_and_is_never_required(self):
        """Most cards have nothing to say. A required note would refuse them."""
        resp = self.edit()
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.job.pk]))
        self.job.refresh_from_db()
        self.assertEqual(self.job.notes, '')

    def test_the_internal_note_never_reaches_the_customer(self):
        """
        The one that matters. It is true today by construction — `invoice.py`
        and the invoice template both read named fields, so a column nobody
        references cannot print — but "by construction" is exactly the kind of
        guarantee that a later generic field loop removes without anyone
        noticing. Asserted against the SHEET, not the whole page: the invoice
        view also renders office-side chrome around the paper.
        """
        secret = 'DO-NOT-PRINT-THIS-LINE'
        self.job.notes = secret
        self.job.save()

        page = self.client.get(reverse('invoice_view', args=[self.job.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(secret, page.content.decode())

    def test_the_label_no_longer_spells_out_that_it_is_not_printed(self):
        """
        INVERTED on 2026-08-16, on the owner's instruction. The label used to
        read "Internal note — never printed on the bill", and the clause was
        doing real work: it is what somebody deciding whether to write something
        candid would look for.

        It came off because "Internal" already says it, and that clause was the
        longest label on the longest form in the app. The GUARANTEE is untouched
        and is the test above this one — it is enforced by construction, not by
        a sentence on a label.
        """
        html = self.rendered()
        self.assertIn('Internal note', html)
        self.assertNotIn('never printed on the bill', html)

    def test_the_box_grows_with_what_is_in_it(self):
        """
        One row when empty — which is most cards — and as many as the note needs
        when it is not. It was a single-line <input>, so a two-sentence note
        could only be read by scrolling sideways through it.

        Asserted on the TEXTAREA, because that is the half that works without
        JavaScript; `autoGrow()` is the improvement on top and cannot be reached
        from here.
        """
        import re
        html = self.rendered()
        match = re.search(r'<textarea[^>]*name="notes"[^>]*>', html)
        self.assertIsNotNone(match, 'the internal note is not a textarea')
        tag = match.group(0)
        self.assertIn('jc-grow', tag)
        self.assertIn('rows="1"', tag)
        self.assertIn('maxlength="255"', tag)

    def test_floor_may_write_one(self):
        """
        It carries no money, so it is not price-locked — a mechanic noting what
        the customer said at handover is the point of the box. Contrast the
        price fields, which `_floor_locked_data` pins for Floor.
        """
        floor = User.objects.create_user(username='flr', password='pw')
        floor.groups.add(self.floor_group)
        client = Client()
        client.login(username='flr', password='pw')
        client.post(reverse('jobcard_edit', args=[self.job.pk]),
                    self.payload(notes='Customer says noise only when cold'))
        self.job.refresh_from_db()
        self.assertEqual(self.job.notes, 'Customer says noise only when cold')


class CustomerDetailsIsFoldedAwayTests(JobCardFormBase):
    """
    Collapsed by default, on the owner's instruction, and the reason is how the
    business runs: Owner 1 deals with customers personally and keeps those
    relationships himself, so the workshop identifies a car by its
    REGISTRATION, not by whose it is. Most job cards carry no name and no
    number, and three permanently empty boxes between Vehicle Details and
    Customer Concerns are three boxes everyone scrolls past on every card.

    Nothing was removed and nothing was made harder — it is the same three
    fields, one tap away.
    """

    def fold(self, html=None):
        html = html or self.rendered()
        at = html.index('class="card shadow-sm mb-4 border-0 jc-fold"')
        return html[html.rindex('<details', 0, at):html.index('</summary>', at)]

    def test_it_is_named_for_what_it_holds(self):
        self.assertIn('Customer &amp; Notes', self.fold())

    def test_a_card_with_no_customer_opens_closed(self):
        self.job.customer_name = ''
        self.job.customer_contact = ''
        self.job.notes = ''
        self.job.save()
        self.assertNotIn('open', self.fold())

    def test_a_closed_section_still_saves_what_is_typed_into_it(self):
        """
        The one that matters. A `<details>` that is shut renders its contents
        `display: none`, and a hidden form control has always still posted — so
        folding the section changed what is on screen and nothing about what is
        stored. If this fails, every customer name in the workshop is being
        wiped on save.
        """
        self.job.customer_name = ''
        self.job.customer_contact = ''
        self.job.save()
        self.assertNotIn('open', self.fold())          # it really is shut

        self.edit(customer_name='Rashid', customer_contact='9876500000',
                  notes='Collects after 6pm')
        self.job.refresh_from_db()
        self.assertEqual(self.job.customer_name, 'Rashid')
        self.assertEqual(self.job.customer_contact, '9876500000')
        self.assertEqual(self.job.notes, 'Collects after 6pm')

    def test_it_opens_itself_when_there_is_something_to_see(self):
        """
        Collapsed-by-default is only right while the section is empty. A card
        that HAS a customer must show them, or the fold turns into a place
        information goes to hide.
        """
        for field, value in (('customer_name', 'Rashid'),
                             ('customer_contact', '9876500000'),
                             ('notes', 'Collects after 6pm')):
            for f in ('customer_name', 'customer_contact', 'notes'):
                setattr(self.job, f, '')
            setattr(self.job, field, value)
            self.job.save()
            self.assertIn('open', self.fold(),
                          'a card carrying %s renders the fold shut' % field)

    def test_it_opens_when_a_refused_save_put_an_error_inside_it(self):
        """
        Otherwise the message is behind a summary nobody thought to click, and
        the page says "not saved" while showing nothing wrong.
        """
        self.job.customer_name = ''
        self.job.save()
        resp = self.edit(customer_name='x' * 200, registration_number='')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('open', self.fold(resp.content.decode()))

    def test_it_needs_no_javascript(self):
        """
        A native `<details>`: nothing to wire, so nothing to get wrong on a
        cloned row or a slow load, and keyboard plus screen-reader behaviour
        comes for free. The three cloning traps in CLAUDE.md all live in
        per-element wiring this simply does not have.
        """
        fold = self.fold()
        self.assertTrue(fold.lstrip().startswith('<details'))
        self.assertIn('<summary', fold)


class TheSpareRowKeepsEveryFieldItPostsTests(JobCardFormBase):
    """
    The columns were reordered on the owner's instruction. Order is cosmetic;
    what is NOT cosmetic is that all eight fields still render, because an
    absent formset field saves as blank — that is how the archived-shop bug
    erased a purchase from a shop's ledger, and a reorder that dropped a cell
    would do it again through a different door.
    """

    def setUp(self):
        super().setUp()
        self.spare = JobCardSpareItem.objects.create(
            job_card=self.job, source=JobCardSpareItem.SOURCE_SHOP,
            spare_part_name='Brake Pad', quantity=D('2'), shop=self.shop,
            shop_name=str(self.shop.pk), unit_price=D('500'), total_price=D('900'),
            status='ORDERED', ordered_date=date.today())

    def test_the_columns_are_in_the_order_the_owner_asked_for(self):
        thead = self.spare_table()['thead']
        wanted = ['Part Name', 'Qty', 'Status', 'Shop', 'Dates',
                  'Shop Price', 'Customer Price']
        positions = [thead.find(w) for w in wanted]
        self.assertNotIn(-1, positions, 'a spare column heading went missing')
        self.assertEqual(positions, sorted(positions),
                         'Spare Parts columns are no longer Part Name · Qty · '
                         'Status · Shop · Dates · Shop Price · Customer Price')

    def test_every_posting_field_still_renders(self):
        html = self.rendered()
        for field in ('spare_part_name', 'quantity', 'shop_name', 'status',
                      'unit_price', 'total_price', 'ordered_date', 'received_date'):
            self.assertIn('spares-0-%s' % field, html,
                          '%s stopped rendering — it will save as blank' % field)

    def test_a_round_trip_through_the_reordered_row_changes_nothing(self):
        """Post the row back exactly as rendered; every value must survive."""
        resp = self.edit(**{
            'spares-TOTAL_FORMS': '1', 'spares-INITIAL_FORMS': '1',
            'spares-0-id': str(self.spare.pk),
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '2',
            'spares-0-shop_name': str(self.shop.pk),
            'spares-0-status': 'ORDERED',
            'spares-0-unit_price': '500',
            'spares-0-total_price': '900',
            'spares-0-ordered_date': str(date.today()),
            'spares-0-received_date': '',
        })
        self.assertRedirects(resp, reverse('jobcard_edit', args=[self.job.pk]))
        self.spare.refresh_from_db()
        self.assertEqual(self.spare.shop_id, self.shop.pk)
        self.assertEqual(self.spare.unit_price, D('500'))
        self.assertEqual(self.spare.total_price, D('900'))
        self.assertEqual(self.spare.quantity, D('2'))

    def test_both_dates_share_one_cell_behind_one_chip(self):
        """
        Two columns became one, and that one shows a chip — "22/07 – 29/07" —
        that opens a panel holding both dates.

        The load-bearing part is that the inputs are STILL the real form fields,
        in the form, with their names: a hidden input submits its value, so
        putting them behind a chip changed where they are shown and nothing
        about what is saved. If this fails, ordered/received dates are being
        wiped on every save.
        """
        tbody = self.spare_table()['tbody']
        cell = tbody.split('jc-dates', 1)[1].split('</td>', 1)[0]
        self.assertIn('jc-date-chip', cell)
        self.assertIn('jc-date-pop', cell)
        self.assertIn('spares-0-ordered_date', cell)
        self.assertIn('spares-0-received_date', cell)

    def test_the_chip_is_a_button_and_cannot_submit_the_card(self):
        """
        A bare `<button>` inside a form submits it. Every button added here is
        `type="button"`; getting one wrong would save the job card when somebody
        went to look at a date.
        """
        tbody = self.spare_table()['tbody']
        for marker in ('jc-date-chip', 'jc-date-done'):
            chunk = tbody.split(marker, 1)[0]
            opening = chunk[chunk.rindex('<button'):]
            self.assertIn('type="button"', opening,
                          '%s is not type="button" — it will submit the form' % marker)

    def test_the_panel_is_fixed_so_it_can_neither_be_clipped_nor_move_a_row(self):
        """
        Both halves of the reason it is `position: fixed`, and both were
        measured in a browser before it was built.

        Clipping: it lives in a `<td>` inside `.table-responsive`, which is
        `overflow-x: auto` — the exact shape that silently cuts off an
        absolutely-positioned panel, which is the `.cb-list` trap CLAUDE.md
        already records an afternoon for. Verified on a 375px phone: the panel
        hangs 8px past the scroller's right edge and `elementFromPoint` still
        returns its inputs.

        Layout: a fixed element is out of flow, so opening one cannot move a
        row. Verified: table height, row height and page height are identical
        with the panel open and shut.
        """
        rule = self.source().split('.jc-date-pop {', 1)[1].split('}', 1)[0]
        self.assertIn('position: fixed', rule)

    def test_floor_still_posts_every_price_it_cannot_see(self):
        """
        The riskiest part of the reorder. Floor sees no Shop and no prices, but
        those inputs must STILL render — inside a `d-none` cell — because an
        absent formset field saves as blank and would wipe what Office entered.
        The reorder moved the Shop column across the `{% if %}` that decides
        this, so both branches had to be rebuilt, and a mistake here is silent:
        the page looks right and the money goes.

        The complementary half — that a Floor POST cannot *change* those
        values — is `_floor_locked_data`, covered in test_jobcard_inventory_section.
        """
        floor = User.objects.create_user(username='flr2', password='pw')
        floor.groups.add(self.floor_group)
        client = Client()
        client.login(username='flr2', password='pw')
        html = client.get(reverse('jobcard_edit', args=[self.job.pk])).content.decode()

        for field in ('spares-0-unit_price', 'spares-0-total_price', 'spares-0-shop_name'):
            self.assertIn(field, html,
                          '%s is not rendered for Floor — it will save as blank '
                          'and wipe what Office entered' % field)
        # …and the ones Floor does work with are on screen as normal.
        for field in ('spares-0-spare_part_name', 'spares-0-quantity',
                      'spares-0-status', 'spares-0-ordered_date'):
            self.assertIn(field, html)

    def test_each_role_gets_as_many_body_cells_as_it_has_headings(self):
        """
        Column ALIGNMENT, which is the thing a reorder actually breaks and the
        thing no round-trip test would notice. A `d-none` cell is
        `display: none`, so the browser drops it out of the table layout
        entirely — which means the count that has to match the headings is the
        count of cells that are NOT hidden. Office and Floor render different
        numbers of both, so both are checked.

        This is what would fail if the Shop column were moved across one of the
        two `{% if %}`s and not the other.
        """
        import re

        def counts(client):
            html = client.get(reverse('jobcard_edit', args=[self.job.pk])).content.decode()
            table = self.spare_table(html)
            row = table['tbody'].split('</tr>', 1)[0]
            cells = re.findall(r'<td\b[^>]*>', row)
            return {
                'headings': len(re.findall(r'<th\b', table['thead'])),
                'visible_cells': len([c for c in cells if 'd-none' not in c]),
            }

        office = counts(self.client)
        self.assertEqual(office['headings'], office['visible_cells'],
                         'Office: %r' % office)

        floor_user = User.objects.create_user(username='flr4', password='pw')
        floor_user.groups.add(self.floor_group)
        floor_client = Client()
        floor_client.login(username='flr4', password='pw')
        floor = counts(floor_client)
        self.assertEqual(floor['headings'], floor['visible_cells'],
                         'Floor: %r' % floor)

        # And they really are different shapes — otherwise the check above could
        # be passing because the role gate silently stopped applying.
        self.assertGreater(office['headings'], floor['headings'])

    def test_a_floor_save_leaves_the_prices_and_the_shop_alone(self):
        """The round trip that proves the point above, end to end."""
        floor = User.objects.create_user(username='flr3', password='pw')
        floor.groups.add(self.floor_group)
        client = Client()
        client.login(username='flr3', password='pw')
        client.post(reverse('jobcard_edit', args=[self.job.pk]), self.payload(**{
            'spares-TOTAL_FORMS': '1', 'spares-INITIAL_FORMS': '1',
            'spares-0-id': str(self.spare.pk),
            'spares-0-spare_part_name': 'Brake Pad',
            'spares-0-quantity': '2',
            'spares-0-shop_name': str(self.shop.pk),
            'spares-0-status': 'RECEIVED',
            'spares-0-unit_price': '500',
            'spares-0-total_price': '900',
            'spares-0-ordered_date': str(date.today()),
            'spares-0-received_date': str(date.today()),
        }))
        self.spare.refresh_from_db()
        self.assertEqual(self.spare.status, 'RECEIVED')      # Floor's own field moved
        self.assertEqual(self.spare.unit_price, D('500'))    # the money did not
        self.assertEqual(self.spare.total_price, D('900'))
        self.assertEqual(self.spare.shop_id, self.shop.pk)

    def test_the_added_row_template_matches_the_live_rows(self):
        """
        `#empty-spare-form` is cloned by script.js when somebody presses "+ Add
        Spare". Its cells must sit in the same order as the rows above it or an
        added row lays out one column adrift of the header — and nothing else
        would catch that, because the clone happens in the browser.
        """
        source = self.source()
        template = source.split('id="empty-spare-form"', 1)[1].split('</tbody>', 1)[0]
        live = source.split('<tbody id="spare-list">', 1)[1].split('</tbody>', 1)[0]

        def order(chunk):
            found = []
            for token in ('quantity', 'status', 'shop_name', 'ordered_date',
                          'received_date', 'unit_price', 'total_price'):
                at = chunk.find(token)
                if at != -1:
                    found.append((at, token))
            return [t for _, t in sorted(found)]

        self.assertEqual(order(template), order(live),
                         'the "+ Add Spare" row template has drifted out of '
                         'column order with the rows it is cloned beside')


class ADatePairIsOnlyDoneWhenBothAreInTests(JobCardFormBase):
    """
    On the owner's instruction (2026-08-13), reversing a briefly-shipped
    exemption: a spare part is finished when it has been ordered AND received,
    so the date chip stays marked until BOTH are filled. Half-filled is still
    incomplete, not half-done.

    Verified in a browser across all four states: neither → marked; ordered
    only → marked, chip reads "22/07 – …"; both → clear, "22/07 – 29/07";
    remove one → marked again.
    """

    def test_neither_date_is_exempt_from_the_mark(self):
        from workshop.forms import JobCardSpareFormSet
        row = JobCardSpareFormSet().empty_form
        for name in ('ordered_date', 'received_date'):
            self.assertNotIn('jc-optional',
                             row.fields[name].widget.attrs.get('class', ''),
                             '%s is exempt again — the pair must stay marked '
                             'until both are filled' % name)

    def test_the_chip_clears_only_when_both_are_filled(self):
        source = self.source()
        self.assertIn("chip.classList.toggle('jc-empty', !(ordered && received));", source)

    def test_the_panel_says_which_of_the_two_is_missing(self):
        """
        The chip is one control for two facts, so on its own it cannot say
        WHICH date is outstanding. The two inputs inside the panel are swept
        like any other box, so opening it marks only the empty one.
        """
        source = self.source()
        # They are ordinary boxes to the sweep — nothing excludes them.
        self.assertNotIn('ordered-date jc-optional', source)
        self.assertNotIn('received-date jc-optional', source)


class TheFormSaysLessTests(JobCardFormBase):
    """
    Three pieces of text came off the form on the owner's instruction, each
    because something else on screen already said it.
    """

    def test_the_jobs_section_does_not_repeat_its_own_heading(self):
        """
        "Job Performed" as a column heading sat directly under a card titled
        "Jobs (Labour)", over one column whose boxes are placeholdered "Job
        Performed". A heading earns its place by telling one column from
        another, and there is only one column.
        """
        html = self.rendered()
        self.assertNotIn('swipe-header', html)
        # The placeholder stays — it is what names the box now.
        self.assertIn('Job Performed', html)

    def test_the_inventory_box_still_says_it_searches_by_type(self):
        """
        The section subtitle went, and the fact it carried did NOT: the picker
        matches category names as well as product names, so "Engine Oil" finds
        Liqui Moly and Castrol. Nobody would guess that, so the placeholder is
        now the only thing saying it and is therefore load-bearing. If this
        fails, the explanation has to go back somewhere before the placeholder
        is shortened.
        """
        html = self.rendered()
        self.assertNotIn('Taken from workshop stock', html)
        self.assertIn('Search by product or type', html)

    def test_the_spare_section_drops_its_decorative_subtitle(self):
        html = self.rendered()
        self.assertNotIn('Ordered from a spare shop', html)

    #: The vehicle and customer boxes carry NO placeholder, on the owner's
    #: instruction (2026-08-16). Every one of them sits under a label that
    #: already names it, so the hint restated the label in quieter type — and on
    #: a form this long that is a second line of text per box for no fact. The
    #: two placeholders that survive elsewhere on this form earn it by saying
    #: something the label cannot: the Inventory box's "or type" (above) and the
    #: money boxes' currency.
    NO_PLACEHOLDER_FIELDS = ('brand_name', 'model_name', 'registration_number',
                             'mileage', 'car_color_other', 'customer_name',
                             'customer_contact', 'notes')

    def test_the_vehicle_and_customer_boxes_carry_no_placeholder(self):
        from workshop.forms import JobCardForm
        form = JobCardForm()
        for name in self.NO_PLACEHOLDER_FIELDS:
            with self.subTest(field=name):
                self.assertNotIn('placeholder', form.fields[name].widget.attrs,
                                 '%s still has a placeholder' % name)

    def test_the_estimate_agrees_with_the_job_card_about_that(self):
        """
        The two forms are filled in by the same people minutes apart. A hint on
        one and not the other reads as one of them being unfinished.
        """
        from workshop.forms import EstimateForm
        form = EstimateForm()
        for name in self.NO_PLACEHOLDER_FIELDS:
            if name in form.fields:
                with self.subTest(field=name):
                    self.assertNotIn('placeholder', form.fields[name].widget.attrs)

    def test_the_old_mileage_hint_is_gone_from_the_page(self):
        self.assertNotIn('Meter 00001', self.rendered())


class EverySectionAnnouncesItselfTheSameWayTests(JobCardFormBase):
    """
    Six sections, one heading shape: a tinted glyph tile, the name, the action.
    They were six hand-rolled flex rows whose only common element was a blue
    `<h6>` — and the Customer block had none at all, so scrolling the form you
    counted "Vehicle Details … (something) … Customer Concerns".
    """

    #: In page order. The glyph is what makes a section findable while scrolling
    #: a form this long.
    SECTIONS = [
        ('Vehicle Details', 'bi-car-front-fill'),
        ('Customer &amp; Notes', 'bi-person-vcard-fill'),
        ('Customer Concerns', 'bi-chat-left-text-fill'),
        ('Job Performed', 'bi-tools'),
        ('Inventory Items', 'bi-box-seam-fill'),
        ('Spare Parts', 'bi-nut-fill'),
    ]

    def test_every_section_has_a_name_and_a_glyph_in_page_order(self):
        import re
        html = self.rendered()
        heads = re.findall(
            r'jc-sec-icon"><i class="bi (bi-[a-z-]+)"></i></span>\s*'
            r'<h6 class="jc-sec-name">([^<]+)</h6>',
            html)
        self.assertEqual([(n.strip(), i) for i, n in heads],
                         [(n, i) for n, i in self.SECTIONS])

    def test_the_customer_block_is_no_longer_the_unnamed_one(self):
        self.assertIn('>Customer &amp; Notes</h6>', self.rendered())

    def test_floor_gets_the_same_shape_with_a_different_name(self):
        """
        Floor is not shown the customer's name or number, so that section holds
        only the internal note — and it is NAMED for what it holds. A heading
        reading "Customer Details" over a box that says nothing about the
        customer is the page misdescribing itself.
        """
        html = self.rendered_as_floor()

        self.assertIn('>Workshop Note</h6>', html)
        self.assertNotIn('>Customer &amp; Notes</h6>', html)

    #: The nav bar's own gradient sampled at 84% — the bar is
    #: `linear-gradient(90deg, #10275c 0%, #1e4fb8 45%, #2f7de8 100%)`.
    #:
    #: A six-step RAMP down that gradient was built first, on the owner's idea,
    #: and the owner looked at it and chose one flat colour. Recorded because
    #: the reasoning outlives the decision: the sections are not a scale of
    #: anything — a car's concerns are not "more" than its vehicle details — so
    #: six shades invited being read as a ranking, and the darkest drew the eye
    #: hardest at the bottom of the form where the least urgent sections live.
    def test_section_headers_wear_light_slate_design(self):
        source = self.source()
        head = source.split('.jc-sec-head {', 1)[1].split('}', 1)[0]
        self.assertIn('background: #f8fafc', head)
        # No per-section shade survives, in the stylesheet or the markup.
        self.assertNotIn('jc-sec--', source)
        self.assertNotIn('jc-sec--', self.rendered())

    def test_light_slate_header_elements_have_high_contrast(self):
        source = self.source()
        for selector, want in (('.jc-sec-name {', 'color: #0f172a'),
                               ('.jc-sec-icon {', 'color: #475569'),
                               ('.jc-fold-chevron {', 'color: #64748b')):
            rule = source.split(selector, 1)[1].split('}', 1)[0]
            self.assertIn(want, rule, '%s does not match expected color' % selector)
        # The Add buttons are styled for the light slate header
        self.assertIn('.jc-sec-head .jc-add', source)

    def test_the_heading_is_bold_and_readable(self):
        rule = self.source().split('.jc-sec-name {', 1)[1].split('}', 1)[0]
        self.assertIn('font-weight: 700', rule)
        self.assertIn('color: #0f172a', rule)

    def test_the_field_labels_are_bold_at_their_original_colour_and_size(self):
        """
        On the owner's instruction: Car Brand, Car Model, Registration Number
        and the rest go bold, keeping the colour and size they already had.
        Weight is the only axis touched — the labels sit above boxes whose
        placeholders are deliberately quiet, and at weight 400 the label and the
        hint read as the same kind of text.

        600 rather than 700: the section band above them is already 700, and at
        700 the labels compete with it for the same voice.
        """
        source = self.source()
        rule = source.split('#jobcardForm .form-label {', 1)[1].split('}', 1)[0]
        self.assertIn('font-weight: 600', rule)
        # Colour and size are NOT touched — that was the instruction.
        for untouched in ('color', 'font-size'):
            self.assertNotIn(untouched, rule)

    def test_a_narrow_screen_drops_the_button_word_not_the_section_name(self):
        """
        Measured on a 375px phone: "Customer Concerns" needs 169px and had 134,
        because "+ Add Concern" was taking 124. The glyph alone is 44px and a
        "+" in a section header says what it does; the name is the one thing on
        the band that cannot be guessed. Same call the Spare Shop header
        records — a header gives up its actions before it gives up its name.
        """
        source = self.source()
        narrow = source.split('@media (max-width: 575.98px) {', 1)[1].split('\n    }', 1)[0]
        self.assertIn('.jc-add-label { display: none; }', narrow)
        self.assertIn('min-width: 44px', narrow)   # a target is only as big as its smaller side

        # Every Add button carries its wording for anyone who cannot see the
        # glyph — this codebase's rule for a control that can become icon-only.
        html = self.rendered()
        for label in ('Add Concern', 'Add Job', 'Add Item', 'Add Spare'):
            self.assertIn('aria-label="%s"' % label, html)
            self.assertIn('<span class="jc-add-label">%s</span>' % label, html)

    def test_no_section_still_uses_the_old_hand_rolled_header(self):
        """
        One shape or it is not a shape. The old `card-header bg-white py-3` is
        allowed to survive on the "Vehicles in Workshop" panel, which is a list
        of cars rather than a part of the form being filled in.
        """
        source = self.source()
        form_part = source.split('<form method="post" id="jobcardForm"', 1)[1]
        self.assertNotIn('card-header bg-white py-3', form_part)


class ThePrimaryActionSaysWhichActItIsTests(JobCardFormBase):
    """
    Creating a job card and correcting one are different acts, so the button is
    not the same button — and the difference is carried by three things, never
    by colour alone.
    """

    @staticmethod
    def button(html):
        """
        The submit button's own markup — NOT the whole page. Every class here is
        also DECLARED in the inline stylesheet, so `assertNotIn('jc-submit--new')`
        over the page finds the CSS rule and reports a green button on the edit
        screen.
        """
        at = html.index('class="btn btn-lg py-3 fw-bold jc-submit')
        start = html.rindex('<button', 0, at)
        return html[start:html.index('</button>', at)]

    def test_a_new_card_and_an_edit_get_different_buttons(self):
        new = self.button(self.client.get(reverse('jobcard_create')).content.decode())
        edit = self.button(self.rendered())

        # Three carriers, never colour alone.
        self.assertIn('jc-submit--new', new)      # green
        self.assertIn('bi-clipboard-plus', new)   # glyph
        self.assertIn('Save Job Card', new)       # wording

        self.assertNotIn('jc-submit--new', edit)  # amber
        self.assertIn('bi-save', edit)
        self.assertIn('Update Job Card', edit)

    def test_the_edit_button_is_amber_and_carries_dark_text(self):
        """
        `btn-primary` blue put the one control that matters most into a page
        that is now mostly blue, so it stopped being the loudest thing on it. A
        deeper navy was tried and rejected by the owner — it solved the problem
        by being a darker blue and still read as one more blue thing.

        Amber is the only colour on this page already about *your changes*: the
        header goes amber, the pill is amber, every box you touched wears an
        amber edge. The button that commits them wearing it is the page
        agreeing with itself.

        Amber forces DARK text and that is not optional — white on `#f59e0b`
        measures 2.2:1 and is unreadable. `#1e293b` on it is 6.81:1, measured.
        """
        source = self.source()
        self.assertIn('--jc-action: #f59e0b;', source)
        self.assertIn('--jc-action-ink: #1e293b;', source)
        # Bootstrap's colour classes are gone from the markup, or they would
        # fight the variable and win on hover.
        button = self.button(self.rendered())
        self.assertNotIn('btn-primary', button)
        self.assertNotIn('btn-success', button)

    def test_neither_button_carries_a_shadow(self):
        """
        On the owner's instruction. The border light is what says "unsaved"
        now; a drop shadow under it was a second, duller version of the same
        message, and it made the buttons look like they float off a page
        nothing else floats off.
        """
        for selector in ('.jc-submit', '.jc-fab'):
            self.assertIn('box-shadow: none', self.css_rule(selector),
                          '%s grew a shadow again' % selector)

    def test_both_doors_read_one_colour_variable(self):
        """
        The big button and the sticky one are the same action, so they cannot be
        allowed to end up different colours. One `--jc-action`, two users.
        """
        self.assertIn('--jc-action', self.css_rule('.jc-submit, .jc-fab'))
        for selector in ('.jc-submit', '.jc-fab'):
            self.assertIn('background: var(--jc-action)', self.css_rule(selector),
                          '%s no longer reads the shared action colour' % selector)

    def test_unsaved_work_runs_a_light_around_the_border(self):
        """
        The owner's replacement for a pulsing shadow — "looks like the button
        has life". Better than the pulse for a reason worth stating: a pulse
        changes the button's apparent SIZE, so the eye keeps being pulled back
        to something growing and shrinking. A light running the edge is movement
        with no change of weight.

        It is confined to the border by a two-mask ring — without the `padding`
        + `mask-composite` pair the gradient washes across the button's face
        instead. And the ANGLE turns, not the element: rotating the element
        would turn the ring with it and skew a wide rectangle.
        """
        source = self.source()
        self.assertIn('@property --jc-orbit', source)
        self.assertIn('@keyframes jc-orbit { to { --jc-orbit: 360deg; } }', source)
        ring = self.css_rule('.jc-dirty .jc-submit:not(:disabled)::before, '
                             '.jc-dirty .jc-fab:not(:disabled)::before')
        self.assertIn('conic-gradient(from var(--jc-orbit)', ring)
        self.assertIn('padding: 2px', ring)
        self.assertIn('mask-composite: exclude', ring)
        self.assertIn('-webkit-mask-composite: xor', ring)   # older Chrome
        self.assertIn('animation: jc-orbit', ring)

    def test_the_light_is_white_because_the_button_is_amber(self):
        """
        The light was amber first — matching the header, the pill and the box
        edges, which are all amber for unsaved work. Then the edit button itself
        became amber on the owner's instruction, and an amber light on an amber
        button is invisible.

        White is the answer that serves both: it reads on the amber edit button
        AND on the green create one, so a single gradient covers both. The
        "unsaved" MEANING is unaffected — it is still carried in amber by the
        header, the pill and every box you touched. This is the movement, not
        the message.
        """
        ring = self.css_rule('.jc-dirty .jc-submit:not(:disabled)::before, '
                             '.jc-dirty .jc-fab:not(:disabled)::before')
        self.assertIn('255, 255, 255', ring)
        self.assertNotIn('251, 191, 36', ring)     # amber-on-amber, invisible
        self.assertNotIn('jc-glow', self.source()) # the old pulse is gone entirely

    def test_an_old_browser_still_says_unsaved(self):
        """
        The ring needs `mask-composite` (Safari 15.4+) and a registered
        `@property` for the angle to interpolate (Safari 16.4+). Both are likely
        on the owners' phones and neither is guaranteed, so a still 2px outline
        in the same white is declared UNCONDITIONALLY and the moving light is
        added on top inside `@supports`, which clears the outline where it can
        draw. An old browser loses the animation and still says "unsaved"; it
        never shows a broken ring and never shows nothing.

        It is an INSET shadow, not a drop shadow: the owner asked for no shadow
        on these buttons, and an inset paints inside the button so it cannot
        change its size either.
        """
        fallback = self.css_rule('.jc-dirty .jc-submit:not(:disabled), '
                                 '.jc-dirty .jc-fab:not(:disabled)')
        self.assertIn('inset', fallback)
        self.assertIn('255, 255, 255', fallback)
        # …and the ring clears it where it CAN be drawn, so they never stack.
        gated = self.source().split('@supports ((mask-composite: exclude)', 1)[1]
        self.assertIn('box-shadow: none;', gated.split('::before', 1)[0])

    def test_the_sticky_button_gets_press_feedback_too(self):
        """
        It was in the `pointerdown` handler but had no `::after` to animate, so
        the one control most likely to be tapped was the one with no press
        feedback at all.
        """
        source = self.source()
        self.assertIn('.jc-add::after, .jc-submit::after, .jc-fab::after', source)
        self.assertIn('.jc-add, .jc-submit, .jc-fab { overflow: hidden;', source)

    def test_the_press_feedback_cannot_reflow_the_form(self):
        """
        `transform`, `box-shadow` and `background-color` are composited or
        paint-only — none of them can move anything. A width, padding or margin
        animation on a control inside these tables would nudge the very box
        somebody is aiming at, on a tablet.
        """
        source = self.source()
        for selector in ('.jc-add:hover', '.jc-add:active',
                         '.jc-submit:hover:not(:disabled)', '.jc-submit:active:not(:disabled)'):
            rule = source.split(selector + ' {', 1)[1].split('}', 1)[0]
            for forbidden in ('width', 'height', 'padding', 'margin', 'font-size', 'border-width'):
                self.assertNotIn(forbidden, rule,
                                 '%s animates %s — that reflows the page' % (selector, forbidden))

    def test_the_feedback_is_built_for_a_finger_not_a_pointer(self):
        """
        These sections are worked on the Floor tablet, where hover is wrong
        twice over: it never fires on a touch screen, and where it does fire it
        STICKS — a tapped button keeps its hover paint until something else is
        tapped, so the last thing pressed sits there looking half-pressed.

        So every hover rule is behind `@media (hover: hover)` and reaches a
        mouse only, and what a finger gets is `:active` — which fires on touch
        and releases with the finger.
        """
        source = self.source()
        # No hover rule may sit outside the gate.
        for line in source.splitlines():
            if ':hover' in line and 'jc-' in line and line.strip().startswith('.'):
                self.assertIn('@media (hover: hover)', source)
        gated = source.split('@media (hover: hover) {', 1)[1]
        self.assertIn('.jc-add:hover', gated)

        # And the press is a real squash, not the token one a pointer needs.
        # Anchored on the line start: `.jc-sec-head .jc-add:active` also
        # contains this selector as a substring and would match first.
        press = source.split('\n    .jc-add:active {', 1)[1].split('}', 1)[0]
        self.assertIn('scale(.94)', press)
        self.assertIn('background-color', press)   # fills in; reads at arm's length

    def test_an_added_row_announces_itself(self):
        """
        On a tablet the "+ Add" button is at the top of its section and the new
        row lands at the bottom of a list that may already be below the fold —
        so without this the only evidence a tap registered is a scrollbar
        changing length. The row lights up and is brought into view.
        """
        source = self.source()
        self.assertIn('@keyframes jc-rowin', source)
        self.assertIn("row.classList.add('jc-row-new')", source)
        self.assertIn("row.scrollIntoView({ block: 'nearest' })", source)
        # Re-adding a class an element already has restarts nothing.
        self.assertIn('void row.offsetWidth;', source)

    def test_the_sweep_is_driven_by_a_class_not_by_active(self):
        """
        A tap releases in about 80ms and takes `:active` with it, so an
        animation hung off `:active` is cut off halfway on the exact device this
        is for. The class is added on `pointerdown` — which covers touch, mouse
        and pen in one path — and removed on `animationend`, so the sweep always
        completes however briefly the finger was down.
        """
        source = self.source()
        self.assertIn('@keyframes jc-sweep', source)
        self.assertIn(".jc-sweep::after { animation: jc-sweep", source)
        self.assertIn("document.addEventListener('pointerdown'", source)
        self.assertNotIn('.jc-submit:active::after', source)
        # A transform, so it is composited and can move nothing.
        frames = source.split('@keyframes jc-sweep {', 1)[1].split('}\n', 1)[0]
        self.assertIn('translateX', frames)
        for forbidden in ('width', 'left', 'margin', 'padding'):
            self.assertNotIn(forbidden, frames)

    def test_the_only_looping_animation_is_the_unsaved_light(self):
        """
        A button that shimmers on its own is noise on a screen staff work all
        day, and it costs battery on the Floor tablet for nothing. The single
        exception is the Save button while there is unsaved work — a state that
        is temporary, that the person can end, and that is worth attention
        precisely because leaving the page loses it. It stops the moment the
        card is saved, so it can never become wallpaper.
        """
        looping = [(sel, body) for sel, body in self.css_rules()
                   if 'infinite' in body]
        self.assertTrue(looping, 'the unsaved light has gone')
        for selector, body in looping:
            # `jc-spin` is the other one, and it is fine: it lives on
            # `.jc-submit .jc-spin`, which is `display: none` until the button
            # goes to "Saving…", so it runs only while a save is in flight.
            if 'jc-spin' in selector or 'jc-spin' in body:
                continue
            self.assertIn('.jc-dirty', selector,
                          'a looping animation that is not gated on unsaved '
                          'work: %s' % selector)
        self.assertTrue(any('.jc-dirty' in sel for sel, _ in looping),
                        'the unsaved light is no longer gated on unsaved work')
        # It animates a custom ANGLE on a pseudo-element — no layout property,
        # so a button that is alive can still never reflow the page under it.
        frames = self.source().split('@keyframes jc-orbit', 1)[1].split('}', 1)[0]
        self.assertIn('--jc-orbit', frames)
        for forbidden in ('width', 'height', 'padding', 'margin', 'top:', 'left:'):
            self.assertNotIn(forbidden, frames)

    def test_touch_targets_clear_the_tablet_minimum(self):
        """
        44px under `@media (hover: none)`, keyed on input method rather than a
        width breakpoint — it is the finger that decides how big a target must
        be, and the Floor tablet is wider than plenty of laptops.
        """
        touch = self.source().split('@media (hover: none) {', 1)[1].split('\n    }', 1)[0]
        self.assertIn('.jc-add { min-height: 44px; }', touch)
        self.assertIn('.jc-date-chip { min-height: 44px; }', touch)

    def test_one_press_makes_one_job_card(self):
        """
        A slow save on workshop wifi invites a second press, and on the create
        page a second POST is a second job card for the same car. `disabled` is
        set in a `setTimeout(0)` and NOT inline, because disabling a submit
        button inside its own submit handler cancels the submission in some
        browsers.
        """
        source = self.source()
        guard = source.split('ONE PRESS, ONE JOB CARD', 1)[1]
        self.assertIn("btn.classList.add('is-working')", guard)
        self.assertIn('if (e.defaultPrevented) return;', guard)
        # Deferred by a task, never inline. Which buttons get disabled is
        # `test_pressing_either_door_stops_the_other`; this is about the timing.
        disable = guard.split('setTimeout(function () {', 1)[1].split('}, 0);', 1)[0]
        self.assertIn('disabled = true', disable)

    def test_motion_is_dropped_for_anyone_who_asked_for_none(self):
        self.assertIn('@media (prefers-reduced-motion: reduce)', self.source())


class TheStickySaveTests(JobCardFormBase):
    """
    A round save button in the bottom-right, on the owner's request, so nobody
    has to scroll to the foot of a form several screens long. The condition was
    "no interruption to the total job card view".
    """

    def fab(self, html=None):
        html = html or self.rendered()
        at = html.index('class="jc-fab')
        return html[html.rindex('<button', 0, at):html.index('</button>', at)]

    def test_it_lives_inside_the_form_so_the_financial_lock_reaches_it(self):
        """
        The one that is an integrity matter rather than a layout one. The
        Financial Lock disables controls with `form.querySelectorAll(...)`, so a
        floating button OUTSIDE the form would be the single control the lock
        never reached — a settled, locked job card, saveable from a button in
        the corner. Inside, it is disabled with everything else for free.

        Verified in a browser: the lock's own selector matches it.
        """
        html = self.rendered()
        form = html.split('<form method="post" id="jobcardForm"', 1)[1].split('</form>', 1)[0]
        self.assertIn('jc-fab', form,
                      'the sticky save escaped the form — the Financial Lock '
                      'no longer disables it')

    def test_it_is_absent_until_there_is_something_to_save(self):
        """
        The answer to "no interruption" is not a smaller button — it is a button
        that is not there. It appears the moment something is typed and leaves
        when the card is saved, so it is never in the way of anybody who has
        nothing to save, and it can never be pressed pointlessly.
        """
        self.assertIn('display: none', self.css_rule('.jc-fab'))
        self.assertIn('display: inline-flex', self.css_rule('.jc-dirty .jc-fab'))

    def test_it_never_covers_the_phone_nav_bar_or_a_date_panel(self):
        """
        On ≤640px the nav bar renders at the BOTTOM of the screen, so a plain
        `bottom: 24px` would put this button on top of it. Measured on a 375×812
        phone: the button sits 15px clear of the bar.

        Stacking: 1020, under the nav (1030) and under the date panel (1035).
        It must never cover navigation, and never cover a popover somebody
        opened deliberately.
        """
        self.assertIn('z-index: 1020', self.css_rule('.jc-fab'))
        # Offset from the bar's own variable, so it follows if the bar changes.
        self.assertIn('bottom: calc(var(--nav-h) + env(safe-area-inset-bottom, 0px)',
                      self.source())

    def test_it_clears_the_touch_minimum(self):
        rule = self.css_rule('.jc-fab')
        for axis in ('width: 58px', 'height: 58px'):
            self.assertIn(axis, rule)

    def test_pressing_either_door_stops_the_other(self):
        """
        It submits the same form as the big button, so leaving either live after
        a submit would let a second tap post the card twice — which on the
        create page is two job cards for one car.
        """
        guard = self.source().split('ONE PRESS, ONE JOB CARD', 1)[1]
        self.assertIn('if (btn) btn.disabled = true;', guard)
        self.assertIn('if (fab) fab.disabled = true;', guard)

    def test_it_says_which_act_it_is_like_the_main_button(self):
        new = self.fab(self.client.get(reverse('jobcard_create')).content.decode())
        edit = self.fab()
        self.assertIn('jc-fab--new', new)          # green, matching Save
        self.assertIn('Save job card', new)
        self.assertNotIn('jc-fab--new', edit)      # primary blue, matching Update
        self.assertIn('Update job card', edit)

    def test_it_is_labelled_for_anyone_who_cannot_see_the_tick(self):
        self.assertIn('aria-label=', self.fab())


class TheFormIsWellFormedTests(JobCardFormBase):
    """
    AUD-0093, closed 2026-08-13. Two `</div>`s used to sit above the submit
    block and closed `<form>` early — the parser pops it when an ancestor div
    closes — so the Save button was a SIBLING of the form rather than inside it.
    It still submitted (the parser's form-element pointer associates it), which
    is precisely what made it a trap: `form.querySelectorAll(...)` silently
    skipped everything past that point.
    """

    def test_the_submit_button_is_inside_the_form_it_submits(self):
        html = self.rendered()
        form = html.split('<form method="post" id="jobcardForm"', 1)[1].split('</form>', 1)[0]
        self.assertIn('jc-submit', form,
                      'the submit button is outside its own <form> again — see AUD-0093')

    def test_the_form_closes_before_its_wrappers_do(self):
        """
        The structural version of the same fact, asserted on the template so it
        fails at the cause rather than at a symptom.
        """
        source = self.source()
        after_form_close = source.split('</form>', 1)[1]
        # The two wrappers this form sits in must be closed AFTER it.
        self.assertLess(after_form_close.index('</div>'),
                        after_form_close.index('{% endblock %}'))


class AnEmptyBoxWearsAHairlineTests(JobCardFormBase):
    """
    The rule is applied in script (a box is empty or it is not, and only the
    browser knows once someone starts typing), so what the server can pin is the
    CONTRACT: which fields are exempt, and that the marker is a border colour
    rather than anything that could move the page.
    """

    #: Exempt on the owner's call, and declared as `jc-optional` on the widget
    #: in forms.py rather than as a list of names in the template's script —
    #: one mechanism, sitting where somebody adding a field will see it.
    #:
    #: Customer Name / Contact / note: blank on most cards by the nature of the
    #: business, so a mark on them would be permanent, and a mark that is always
    #: on is a mark nobody reads. A SHOP spare's Qty: nothing refuses a save
    #: without it and the live data is full of rows that never had one.
    #:
    #: The two DATES are deliberately NOT here — see
    #: `ADatePairIsOnlyDoneWhenBothAreInTests`. They were briefly exempt and the
    #: owner reversed it: a spare is finished when it has been ordered AND
    #: received, so the pair stays marked until both are filled.
    EXEMPT = ('customer_name', 'customer_contact', 'notes')
    EXEMPT_SPARE = ('quantity',)

    def test_the_exempt_fields_are_marked_at_the_field_not_in_a_list(self):
        from workshop.forms import JobCardForm, JobCardSpareFormSet

        for name in self.EXEMPT:
            widget = JobCardForm().fields[name].widget
            self.assertIn('jc-optional', widget.attrs.get('class', ''),
                          '%s lost its jc-optional exemption' % name)

        row = JobCardSpareFormSet().empty_form
        for name in self.EXEMPT_SPARE:
            self.assertIn('jc-optional', row.fields[name].widget.attrs.get('class', ''),
                          'spare %s lost its jc-optional exemption' % name)

        # …and the script honours the class rather than keeping its own list,
        # which is what stops the two drifting apart.
        self.assertIn("el.classList.contains('jc-optional')", self.source())
        self.assertNotIn('NEVER_FLAG', self.source())

    def test_an_inventory_quantity_is_still_marked_when_a_spare_one_is_not(self):
        """
        The same word carries two different obligations, and the mark follows
        the obligation rather than the label. A warehouse draw is REFUSED
        without a quantity — it is the number that leaves the shelf — so its box
        keeps the hairline. A shop spare's quantity is genuinely optional.
        """
        from workshop.forms import InventoryDrawForm, JobCardSpareFormSet

        draw_qty = InventoryDrawForm().fields['quantity'].widget.attrs.get('class', '')
        shop_qty = JobCardSpareFormSet().empty_form.fields['quantity'].widget.attrs.get('class', '')
        self.assertNotIn('jc-optional', draw_qty)
        self.assertIn('jc-optional', shop_qty)

    def test_the_marker_cannot_move_anything_on_the_page(self):
        """
        Border COLOUR only, and an inset shadow for the changed marker. Both
        paint inside the box the browser has already laid out, so a marked
        control is exactly the same size as an unmarked one. A border WIDTH or a
        margin here would shift every row as you typed — the form is a table on
        a tablet, and that is the trap `.inventory-stock-hint` already exists to
        avoid.
        """
        source = self.source()
        rule = source.split('.jc-empty,', 1)[1].split('}', 1)[0]
        self.assertIn('border-color', rule)
        for forbidden in ('border-width', 'padding', 'margin', 'font-size'):
            self.assertNotIn(forbidden, rule)

    def test_a_settled_card_wears_no_hairlines(self):
        """
        The Financial Lock disables every box on a PAID card, and an empty box
        on a closed record is nothing anybody is going to fill in. Handled in
        CSS rather than in the sweep, because the lock is applied on a timer and
        script reading that state would be racing it.
        """
        self.assertIn('.jc-empty:disabled', self.source())


class NothingIsLostByLeavingTheFormTests(JobCardFormBase):
    """
    The Job Card is the longest form in the app and its own header offers two
    links away from it. What the server can guarantee is that the markers exist
    and start hidden; the dirtying itself is script.
    """

    def test_the_unsaved_pill_ships_hidden(self):
        html = self.rendered()
        self.assertIn('jc-dirty-pill', html)
        rule = self.source().split('.jc-dirty-pill {', 1)[1].split('}', 1)[0]
        self.assertIn('display: none', rule)

    def test_every_signal_hangs_off_one_switch(self):
        """
        Three marks, one class on the body: the amber header, the pill in it,
        and the note on the Save button. They say the same thing in the places a
        person's eye actually is — the header is sticky, the button is where you
        end up — and hanging them off one switch is what stops two of them
        disagreeing about whether there is anything to save.
        """
        source = self.source()
        self.assertIn('.jc-dirty #jcHead', source)
        self.assertIn('.jc-dirty .jc-dirty-pill', source)
        self.assertIn('.jc-dirty .jc-submit:not(:disabled)', source)
        self.assertIn("document.body.classList.add('jc-dirty')", source)
        # The "You have unsaved changes" line under the button was a THIRD copy
        # of the same fact and the only one that changed the button's height
        # when it appeared. Removed on the owner's instruction.
        self.assertNotIn('jc-save-note', source)
        self.assertNotIn('You have unsaved changes', self.rendered())

    def test_the_warning_never_costs_the_car_its_name_on_a_phone(self):
        """
        The signal that carries at every width is the HEADER going amber, not
        the pill. Measured on a 375px phone: the title had 150px for 240px of
        "Editing: Audi A4 KL 10 AA 1919" and was already truncating, and adding
        a 79px pill to that flex row cut it to 63px — "Editing:" and nothing.
        That is the Spare Shop header rule again (a header gives up its actions
        before it gives up its name), so the wording is held back until 576px
        and a background colour, which occupies no width, does the work below it.

        `!important` is required and is not laziness: the header sets its
        background inline, because it also carries the car-colour rail.
        """
        source = self.source()
        self.assertIn('.jc-dirty #jcHead', source)
        tint = source.split('.jc-dirty #jcHead {', 1)[1].split('}', 1)[0]
        self.assertIn('!important', tint)
        # Paint only — a header that changed size when you typed would push the
        # whole form down at the moment somebody is aiming at a box.
        for forbidden in ('height', 'padding', 'margin', 'font-size', 'display'):
            self.assertNotIn(forbidden, tint)

        wording = source.split('@media (min-width: 576px) {', 1)[1].split('}', 1)[0]
        self.assertIn('.jc-dirty .jc-dirty-pill', wording)

    def test_a_refused_submit_does_not_clear_the_warning(self):
        """
        `dirty` is cleared only when the submit was not prevented. The Financial
        Lock cancels a locked record and the Inventory guard cancels a row with
        no quantity; clearing the warning on a submit that never left would drop
        it on the one card that still needs it.
        """
        self.assertIn('if (!e.defaultPrevented) dirty = false;', self.source())


class ABlankRowIsRecomputedOnEverySubmitTests(JobCardFormBase):
    """
    The four "is this row blank" passes assign `checked = <blank>` rather than
    only ever setting it true. A submit can be cancelled after they have run —
    the Financial Lock does exactly that, and so does the Inventory quantity
    guard — and a row left marked for deletion on a refused attempt used to stay
    marked, so typing into it and saving dropped what had just been typed.
    """

    def test_the_delete_flag_is_assigned_not_only_set(self):
        source = self.source()
        for keyed_on in ('textInput', 'partInput', 'itemId', 'jobInput'):
            self.assertIn('deleteCheckbox.checked = !%s.value.trim();' % keyed_on,
                          source)
        self.assertNotIn('deleteCheckbox.checked = true;', source)

    def test_the_quantity_guard_stops_the_submit_reaching_them(self):
        """
        The guard runs on `document` in the CAPTURE phase and stops
        propagation, so a refused submit never reaches the handlers that tick
        DELETE boxes. Belt and braces with the assignment above, and the reason
        the guard is not the browser's own `required` attribute — that would
        block the submit EVENT itself, and the blank-row handling lives in it.
        """
        source = self.source()
        guard = source.split("A DRAWN PART MUST SAY HOW MANY", 1)[1]
        self.assertIn('e.stopPropagation();', guard)
        self.assertIn('}, true);', guard)


class TheCarWearsItsColourTests(JobCardFormBase):
    """
    The same wash Car Profiles and the Live Report use, at the same alpha, on
    the owner's request. Two rules travel with it and both are load-bearing.
    """

    def test_the_page_wash_was_removed_and_the_rail_kept(self):
        """
        A full-page tint in the car's colour was built here and removed on the
        owner's instruction: on a form this long it sat behind every section for
        several screens, which is a lot of colour to carry for a fact the header
        rail and the colour dot beside the registration already state.

        The RAIL stays — one strip at the top, not the whole screen — so the
        card still says which car you are on at a glance.
        """
        source = self.source()
        self.assertNotIn('jc-tint', source)
        self.assertNotIn('--jc-shade', source)
        self.assertIn('.jc-head::before', source)     # the rail

    def test_a_card_with_no_colour_still_hatches_its_rail(self):
        """
        "Nobody wrote it down" is a different fact from "this car is grey", so
        the rail hatches rather than going slate. That rule outlived the wash.
        """
        self.job.car_color = ''
        self.job.save()
        html = self.rendered()
        self.assertIn('jc-head--unset', self.open_tag(html, 'jcHead'))
        self.assertNotIn('--jc-accent', self.open_tag(html, 'jcColour'))

    def test_a_recorded_colour_paints_the_rail(self):
        html = self.rendered()          # self.job is Red
        self.assertIn('--jc-accent: #dc2626', self.open_tag(html, 'jcColour'))
        self.assertNotIn('jc-head--unset', self.open_tag(html, 'jcHead'))

    def test_a_white_car_gets_an_outlined_rail(self):
        """Or it disappears into the header it is drawn on."""
        self.job.car_color = 'White'
        self.job.save()
        self.assertIn('jc-head--white', self.open_tag(self.rendered(), 'jcHead'))

    def test_the_picker_announces_its_choice_rather_than_reaching_in(self):
        """
        Setting `.value` in script fires no event, so the shared picker
        dispatches one. That keeps the include free of a branch per caller —
        the Estimate uses the identical control and wants none of this.
        """
        with open('workshop/templates/workshop/includes/_car_color_picker.html',
                  encoding='utf-8') as fh:
            picker = fh.read()
        self.assertIn("CustomEvent('carcolour:change'", picker)
        self.assertIn("carcolour:change", self.source())


class ALockedRecordLooksLockedTests(JobCardFormBase):
    """
    The Financial Lock disables every field on a settled card, and until
    2026-08-16 a disabled field was painted in the SAME `#f1f5f9` the soft
    surface uses for a live one — so the banner said LOCKED while the form under
    it looked ready to type into. The one screen where an edit is dangerous was
    the one screen giving no sign of it.

    Nothing here runs the script; these assert the contract the script relies
    on — that the stylesheet gives `:disabled` its own palette, and that the
    locked state is expressed as an attribute CSS can read.
    """

    def rule(self, selector):
        return self.source().split(selector, 1)[1].split('}', 1)[0]

    def test_a_disabled_box_is_not_painted_like_a_live_one(self):
        source = self.source()
        # The live soft surface, and the locked palette, must not be the same
        # colour — that equality IS the defect this closed.
        self.assertIn('background-color: #f1f5f9', source)
        dead = self.rule('.form-control:disabled,')
        self.assertNotIn('#f1f5f9', dead)
        self.assertIn('cursor: not-allowed', dead)

    def test_readonly_is_treated_the_same_as_disabled(self):
        """
        The settlement screen uses `readonly` deliberately — a disabled input is
        not submitted — and it means the same thing to whoever is looking at it.
        """
        source = self.source()
        block = source.split('.form-control:disabled,', 1)[1].split('{', 1)[0]
        self.assertIn('.form-control[readonly]', block)

    def test_the_lock_is_readable_from_css_not_only_from_script(self):
        """
        `toggleRecordLock()` maintains `data-locked` on the form. The lock is
        applied on a `setTimeout(…, 100)`, so anything that reads the state in
        script would be racing it — the same reason `.jc-empty:disabled` drops
        the hairline in CSS rather than in JS.
        """
        source = self.source()
        self.assertIn("form.setAttribute('data-locked', 'true')", source)
        self.assertIn('#jobcardForm[data-locked="true"]', source)

    def test_the_two_actionable_marks_are_dropped_while_locked(self):
        """
        An empty box on a closed record is nothing anybody is going to fill, and
        there is nothing unsaved to warn about.
        """
        source = self.source()
        self.assertIn('#jobcardForm[data-locked="true"] .jc-empty', source)
        self.assertIn('#jobcardForm[data-locked="true"] .jc-changed', source)

    def test_the_state_is_restated_on_every_section(self):
        """
        The banner is at the top of a form several screens long, so the state
        has to be legible wherever you happen to be scrolled to. Text, not an
        icon-font codepoint — the glyph would depend on a stylesheet fetched
        from a CDN, and this is not the screen to take that bet on.
        """
        rule = self.rule('#jobcardForm[data-locked="true"] .jc-sec-name::after {')
        self.assertIn('content: "LOCKED"', rule)
        self.assertNotIn('font-family: "bootstrap-icons"', rule)


class WhoTheCustomerIsIsOfficeOnlyTests(JobCardFormBase):
    """
    Added 2026-08-16 on the owner's instruction.

    The customer's NAME and NUMBER are Office and Owner only, and it is the same
    reasoning that folded the section away in the first place: Owner 1 keeps
    those relationships himself, and the workshop identifies a car by its
    registration. A mechanic never needs to know whose car it is — and this form
    was the only screen that would have told them, since the invoice, Car
    Profiles and the Fleet pages are all `@office_required` already.

    The INTERNAL NOTE stays open to everybody. It is about the car, not the
    customer ("noise only when cold", "do not wash"), and the mechanic is
    usually the one who finds out.
    """

    def test_office_sees_both_boxes(self):
        html = self.rendered()
        self.assertIn('name="customer_name"', html)
        self.assertIn('name="customer_contact"', html)

    def test_floor_sees_neither_box_nor_the_stored_value(self):
        """
        Not merely hidden. A `d-none` cell would still put the customer's phone
        number in HTML a mechanic can read — the same reason the Live Report's
        board is not built for Floor rather than hidden from it.
        """
        html = self.rendered_as_floor()

        self.assertNotIn('name="customer_name"', html)
        self.assertNotIn('name="customer_contact"', html)
        self.assertNotIn('John', html)
        self.assertNotIn('1234567890', html)

    def test_floor_still_gets_the_note(self):
        self.assertIn('name="notes"', self.rendered_as_floor())

    def test_a_crafted_post_from_floor_cannot_rename_the_customer(self):
        """
        Hiding the box is presentation; this is the control. Exactly the shape
        of AUD-0081 — the price fields were hidden in the template for a year
        while a raw POST rewrote them — so the pinning lives in
        `_floor_locked_data` beside them.
        """
        client = self.floor_client()
        client.post(reverse('jobcard_edit', args=[self.job.pk]),
                    self.payload(customer_name='Somebody Else',
                                 customer_contact='9999999999'))

        self.job.refresh_from_db()
        self.assertEqual(self.job.customer_name, 'John')
        self.assertEqual(self.job.customer_contact, '1234567890')

    def test_a_crafted_post_from_floor_cannot_ERASE_the_customer_either(self):
        """
        The other direction, and the one a blunt "drop the key" fix would miss:
        an absent field on a ModelForm leaves the stored value alone, but a
        field posted EMPTY overwrites it with blank.
        """
        client = self.floor_client()
        client.post(reverse('jobcard_edit', args=[self.job.pk]),
                    self.payload(customer_name='', customer_contact=''))

        self.job.refresh_from_db()
        self.assertEqual(self.job.customer_name, 'John')
        self.assertEqual(self.job.customer_contact, '1234567890')

    def test_floor_creating_a_card_simply_records_no_customer(self):
        """
        A new card has nothing stored to preserve, so the pin is blank rather
        than a refusal — Floor opens the job, Office adds the customer if there
        is one.
        """
        client = self.floor_client()
        payload = self.payload(registration_number='KL09ZZ0001',
                               customer_name='Invented', customer_contact='9000000000')
        client.post(reverse('jobcard_create'), payload)

        card = JobCard.objects.get(registration_number='KL09ZZ0001')
        self.assertFalse(card.customer_name)
        self.assertFalse(card.customer_contact)

    def test_office_is_unaffected(self):
        self.edit(customer_name='Rashid', customer_contact='9876500000')
        self.job.refresh_from_db()
        self.assertEqual(self.job.customer_name, 'Rashid')
        self.assertEqual(self.job.customer_contact, '9876500000')

    def test_the_read_only_view_of_a_card_is_gated_the_same_way(self):
        """
        The rule has to hold wherever the customer is SHOWN, not only where they
        are typed. `jobcard_detail` is `@staff_required` — Floor legitimately
        reads a card there — and it printed the name and number with no gate at
        all, so hiding them on the form alone would have moved the door rather
        than closing it. Every other screen that names a customer (the invoice,
        Car Profiles, Job Cards, Completed, Paid Bills, the Fleet pages) is
        `@office_required` already.
        """
        detail = reverse('jobcard_detail', args=[self.job.pk])

        office = self.client.get(detail).content.decode()
        self.assertIn('John', office)
        self.assertIn('1234567890', office)

        floor = self.floor_client().get(detail).content.decode()
        self.assertEqual(floor.count('Customer Name'), 0)
        self.assertNotIn('John', floor)
        self.assertNotIn('1234567890', floor)
