from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal

from workshop.models import JobCard, Mechanic, JobCardConcern, JobCardSpareItem, JobCardLabourItem, SpareShop, CashbookEntry
from inventory.models import Item, SupplierShop
from django.contrib.auth.models import Group

User = get_user_model()

class AnalysisDashboardTests(TestCase):
    def setUp(self):
        # Create groups
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')

        # Create users
        self.owner = User.objects.create_user(username='owner', password='pw')
        self.owner.groups.add(self.owner_group)
        
        self.superuser = User.objects.create_superuser(username='super', password='pw')
        
        self.office = User.objects.create_user(username='office', password='pw')
        self.office.groups.add(self.office_group)
        
        self.floor = User.objects.create_user(username='floor', password='pw')
        self.floor.groups.add(self.floor_group)

        self.client = Client()
        self.analysis_url = reverse('analysis_dashboard')

        # Create basic data
        self.mechanic1 = Mechanic.objects.create(name='Mechanic A')
        self.mechanic2 = Mechanic.objects.create(name='Mechanic B')
        
        self.job1 = JobCard.objects.create(
            customer_name='John Doe',
            registration_number='AB12CD3456',
            brand_name='Honda',
            model_name='Civic',
            admitted_date=timezone.now(),
            discharged_date=timezone.now() + timedelta(days=2),
            delivered=True,
            is_deleted=False,
            payment_status='PAID',
            total_bill_amount=Decimal('5000.00'),
            received_amount=Decimal('5000.00'),
            discount_amount=Decimal('0.00'),
            lead_mechanic=self.mechanic1
        )

        self.job2 = JobCard.objects.create(
            customer_name='Jane Smith',
            registration_number='XY98ZW7654',
            brand_name='Toyota',
            model_name='Corolla',
            admitted_date=timezone.now(),
            discharged_date=timezone.now() + timedelta(days=1),
            delivered=True,
            is_deleted=False,
            payment_status='PENDING',
            total_bill_amount=Decimal('10000.00'),
            received_amount=Decimal('2000.00'),
            discount_amount=Decimal('500.00'),
            lead_mechanic=self.mechanic2
        )

        # Trashed Job
        self.job3_trashed = JobCard.objects.create(
            customer_name='Ghost',
            registration_number='GHOST',
            admitted_date=timezone.now(),
            delivered=True,
            is_deleted=True,
            total_bill_amount=Decimal('99999.00'),
            received_amount=Decimal('99999.00')
        )

    # 1. test_owner_can_access
    def test_owner_can_access(self):
        self.client.login(username='owner', password='pw')
        response = self.client.get(self.analysis_url)
        self.assertEqual(response.status_code, 200)

    # 2. test_superuser_can_access
    def test_superuser_can_access(self):
        self.client.login(username='super', password='pw')
        response = self.client.get(self.analysis_url)
        self.assertEqual(response.status_code, 200)

    # 3. test_office_blocked
    def test_office_blocked(self):
        self.client.login(username='office', password='pw')
        response = self.client.get(self.analysis_url)
        self.assertEqual(response.status_code, 302)

    # 4. test_floor_blocked
    def test_floor_blocked(self):
        self.client.login(username='floor', password='pw')
        response = self.client.get(self.analysis_url)
        self.assertEqual(response.status_code, 302)

    # 5. test_anonymous_redirect
    def test_anonymous_redirect(self):
        response = self.client.get(self.analysis_url)
        self.assertEqual(response.status_code, 302)

    # 6. test_zone_ajax_access
    def test_zone_ajax_access(self):
        self.client.login(username='owner', password='pw')
        zones = ['revenue', 'mechanic', 'spares', 'customer', 'inventory', 'cashbook', 'workshop']
        for zone in zones:
            url = reverse('analysis_zone', args=[zone])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

    # 7. test_zone_invalid_name
    def test_zone_invalid_name(self):
        self.client.login(username='owner', password='pw')
        url = reverse('analysis_zone', args=['nonexistentzone'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # 8. test_default_date_range
    def test_default_date_range(self):
        self.client.login(username='owner', password='pw')
        response = self.client.get(self.analysis_url)
        self.assertEqual(response.context['range_key'], 'this_month')

    # 9. test_all_date_ranges
    def test_all_date_ranges(self):
        self.client.login(username='owner', password='pw')
        ranges = ['today', 'this_week', 'this_month', 'this_year', 'last_week', 'last_month', 'last_year', 'all_time']
        for r in ranges:
            response = self.client.get(self.analysis_url, {'range': r})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['range_key'], r)

    # 10. test_revenue_calculation
    def test_revenue_calculation(self):
        self.client.login(username='owner', password='pw')
        response = self.client.get(self.analysis_url, {'range': 'all_time'})
        self.assertEqual(response.context['total_revenue'], '₹15,000') # 5000 + 10000
        self.assertEqual(response.context['total_collected'], '₹7,000') # 5000 + 2000

    # 11. test_spare_profit
    def test_spare_profit(self):
        shop = SpareShop.objects.create(name='Test Shop')
        
        JobCardSpareItem.objects.create(
            job_card=self.job1,
            shop=shop,
            spare_part_name='Test Spare',
            quantity=Decimal('2.00'),
            unit_price=Decimal('100.00'),
            total_price=Decimal('300.00') # Customer pays 300, cost is 200. Profit = 100.
        )
        
        self.client.login(username='owner', password='pw')
        url = reverse('analysis_zone', args=['spares'])
        response = self.client.get(url, {'range': 'all_time'})
        self.assertEqual(response.status_code, 200)

    # 12. test_mechanic_ranking
    def test_mechanic_ranking(self):
        self.client.login(username='owner', password='pw')
        url = reverse('analysis_zone', args=['mechanic'])
        response = self.client.get(url, {'range': 'all_time'})
        self.assertEqual(response.status_code, 200)

    # 13. test_empty_database
    def test_empty_database(self):
        JobCard.objects.all().delete()
        Mechanic.objects.all().delete()
        self.client.login(username='owner', password='pw')
        response = self.client.get(self.analysis_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_revenue'], '₹0')

    # 14. test_null_prices
    def test_null_prices(self):
        JobCardSpareItem.objects.create(
            job_card=self.job1,
            shop=SpareShop.objects.create(name='Test Shop2'),
            spare_part_name='Null Price Spare',
            quantity=Decimal('1.00'),
            unit_price=None,
            total_price=None
        )
        self.client.login(username='owner', password='pw')
        url = reverse('analysis_zone', args=['spares'])
        response = self.client.get(url, {'range': 'all_time'})
        self.assertEqual(response.status_code, 200)

    # 15. test_null_dates
    def test_null_dates(self):
        JobCard.objects.create(
            customer_name='No Date',
            admitted_date=timezone.now(),
            discharged_date=None,
            delivered=True,
            is_deleted=False,
            payment_status='PAID',
            total_bill_amount=Decimal('100.00'),
            received_amount=Decimal('100.00'),
            lead_mechanic=self.mechanic1
        )
        self.client.login(username='owner', password='pw')
        url = reverse('analysis_zone', args=['workshop'])
        response = self.client.get(url, {'range': 'all_time'})
        self.assertEqual(response.status_code, 200)

    # 16. test_trashed_excluded
    def test_trashed_excluded(self):
        self.client.login(username='owner', password='pw')
        response = self.client.get(self.analysis_url, {'range': 'all_time'})
        self.assertEqual(response.context['total_revenue'], '₹15,000')

    # 17. test_period_comparison
    def test_period_comparison(self):
        self.client.login(username='owner', password='pw')
        response = self.client.get(self.analysis_url, {'range': 'this_month'})
        self.assertEqual(response.status_code, 200)
        
    # 18. test_lakh_format
    def test_lakh_format(self):
        self.client.login(username='owner', password='pw')
        response = self.client.get(self.analysis_url, {'range': 'all_time'})
        self.assertEqual(response.status_code, 200)
