from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Group
from workshop.models import CashbookEntry, DeletionLog, EditLog, Notification
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


class TheCashbookSpeaksOnlyPastOfficesLimitsTests(TestCase):
    """
    ⚠ THE CASHBOOK'S OWN RULE (2026-09-24, the owners' call) — and it
    REVERSES this class's predecessor, which asserted that every Office edit
    here reached the bell (AUD-0083, "an edit says so the way a delete does").

    The Cashbook's daily rhythm made that noise: a worker is handed ₹2,000,
    comes back hours later having spent ₹1,800, and Office edits the row — or
    deletes and re-adds it — every day. Announcing and keeping each one buried
    the changes that matter and put a bell note up daily. So:

      * an edit or delete Office could make (inside 24 hours) is QUIET — not
        kept in Edit or Deletion History, not announced;
      * one ONLY AN OWNER could make (past 24 hours, or a date moved past the
        three-day limit) is kept AND reaches the other owner's phone;
      * BACK-DATING IS NEVER QUIET — moving an entry to an earlier day reaches
        the bell exactly as keying it there would, or the quiet rule would be
        a way round the add form's own alert.
    """

    EVENTS = ('RECORD_CHANGED', 'OLD_RECORD_CHANGED', 'DATED_BACK',
              'DATED_BACK_PAST_LIMIT', 'RECORD_DELETED')

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
        return Notification.objects.filter(event__in=self.EVENTS)

    def _second_owner(self):
        second = User.objects.create_user(username='rijas', password='pw')
        second.groups.add(Group.objects.get(name='Owner'))
        return second

    def _age(self, **delta):
        CashbookEntry.objects.filter(pk=self.entry.pk).update(
            created_at=timezone.now() - timedelta(**delta))

    # -- inside Office's 24 hours: the day's work, said nowhere ----------------

    def test_settling_the_amount_the_same_day_is_quiet(self):
        """₹2,000 handed out, ₹1,800 spent: the second half of one act."""
        self._post(amount='1800')
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal('1800.00'), 'the edit itself still saves')
        self.assertEqual(self._alerts().count(), 0)
        self.assertFalse(EditLog.objects.exists())

    def test_flipping_the_side_the_same_day_is_quiet(self):
        self._post(entry_type='INCOME')
        self.assertEqual(self._alerts().count(), 0)
        self.assertFalse(EditLog.objects.exists())

    def test_an_owners_same_day_edit_is_quiet_too(self):
        """The tier is the RECORD's, never the person's: inside Office's
        limits an owner's edit is the same routine act."""
        self._second_owner()
        self.client.login(username='sahad', password='pw')
        self._post(amount='5')
        self.assertEqual(self._alerts().count(), 0)
        self.assertFalse(EditLog.objects.exists())

    def test_a_same_day_delete_is_not_logged_and_says_nothing(self):
        self.client.post(reverse('manage_delete_cashbook_entry', args=[self.entry.pk]),
                         {'reason': 'settled at 1800, re-adding'})
        self.assertFalse(CashbookEntry.objects.filter(pk=self.entry.pk).exists())
        self.assertFalse(DeletionLog.objects.exists())
        self.assertEqual(self._alerts().count(), 0)

    def test_a_note_or_a_method_raises_nothing(self):
        self._post(payment_method='UPI', description='paid at the counter')
        self.assertEqual(self._alerts().count(), 0)

    def test_saving_an_unchanged_entry_raises_nothing(self):
        self._post()
        self.assertEqual(self._alerts().count(), 0)

    # -- back-dating is never quiet --------------------------------------------

    def test_moving_it_to_an_earlier_day_reaches_the_bell_as_back_dating(self):
        old = self.entry.date
        self._post(date=(old - timedelta(days=2)).isoformat())
        row = self._alerts().get()
        self.assertEqual(row.event, 'DATED_BACK')
        self.assertEqual(row.recipient, self.owner)
        self.assertIn(old.strftime('%b'), row.detail, 'it says what the date WAS')
        self.assertFalse(EditLog.objects.exists(), 'kept by the Back-dated tab, not as an edit')

    def test_moving_it_to_a_later_day_says_nothing(self):
        CashbookEntry.objects.filter(pk=self.entry.pk).update(
            date=timezone.localdate() - timedelta(days=2))
        self.entry.refresh_from_db()
        self._post(date=(timezone.localdate() - timedelta(days=1)).isoformat())
        self.assertEqual(self._alerts().count(), 0)

    def test_tapping_it_opens_a_page_the_entry_is_actually_on(self):
        """
        Follow the link and look at the RENDERED page: `/cashbook/` opens on
        filter=today, so the bare route is guaranteed not to contain an entry
        moved to another day. The entry is found by its ROW, never its name —
        the add form's datalist carries every category already in use.
        """
        moved = self.entry.date - timedelta(days=2)
        self._post(date=moved.isoformat(), category='Switchgear')

        row = self._alerts().get()
        self.client.login(username='sahad', password='pw')
        marker = 'data-id="%d"' % self.entry.pk

        bare = self.client.get(reverse('cashbook'), follow=True)
        self.assertNotIn(self.entry, bare.context['entries'],
                         'the bare cashbook now lists a moved entry - this '
                         'test has stopped proving anything')

        page = self.client.get(row.url, follow=True)
        self.assertEqual(page.status_code, 200, 'the alert opens a dead page')
        self.assertIn(self.entry, page.context['entries'])
        self.assertContains(page, marker)

    def test_the_link_is_built_from_reverse_not_a_hardcoded_path(self):
        self._post(date=(self.entry.date - timedelta(days=1)).isoformat())
        self.assertTrue(self._alerts().get().url.startswith(reverse('cashbook')))

    # -- past the limits: only an owner, kept, and the other owner's phone ------

    def test_an_owner_editing_an_old_entry_is_kept_and_phones_the_other_owner(self):
        second = self._second_owner()
        self._age(days=5)
        self.client.login(username='sahad', password='pw')
        self._post(amount='5')
        row = self._alerts().get()
        self.assertEqual(row.event, 'OLD_RECORD_CHANGED')
        self.assertEqual(row.recipient, second)
        self.assertIn('50,000', row.detail)
        log = EditLog.objects.get()
        self.assertEqual(log.edited_by, self.owner)
        self.assertEqual([c['field'] for c in log.changes], ['Amount'])

    def test_an_owner_moving_a_date_past_the_limit_is_kept_and_phoned_once(self):
        """Owner-only even inside 24 hours: one act, one alert — the edit's,
        not a second back-dating one beside it."""
        self._second_owner()
        self.client.login(username='sahad', password='pw')
        self._post(date=(self.entry.date - timedelta(days=10)).isoformat())
        self.assertEqual(list(self._alerts().values_list('event', flat=True)),
                         ['OLD_RECORD_CHANGED'])
        self.assertEqual(EditLog.objects.count(), 1)

    def test_an_owner_deleting_an_old_entry_is_logged_and_phones_the_other_owner(self):
        second = self._second_owner()
        self._age(days=5)
        self.client.login(username='sahad', password='pw')
        self.client.post(reverse('manage_delete_cashbook_entry', args=[self.entry.pk]),
                         {'reason': 'duplicate of the 3rd'})
        log = DeletionLog.objects.get(entity_type=DeletionLog.ENTITY_CASHBOOK)
        self.assertEqual(log.reason, 'duplicate of the 3rd')
        self.assertEqual(self._alerts().get().recipient, second)

    def test_office_cannot_edit_an_entry_past_24_hours(self):
        self._age(hours=25)
        self._post(amount='5')
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal('50000.00'))
        self.assertEqual(self._alerts().count(), 0)

    def test_an_edit_writes_no_deletion_log_row(self):
        self._second_owner()
        self._age(days=5)
        self.client.login(username='sahad', password='pw')
        self._post(amount='5')
        self.assertEqual(DeletionLog.objects.count(), 0)

    # -- the delete dialog asks for a reason only when there is a log to keep it

    def test_the_reason_box_is_offered_only_for_a_delete_that_is_logged(self):
        """A reason typed for a delete that is not logged would go nowhere —
        a field whose value is silently dropped. The row says which it is."""
        old = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Diesel', amount=Decimal('700'),
            payment_method='CASH', created_by=self.office, date=timezone.localdate())
        CashbookEntry.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=5))
        self.client.login(username='sahad', password='pw')
        html = self.client.get(reverse('cashbook')).content.decode()
        self.assertEqual(html.count('data-logged="1"'), 1, 'only the old row is logged')
        self.assertEqual(html.count('data-logged=""'), 1, 'the fresh row is not')
        self.assertIn('id="cbDelReasonField" hidden', html, 'the box starts hidden')
