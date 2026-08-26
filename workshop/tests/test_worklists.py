"""
THE TWO WORK LISTS — what each one carries, and in what order.

Both defects here were pure findability: the data was right, the page was
unusable. Completed buried the car just handed over somewhere in the middle of
today's list, and Pending Bills listed every car still on the floor alongside
the bills somebody is actually chasing.
"""
from datetime import timedelta

from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workshop.models import JobCard, BulkPayer


class WorklistBase(TestCase):
    def setUp(self):
        office = Group.objects.get_or_create(name='Office')[0]
        self.user = User.objects.create_user('office', password='pw')
        self.user.groups.add(office)
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def card(self, reg, **kw):
        kw.setdefault('admitted_date', self.today)
        return JobCard.objects.create(registration_number=reg, **kw)


class TheCompletedListPutsTheNewestFirstTests(WorklistBase):
    """
    `completed_date` is a DateField, so every car handed over today shares one
    value and the order inside that day was whatever the database returned —
    on the default 'today' filter, that is the entire page. The car somebody
    opened this list to see could be anywhere in it.
    """

    def _regs(self, url):
        page = self.client.get(url).context['completed_jobcards']
        return [c.registration_number for c in page]

    def test_the_last_car_completed_today_is_at_the_top(self):
        self.card('KL01AAA', completed=True, completed_date=self.today)
        self.card('KL01BBB', completed=True, completed_date=self.today)
        self.card('KL01CCC', completed=True, completed_date=self.today)

        self.assertEqual(
            self._regs(reverse('completed_list')),
            ['KL01CCC', 'KL01BBB', 'KL01AAA'],
            'newest first — without a tiebreaker this order is undefined')

    def test_the_DATE_still_leads_and_the_tiebreaker_only_breaks_ties(self):
        """
        The card created LAST is the one completed EARLIEST, so an ordering
        that had drifted to plain `-id` would put it on top of cars handed
        over today.
        """
        self.card('KL01TODAY', completed=True, completed_date=self.today)
        self.card('KL01YESTER', completed=True,
                  completed_date=self.today - timedelta(days=1))

        self.assertEqual(
            self._regs(reverse('completed_list') + '?filter=all'),
            ['KL01TODAY', 'KL01YESTER'])

    def test_an_unrelated_edit_does_not_move_an_old_card_to_the_top(self):
        """
        Why the tiebreaker is `-id` and not `-updated_at`, which is
        `auto_now=True`. This is the defect `paid_date` exists to keep off
        Paid Bills, one list over.
        """
        old = self.card('KL01OLD', completed=True, completed_date=self.today)
        self.card('KL01NEW', completed=True, completed_date=self.today)

        old.customer_name = 'Corrected Name'
        old.save()

        self.assertEqual(
            self._regs(reverse('completed_list'))[0], 'KL01NEW',
            'editing an old card must not resurface it above a newer one')


class PendingBillsListsOnlyHandedOverCarsTests(WorklistBase):
    """
    A card is PENDING from the moment it is created, so every car on the floor
    sat in the chase list. Nothing about them is chaseable — no figure is
    final and no bill was handed over — and they buried the ones that are.
    """

    def _page(self):
        return self.client.get(reverse('pending_payments_list'))

    def test_a_car_still_on_the_floor_is_not_a_pending_bill(self):
        self.card('KL01LIVE', completed=False,
                  payment_status='PENDING', total_bill_amount=5000)
        self.card('KL01DONE', completed=True, completed_date=self.today,
                  payment_status='PENDING', total_bill_amount=3000)

        res = self._page()
        regs = [c.registration_number for c in res.context['pending_jobs']]
        self.assertEqual(regs, ['KL01DONE'])

    def test_the_total_counts_only_what_is_listed(self):
        """
        The figure above the rows must add up from the rows themselves. It is
        deliberately smaller than the Profit page's "Customers owe us", which
        counts fleet and still-on-the-floor cards too.
        """
        self.card('KL01LIVE', completed=False,
                  payment_status='PENDING', total_bill_amount=5000)
        self.card('KL01DONE', completed=True, completed_date=self.today,
                  payment_status='PENDING', total_bill_amount=3000)

        self.assertEqual(self._page().context['total_outstanding'], 3000)

    def test_it_appears_the_moment_the_car_is_marked_completed(self):
        """Nothing is stranded — the bill moves onto the list, it is not lost."""
        card = self.card('KL01LIVE', completed=False,
                         payment_status='PENDING', total_bill_amount=5000)
        self.assertEqual(list(self._page().context['pending_jobs']), [])

        card.mark_completed()
        card.save()

        regs = [c.registration_number for c in self._page().context['pending_jobs']]
        self.assertEqual(regs, ['KL01LIVE'])

    def test_a_fleet_card_is_still_excluded(self):
        """Settled from the fleet account's own page — unchanged by the above."""
        payer = BulkPayer.objects.create(customer_name='Acme Fleet')
        self.card('KL01FLEET', completed=True, completed_date=self.today,
                  payment_status='PENDING', total_bill_amount=4000,
                  bulk_payer=payer)

        self.assertEqual(list(self._page().context['pending_jobs']), [])
