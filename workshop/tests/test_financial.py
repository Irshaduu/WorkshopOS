"""
Financial Integration Tests for Titan WorkshopOS.
Covers: spare shop quantity math, cascade payments, payment reversal,
invoice totals, delivered date filters.
"""
from decimal import Decimal
from datetime import date, timedelta

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse

from workshop.models import (
    JobCard, Mechanic, CarBrand, SparePart,
    JobCardSpareItem, JobCardLabourItem,
    BulkPayer, BulkPaymentHistory,
    SpareShop, SpareShopPayment,
    FailedAttempt,
)


class FinancialIntegrationTests(TestCase):
    """
    TEST-1: End-to-end financial math verification.
    Covers quantity-aware pricing, cascade payment distribution,
    and payment reversal integrity.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')

        self.owner = User.objects.create_user(username='Sahad', password='pass')
        self.owner.groups.add(self.owner_group)

        self.office = User.objects.create_user(username='office', password='pass')
        self.office.groups.add(self.office_group)

        self.client = Client()
        self.client.login(username='office', password='pass')

        self.mechanic = Mechanic.objects.create(name='Mech')
        self.shop = SpareShop.objects.create(name='TestShop')

    def _create_jobcard(self, reg='KL01XX0001', **kwargs):
        defaults = {
            'registration_number': reg,
            'brand_name': 'Toyota',
            'model_name': 'Corolla',
            'admitted_date': date.today(),
            'lead_mechanic': self.mechanic,
        }
        defaults.update(kwargs)
        return JobCard.objects.create(**defaults)

    # -------------------------------------------------------------------------
    # Spare Shop: Quantity × Unit Price math
    # -------------------------------------------------------------------------
    def test_spare_shop_quantity_math(self):
        """Verify that total_purchases = unit_price × quantity, not just unit_price."""
        jc = self._create_jobcard()
        JobCardSpareItem.objects.create(
            job_card=jc,
            spare_part_name='Brake Pad',
            unit_price=Decimal('500'),
            quantity=Decimal('3'),
            shop=self.shop,
        )

        resp = self.client.get(reverse('spare_shop_detail', args=[self.shop.pk]))
        self.assertEqual(resp.status_code, 200)
        # 500 × 3 = 1500
        self.assertEqual(resp.context['total_purchases'], Decimal('1500'))
        self.assertEqual(resp.context['total_balance'], Decimal('1500'))

    def test_spare_shop_bulk_pay_and_waterfall(self):
        """Lump sum should generate a payment record and update shop totals."""
        jc = self._create_jobcard()
        item = JobCardSpareItem.objects.create(
            job_card=jc,
            spare_part_name='Oil Filter',
            unit_price=Decimal('200'),
            quantity=Decimal('4'),
            total_price=Decimal('1000'),
            shop=self.shop,
        )

        resp = self.client.post(
            reverse('spare_shop_pay', args=[self.shop.pk]),
            {'lump_sum': '800', 'payment_method': 'CASH'},
        )
        self.assertEqual(resp.status_code, 302)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_paid_amount, Decimal('800'))
        self.assertEqual(self.shop.total_purchased_amount, Decimal('800'))

        # Verify payment record
        payment = SpareShopPayment.objects.filter(shop=self.shop).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal('800'))

    # -------------------------------------------------------------------------
    # Spare Shop: Payment Reversal
    # -------------------------------------------------------------------------
    def test_spare_shop_payment_reversal(self):
        """Reversing a payment should subtract from shop.total_paid_amount."""
        jc = self._create_jobcard()
        item = JobCardSpareItem.objects.create(
            job_card=jc, spare_part_name='Spark Plug',
            unit_price=Decimal('150'), quantity=Decimal('2'),
            total_price=Decimal('400'),
            shop=self.shop,
        )

        # Pay it
        self.client.post(
            reverse('spare_shop_pay', args=[self.shop.pk]),
            {'lump_sum': '300', 'payment_method': 'CASH'},
        )
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_paid_amount, Decimal('300'))

        # Reverse it (need owner)
        self.client.login(username='Sahad', password='pass')
        payment = SpareShopPayment.objects.filter(shop=self.shop).first()
        resp = self.client.post(
            reverse('spare_shop_payment_reverse', args=[self.shop.pk, payment.pk])
        )
        self.assertEqual(resp.status_code, 302)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.total_paid_amount, Decimal('0'))

        payment.refresh_from_db()
        self.assertTrue(payment.is_trashed)

    # -------------------------------------------------------------------------
    # Bulk Payer: Cascade across job cards
    # -------------------------------------------------------------------------
    def test_bulk_payer_cascade_distribution(self):
        """Lump sum to a bulk payer should cascade oldest-first across jobs."""
        jc1 = self._create_jobcard('KL01BB0001', admitted_date=date.today() - timedelta(days=20))
        jc2 = self._create_jobcard('KL01BB0002', admitted_date=date.today() - timedelta(days=10))

        # Job 1: total_price=1000, labour=500 → total=1500, received=0 → balance=1500
        JobCardSpareItem.objects.create(
            job_card=jc1, spare_part_name='Engine Oil',
            unit_price=Decimal('500'), quantity=Decimal('2'),
            total_price=Decimal('1000'),  # Customer price (used for billing)
        )
        JobCardLabourItem.objects.create(
            job_card=jc1, job_description='Oil Change',
            amount=Decimal('500'),
        )

        # Job 2: total_price=800, labour=200 → total=1000, received=0 → balance=1000
        JobCardSpareItem.objects.create(
            job_card=jc2, spare_part_name='Air Filter',
            unit_price=Decimal('400'), quantity=Decimal('2'),
            total_price=Decimal('800'),  # Customer price
        )
        JobCardLabourItem.objects.create(
            job_card=jc2, job_description='Filter Replace',
            amount=Decimal('200'),
        )

        # Create bulk payer and add both jobs
        bp = BulkPayer.objects.create(customer_name='Fleet Customer')
        bp.job_cards.add(jc1, jc2)

        # Pay 2000 → should fully pay jc1 (1500) and partially pay jc2 (500)
        resp = self.client.post(
            reverse('bulk_payer_pay', args=[bp.pk]),
            {'lump_sum': '2000', 'payment_method': 'TRANSFER'},
        )
        self.assertEqual(resp.status_code, 302)

        jc1.refresh_from_db()
        jc2.refresh_from_db()

        self.assertEqual(jc1.received_amount, Decimal('1500'))
        self.assertEqual(jc1.payment_status, 'BULK_PAID')
        self.assertEqual(jc2.received_amount, Decimal('500'))
        self.assertEqual(jc2.payment_status, 'PARTIAL')

        # Verify history record
        history = BulkPaymentHistory.objects.filter(bulk_payer=bp).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.amount, Decimal('2000'))
        self.assertEqual(history.jobs_affected, 2)

    # -------------------------------------------------------------------------
    # Invoice: Total = spares + labours
    # -------------------------------------------------------------------------
    def test_invoice_total_matches(self):
        """Invoice grand_total should equal sum(total_price) + sum(labours)."""
        jc = self._create_jobcard()
        JobCardSpareItem.objects.create(
            job_card=jc, spare_part_name='Brake Disc',
            unit_price=Decimal('2000'), quantity=Decimal('2'),
            total_price=Decimal('5000'),  # Customer price (with markup)
        )
        JobCardLabourItem.objects.create(
            job_card=jc, job_description='Brake Work',
            amount=Decimal('1500'),
        )

        resp = self.client.get(reverse('invoice_view', args=[jc.pk]))
        self.assertEqual(resp.status_code, 200)
        # Spares total_price: 5000, Labour: 1500 → Total: 6500
        self.assertEqual(resp.context['grand_total'], Decimal('6500'))


class DeliveredDateFilterTests(TestCase):
    """
    TEST-2: Verify delivered_list date filter logic.
    """

    def setUp(self):
        FailedAttempt.objects.all().delete()
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office', password='pass')
        self.user.groups.add(self.office_group)

        self.client = Client()
        self.client.login(username='office', password='pass')

        self.mechanic = Mechanic.objects.create(name='Mech')

        # Create delivered job cards with various dates
        today = date.today()
        for i, days_ago in enumerate([0, 3, 15, 60, 200]):
            jc = JobCard.objects.create(
                registration_number=f'KL01DD{i:04d}',
                brand_name='Test',
                model_name='Car',
                admitted_date=today - timedelta(days=days_ago + 5),
                lead_mechanic=self.mechanic,
                delivered=True,
                discharged_date=today - timedelta(days=days_ago),
            )

    def test_delivered_today_filter(self):
        """Full page load defaults to 'today' filter."""
        resp = self.client.get(reverse('delivered_list'))
        self.assertEqual(resp.status_code, 200)
        # Only 1 job delivered today
        self.assertEqual(resp.context['page_obj'].paginator.count, 1)

    def test_delivered_week_filter(self):
        """Week filter shows jobs from Monday of the current calendar week onwards."""
        resp = self.client.get(
            reverse('delivered_list') + '?filter=this_week',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # Dynamically compute how many of the setUp dates fall in the current calendar week
        # (the view uses Monday-aligned weeks, not a rolling 7-day window)
        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday of this week
        expected = sum(
            1 for days_ago in [0, 3, 15, 60, 200]
            if today - timedelta(days=days_ago) >= week_start
        )
        self.assertEqual(resp.context['page_obj'].paginator.count, expected)

    def test_delivered_month_filter(self):
        """Month filter should show jobs delivered in last 30 days."""
        resp = self.client.get(
            reverse('delivered_list') + '?filter=this_month',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # Jobs at 0, 3, and 15 days ago = 3
        self.assertEqual(resp.context['page_obj'].paginator.count, 3)

    def test_delivered_year_filter(self):
        """Year filter should show jobs delivered in last 365 days."""
        resp = self.client.get(
            reverse('delivered_list') + '?filter=year',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # All 5 jobs within a year
        self.assertEqual(resp.context['page_obj'].paginator.count, 5)

    def test_delivered_custom_filter(self):
        """Custom date range filter should return correct subset."""
        today = date.today()
        start = (today - timedelta(days=20)).isoformat()
        end = (today - timedelta(days=1)).isoformat()

        resp = self.client.get(
            reverse('delivered_list') + f'?filter=custom&start_date={start}&end_date={end}',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # Jobs at 3 and 15 days ago should be in range
        self.assertEqual(resp.context['page_obj'].paginator.count, 2)

    def test_delivered_search_with_filter(self):
        """Search combined with filter should narrow results further."""
        resp = self.client.get(
            reverse('delivered_list') + '?filter=year&q=KL01DD0000',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # Only 1 matching registration
        self.assertEqual(resp.context['page_obj'].paginator.count, 1)
