from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Group
from workshop.models import CashbookEntry, DeletionLog, Notification
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta
# Cashbook views live in workshop/cashbook_views.py — NOT management_views


class CashbookTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Create groups
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')
        
        # Create users
        self.owner = User.objects.create_user(username='owner', password='password')
        self.owner.groups.add(self.owner_group)
        
        self.office = User.objects.create_user(username='office', password='password')
        self.office.groups.add(self.office_group)
        
        self.floor = User.objects.create_user(username='floor', password='password')
        self.floor.groups.add(self.floor_group)
        
        # Create some initial entries
        self.expense1 = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Electricity', amount=Decimal('500.00'), payment_method='CASH', created_by=self.owner, date=timezone.localdate()
        )
        self.income1 = CashbookEntry.objects.create(
            entry_type='INCOME', category='Scrap Sell', amount=Decimal('1500.00'), payment_method='UPI', created_by=self.office, date=timezone.localdate()
        )
        
    def test_access_control(self):
        """Test that Floor users cannot access cashbook, but Office/Owner can"""
        # Unauthenticated
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(response.status_code, 302)
        # '/admin-login/' until 2026-08-12, when the two login faces merged.
        self.assertTrue(response.url.startswith('/login/'))
        
        # Floor user — signed in but wrong role, so 403 rather than a redirect.
        # This used to bounce to the login form, which looks identical to being
        # logged out and reads as the app being broken. Changed 2026-07-28.
        self.client.login(username='floor', password='password')
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(response.status_code, 403)
        
        # Office user
        self.client.login(username='office', password='password')
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/cashbook/cashbook.html')
        
    def test_cashbook_view_filtering(self):
        """Test default and specific filtering"""
        self.client.login(username='owner', password='password')

        # Default should be 'today'. The page is one stream now, not an
        # expenses list beside an income list — `entries` is that stream.
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(response.context['filter_type'], 'today')
        self.assertEqual(len(response.context['entries']), 2)
        self.assertEqual(response.context['type_counts'],
                         {'all': 2, 'expense': 1, 'income': 1})

        # Test totals
        totals = response.context['cashbook_totals']
        self.assertEqual(totals['expense'], Decimal('500.00'))
        self.assertEqual(totals['income'], Decimal('1500.00'))
        self.assertEqual(totals['net'], Decimal('1000.00'))

        # Add an entry dated yesterday
        yesterday = timezone.localdate() - timedelta(days=1)
        old_expense = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Old Expense', amount=Decimal('200.00')
        )
        # Update date bypassing auto_now_add
        CashbookEntry.objects.filter(id=old_expense.id).update(date=yesterday)

        # Fetch today filter again
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(len(response.context['entries']), 2)  # not yesterday's

        # Fetch this_week filter
        response = self.client.get(reverse('cashbook') + '?filter=this_week')
        self.assertEqual(response.context['filter_type'], 'this_week')

        # Fetch this_month filter
        response = self.client.get(reverse('cashbook') + '?filter=this_month')
        self.assertEqual(response.context['filter_type'], 'this_month')

        # Fetch this_year filter
        response = self.client.get(reverse('cashbook') + '?filter=this_year')
        self.assertEqual(response.context['filter_type'], 'this_year')

        # Fetch with AJAX
        response = self.client.get(reverse('cashbook') + '?filter=this_week', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTemplateUsed(response, 'workshop/cashbook/cashbook_partial.html')

        # Fetch AJAX with no filter (defaults to today)
        response = self.client.get(reverse('cashbook'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.context['filter_type'], 'today')

    def test_add_cashbook_entry_valid(self):
        """Test adding a valid entry"""
        self.client.login(username='office', password='password')
        response = self.client.post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE',
            'category': 'Rent',
            'amount': '1000.00',
            'payment_method': 'CASH',
            'description': 'Monthly Rent'
        })
        self.assertRedirects(response, reverse('cashbook'))
        
        # Verify db
        self.assertEqual(CashbookEntry.objects.filter(category='Rent').count(), 1)
        
    def test_add_cashbook_entry_invalid_type(self):
        """Test trying to bypass HTML and send invalid entry_type"""
        self.client.login(username='owner', password='password')
        response = self.client.post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'HACKED',
            'category': 'Test',
            'amount': '100.00',
        })
        self.assertRedirects(response, reverse('cashbook'))
        
        # Verify it was NOT saved
        self.assertEqual(CashbookEntry.objects.filter(category='Test').count(), 0)
        
    def test_add_cashbook_entry_invalid_amount(self):
        """Test trying to bypass HTML and send negative or empty amount"""
        self.client.login(username='owner', password='password')
        
        # Negative
        response = self.client.post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE',
            'category': 'Negative Test',
            'amount': '-500',
        })
        self.assertEqual(CashbookEntry.objects.filter(category='Negative Test').count(), 0)
        
        # Empty string
        response = self.client.post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE',
            'category': 'Empty Test',
            'amount': '',
        })
        self.assertEqual(CashbookEntry.objects.filter(category='Empty Test').count(), 0)
        
        # Invalid string
        response = self.client.post(reverse('manage_add_cashbook_entry'), {
            'entry_type': 'EXPENSE',
            'category': 'String Test',
            'amount': 'abc',
        })
        self.assertEqual(CashbookEntry.objects.filter(category='String Test').count(), 0)
        
    def test_edit_cashbook_entry(self):
        """Test editing an existing entry safely"""
        self.client.login(username='office', password='password')
        response = self.client.post(reverse('manage_edit_cashbook_entry', args=[self.expense1.id]), {
            'category': 'Updated Electricity',
            'amount': '600.00',
            'payment_method': 'UPI'
        })
        self.assertRedirects(response, reverse('cashbook'))
        
        self.expense1.refresh_from_db()
        self.assertEqual(self.expense1.category, 'Updated Electricity')
        self.assertEqual(self.expense1.amount, Decimal('600.00'))
        self.assertEqual(self.expense1.payment_method, 'UPI')
        
        # Edit with invalid string amount
        response = self.client.post(reverse('manage_edit_cashbook_entry', args=[self.expense1.id]), {
            'category': 'Updated Electricity',
            'amount': 'abc',
        })
        self.assertRedirects(response, reverse('cashbook'))
        
        # Edit with missing amount
        response = self.client.post(reverse('manage_edit_cashbook_entry', args=[self.expense1.id]), {
            'category': 'Updated Electricity',
            'amount': '',
        })
        self.assertRedirects(response, reverse('cashbook'))
        
        # Edit with negative amount
        response = self.client.post(reverse('manage_edit_cashbook_entry', args=[self.expense1.id]), {
            'category': 'Updated Electricity',
            'amount': '-100',
        })
        self.assertRedirects(response, reverse('cashbook'))
        
    def test_delete_cashbook_entry(self):
        """Test deleting an entry"""
        self.client.login(username='owner', password='password')
        response = self.client.post(reverse('manage_delete_cashbook_entry', args=[self.income1.id]))
        self.assertRedirects(response, reverse('cashbook'))

        # Verify db
        self.assertEqual(CashbookEntry.objects.filter(id=self.income1.id).count(), 0)


class TheLedgerIsOneSearchableStreamTests(TestCase):
    """
    The page was rebuilt on 2026-08-03 from two mirrored lists into one
    stream with a search box, type chips and pages. Three properties hold it
    together, and each is a thing a reader could otherwise be misled by.
    """

    def setUp(self):
        self.client = Client()
        Group.objects.get_or_create(name='Office')
        self.office = User.objects.create_user(username='office', password='password')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.client.login(username='office', password='password')
        self.today = timezone.localdate()

        def entry(kind, name, amount, note='', method='CASH'):
            return CashbookEntry.objects.create(
                entry_type=kind, category=name, amount=Decimal(amount),
                payment_method=method, description=note, date=self.today,
            )

        self.electricity = entry('EXPENSE', 'Electricity', '500.00', note='KSEB bill 7781')
        entry('EXPENSE', 'Rent', '20000.00', method='TRANSFER')
        entry('INCOME', 'Scrap Sell', '1500.00', method='UPI')

    def _get(self, **params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(f"{reverse('cashbook')}?filter=today&{query}")

    def test_the_totals_describe_the_period_not_the_chip(self):
        """
        The three headline figures sit above a type chip that narrows the list
        below them. If the chip moved them too, tapping 'In' would make the
        expenses appear to vanish from a period they are still part of — the
        page would look like it had lost money.
        """
        for chosen in ('all', 'expense', 'income'):
            with self.subTest(type=chosen):
                totals = self._get(type=chosen).context['cashbook_totals']
                self.assertEqual(totals['expense'], Decimal('20500.00'))
                self.assertEqual(totals['income'], Decimal('1500.00'))
                self.assertEqual(totals['net'], Decimal('-19000.00'))

    def test_the_chip_narrows_the_list(self):
        self.assertEqual(len(self._get(type='all').context['entries']), 3)

        expenses = self._get(type='expense').context['entries']
        self.assertEqual({e.entry_type for e in expenses}, {'EXPENSE'})
        self.assertEqual(len(expenses), 2)

        income = self._get(type='income').context['entries']
        self.assertEqual([e.category for e in income], ['Scrap Sell'])

    def test_a_crafted_type_falls_back_to_all_rather_than_emptying_the_page(self):
        response = self._get(type='HACKED')
        self.assertEqual(response.context['entry_type_filter'], 'all')
        self.assertEqual(len(response.context['entries']), 3)

    def test_search_covers_everything_the_row_shows(self):
        """
        Name, note, method and the amount itself — nobody should have to
        remember which box a word was typed into to find the row again.
        """
        cases = {
            'lectrici': ['Electricity'],       # name, mid-word
            'KSEB':     ['Electricity'],       # the note
            'transfer': ['Rent'],              # the method, by its stored code
            'bank':     ['Rent'],              # the method, by the label shown
            'upi':      ['Scrap Sell'],
            '1500':     ['Scrap Sell'],        # the amount, exactly
        }
        for term, expected in cases.items():
            with self.subTest(q=term):
                entries = self._get(q=term).context['entries']
                self.assertEqual(sorted(e.category for e in entries), expected)

    def test_a_one_letter_term_does_not_drag_in_a_whole_payment_method(self):
        """
        'ca' is inside both CASH and CARD. Matching the method on two letters
        would answer a search for a name with every cash row in the period.
        """
        entries = self._get(q='ca').context['entries']
        self.assertEqual([e.category for e in entries], [])

    def test_the_totals_follow_the_search(self):
        """
        Searching narrows what is on screen, so it must narrow the figures
        above it too — otherwise the headline belongs to rows the reader
        cannot see.
        """
        context = self._get(q='Electricity').context
        self.assertEqual(context['cashbook_totals']['expense'], Decimal('500.00'))
        self.assertEqual(context['cashbook_totals']['income'], Decimal('0'))
        self.assertEqual(context['type_counts'], {'all': 1, 'expense': 1, 'income': 0})

    def test_a_nonsense_amount_search_does_not_reach_the_database(self):
        """
        parse_money is what stops 'Infinity' or a 20-digit figure being handed
        to a numeric comparison — Postgres 500s on the latter. Unparseable
        terms simply search the text columns.
        """
        for term in ('Infinity', 'NaN', '999999999999999'):
            with self.subTest(q=term):
                response = self._get(q=term)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context['entries']), 0)

    def test_every_row_is_reachable_by_paging(self):
        """
        The list used to be capped at 300 rows while the total above it counted
        the whole period, so a busy month printed a figure that could not be
        added up from what was on screen and the rest was unreachable. Pages
        replaced the cap: the count and the rows must agree.
        """
        CashbookEntry.objects.bulk_create([
            CashbookEntry(entry_type='EXPENSE', category=f'Sundry {i}',
                          amount=Decimal('10.00'), date=self.today)
            for i in range(60)
        ])
        first = self._get(page=1).context
        self.assertEqual(first['page_obj'].paginator.count, 63)
        self.assertEqual(len(first['entries']), 45)

        seen = []
        for page in range(1, first['page_obj'].paginator.num_pages + 1):
            seen += [e.pk for e in self._get(page=page).context['entries']]
        self.assertEqual(len(seen), 63)
        self.assertEqual(len(set(seen)), 63)

    def test_an_unknown_filter_falls_back_to_today_rather_than_all_time(self):
        """
        An unrecognised value used to miss every branch, so the queryset was
        never narrowed while the heading still said "Today" — the whole
        ledger's total presented as one day's takings.
        """
        CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Ancient', amount=Decimal('99.00'),
            date=self.today - timedelta(days=400))
        response = self.client.get(f"{reverse('cashbook')}?filter=whenever")
        self.assertEqual(response.context['filter_type'], 'today')
        self.assertNotIn('Ancient', [e.category for e in response.context['entries']])
        self.assertEqual(response.context['cashbook_totals']['expense'], Decimal('20500.00'))

    def test_a_junk_page_number_shows_a_page_rather_than_500ing(self):
        for page in ('0', '-4', 'abc', '99999'):
            with self.subTest(page=page):
                self.assertEqual(self._get(page=page).status_code, 200)

    def test_the_note_can_be_corrected(self):
        """
        A note could be written when the entry was created and never touched
        again — a typo on the one field that explains the row was permanent.
        """
        self.client.post(reverse('manage_edit_cashbook_entry', args=[self.electricity.id]), {
            'category': 'Electricity', 'amount': '500.00',
            'payment_method': 'CASH', 'date': self.today.isoformat(),
            'entry_type': 'EXPENSE', 'description': 'KSEB bill 7782',
        })
        self.electricity.refresh_from_db()
        self.assertEqual(self.electricity.description, 'KSEB bill 7782')

    def test_a_payload_without_a_note_leaves_the_existing_one_alone(self):
        """
        Same shape as the entry_type rule beside it: absent means unchanged,
        never silently cleared.
        """
        self.client.post(reverse('manage_edit_cashbook_entry', args=[self.electricity.id]), {
            'category': 'Electricity', 'amount': '600.00',
            'payment_method': 'CASH', 'date': self.today.isoformat(),
        })
        self.electricity.refresh_from_db()
        self.assertEqual(self.electricity.amount, Decimal('600.00'))
        self.assertEqual(self.electricity.description, 'KSEB bill 7781')


class BothSidesAreCollectedEvenThoughOnlyTwoAreShownTests(TestCase):
    """
    The Cashbook headline is two figures — Money Out and Money In — and carries
    NO net card, on the owner's instruction (2026-08-16). The workshop does not
    work out a cashbook net; it records what went out and what came in, and the
    netting off belongs to the owner's Analysis section.

    So what this page owes the business is that BOTH sides are captured
    accurately and both reach that page. These assert exactly that, because it
    is the half a UI change could silently break.
    """

    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.owner = User.objects.create_user(username='cb_owner', password='pw')
        self.owner.groups.add(self.owner_group)
        self.client.force_login(self.owner)
        self.today = timezone.localdate()

        CashbookEntry.objects.create(entry_type='EXPENSE', category='Electricity',
                                     amount=Decimal('2000.00'), payment_method='CASH',
                                     created_by=self.owner, date=self.today)
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Rent',
                                     amount=Decimal('8000.00'), payment_method='CASH',
                                     created_by=self.owner, date=self.today)
        CashbookEntry.objects.create(entry_type='INCOME', category='Scrap',
                                     amount=Decimal('3000.00'), payment_method='CASH',
                                     created_by=self.owner, date=self.today)

    def page(self, query=''):
        return self.client.get(reverse('cashbook') + query)

    def test_both_totals_are_right(self):
        totals = self.page().context['cashbook_totals']
        self.assertEqual(totals['expense'], Decimal('10000.00'))
        self.assertEqual(totals['income'], Decimal('3000.00'))

    def test_net_is_still_computed_even_though_it_is_not_drawn(self):
        """
        Kept in the context deliberately: dropping the card is a decision about
        what this screen shows, not about what the ledger knows.
        """
        self.assertEqual(self.page().context['cashbook_totals']['net'],
                         Decimal('-7000.00'))

    def test_the_page_draws_no_net_card(self):
        page = self.page().content.decode()
        self.assertIn('Money Out', page)
        self.assertIn('Money In', page)
        self.assertNotIn('Net movement', page)
        self.assertNotIn('cb-stat--net', page)

    def test_the_analysis_section_still_sees_both_sides(self):
        """
        The Profit page does not read this screen — `cashbook_income()` and
        `cashbook_expense()` aggregate the entries themselves — which is why
        removing a card here could not move a rupee there. Pinned so that stays
        true the day somebody wires the two together.
        """
        from workshop import analysis_engine as engine
        self.assertEqual(engine.cashbook_income(self.today, self.today),
                         Decimal('3000.00'))
        self.assertEqual(engine.cashbook_expense(self.today, self.today)['total'],
                         Decimal('10000.00'))

    def test_both_subtitles_name_the_same_window(self):
        """
        The label was written out twice in the template, once per figure — two
        copies of one fact, free to drift into naming different periods on the
        very headline whose job is to say which period the figures belong to.
        It comes from the view now.
        """
        response = self.page('?filter=this_month')
        self.assertEqual(response.context['filter_label'], 'This Month')
        self.assertEqual(response.content.decode().count('Total This Month'), 2)

    def test_a_custom_window_names_its_dates(self):
        response = self.page('?filter=custom&start_date=2026-01-01&end_date=2026-01-31')
        self.assertEqual(response.context['filter_label'], '2026-01-01 – 2026-01-31')

    def test_an_unusable_custom_window_still_renders(self):
        self.assertEqual(self.page('?filter=custom&start_date=abc&end_date=zz').status_code, 200)


class AnEditSaysSoTheWayADeleteDoesTests(TestCase):
    """
    AUD-0083. Deleting a cashbook entry has written a `DeletionLog` row and
    raised `RECORD_DELETED` since day one. EDITING one said nothing at all -
    and an edit here can do the same damage: a Rs 50,000 expense retyped as
    Rs 5, or moved into a month the Profit page has already been read against.
    This is the only screen in the app that can move money between two closed
    reporting periods.

    INFO rather than CRITICAL, on the catalogue's own rule: the Cashbook is the
    most frequently keyed money screen in the app, and a phone that buzzes for
    routine bookkeeping is how the thirteen critical events stop being read.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.owner = User.objects.create_user(username='sahad', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.office = User.objects.create_user(username='officestaff', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.client.login(username='officestaff', password='pw')

        self.entry = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Electricity',
            amount=Decimal('50000.00'), payment_method='CASH',
            created_by=self.office, date=timezone.localdate(),
        )

    def _post(self, **over):
        payload = {
            'category': self.entry.category,
            'amount': str(self.entry.amount),
            'payment_method': self.entry.payment_method,
            'entry_type': self.entry.entry_type,
            'date': self.entry.date.isoformat(),
        }
        payload.update(over)
        return self.client.post(
            reverse('manage_edit_cashbook_entry', args=[self.entry.pk]), payload)

    def _alerts(self):
        return Notification.objects.filter(event='CASHBOOK_EDITED')

    # -- the three that move money -------------------------------------------

    def test_retyping_the_amount_reaches_the_owner(self):
        self._post(amount='5')
        row = self._alerts().get()
        self.assertEqual(row.recipient, self.owner, 'owners are the audience')
        self.assertIn('Electricity', row.body)
        self.assertIn('5', row.body)
        self.assertIn('50,000', row.detail,
                      'the alert has to carry what the figure WAS')

    def test_moving_it_into_another_month_reaches_the_owner(self):
        old = self.entry.date
        moved = (old.replace(day=1) - timedelta(days=1))
        self._post(date=moved.isoformat())
        self.assertIn(old.strftime('%b'), self._alerts().get().detail)

    def test_flipping_the_side_of_the_equation_reaches_the_owner(self):
        """Income mis-keyed as an expense is a double-sized error."""
        self._post(entry_type='INCOME')
        self.assertIn('Expense', self._alerts().get().detail)

    # -- and nothing else does -----------------------------------------------

    def test_a_note_or_a_method_raises_nothing(self):
        """
        Confirming what cannot surprise anyone is how confirmations stop being
        read - the settle dialog's rule, applied to the feed. Only the figure,
        the month and the side are worth an owner's attention.
        """
        self._post(payment_method='UPI', description='paid at the counter')
        self.assertEqual(self._alerts().count(), 0)

    def test_saving_an_unchanged_entry_raises_nothing(self):
        self._post()
        self.assertEqual(self._alerts().count(), 0)

    # -- where it lands -------------------------------------------------------

    def test_tapping_it_opens_a_page_the_entry_is_actually_on(self):
        """
        CLAUDE.md's rule, and the one it records breaking twice: follow the
        link and look at the RENDERED page, because comparing a stored url
        against a `reverse()` proves the route exists and says nothing about
        whether the destination shows the thing.

        It bites harder here than it did for `ACCOUNT_LOCKED`, because the
        whole point of this alert is an entry that was moved into ANOTHER
        MONTH - and `/cashbook/` defaults to filter=today, so the bare route
        is guaranteed not to contain it. A notification STORES its url, so a
        wrong one is wrong for every row ever written.

        The entry is looked for by its ROW, never by its category name. The
        add form offers every spelling already in use as a `<datalist>`, so a
        whole-page search for "Switchgear" finds it on a page that lists no
        such entry - which is how the first version of this test passed while
        proving nothing.
        """
        # The 1st of LAST month: a different month from today, and still
        # inside Office's own backdate floor, so the edit is actually
        # accepted. Anything older is refused by `too_far_back` before a
        # notification could exist to test.
        first_of_this = self.entry.date.replace(day=1)
        moved = (first_of_this - timedelta(days=1)).replace(day=1)
        self._post(date=moved.isoformat(), category='Switchgear')

        row = self._alerts().get()
        self.client.login(username='sahad', password='pw')   # owner reads it
        marker = 'data-id="%d"' % self.entry.pk

        # The defect this is pinned against, stated as a fact rather than
        # assumed: the bare route CANNOT show the entry, because the Cashbook
        # opens on filter=today. If this ever stops being true the test below
        # is passing for free and should be rewritten.
        bare = self.client.get(reverse('cashbook'), follow=True)
        self.assertNotIn(self.entry, bare.context['entries'],
                         'the bare cashbook now lists a moved entry - this '
                         'test has stopped proving anything')

        page = self.client.get(row.url, follow=True)
        self.assertEqual(page.status_code, 200, 'the alert opens a dead page')
        self.assertIn(self.entry, page.context['entries'],
                      'the alert lands on a window the entry is not in')
        self.assertContains(
            page, marker,
            msg_prefix='the entry is in the queryset but its row is not drawn')

    def test_the_link_is_built_from_reverse_not_a_hardcoded_path(self):
        """A hardcoded path survives a urls.py edit silently; `reverse()`
        does not. The query string is ours, the route is not."""
        self._post(amount='5')
        self.assertTrue(self._alerts().get().url.startswith(reverse('cashbook')))

    # -- how it is filed ------------------------------------------------------

    def test_it_is_INFO_so_it_never_reaches_a_phone(self):
        from workshop.notifications import EVENTS, INFO
        self.assertEqual(EVENTS['CASHBOOK_EDITED'].severity, INFO)

    def test_it_writes_no_deletion_log_row(self):
        """
        `DeletionLog`'s columns are `deleted_by` and `deleted_at`, its page is
        called Deletion History, and `record()` always raises `RECORD_DELETED`
        with the word "deleted" in the body. An edit filed there would make
        three surfaces state something untrue.
        """
        self._post(amount='5')
        self.assertEqual(DeletionLog.objects.count(), 0)

    def test_an_owners_own_edit_does_not_buzz_that_owner(self):
        """`notify()` excludes the actor - what arrives is always somebody
        ELSE did this, which with two owners is corroboration."""
        second = User.objects.create_user(username='rijas', password='pw')
        second.groups.add(Group.objects.get(name='Owner'))
        self.client.login(username='sahad', password='pw')
        self._post(amount='5')
        self.assertEqual(list(self._alerts().values_list('recipient', flat=True)),
                         [second.pk])
