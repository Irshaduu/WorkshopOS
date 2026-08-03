"""
Financial Integration Tests for Titan WorkshopOS.
Covers: spare shop quantity math, cascade payments, payment reversal,
invoice totals, completed date filters.
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

        # Payment is now permanently deleted (not soft-trashed) and recorded
        # in the Owner-only Deletion History.
        from workshop.models import DeletionLog
        self.assertFalse(SpareShopPayment.objects.filter(pk=payment.pk).exists())
        self.assertTrue(
            DeletionLog.objects.filter(entity_type=DeletionLog.ENTITY_SHOP_PAYMENT).exists()
        )

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
        JobCardLabourItem.objects.create(job_card=jc1, job_description='Oil Change')
        jc1.labour_amount = Decimal('500')
        jc1.save()
        jc1.update_totals()

        # Job 2: total_price=800, labour=200 → total=1000, received=0 → balance=1000
        JobCardSpareItem.objects.create(
            job_card=jc2, spare_part_name='Air Filter',
            unit_price=Decimal('400'), quantity=Decimal('2'),
            total_price=Decimal('800'),  # Customer price
        )
        JobCardLabourItem.objects.create(job_card=jc2, job_description='Filter Replace')
        jc2.labour_amount = Decimal('200')
        jc2.save()
        jc2.update_totals()

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

    def test_bulk_payment_history_delete_reverses_and_logs(self):
        """Deleting a Fleet payment must reverse balances, log a snapshot, then hard-delete."""
        from workshop.models import DeletionLog
        jc1 = self._create_jobcard('KL01CC0001', admitted_date=date.today() - timedelta(days=20))
        jc2 = self._create_jobcard('KL01CC0002', admitted_date=date.today() - timedelta(days=10))
        JobCardSpareItem.objects.create(
            job_card=jc1, spare_part_name='Engine Oil',
            unit_price=Decimal('500'), quantity=Decimal('2'), total_price=Decimal('1000'),
        )
        JobCardLabourItem.objects.create(job_card=jc1, job_description='Svc')
        jc1.labour_amount = Decimal('500')
        jc1.save()
        jc1.update_totals()
        JobCardSpareItem.objects.create(
            job_card=jc2, spare_part_name='Air Filter',
            unit_price=Decimal('400'), quantity=Decimal('2'), total_price=Decimal('800'),
        )
        JobCardLabourItem.objects.create(job_card=jc2, job_description='Filt')
        jc2.labour_amount = Decimal('200')
        jc2.save()
        jc2.update_totals()

        bp = BulkPayer.objects.create(customer_name='Reversal Fleet')
        bp.job_cards.add(jc1, jc2)

        # Pay 2000 → jc1 fully paid (1500), jc2 partial (500)
        self.client.post(
            reverse('bulk_payer_pay', args=[bp.pk]),
            {'lump_sum': '2000', 'payment_method': 'CASH'},
        )
        history = BulkPaymentHistory.objects.filter(bulk_payer=bp).first()
        self.assertIsNotNone(history)

        # Delete the payment → reverse effect + log + hard-delete, atomically
        resp = self.client.post(
            reverse('bulk_payment_history_delete', args=[bp.pk, history.pk])
        )
        self.assertEqual(resp.status_code, 302)

        jc1.refresh_from_db()
        jc2.refresh_from_db()
        # Balances fully restored
        self.assertEqual(jc1.received_amount, Decimal('0'))
        self.assertEqual(jc1.payment_status, 'PENDING')
        self.assertEqual(jc2.received_amount, Decimal('0'))
        self.assertEqual(jc2.payment_status, 'PENDING')

        # Record is hard-deleted (not soft-trashed) and recorded in Deletion History
        self.assertFalse(BulkPaymentHistory.objects.filter(pk=history.pk).exists())
        self.assertTrue(
            DeletionLog.objects.filter(entity_type=DeletionLog.ENTITY_BULK_PAYMENT).exists()
        )

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
        JobCardLabourItem.objects.create(job_card=jc, job_description='Brake Work')
        jc.labour_amount = Decimal('1500')
        jc.save()
        jc.update_totals()

        resp = self.client.get(reverse('invoice_view', args=[jc.pk]))
        self.assertEqual(resp.status_code, 200)
        # Spares total_price: 5000, Labour: 1500 → Total: 6500
        self.assertEqual(resp.context['grand_total'], Decimal('6500'))


class CompletedDateFilterTests(TestCase):
    """
    TEST-2: Verify completed_list date filter logic.
    """

    # How many days before today each fixture was completed. One list, used by
    # setUp and by every expectation below, so a fixture can never be added
    # without the assertions seeing it.
    #
    # 400 earns its place: it is the only offset guaranteed to fall in a
    # PREVIOUS calendar year on every possible run date, which is what stops
    # test_completed_year_filter passing vacuously. Without it, all fixtures sit
    # inside the current year for most of the year, so the year filter returning
    # everything — including a filter that silently never applied — looked
    # correct.
    COMPLETED_OFFSETS = [0, 3, 15, 60, 200, 400]

    def setUp(self):
        FailedAttempt.objects.all().delete()
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.user = User.objects.create_user(username='office', password='pass')
        self.user.groups.add(self.office_group)

        self.client = Client()
        self.client.login(username='office', password='pass')

        self.mechanic = Mechanic.objects.create(name='Mech')

        # Create completed job cards with various dates
        today = date.today()
        for i, days_ago in enumerate(self.COMPLETED_OFFSETS):
            jc = JobCard.objects.create(
                registration_number=f'KL01DD{i:04d}',
                brand_name='Test',
                model_name='Car',
                admitted_date=today - timedelta(days=days_ago + 5),
                lead_mechanic=self.mechanic,
                completed=True,
                completed_date=today - timedelta(days=days_ago),
            )

    def test_completed_today_filter(self):
        """Full page load defaults to 'today' filter."""
        resp = self.client.get(reverse('completed_list'))
        self.assertEqual(resp.status_code, 200)
        # Only 1 job completed today
        self.assertEqual(resp.context['page_obj'].paginator.count, 1)

    def test_completed_week_filter(self):
        """Week filter shows jobs from Monday of the current calendar week onwards."""
        resp = self.client.get(
            reverse('completed_list') + '?filter=this_week',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # Computed, not hardcoded: the view uses Monday-aligned weeks, not a
        # rolling 7-day window, so the right answer depends on today's date.
        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday of this week
        self.assertEqual(
            {jc.registration_number for jc in resp.context['page_obj']},
            self._expected_regs(week_start),
        )

    def _expected_regs(self, since):
        """The setUp fixtures whose completed_date falls on or after `since`.

        Computed rather than hardcoded, because the view's filters are
        CALENDAR-aligned (from the 1st, from January) and the fixtures are
        placed at rolling offsets — so the right answer depends on today's
        date. The count was hardcoded here until 2026-08-02, which made this
        class fail on the first half of every month.
        """
        today = date.today()
        return {
            f'KL01DD{i:04d}'
            for i, days_ago in enumerate(self.COMPLETED_OFFSETS)
            if today - timedelta(days=days_ago) >= since
        }

    def test_completed_month_filter(self):
        """Month filter shows jobs completed since the 1st of this calendar
        month — not a rolling 30-day window."""
        resp = self.client.get(
            reverse('completed_list') + '?filter=this_month',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # Compared as a SET of registrations, not a count: a count alone passes
        # whenever the expected number happens to equal the fixture total, which
        # is exactly what a filter that silently failed to apply would return.
        self.assertEqual(
            {jc.registration_number for jc in resp.context['page_obj']},
            self._expected_regs(date.today().replace(day=1)),
        )

    def test_completed_year_filter(self):
        """Year filter shows jobs completed since 1 January — not a rolling
        365-day window."""
        # `?filter=year` used to be sent here, which matches no branch in
        # completed_list, so the queryset came back UNFILTERED and the test
        # asserted nothing. The view's key is `this_year`.
        resp = self.client.get(
            reverse('completed_list') + '?filter=this_year',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            {jc.registration_number for jc in resp.context['page_obj']},
            self._expected_regs(date.today().replace(month=1, day=1)),
        )

    def test_completed_custom_filter(self):
        """Custom date range filter should return correct subset."""
        today = date.today()
        start = (today - timedelta(days=20)).isoformat()
        end = (today - timedelta(days=1)).isoformat()

        resp = self.client.get(
            reverse('completed_list') + f'?filter=custom&start_date={start}&end_date={end}',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # Jobs at 3 and 15 days ago should be in range
        self.assertEqual(resp.context['page_obj'].paginator.count, 2)

    def test_completed_search_with_filter(self):
        """Search combined with filter should narrow results further."""
        # `this_year`, not the dead `year` key — see test_completed_year_filter.
        # KL01DD0000 is completed today, so it is in range on every run date.
        resp = self.client.get(
            reverse('completed_list') + '?filter=this_year&q=KL01DD0000',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        # Only 1 matching registration
        self.assertEqual(resp.context['page_obj'].paginator.count, 1)
