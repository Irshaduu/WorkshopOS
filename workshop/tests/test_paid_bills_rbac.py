from datetime import timedelta
from django.test import TestCase
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone
from workshop.models import JobCard


class PaidBillsRBACTests(TestCase):
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')

        self.owner_user = User.objects.create_user('owner', password='pw')
        self.owner_user.groups.add(self.owner_group)

        self.office_user = User.objects.create_user('office', password='pw')
        self.office_user.groups.add(self.office_group)

        self.floor_user = User.objects.create_user('floor', password='pw')
        self.floor_user.groups.add(self.floor_group)

        self.today = timezone.localdate()

        # Recent bill (2 days ago)
        self.recent_job = JobCard.objects.create(
            registration_number='KL01RECENT',
            customer_name='Recent Customer',
            brand_name='Honda',
            model_name='City',
            payment_status='PAID',
            received_amount=5000,
            total_bill_amount=5000,
            paid_date=timezone.now() - timedelta(days=2),
            admitted_date=self.today - timedelta(days=3),
        )

        # Old bill (20 days ago)
        self.old_job = JobCard.objects.create(
            registration_number='KL01OLD',
            customer_name='Old Customer',
            brand_name='Toyota',
            model_name='Innova',
            payment_status='PAID',
            received_amount=15000,
            total_bill_amount=15000,
            paid_date=timezone.now() - timedelta(days=20),
            admitted_date=self.today - timedelta(days=25),
        )

    def test_owner_can_see_the_full_history(self):
        self.client.force_login(self.owner_user)
        response = self.client.get(reverse('paid_bills_list') + '?filter=all')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'KL01RECENT')
        self.assertContains(response, 'KL01OLD')
        self.assertEqual(response.context['total_count'], 2)

    def test_office_user_is_restricted_to_last_7_days(self):
        self.client.force_login(self.office_user)
        # Even if filter=all or filter=last_month is requested, office is hard-capped to 7 days
        response = self.client.get(reverse('paid_bills_list') + '?filter=all')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'KL01RECENT')
        self.assertNotContains(response, 'KL01OLD')

    def test_the_page_carries_no_money_total_for_anyone(self):
        """
        THE GRAND TOTAL WAS REMOVED, not re-gated, so this is no longer an
        RBAC rule at all — it is the same for both roles.

        It summed `received_amount` over cards that reached fully-settled
        status in the window: exact for a walk-in, who pays once at pickup,
        and wrong for a fleet three ways over — a card closed this month
        carried its whole cumulative receipt, a PARTIAL card holding real cash
        appeared nowhere, and banked advance credit appeared nowhere. The
        question it reached for is answered by Cash Tracking on the Profit
        page, which reads fleet money one payment at a time.

        The COUNT stays: how many bills are in the list is a fact about the
        list, not a business figure.
        """
        for user in (self.owner_user, self.office_user):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse('paid_bills_list'))
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('total_collected', response.context)
                self.assertNotContains(response, 'Total Collected')
                self.assertNotContains(response, 'class="pb-total-block"')
                self.assertNotContains(response, 'id="pbTotalLabel"')
                self.assertIsNotNone(response.context['total_count'])

    def test_office_user_headline_and_no_filter_dropdown(self):
        self.client.force_login(self.office_user)
        response = self.client.get(reverse('paid_bills_list'))
        self.assertEqual(response.status_code, 200)
        # Office sees Paid Bills Last 7 Days pill
        self.assertContains(response, 'Paid Bills')
        self.assertContains(response, 'Last 7 Days')
        # Office does NOT have date filter dropdown
        self.assertNotContains(response, 'id="pbFilterDropdownBtn"')

    def test_owner_user_has_filter_dropdown_without_last_7_days(self):
        self.client.force_login(self.owner_user)
        response = self.client.get(reverse('paid_bills_list'))
        self.assertEqual(response.status_code, 200)
        # Owner has date filter dropdown
        self.assertContains(response, 'id="pbFilterDropdownBtn"')
        self.assertContains(response, 'data-filter="this_month"')
        self.assertContains(response, 'data-filter="this_year"')
        # Owner dropdown does NOT contain last_7_days filter
        self.assertNotContains(response, 'data-filter="last_7_days"')

    def test_floor_user_is_forbidden(self):
        self.client.force_login(self.floor_user)
        response = self.client.get(reverse('paid_bills_list'))
        self.assertEqual(response.status_code, 403)

    def test_office_cannot_widen_its_window_from_the_url(self):
        """
        The dropdown is hidden for Office, but hiding a control is not a
        control. `?filter=all` and a hand-written custom range are the two ways
        round it, and the view honours neither.
        """
        self.client.force_login(self.office_user)
        for query in ('?filter=all', '?filter=last_year',
                      '?filter=custom&start_date=2000-01-01&end_date=2099-12-31'):
            with self.subTest(query=query):
                response = self.client.get(reverse('paid_bills_list') + query)
                self.assertContains(response, 'KL01RECENT')
                self.assertNotContains(response, 'KL01OLD')

    def test_a_junk_custom_range_does_not_500(self):
        """
        Handed straight to the ORM these raise in `get_prep_value`, which is a
        500 from a hand-edited URL. An unusable range is ignored instead.
        """
        self.client.force_login(self.owner_user)
        response = self.client.get(
            reverse('paid_bills_list') + '?filter=custom&start_date=abc&end_date=zzz')
        self.assertEqual(response.status_code, 200)


class TheDiscountAuditStaysOwnerOnlyTests(TestCase):
    """
    AUD-0041. Paid Bills is Office-visible; this page is not — it reads as what
    the workshop settled for against what it billed, which is the compensating
    control for the rule that a part-paid walk-in books its shortfall as a
    permanent discount.

    Its entry in the Paid Bills ⋮ menu is gated to match, because a door Office
    can see but not open is worse than no door.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.owner = User.objects.create_user('aud_owner', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.office = User.objects.create_user('aud_office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.floor = User.objects.create_user('aud_floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))

    def test_an_owner_may_read_it(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('audit_high_discounts')).status_code, 200)

    def test_office_may_not(self):
        self.client.force_login(self.office)
        self.assertEqual(self.client.get(reverse('audit_high_discounts')).status_code, 403)

    def test_floor_may_not(self):
        self.client.force_login(self.floor)
        self.assertEqual(self.client.get(reverse('audit_high_discounts')).status_code, 403)

    def test_only_an_owner_is_shown_the_link_to_it(self):
        url = reverse('audit_high_discounts')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('paid_bills_list')), url)
        self.client.force_login(self.office)
        self.assertNotContains(self.client.get(reverse('paid_bills_list')), url)

    def test_a_junk_custom_range_does_not_500(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('audit_high_discounts') + '?filter=custom&start_date=abc&end_date=zzz')
        self.assertEqual(response.status_code, 200)
