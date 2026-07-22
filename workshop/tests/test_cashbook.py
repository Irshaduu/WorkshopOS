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
            entry_type='EXPENSE', category='Electricity', amount=Decimal('500.00'), payment_method='CASH', created_by=self.owner, date=timezone.now().date()
        )
        self.income1 = CashbookEntry.objects.create(
            entry_type='INCOME', category='Scrap Sell', amount=Decimal('1500.00'), payment_method='UPI', created_by=self.office, date=timezone.now().date()
        )
        
    def test_access_control(self):
        """Test that Floor users cannot access cashbook, but Office/Owner can"""
        # Unauthenticated
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/admin-login/'))
        
        # Floor user
        self.client.login(username='floor', password='password')
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(response.status_code, 302) # Redirects to admin-login since decorator requires office/owner
        
        # Office user
        self.client.login(username='office', password='password')
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/cashbook/cashbook.html')
        
    def test_cashbook_view_filtering(self):
        """Test default and specific filtering"""
        self.client.login(username='owner', password='password')
        
        # Default should be 'today'
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(response.context['filter_type'], 'today')
        self.assertEqual(len(response.context['expenses']), 1)
        self.assertEqual(len(response.context['incomes']), 1)
        
        # Test totals
        totals = response.context['cashbook_totals']
        self.assertEqual(totals['expense'], Decimal('500.00'))
        self.assertEqual(totals['income'], Decimal('1500.00'))
        self.assertEqual(totals['net'], Decimal('1000.00'))
        
        # Add an entry dated yesterday
        yesterday = timezone.now().date() - timedelta(days=1)
        old_expense = CashbookEntry.objects.create(
            entry_type='EXPENSE', category='Old Expense', amount=Decimal('200.00')
        )
        # Update date bypassing auto_now_add
        CashbookEntry.objects.filter(id=old_expense.id).update(date=yesterday)
        
        # Fetch today filter again
        response = self.client.get(reverse('cashbook'))
        self.assertEqual(len(response.context['expenses']), 1) # Should not include yesterday's
        
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
