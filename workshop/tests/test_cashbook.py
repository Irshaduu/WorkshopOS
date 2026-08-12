from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Group
from workshop.models import CashbookEntry
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
