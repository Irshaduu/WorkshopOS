"""
Tests for Owner → Analysis & Reports (rebuilt 2026-07-27).

The Profit page is what the owners distribute profit from, so these tests are
deliberately weighted towards the *arithmetic* rather than the markup. In
particular `DoubleCountRuleTests` is the regression guard for the rule that the
whole expense model rests on — if it starts failing, the workshop is being
charged twice for parts it bought once.

Convention reminder (TITAN_MASTER_HANDOVER.md): when one of these fails, fix
the code, not the test.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from workshop import analysis_engine as engine
from workshop.models import (
    JobCard, JobCardSpareItem, JobCardLabourItem, Mechanic, SpareShop,
    CashbookEntry, BulkPayer, SalaryAdvance, SalaryPayment, SalaryPaymentLine,
)
from inventory.models import (
    Category, Item, SupplierShop, SupplierRestockBill, SupplierRestockItem,
)

User = get_user_model()
D = Decimal


class AnalysisBase(TestCase):
    """Shared fixture: one owner, and a single job card dated inside TODAY's month."""

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)

        self.owner = User.objects.create_user(username='an_owner', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.client = Client()
        self.client.force_login(self.owner)

        self.today = timezone.localdate()
        self.mech = Mechanic.objects.create(name='Ravi')
        self.shop = SpareShop.objects.create(name='Alpha Spares')

    def make_card(self, bill='0', discount='0', received='0', when=None, **kw):
        kw.setdefault('registration_number', f'KL 01 AB {JobCard.objects.count():04d}')
        kw.setdefault('brand_name', 'Toyota')
        kw.setdefault('model_name', 'Innova')
        return JobCard.objects.create(
            admitted_date=when or self.today,
            total_bill_amount=D(bill), discount_amount=D(discount), received_amount=D(received),
            lead_mechanic=self.mech, **kw,
        )


# =============================================================================
# ACCESS
# =============================================================================
class AnalysisAccessTests(AnalysisBase):

    def test_owner_and_superuser_get_in(self):
        self.assertEqual(self.client.get(reverse('analysis_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('analysis_insights')).status_code, 200)

        su = User.objects.create_superuser(username='an_su', password='pw')
        c = Client(); c.force_login(su)
        self.assertEqual(c.get(reverse('analysis_dashboard')).status_code, 200)

    def test_office_and_floor_are_blocked(self):
        for group in ('Office', 'Floor'):
            u = User.objects.create_user(username=f'an_{group}', password='pw')
            u.groups.add(Group.objects.get(name=group))
            c = Client(); c.force_login(u)
            self.assertNotEqual(c.get(reverse('analysis_dashboard')).status_code, 200)
            self.assertNotEqual(c.get(reverse('analysis_insights')).status_code, 200)
            self.assertNotEqual(
                c.get(reverse('analysis_insight_section', args=['mechanics'])).status_code, 200)

    def test_anonymous_is_redirected(self):
        self.assertEqual(Client().get(reverse('analysis_dashboard')).status_code, 302)

    def test_unknown_insight_section_404s(self):
        self.assertEqual(
            self.client.get(reverse('analysis_insight_section', args=['nope'])).status_code, 404)


# =============================================================================
# PERIODS
# =============================================================================
class PeriodTests(AnalysisBase):

    def test_every_period_resolves_and_renders(self):
        for key, _label in engine.PERIOD_CHOICES:
            start, end, resolved, label = engine.resolve_period(key)
            self.assertLessEqual(start, end, f"{key} produced an inverted range")
            self.assertTrue(label)
            self.assertEqual(self.client.get(reverse('analysis_dashboard'), {'range': key}).status_code, 200)

    def test_this_month_covers_the_whole_calendar_month(self):
        start, end, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(start, self.today.replace(day=1))
        self.assertEqual(end.month, self.today.month)
        # end must be the real last day, including a 31-day month and February
        self.assertEqual((end + timedelta(days=1)).day, 1)

    def test_last_month_never_lands_in_this_month(self):
        start, end, _k, _l = engine.resolve_period('last_month')
        self.assertEqual(start.day, 1)
        self.assertLess(end, self.today.replace(day=1))
        self.assertEqual(start.month, end.month)

    def test_all_time_reaches_back_past_the_first_job_card(self):
        """
        A workshop going live seeds opening stock and shop balances before its
        first job card. Anchoring All Time to job cards alone would drop that
        spend out of the only window meant to hold everything.
        """
        self.make_card(bill='1000', when=self.today - timedelta(days=30))
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Opening Stock',
                                     amount=D('50000'), date=self.today - timedelta(days=365))
        start, end, _k, _l = engine.resolve_period('all_time')
        self.assertLessEqual(start, self.today - timedelta(days=365))
        self.assertEqual(engine.build_profit_report(start, end)['expense_total'], D('50000'))

    def test_all_time_includes_a_forward_dated_record(self):
        self.make_card(bill='4000', when=self.today + timedelta(days=10))
        start, end, _k, _l = engine.resolve_period('all_time')
        self.assertGreaterEqual(end, self.today + timedelta(days=10))
        self.assertEqual(engine.build_profit_report(start, end)['turnover'], D('4000'))

    def test_custom_range_is_honoured(self):
        s, e, key, _l = engine.resolve_period('custom', '2025-03-01', '2025-03-31')
        self.assertEqual((s, e, key), (date(2025, 3, 1), date(2025, 3, 31), 'custom'))

    def test_reversed_custom_range_is_swapped_not_rejected(self):
        s, e, _k, _l = engine.resolve_period('custom', '2025-03-31', '2025-03-01')
        self.assertEqual((s, e), (date(2025, 3, 1), date(2025, 3, 31)))

    def test_garbage_input_falls_back_to_default(self):
        for args in (('custom', 'junk', 'junk'), ('custom', None, None), ('nonsense', None, None), (None, None, None)):
            _s, _e, key, _l = engine.resolve_period(*args)
            self.assertEqual(key, 'this_month')

    def test_garbage_query_string_still_renders(self):
        for params in ({'range': 'custom', 'start': 'x', 'end': 'y'}, {'range': 'zzz'}, {}):
            self.assertEqual(self.client.get(reverse('analysis_dashboard'), params).status_code, 200)


# =============================================================================
# THE EQUATION
# =============================================================================
class ProfitEquationTests(AnalysisBase):

    def test_turnover_minus_expenses_equals_profit(self):
        self.make_card(bill='10000', received='10000')
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Rent',
                                     amount=D('2000'), date=self.today)
        CashbookEntry.objects.create(entry_type='INCOME', category='Scrap',
                                     amount=D('500'), date=self.today)

        s, e, _k, _l = engine.resolve_period('this_month')
        r = engine.build_profit_report(s, e)
        self.assertEqual(r['profit'], r['turnover'] - r['expense_total'])
        self.assertEqual(r['turnover'], D('10500'))     # 10000 bill + 500 misc income
        self.assertEqual(r['expense_total'], D('2000'))
        self.assertEqual(r['profit'], D('8500'))

    def test_discount_is_netted_off_turnover(self):
        """A discount is money never earned — it reduces turnover, it is not an expense."""
        self.make_card(bill='10000', discount='1500', received='8500')
        s, e, _k, _l = engine.resolve_period('this_month')
        r = engine.build_profit_report(s, e)
        self.assertEqual(r['bills']['gross'], D('10000'))
        self.assertEqual(r['bills']['discount'], D('1500'))
        self.assertEqual(r['bills']['net'], D('8500'))
        self.assertEqual(r['turnover'], D('8500'))
        self.assertEqual(r['expense_total'], D('0'))

    def test_deleted_cards_are_excluded(self):
        self.make_card(bill='10000', received='10000')
        self.make_card(bill='99999', received='99999', is_deleted=True)
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.build_profit_report(s, e)['turnover'], D('10000'))

    def test_cards_outside_the_window_are_excluded(self):
        self.make_card(bill='10000', received='10000')
        self.make_card(bill='55555', when=self.today - timedelta(days=400))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.build_profit_report(s, e)['turnover'], D('10000'))

    def test_empty_database_gives_zeros_not_errors(self):
        JobCard.objects.all().delete()
        s, e, _k, _l = engine.resolve_period('this_month')
        r = engine.build_profit_report(s, e)
        self.assertEqual((r['turnover'], r['expense_total'], r['profit']), (D('0'), D('0'), D('0')))
        self.assertEqual(r['margin'], 0.0)
        self.assertEqual(self.client.get(reverse('analysis_dashboard')).status_code, 200)

    def test_a_loss_is_reported_as_negative(self):
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Rent',
                                     amount=D('9000'), date=self.today)
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.build_profit_report(s, e)['profit'], D('-9000'))


# =============================================================================
# THE DOUBLE-COUNT RULE  — the guard the expense model rests on
# =============================================================================
class DoubleCountRuleTests(AnalysisBase):
    """
    A part is paid for exactly once, by exactly one route:

      source=SHOP + a shop     → charged as a Spare Shops expense
      source=SHOP, no shop     → real money with no payee, its own line
      source=INVENTORY         → already paid via a restock bill, so NOT charged

    If this class starts failing, the Profit page has begun charging the
    workshop twice for the same part. Do not "fix" it by counting both.

    The fixtures below declare their route explicitly (2026-07-30). They used to
    imply it — a NULL shop plus a name matching an inventory product — back when
    the engine inferred the route instead of reading `source`. **Every assertion
    is unchanged from that version**, which is the point: classifying by the
    stored column produces the identical figures, it just cannot disagree with
    the stock signals any more.
    """

    def setUp(self):
        super().setUp()
        cat = Category.objects.create(name='Oils')
        self.item = Item.objects.create(category=cat, name='Engine Oil 5W30', average_stock=D('10'))
        self.card = self.make_card(bill='10000', received='10000')

    def test_shop_linked_spare_is_an_expense(self):
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Brake Pad', shop=self.shop,
            quantity=D('2'), unit_price=D('500'), total_price=D('1400'))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.spare_shop_expense(s, e), D('1000'))   # 500 x 2

    def test_warehouse_drawn_spare_is_never_an_expense(self):
        """source=INVENTORY ⇒ already paid for by a restock bill."""
        JobCardSpareItem.objects.create(
            job_card=self.card, source=JobCardSpareItem.SOURCE_INVENTORY, item=self.item,
            quantity=D('3'), unit_price=D('400'), total_price=D('1800'))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.spare_shop_expense(s, e), D('0'))
        self.assertEqual(engine.unattributed_spare_expense(s, e), D('0'))
        self.assertEqual(engine.warehouse_drawn_spare_cost(s, e), D('1200'))
        # …and it must not have leaked into the expense total by another door.
        self.assertEqual(engine.build_profit_report(s, e)['expense_total'], D('0'))

    def test_orphan_spare_is_surfaced_not_swallowed(self):
        """A shop purchase with no shop recorded — real money, so it gets its own line."""
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Mystery Widget',
            shop=None, quantity=D('1'), unit_price=D('750'), total_price=D('900'))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.unattributed_spare_expense(s, e), D('750'))
        r = engine.build_profit_report(s, e)
        self.assertEqual(r['expense_total'], D('750'))
        self.assertIn('other_spares', [l['key'] for l in r['expense_lines']])

    def test_the_three_routes_partition_all_spare_cost_exactly(self):
        """Every rupee of spare cost lands in exactly one bucket — none lost, none doubled."""
        JobCardSpareItem.objects.create(job_card=self.card, spare_part_name='Brake Pad',
                                        shop=self.shop, quantity=D('2'), unit_price=D('500'),
                                        total_price=D('1400'))
        JobCardSpareItem.objects.create(job_card=self.card,
                                        source=JobCardSpareItem.SOURCE_INVENTORY,
                                        item=self.item, quantity=D('3'), unit_price=D('400'),
                                        total_price=D('1800'))
        JobCardSpareItem.objects.create(job_card=self.card, spare_part_name='Mystery Widget',
                                        shop=None, quantity=D('1'), unit_price=D('750'),
                                        total_price=D('900'))
        s, e, _k, _l = engine.resolve_period('this_month')
        total = D('1000') + D('1200') + D('750')
        self.assertEqual(
            engine.spare_shop_expense(s, e)
            + engine.warehouse_drawn_spare_cost(s, e)
            + engine.unattributed_spare_expense(s, e),
            total)
        # Only the two genuinely-unpaid routes reach the expense total.
        self.assertEqual(engine.build_profit_report(s, e)['expense_total'], D('1750'))

    def test_null_quantity_counts_as_one_unit(self):
        """Matches SpareShop.update_totals — a missing quantity is one unit, not zero."""
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Filter', shop=self.shop,
            quantity=None, unit_price=D('300'), total_price=D('450'))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.spare_shop_expense(s, e), D('300'))

    def test_null_unit_price_is_zero_not_a_crash(self):
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Freebie', shop=self.shop,
            quantity=D('2'), unit_price=None, total_price=D('100'))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.spare_shop_expense(s, e), D('0'))


# =============================================================================
# INVENTORY / SUPPLIES STREAM
# =============================================================================
class InventoryExpenseTests(AnalysisBase):

    def test_restock_bill_counts_at_its_effective_amount(self):
        cat = Category.objects.create(name='Filters')
        item = Item.objects.create(category=cat, name='Air Filter', average_stock=D('5'))
        sup = SupplierShop.objects.create(name='Bulk Supplies')
        bill = SupplierRestockBill.objects.create(supplier=sup, bill_date=self.today,
                                                  discount_amount=D('200'))
        SupplierRestockItem.objects.create(bill=bill, item=item, quantity=D('10'),
                                           total_price=D('2000'))
        s, e, _k, _l = engine.resolve_period('this_month')
        # total_amount is denormalized to 2000 by the item save; less 200 discount
        self.assertEqual(engine.inventory_expense(s, e), D('1800'))


# =============================================================================
# SALARY STREAM
# =============================================================================
class SalaryExpenseTests(AnalysisBase):

    def test_settled_month_counts_net_plus_advance_once(self):
        """
        The advance already left the drawer; the settlement pays the remainder.
        Wage cost is therefore net + advance — which is salary minus leave, and
        counts each half of one month's pay exactly once.
        """
        month = self.today.replace(day=1)
        SalaryAdvance.objects.create(staff=self.mech, amount=D('2000'), date=month + timedelta(days=5))
        pay = SalaryPayment.objects.create(month=month)
        SalaryPaymentLine.objects.create(payment=pay, staff=self.mech, salary_used=D('20000'),
                                          leave_days=D('0'), advance_used=D('2000'),
                                          net_amount=D('18000'))
        s, e, _k, _l = engine.resolve_period('this_month')
        out = engine.salary_expense(s, e)
        self.assertEqual(out['settled_net'], D('18000'))
        self.assertEqual(out['settled_advance'], D('2000'))
        self.assertEqual(out['unsettled_advance'], D('0'), "advance double counted")
        self.assertEqual(out['total'], D('20000'))

    def test_advance_in_an_unsettled_month_still_counts(self):
        SalaryAdvance.objects.create(staff=self.mech, amount=D('1500'), date=self.today)
        s, e, _k, _l = engine.resolve_period('this_month')
        out = engine.salary_expense(s, e)
        self.assertEqual(out['settled_months'], 0)
        self.assertEqual(out['unsettled_advance'], D('1500'))
        self.assertEqual(out['total'], D('1500'))

    def test_wages_do_not_come_from_the_cashbook(self):
        """
        Wages are owned by Salary & Advance. A cashbook row is a general
        expense whatever it is called, and is never re-read as salary.
        """
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Staff Salaries',
                                     amount=D('5000'), date=self.today)
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.salary_expense(s, e)['total'], D('0'))
        self.assertEqual(engine.cashbook_expense(s, e)['total'], D('5000'))

    def test_wage_looking_cashbook_categories_are_flagged_not_dropped(self):
        """
        A cashbook row named like wages is still counted — dropping rows on a
        keyword match would hide real money. It is flagged instead, so the
        owner decides.
        """
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Staff Salaries',
                                     amount=D('5000'), date=self.today)
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Workshop Rent',
                                     amount=D('3000'), date=self.today)
        s, e, _k, _l = engine.resolve_period('this_month')
        cb = engine.cashbook_expense(s, e)
        self.assertEqual(cb['total'], D('8000'), "a flagged row must still be counted")
        self.assertEqual(cb['wage_suspect_total'], D('5000'))
        self.assertEqual([r['category'] for r in cb['wage_suspects']], ['Staff Salaries'])
        rent = next(r for r in cb['by_category'] if r['category'] == 'Workshop Rent')
        self.assertFalse(rent['looks_like_wages'])

    def test_warning_shows_only_when_both_sources_have_wages(self):
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Staff Salaries',
                                     amount=D('5000'), date=self.today)
        # No salary settlement yet → nothing to double count → no warning.
        resp = self.client.get(reverse('analysis_dashboard'), {'range': 'this_month'})
        self.assertNotContains(resp, 'Wages may be counted twice')

        SalaryAdvance.objects.create(staff=self.mech, amount=D('1000'), date=self.today)
        resp = self.client.get(reverse('analysis_dashboard'), {'range': 'this_month'})
        self.assertContains(resp, 'Wages may be counted twice')


# =============================================================================
# CHART / TOTALS CONSISTENCY
# =============================================================================
class ConsistencyTests(AnalysisBase):

    def test_monthly_series_totals_match_the_headline(self):
        """
        The chart is built from separate queries to the headline figure. If the
        two ever drift, the owner is shown a total that its own breakdown
        contradicts — so they are asserted equal.
        """
        self.make_card(bill='10000', received='10000')
        self.make_card(bill='7000', discount='500', when=self.today - timedelta(days=40))
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Rent',
                                     amount=D('2000'), date=self.today)
        CashbookEntry.objects.create(entry_type='INCOME', category='Scrap',
                                     amount=D('300'), date=self.today - timedelta(days=40))
        SalaryAdvance.objects.create(staff=self.mech, amount=D('900'), date=self.today)
        JobCardSpareItem.objects.create(job_card=JobCard.objects.first(), spare_part_name='Pad',
                                        shop=self.shop, quantity=D('2'), unit_price=D('250'),
                                        total_price=D('700'))

        s, e, _k, _l = engine.resolve_period('all_time')
        report = engine.build_profit_report(s, e)
        series = engine.monthly_series(s, e)
        self.assertEqual(sum(m['turnover'] for m in series), report['turnover'])
        self.assertEqual(sum(m['expenses'] for m in series), report['expense_total'])
        self.assertEqual(sum(m['profit'] for m in series), report['profit'])

    def test_expense_lines_sum_to_the_expense_total(self):
        self.make_card(bill='10000', received='10000')
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Power',
                                     amount=D('800'), date=self.today)
        SalaryAdvance.objects.create(staff=self.mech, amount=D('400'), date=self.today)
        s, e, _k, _l = engine.resolve_period('this_month')
        r = engine.build_profit_report(s, e)
        self.assertEqual(sum(l['amount'] for l in r['expense_lines']), r['expense_total'])


# =============================================================================
# POSITION
# =============================================================================
class FinancialPositionTests(AnalysisBase):

    def test_receivable_counts_only_unsettled_cards(self):
        self.make_card(bill='10000', received='10000', payment_status='PAID')
        self.make_card(bill='8000', received='3000', payment_status='PARTIAL')
        self.make_card(bill='5000', received='0', payment_status='PENDING')
        pos = engine.financial_position()
        self.assertEqual(pos['receivable'], D('10000'))   # 5000 owed + 5000 owed

    def test_payables_come_from_both_shop_kinds(self):
        SpareShop.objects.filter(pk=self.shop.pk).update(
            total_purchased_amount=D('9000'), total_paid_amount=D('4000'))
        SupplierShop.objects.create(name='S1', total_billed_amount=D('3000'),
                                    total_paid_amount=D('1000'))
        pos = engine.financial_position()
        self.assertEqual(pos['payable_spare'], D('5000'))
        self.assertEqual(pos['payable_supplier'], D('2000'))
        self.assertEqual(pos['payable_total'], D('7000'))


# =============================================================================
# INSIGHTS
# =============================================================================
class InsightSectionTests(AnalysisBase):

    def setUp(self):
        super().setUp()
        self.card = self.make_card(bill='10000', received='10000', payment_status='PAID',
                                   payment_method='CASH', completed=True)
        JobCardSpareItem.objects.create(job_card=self.card, spare_part_name='Brake Pad',
                                        shop=self.shop, quantity=D('2'), unit_price=D('500'),
                                        total_price=D('1500'))
        JobCardLabourItem.objects.create(job_card=self.card, job_description='Fitting',
                                         amount=D('800'))

    def test_all_sections_render(self):
        for key, *_ in __import__('workshop.analysis_views', fromlist=['x']).INSIGHT_SECTIONS:
            resp = self.client.get(reverse('analysis_insight_section', args=[key]))
            self.assertEqual(resp.status_code, 200, f"section '{key}' failed to render")

    def test_all_sections_render_on_an_empty_database(self):
        JobCard.objects.all().delete()
        for key, *_ in __import__('workshop.analysis_views', fromlist=['x']).INSIGHT_SECTIONS:
            resp = self.client.get(reverse('analysis_insight_section', args=[key]))
            self.assertEqual(resp.status_code, 200, f"section '{key}' crashed when empty")

    def test_mechanic_profit_is_revenue_minus_parts(self):
        # total_bill_amount is denormalized: adding a spare/labour re-derives it
        # via update_totals(), so the card bills 1500 (spare) + 800 (labour).
        from workshop.analysis_views import _insight_mechanics
        self.card.refresh_from_db()
        self.assertEqual(self.card.total_bill_amount, D('2300'))

        s, e, _k, _l = engine.resolve_period('this_month')
        rows = _insight_mechanics(s, e)['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['revenue'], D('2300'))
        self.assertEqual(rows[0]['cost'], D('1000'))       # 500 x 2
        self.assertEqual(rows[0]['profit'], D('1300'))

    def test_mechanic_revenue_is_not_inflated_by_multiple_spares(self):
        """
        Regression guard: annotating spare cost onto the same queryset as the
        revenue Sum fans the revenue out across the join rows. With two spares
        on one card an inflated query would report double (5200) instead of the
        card's real 2600.
        """
        JobCardSpareItem.objects.create(job_card=self.card, spare_part_name='Filter',
                                        shop=self.shop, quantity=D('1'), unit_price=D('200'),
                                        total_price=D('300'))
        from workshop.analysis_views import _insight_mechanics
        s, e, _k, _l = engine.resolve_period('this_month')
        rows = _insight_mechanics(s, e)['rows']
        # 1500 + 300 spares + 800 labour, counted once
        self.assertEqual(rows[0]['revenue'], D('2600'), "revenue inflated by the spare join")
        self.assertEqual(rows[0]['cost'], D('1200'))

    def test_vehicles_section_reports_customer_name_coverage(self):
        from workshop.analysis_views import _insight_vehicles
        s, e, _k, _l = engine.resolve_period('this_month')
        out = _insight_vehicles(s, e)
        self.assertEqual(out['distinct_vehicles'], 1)
        self.assertEqual(out['named_count'], 0)     # card was created without a customer name
        self.assertEqual(out['named_pct'], 0)
