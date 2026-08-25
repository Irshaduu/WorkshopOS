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
from workshop.analysis_engine import _month_end
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
        # 500 is the shop's LINE total for the row, not a rate — the quantity
        # beside it does not multiply it (see SHOP_LINE_COST).
        self.assertEqual(engine.spare_shop_expense(s, e), D('500'))

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
        # The two SHOP-route lines cost what was typed (500, 750); the warehouse
        # draw is still 400 × 3, because there the price is a per-unit average
        # taken off the shelf rather than a figure anyone typed. The partition
        # property is what this test is for, and it is unchanged: every rupee
        # lands in exactly one bucket.
        total = D('500') + D('1200') + D('750')
        self.assertEqual(
            engine.spare_shop_expense(s, e)
            + engine.warehouse_drawn_spare_cost(s, e)
            + engine.unattributed_spare_expense(s, e),
            total)
        # Only the two genuinely-unpaid routes reach the expense total.
        self.assertEqual(engine.build_profit_report(s, e)['expense_total'], D('1250'))

    def test_a_missing_quantity_changes_nothing_on_a_shop_line(self):
        """
        Renamed 2026-08-17. It used to read "a missing quantity is one unit, not
        zero", which mattered when the cost was `unit_price × quantity` and a
        NULL would have zeroed the line. A shop line is no longer multiplied by
        anything, so the quantity — present, absent or wrong — cannot move what
        the shop is owed. Kept because that is worth asserting rather than
        assuming, and because the warehouse route still coalesces a NULL to 1.
        """
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
        JobCardLabourItem.objects.create(job_card=self.card, job_description='Fitting')
        self.card.labour_amount = D('800')
        self.card.save()
        self.card.update_totals()

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
        self.assertEqual(rows[0]['cost'], D('500'))        # the shop's line total
        self.assertEqual(rows[0]['profit'], D('1800'))

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
        # Two shop lines, each costing what was typed for it: 500 + 200.
        self.assertEqual(rows[0]['cost'], D('700'))

    def test_vehicles_section_reports_customer_name_coverage(self):
        from workshop.analysis_views import _insight_vehicles
        s, e, _k, _l = engine.resolve_period('this_month')
        out = _insight_vehicles(s, e)
        self.assertEqual(out['distinct_vehicles'], 1)
        self.assertEqual(out['named_count'], 0)     # card was created without a customer name
        self.assertEqual(out['named_pct'], 0)


# =============================================================================
# THE FIXES OF 2026-08-25
#
# Everything below guards something the Profit page got WRONG on real data,
# found by reading the rendered page against the database rather than by a test
# failing. Each class names the figure that was wrong and by how much, because
# "why is this asserted" is the part that goes stale first.
# =============================================================================
_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
           'July', 'August', 'September', 'October', 'November', 'December']


class ASupplierDiscountCannotRaiseProfitTests(AnalysisBase):
    """
    A discount bigger than the bill it sits on made the Supplies Shops expense
    NEGATIVE, which *raised* reported profit — a mistyped extra zero was enough.

    `SupplierRestockBill.get_effective_amount` has always floored this at zero.
    Three aggregates on the analysis pages hand-rolled `total - discount` and
    did not, so the model and the page disagreed about the same bill.
    """

    def _impossible_bill(self):
        shop = SupplierShop.objects.create(name='Ninoos')
        cat = Category.objects.create(name='Fluids')
        item = Item.objects.create(name='5W-30', category=cat, average_stock=D('10'))
        bill = SupplierRestockBill.objects.create(supplier=shop, bill_date=self.today)
        SupplierRestockItem.objects.create(bill=bill, item=item, quantity=D('1'),
                                           total_price=D('5000'))
        # Straight to the column, the way a bad row already in the database
        # looks — the point is that the PAGE survives it, not that the form
        # allows it.
        SupplierRestockBill.objects.filter(pk=bill.pk).update(discount_amount=D('50000'))
        return shop, bill

    def test_the_expense_is_floored_at_zero_never_negative(self):
        self._impossible_bill()
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.inventory_expense(s, e), D('0'),
                         "a negative expense would raise reported profit")

    def test_the_engine_agrees_with_the_model_property(self):
        _shop, bill = self._impossible_bill()
        s, e, _k, _l = engine.resolve_period('this_month')
        bill.refresh_from_db()
        self.assertEqual(engine.inventory_expense(s, e), bill.get_effective_amount)

    def test_the_chart_cannot_disagree_with_the_headline(self):
        self._impossible_bill()
        s, e, _k, _l = engine.resolve_period('this_month')
        report = engine.build_profit_report(s, e)
        series = engine.monthly_series(s, e)
        self.assertEqual(sum((m['expenses'] for m in series), D('0')),
                         report['expense_total'])

    def test_the_shops_insight_uses_the_same_floor(self):
        self._impossible_bill()
        from workshop.analysis_views import _insight_shops
        s, e, _k, _l = engine.resolve_period('this_month')
        rows = _insight_shops(s, e)['supplier_rows']
        self.assertEqual(rows[0]['spend'], D('0'))


class AnIncompletePeriodIsComparedLikeForLikeTests(AnalysisBase):
    """
    THE NUMBER THAT SAID "DOWN" ON A WORKSHOP THAT WAS GROWING.

    `this_year` resolves to the whole calendar year, but the data only reaches
    today. Comparing that against a FULL previous year compared 8 months against
    12: the page reported "-8.5% vs previous" while turnover per trading day was
    running ~11% AHEAD, on the page profit distribution is decided from.
    """

    def test_a_part_year_is_compared_against_the_same_part_of_last_year(self):
        s, e, _k, _l = engine.resolve_period('this_year')
        prev_start, prev_end, read_to, label, partial = engine.comparison_window(s, e)
        today = timezone.localdate()
        self.assertTrue(partial)
        self.assertEqual(read_to, today,
                         "an unfinished year is only measured as far as it has data")
        self.assertEqual(prev_start, s.replace(year=s.year - 1))
        self.assertEqual(prev_end, today.replace(year=today.year - 1))
        self.assertEqual(label, 'vs same period last year')

    def test_a_part_month_is_compared_against_the_same_days_last_month(self):
        s, e, _k, _l = engine.resolve_period('this_month')
        prev_start, prev_end, read_to, label, partial = engine.comparison_window(s, e)
        self.assertTrue(partial)
        self.assertEqual(read_to, timezone.localdate())
        self.assertEqual(prev_start.day, 1)
        self.assertEqual(label, 'vs same days last month')
        self.assertEqual((prev_end.year, prev_end.month),
                         (prev_start.year, prev_start.month))

    def test_a_finished_month_compares_against_the_previous_CALENDAR_month(self):
        """
        Not "31 days earlier". July is 31 days, so the day-count version put
        Last Month's comparison at 31 May - 30 June — a window straddling two
        months, labelled as the month before.
        """
        s, e, _k, _l = engine.resolve_period('last_month')
        prev_start, prev_end, read_to, _label, partial = engine.comparison_window(s, e)
        self.assertFalse(partial)
        self.assertEqual(read_to, e)
        self.assertEqual(prev_start.day, 1)
        self.assertEqual(prev_end, s - timedelta(days=1))
        self.assertEqual((prev_start.year, prev_start.month),
                         (prev_end.year, prev_end.month),
                         "the comparison window must sit inside ONE calendar month")

    def test_a_finished_year_compares_against_the_whole_previous_year(self):
        """A leap year made the day-count version start on 2 January and quietly
        drop New Year's Day."""
        s, e, _k, _l = engine.resolve_period('last_year')
        prev_start, prev_end, _read_to, _label, _partial = engine.comparison_window(s, e)
        self.assertEqual((prev_start.month, prev_start.day), (1, 1))
        self.assertEqual((prev_end.month, prev_end.day), (12, 31))
        self.assertEqual(prev_start.year, s.year - 1)

    def test_a_mistyped_year_does_not_500_the_page(self):
        """
        `prev_start = prev_end - span` raised OverflowError off the bottom of
        the calendar. A mis-keyed year in a date box is enough, and a 500 on the
        profit page is not an acceptable answer to a typo.
        """
        for bad in ('0001-01-01', '1000-01-01'):
            s, e, _k, _l = engine.resolve_period('custom', bad, '2026-12-31')
            engine.comparison_window(s, e)          # must not raise
            r = self.client.get(reverse('analysis_dashboard'),
                                {'range': 'custom', 'start': bad, 'end': '2026-12-31'})
            self.assertEqual(r.status_code, 200, f"{bad} 500'd the profit page")


class UnsettledWagesAreNamedNotHiddenTests(AnalysisBase):
    """
    THE WARNING THAT FIRES ON THE DEFAULT VIEW, EVERY MONTH.

    A salary month is settled in the first days of the NEXT one, so for the
    whole of any month "This Month" contains a month with no settlement and its
    wages are genuinely not in the profit. Measured on real data: Rs 4,90,577 at
    a 44.4% margin with the salary line reading Rs 0, against a true wage bill
    of about Rs 1,20,000 a month — a third of the profit. All the page said was
    "0 month(s) settled".
    """

    def setUp(self):
        super().setUp()
        # One advance, so the workshop has salary history at all — the warning
        # is bounded to months at or after the first wage activity.
        SalaryAdvance.objects.create(staff=self.mech, amount=D('500'),
                                     date=self.today.replace(day=1))

    def test_the_current_month_is_named_when_it_has_no_settlement(self):
        s, e, _k, _l = engine.resolve_period('this_month')
        salary = engine.salary_expense(s, e)
        self.assertEqual(salary['unsettled_months'], [self.today.strftime('%B %Y')])

    def test_nothing_is_named_once_the_month_is_settled(self):
        SalaryPayment.objects.create(month=self.today.replace(day=1))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.salary_expense(s, e)['unsettled_months'], [])

    def test_a_future_month_is_never_called_unsettled(self):
        """`this_year` runs to 31 December. Reporting ten months that have not
        happened would bury the one that matters."""
        s, e, _k, _l = engine.resolve_period('this_year')
        for name in engine.salary_expense(s, e)['unsettled_months']:
            word, year = name.split()
            month = date(int(year), _MONTHS.index(word) + 1, 1)
            self.assertLessEqual(month, self.today.replace(day=1),
                                 f"{name} has not happened yet")

    def test_months_before_the_workshop_had_any_wages_are_not_flagged(self):
        """A window wider than the section's own history would otherwise flag
        every month up to the day Salary & Advance was first used."""
        SalaryAdvance.objects.all().delete()
        s, e, _k, _l = engine.resolve_period('this_year')
        self.assertEqual(engine.salary_expense(s, e)['unsettled_months'], [])

    def test_no_wage_figure_is_ever_invented(self):
        """The page names the gap; it never estimates into it. A number nobody
        paid inside the profit equation is how this page would go from
        incomplete to wrong."""
        s, e, _k, _l = engine.resolve_period('this_month')
        salary = engine.salary_expense(s, e)
        self.assertTrue(salary['unsettled_months'])
        self.assertEqual(salary['settled_net'], D('0'))
        self.assertEqual(salary['total'], D('500'),
                         "only the real advance, nothing estimated")

    def test_the_hint_says_what_is_MISSING_not_what_was_counted(self):
        s, e, _k, _l = engine.resolve_period('this_month')
        report = engine.build_profit_report(s, e)
        line = next(l for l in report['expense_lines'] if l['key'] == 'salary')
        self.assertIn('not settled', line['hint'])
        self.assertNotIn('month(s) settled', line['hint'])

    def test_the_page_names_the_month_on_screen(self):
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('are not in this profit', html)
        self.assertIn(self.today.strftime('%B %Y'), html)

    def test_a_midmonth_window_does_not_flag_a_month_that_IS_settled(self):
        """
        A salary month is dated the 1st, so a window that does not start on a
        1st — any mid-month custom range — excludes that month's settlement from
        `salary_expense`'s own filter. That is the dating rule working. Reading
        the gap off that same filter would then raise the banner on a month that
        HAS been settled, and a false warning on this page is worse than none.
        """
        first = self.today.replace(day=1)
        SalaryPayment.objects.create(month=first)
        start = first + timedelta(days=16)
        end = _month_end(first)
        self.assertEqual(engine.unsettled_months(start, end), [],
                         "flagged a month that was settled on the 1st")

    def test_a_midmonth_window_still_flags_a_month_that_is_NOT_settled(self):
        first = self.today.replace(day=1)
        start = first + timedelta(days=9)
        end = min(_month_end(first), self.today)
        self.assertEqual(engine.unsettled_months(start, end),
                         [first.strftime('%B %Y')])

    def test_a_wholly_future_window_says_nothing(self):
        start = date(self.today.year + 1, 1, 1)
        self.assertEqual(engine.unsettled_months(start, date(self.today.year + 1, 12, 31)), [])


class AllTimeReachesEverySalaryMonthTests(AnalysisBase):
    """
    "All Time" anchored its window to job cards, cashbook and restock bills, and
    never to salary. A salary month is dated the 1st; the earliest job card fell
    on the 17th — so the window opened on the 17th, that month's settlement sat
    outside it, and All Time reported the wage bill Rs 1,22,167 short while
    claiming to cover everything.
    """

    def test_a_settlement_before_the_first_job_card_is_still_counted(self):
        # Card mid-month, settlement on the 1st — the exact shape that failed.
        first = self.today.replace(day=1)
        JobCard.objects.all().delete()
        self.make_card(bill='1000', when=first + timedelta(days=16))
        payment = SalaryPayment.objects.create(month=first)
        SalaryPaymentLine.objects.create(payment=payment, staff=self.mech,
                                         salary_used=D('9000'), leave_days=0,
                                         advance_used=D('1000'), net_amount=D('8000'))

        start, end, _k, _l = engine.resolve_period('all_time')
        self.assertLessEqual(start, first, "All Time must reach the salary month")
        self.assertEqual(engine.salary_expense(start, end)['total'], D('9000'))

    def test_all_time_still_reaches_back_past_the_first_job_card(self):
        """The original behaviour, kept: opening stock and shop balances are
        seeded before the first card."""
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Opening',
                                     amount=D('100'),
                                     date=self.today - timedelta(days=400))
        start, _e, _k, _l = engine.resolve_period('all_time')
        self.assertLessEqual(start, self.today - timedelta(days=400))


class ABalanceThatWentTheOtherWayIsSaidInWordsTests(AnalysisBase):
    """
    A spare shop paid ahead of its purchases is in CREDIT. The tile printed the
    minus sign as-is — "We owe spare shops Rs -7,65,938" — which reads as a
    broken figure rather than a real position.
    """

    def test_an_overpaid_shop_reads_as_paid_ahead_with_a_positive_figure(self):
        SpareShop.objects.filter(pk=self.shop.pk).update(
            total_purchased_amount=D('1000'), total_paid_amount=D('4000'))
        tiles = {t['label']: t for t in engine.financial_position()['tiles']}
        self.assertIn('Spare shops paid ahead', tiles)
        self.assertEqual(tiles['Spare shops paid ahead']['amount'], D('3000'))
        self.assertEqual(tiles['Spare shops paid ahead']['direction'], 'credit')
        self.assertNotIn('We owe spare shops', tiles)

    def test_a_normal_balance_still_reads_as_owed(self):
        SpareShop.objects.filter(pk=self.shop.pk).update(
            total_purchased_amount=D('4000'), total_paid_amount=D('1000'))
        tiles = {t['label']: t for t in engine.financial_position()['tiles']}
        self.assertEqual(tiles['We owe spare shops']['amount'], D('3000'))
        self.assertEqual(tiles['We owe spare shops']['direction'], 'out')

    def test_no_tile_ever_prints_a_negative(self):
        SpareShop.objects.filter(pk=self.shop.pk).update(
            total_purchased_amount=D('0'), total_paid_amount=D('9000'))
        for t in engine.financial_position()['tiles']:
            self.assertGreaterEqual(t['amount'], D('0'),
                                    f"{t['label']} printed a minus sign")

    def test_the_page_does_not_render_a_minus_rupee_figure(self):
        SpareShop.objects.filter(pk=self.shop.pk).update(
            total_purchased_amount=D('0'), total_paid_amount=D('9000'))
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertNotIn('₹-', html)


class TheFleetLineIsASliceOfTheLineAboveItTests(AnalysisBase):
    """
    The page labels this "Of that, fleet accounts" directly under "Customers owe
    us", so it claims to be a slice of it. It was cut from a different
    population by a different expression: `BulkPayer`'s stored totals are GROSS
    of discount and span settled cards too, while `receivable` is net of
    discount over unsettled cards only.
    """

    def test_a_discounted_fleet_card_cannot_make_the_slice_exceed_the_whole(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        self.make_card(bill='10000', discount='2000', received='0',
                       payment_status='PENDING', bulk_payer=payer)
        payer.update_totals()
        pos = engine.financial_position()
        self.assertEqual(pos['fleet_due'], D('8000'), "net of discount, like receivable")
        self.assertLessEqual(pos['fleet_due'], pos['receivable'])

    def test_a_settled_fleet_card_leaves_the_slice(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        self.make_card(bill='5000', received='5000',
                       payment_status='BULK_PAID', bulk_payer=payer)
        payer.update_totals()
        self.assertEqual(engine.financial_position()['fleet_due'], D('0'))


class UnassignedShopPurchasesAreDisclosedTests(AnalysisBase):
    """
    A part ordered from a spare shop for one car, not used on it, and kept for
    the next car that needs it. It counts in `SpareShop.update_totals()` — so it
    is inside "We owe spare shops" — and `spare_shop_expense` filters
    `job_card__isnull=False`, so it is in no period's expenses. The page showed
    a debt with no cost behind it.

    NOT counting it is correct, and not merely conservative: nothing in the app
    attaches an unassigned row to a job card, so the part is fitted by typing it
    onto the card and deleting the unassigned row. Expensing it while it waits
    would therefore make a PAST month's profit MOVE on the day somebody fits the
    part — the earlier expense leaves with the deleted row. A settled month's
    profit changing weeks later is worse than a cost arriving a month late.
    """

    def _unassigned(self, amount='2500'):
        return JobCardSpareItem.objects.create(
            job_card=None, shop=self.shop, spare_part_name='Brake disc (spare)',
            source=JobCardSpareItem.SOURCE_SHOP, quantity=D('1'), unit_price=D(amount))

    def test_it_is_still_not_an_expense(self):
        self._unassigned()
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.spare_shop_expense(s, e), D('0'))
        self.assertEqual(engine.unattributed_spare_expense(s, e), D('0'))

    def test_it_is_reported_so_the_shop_balance_reconciles(self):
        self._unassigned()
        self.shop.update_totals()
        out = engine.unassigned_spare_purchases()
        self.assertEqual(out['amount'], D('2500'))
        self.assertEqual(out['count'], 1)
        # The whole point: this is exactly the part of the payable with no
        # matching expense YET.
        self.assertEqual(engine.financial_position()['payable_spare'], D('2500'))

    def test_the_page_says_so(self):
        self.make_card(bill='4000', received='4000', payment_status='PAID')
        self._unassigned()
        self.shop.update_totals()
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('not yet fitted to a car', html)

    def test_an_ordinary_period_says_nothing_about_it(self):
        """No unassigned rows, no line — the page must not carry a footnote
        about something that has not happened."""
        self.make_card(bill='4000', received='4000', payment_status='PAID')
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertNotIn('not yet fitted to a car', html)


class ArchivingAShopCannotHideWhatIsOwedTests(AnalysisBase):
    """
    `AUD-0082`. `payable_spare` filtered `is_trashed=False` and
    `payable_supplier` filtered `is_active=True`, and nothing else counted that
    money — so archiving a shop the workshop owed removed the debt from the only
    screen that reports it.

    STRICTLY WORSE THAN THE FLEET TWIN that was fixed first: a receivable that
    vanishes understates what is owed TO the workshop, but a PAYABLE that
    vanishes silently RAISES reported profit.

    Fixed on both sides at once, and both halves are load-bearing — the filter
    is gone so an already-archived shop still counts, and the archive views
    refuse a shop carrying a balance so nothing new gets into that state.
    """

    def _owed_spare_shop(self, owed='50000'):
        SpareShop.objects.filter(pk=self.shop.pk).update(
            total_purchased_amount=D(owed), total_paid_amount=D('0'))
        self.shop.refresh_from_db()
        return self.shop

    def test_an_archived_spare_shops_debt_still_counts(self):
        shop = self._owed_spare_shop()
        SpareShop.objects.filter(pk=shop.pk).update(is_trashed=True)
        self.assertEqual(engine.financial_position()['payable_spare'], D('50000'),
                         "archiving hid a real debt and raised reported profit")

    def test_an_archived_supplies_shops_debt_still_counts(self):
        SupplierShop.objects.create(name='Old Supplier', is_active=False,
                                    total_billed_amount=D('9000'),
                                    total_paid_amount=D('1000'))
        self.assertEqual(engine.financial_position()['payable_supplier'], D('8000'))

    def test_a_spare_shop_carrying_a_balance_cannot_be_archived(self):
        shop = self._owed_spare_shop()
        self.client.post(reverse('spare_shop_delete', args=[shop.pk]))
        shop.refresh_from_db()
        self.assertFalse(shop.is_trashed, "a shop still owed money was archived")

    def test_a_settled_spare_shop_archives_normally(self):
        SpareShop.objects.filter(pk=self.shop.pk).update(
            total_purchased_amount=D('5000'), total_paid_amount=D('5000'))
        self.client.post(reverse('spare_shop_delete', args=[self.shop.pk]))
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_trashed)

    def test_a_supplies_shop_carrying_a_balance_cannot_be_archived(self):
        shop = SupplierShop.objects.create(name='Ninoos',
                                           total_billed_amount=D('9000'),
                                           total_paid_amount=D('1000'))
        self.client.post(reverse('deactivate_supplier_shop', args=[shop.id]))
        shop.refresh_from_db()
        self.assertTrue(shop.is_active, "a supplier still owed money was archived")

    def test_a_shop_paid_AHEAD_can_still_be_archived(self):
        """A credit balance is not a debt — refusing it would trap a shop that
        has been overpaid and has no more purchases coming."""
        SpareShop.objects.filter(pk=self.shop.pk).update(
            total_purchased_amount=D('1000'), total_paid_amount=D('4000'))
        self.client.post(reverse('spare_shop_delete', args=[self.shop.pk]))
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.is_trashed)


# =============================================================================
# DEEP ANALYSIS — the 2026-08-25 pass over the insight sections
#
# The Profit page was audited first; these six sections were only read for
# their wording. Going through the queries turned up four more defects, all of
# the same shape: a figure that was arithmetically fine and could not be read
# correctly off the screen it appeared on.
# =============================================================================
class TheTwoSpareRoutesAreNeverMergedIntoOneListTests(AnalysisBase):
    """
    The Spares section listed 'Castrol 5W-30' and 'Brake Pads - Front' in one
    table under one Cost column, with nothing saying which shelf each came off.

    They are two different businesses. The Job Card edits them as two sections,
    the Live Report lists them as two sections, and only a shop part has a shop,
    an ordering state and a payable behind it. The COST columns are not even the
    same kind of number — a shop line's cost is the line total as typed, a
    draw's is a weighted average times quantity.
    """

    def setUp(self):
        super().setUp()
        self.card = self.make_card(bill='0')
        cat = Category.objects.create(name='Engine Oil')
        self.item = Item.objects.create(name='Castrol 5W-30', category=cat,
                                        average_stock=D('20'), avg_cost=D('500'))
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Brake Pads - Front',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('2'), unit_price=D('1000'), total_price=D('1500'))
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Castrol 5W-30',
            source=JobCardSpareItem.SOURCE_INVENTORY, item=self.item,
            quantity=D('5'), total_price=D('4000'))

    def _out(self):
        from workshop.analysis_views import _insight_spares
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_spares(s, e)

    def test_a_shop_part_never_appears_in_the_stock_table(self):
        out = self._out()
        self.assertEqual([r['item__name'] for r in out['stock_rows']], ['Castrol 5W-30'])
        self.assertNotIn('Brake Pads - Front',
                         [r['item__name'] for r in out['stock_rows']])

    def test_a_draw_never_appears_in_the_shop_table(self):
        out = self._out()
        self.assertEqual([r['name'] for r in out['shop_rows']], ['Brake Pads - Front'])

    def test_each_route_is_costed_by_its_own_rule(self):
        """The shop line cost 1,000 — the LINE TOTAL, not 1,000 x 2. The draw
        cost 500 a litre x 5."""
        out = self._out()
        self.assertEqual(out['shop_totals']['cost'], D('1000'))
        self.assertEqual(out['stock_totals']['cost'], D('2500'))

    def test_the_two_subtotals_add_back_to_the_headline(self):
        """The split may reorganise the screen; it may not change the total."""
        out = self._out()
        for key in ('revenue', 'cost', 'profit'):
            self.assertEqual(out['shop_totals'][key] + out['stock_totals'][key],
                             out['totals'][key], f"{key} does not reconcile")

    def test_the_chart_carries_the_route_so_it_can_be_read(self):
        out = self._out()
        self.assertEqual(len(out['chart_labels']), len(out['chart_is_shop']))
        by_label = dict(zip(out['chart_labels'], out['chart_is_shop']))
        self.assertTrue(by_label['Brake Pads - Front'])
        self.assertFalse(by_label['Castrol 5W-30'])

    def test_a_stock_row_is_grouped_by_the_PRODUCT_not_its_name(self):
        """
        `spare_part_name` on a draw is a SNAPSHOT taken when the part left the
        shelf, and it is not rewritten when the product is renamed. Grouping by
        it would split one product's history in two the day somebody corrects a
        spelling.
        """
        other = self.make_card(bill='0')
        JobCardSpareItem.objects.create(
            job_card=other, spare_part_name='Castrol 5W-30 (old label)',
            source=JobCardSpareItem.SOURCE_INVENTORY, item=self.item,
            quantity=D('5'), total_price=D('4000'))
        rows = self._out()['stock_rows']
        self.assertEqual(len(rows), 1, "one product split into two rows")
        self.assertEqual(rows[0]['times'], 2)

    def test_a_branded_SKU_keeps_its_real_casing(self):
        """The old display lowered the name to group it and then re-title-cased
        it, which turned 'DOT 4' into 'Dot 4' and would turn 'CR-V' into
        'Cr-V'."""
        cat = Category.objects.get(name='Engine Oil')
        item = Item.objects.create(name='Bosch Brake Oil DOT 4', category=cat,
                                   average_stock=D('8'), avg_cost=D('600'))
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Bosch Brake Oil DOT 4',
            source=JobCardSpareItem.SOURCE_INVENTORY, item=item,
            quantity=D('1'), total_price=D('1000'))
        names = [r['item__name'] for r in self._out()['stock_rows']]
        self.assertIn('Bosch Brake Oil DOT 4', names)
        self.assertNotIn('Bosch Brake Oil Dot 4', names)

    def test_an_uncosted_draw_is_counted_so_the_margin_can_be_doubted(self):
        """A draw with no cost reads as a FREE part and pushes the margin up —
        the one way this table is wrong without looking wrong."""
        JobCardSpareItem.objects.filter(source=JobCardSpareItem.SOURCE_INVENTORY)\
                                .update(unit_price=None)
        self.assertEqual(self._out()['uncosted_draws'], 1)

    def test_the_section_renders(self):
        r = self.client.get(reverse('analysis_insight_section', args=['spares']))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('Spare Parts', html)
        self.assertIn('Inventory Items', html)


class TheFleetBalanceIsCutTheSameWayTheReceivableIsTests(AnalysisBase):
    """
    The Fleet section put a "Billed" column that is NET of discount beside a
    "Balance now" taken from `BulkPayer`'s stored totals, which are GROSS of
    discount and span settled cards too. Same defect as the Profit page's fleet
    line, one screen over.
    """

    def setUp(self):
        super().setUp()
        self.payer = BulkPayer.objects.create(customer_name='Fleet Co')

    def _rows(self):
        from workshop.analysis_views import _insight_fleet
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_fleet(s, e)['rows']

    def test_a_discount_does_not_inflate_the_balance(self):
        self.make_card(bill='10000', discount='2000', received='0',
                       payment_status='PENDING', bulk_payer=self.payer)
        self.payer.update_totals()
        row = self._rows()[0]
        self.assertEqual(row['billed'], D('8000'))
        self.assertEqual(row['owed'], D('8000'), "stored totals are gross of discount")

    def test_a_settled_card_leaves_the_balance(self):
        self.make_card(bill='5000', received='5000',
                       payment_status='BULK_PAID', bulk_payer=self.payer)
        self.payer.update_totals()
        self.assertEqual(self._rows()[0]['owed'], D('0'))

    def test_it_agrees_with_the_profit_pages_fleet_line(self):
        """Two screens, one figure. If they disagree, one of them is lying."""
        self.make_card(bill='10000', discount='1000', received='2000',
                       payment_status='PARTIAL', bulk_payer=self.payer)
        self.payer.update_totals()
        self.assertEqual(sum((r['owed'] for r in self._rows()), D('0')),
                         engine.financial_position()['fleet_due'])

    def test_an_account_paid_ahead_reads_as_credit_not_a_minus(self):
        """`advance_balance` was computed and never rendered, so a fleet account
        paid ahead showed '₹0 owed' with its credit nowhere on the page."""
        self.make_card(bill='5000', received='5000',
                       payment_status='BULK_PAID', bulk_payer=self.payer)
        BulkPayer.objects.filter(pk=self.payer.pk).update(advance_balance=D('3000'))
        self.payer.update_totals()
        row = self._rows()[0]
        self.assertEqual(row['credit'], D('3000'))
        self.assertEqual(row['owed'], D('0'))

    def test_the_section_never_prints_a_minus_rupee_figure(self):
        self.make_card(bill='5000', received='5000',
                       payment_status='BULK_PAID', bulk_payer=self.payer)
        BulkPayer.objects.filter(pk=self.payer.pk).update(advance_balance=D('3000'))
        self.payer.update_totals()
        r = self.client.get(reverse('analysis_insight_section', args=['fleet']))
        self.assertNotIn('₹-', r.content.decode())


class EveryJobCardIsAccountedForInHowCustomersPaidTests(AnalysisBase):
    """
    The table excluded any card with no `payment_method`, so its Jobs column
    added to less than the job count with nothing on screen saying why. Two
    kinds of card have none — a fleet card (the method sits on the fleet
    payment) and a card nobody has settled yet — and in the demo data that was
    13 of 150, silently.
    """

    def _out(self):
        from workshop.analysis_views import _insight_operations
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_operations(s, e)

    def test_a_fleet_card_is_named_rather_than_dropped(self):
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        self.make_card(bill='5000', received='5000',
                       payment_status='BULK_PAID', bulk_payer=payer)
        out = self._out()
        self.assertEqual(out['unmethoded']['fleet'], 1)
        self.assertEqual(out['unmethoded']['unsettled'], 0)

    def test_an_unsettled_card_is_named_rather_than_dropped(self):
        self.make_card(bill='5000', received='0', payment_status='PENDING')
        out = self._out()
        self.assertEqual(out['unmethoded']['unsettled'], 1)
        self.assertEqual(out['unmethoded']['fleet'], 0)

    def test_the_rows_add_up_to_the_job_count(self):
        """The property that matters: nothing falls out of the table unseen."""
        payer = BulkPayer.objects.create(customer_name='Fleet Co')
        self.make_card(bill='5000', received='5000', payment_status='PAID',
                       payment_method='CASH')
        self.make_card(bill='5000', received='5000', payment_status='PAID',
                       payment_method='UPI')
        self.make_card(bill='5000', received='5000', payment_status='BULK_PAID',
                       bulk_payer=payer)
        self.make_card(bill='5000', received='0', payment_status='PENDING')
        out = self._out()
        counted = (sum(m['n'] for m in out['methods'])
                   + out['unmethoded']['fleet'] + out['unmethoded']['unsettled'])
        self.assertEqual(counted, out['total_cards'])


class TheShopsSectionSelectsByRouteNotByCoincidenceTests(AnalysisBase):
    """
    `spare_rows` selected "has a shop" and relied on a warehouse draw never
    having one. That is true of the data today and is not the rule — the rule is
    `source=SHOP`, and only the rule survives somebody attaching a shop
    reference to the inventory route.
    """

    def test_a_draw_carrying_a_shop_is_still_excluded(self):
        from workshop.analysis_views import _insight_shops
        card = self.make_card(bill='0')
        cat = Category.objects.create(name='Engine Oil')
        item = Item.objects.create(name='Castrol 5W-30', category=cat,
                                   average_stock=D('10'), avg_cost=D('500'))
        row = JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Castrol 5W-30',
            source=JobCardSpareItem.SOURCE_INVENTORY, item=item,
            quantity=D('5'), total_price=D('4000'))
        # Straight to the column: the shape the filter has to survive.
        JobCardSpareItem.objects.filter(pk=row.pk).update(shop=self.shop)

        s, e, _k, _l = engine.resolve_period('this_month')
        out = _insight_shops(s, e)
        self.assertEqual(out['spare_total'], D('0'),
                         "a warehouse draw was counted as a spare-shop purchase")

    def test_the_spend_matches_the_profit_pages_spare_shops_expense(self):
        from workshop.analysis_views import _insight_shops
        card = self.make_card(bill='0')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Pads',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('2'), unit_price=D('1000'), total_price=D('1500'))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(_insight_shops(s, e)['spare_total'],
                         engine.spare_shop_expense(s, e))

    def test_parts_not_yet_fitted_are_disclosed_on_the_shops_row(self):
        """"Owed now" counts them and "Spent" cannot, so the two columns look
        like they should reconcile and do not."""
        from workshop.analysis_views import _insight_shops
        card = self.make_card(bill='0')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Pads',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=D('1000'), total_price=D('1500'))
        JobCardSpareItem.objects.create(
            job_card=None, spare_part_name='Spare disc',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=D('2500'))
        s, e, _k, _l = engine.resolve_period('this_month')
        row = _insight_shops(s, e)['spare_rows'][0]
        self.assertEqual(row['spend'], D('1000'))
        self.assertEqual(row['waiting'], D('2500'))
