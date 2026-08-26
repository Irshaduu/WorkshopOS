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

import io
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
    SpareShopPayment,
)
from inventory.models import (
    Category, Item, SupplierShop, SupplierRestockBill, SupplierRestockItem,
    SupplierPayment,
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
    A part is charged EXACTLY ONCE, at the moment it is fitted to a car:

      source=SHOP + a shop     → the Spare Shops expense
      source=SHOP, no shop     → real money with no payee, its own line
      source=INVENTORY         → the Inventory Used expense, at shelf cost

    If this class starts failing, the Profit page has begun charging the
    workshop twice for the same part. Do not "fix" it by counting both.

    ⚠ THE SECOND HELPING TO GUARD AGAINST IS THE RESTOCK BILL (changed
    2026-08-25). Until then the BILL was the expense and the draw was excluded;
    now it is the other way round, because the spare-shop route had always
    charged parts when they were FITTED and the warehouse route was the odd one
    out. The invariant is the same shape — every rupee of parts cost in exactly
    one bucket — it just names a different thing that must not be added on top.
    `test_a_restock_bill_is_not_an_expense_by_itself` is that guard.

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

    def test_a_warehouse_draw_is_charged_at_shelf_cost_when_it_is_FITTED(self):
        """source=INVENTORY ⇒ charged here, once, at the weighted average."""
        JobCardSpareItem.objects.create(
            job_card=self.card, source=JobCardSpareItem.SOURCE_INVENTORY, item=self.item,
            quantity=D('3'), unit_price=D('400'), total_price=D('1800'))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.spare_shop_expense(s, e), D('0'))
        self.assertEqual(engine.unattributed_spare_expense(s, e), D('0'))
        self.assertEqual(engine.warehouse_drawn_spare_cost(s, e), D('1200'))
        # 400 x 3, and it reaches the total by exactly one door.
        self.assertEqual(engine.build_profit_report(s, e)['expense_total'], D('1200'))

    def test_a_restock_bill_is_not_an_expense_by_itself(self):
        """
        THE DOUBLE COUNT THIS PAGE NOW HAS TO GUARD AGAINST.

        Buying stock turns cash (or a promise to pay) into goods on a shelf. It
        is not a cost until the goods are used, and the draw above is what
        charges them. Adding the bill on top charges one delivery twice.
        """
        from inventory.models import SupplierRestockBill, SupplierRestockItem, SupplierShop
        supplier = SupplierShop.objects.create(name='Bulk Oils')
        bill = SupplierRestockBill.objects.create(supplier=supplier, bill_date=self.today)
        SupplierRestockItem.objects.create(bill=bill, item=self.item,
                                           quantity=D('50'), total_price=D('25000'))
        s, e, _k, _l = engine.resolve_period('this_month')

        # Billed, and reported — but nothing was fitted to a car, so nothing
        # was spent on doing work.
        self.assertEqual(engine.supplier_billed(s, e), D('25000'))
        self.assertEqual(engine.build_profit_report(s, e)['expense_total'], D('0'))

        # It moved the payable instead. That is where a purchase belongs.
        self.assertEqual(engine.financial_position()['payable_supplier'], D('25000'))

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
        # ALL THREE reach the expense total now, because all three are parts
        # fitted to a car in this period. The bucket totals and the expense
        # total are the same number seen twice.
        self.assertEqual(engine.build_profit_report(s, e)['expense_total'], total)

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
class SupplierBilledTests(AnalysisBase):
    """
    `supplier_billed()` reports what the Supplies Shops billed in a window. It
    is NOT a profit stream — see `DoubleCountRuleTests` — but the floored
    expression behind it still has to be right, because the shop's own balance
    and the payment waterfall read the same declaration.
    """

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
        self.assertEqual(engine.supplier_billed(s, e), D('1800'))


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
        self.assertEqual(engine.supplier_billed(s, e), D('0'),
                         "a negative expense would raise reported profit")

    def test_the_engine_agrees_with_the_model_property(self):
        _shop, bill = self._impossible_bill()
        s, e, _k, _l = engine.resolve_period('this_month')
        bill.refresh_from_db()
        self.assertEqual(engine.supplier_billed(s, e), bill.get_effective_amount)

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

    def test_the_shops_own_BALANCE_uses_the_same_floor(self):
        """
        THE FOURTH COPY, and the one left behind.

        `SupplierShop.update_totals()` hand-rolled `total − discount` with no
        floor, so an underwater bill SUBTRACTED from the shop's balance: real
        debt on its other bills read as smaller than it is, or vanished. That
        is the payable understating what is owed — and `deactivate_supplier_shop`
        reads this figure, so it would also let a shop the workshop still owes
        be archived.
        """
        shop, bill = self._impossible_bill()
        # A second, ordinary bill: the whole point is that the broken one
        # cannot eat into what this one genuinely owes.
        good = SupplierRestockBill.objects.create(supplier=shop, bill_date=self.today)
        SupplierRestockItem.objects.create(
            bill=good, item=Item.objects.first(), quantity=D('1'), total_price=D('8000'))
        shop.refresh_from_db()
        self.assertEqual(shop.total_billed_amount, D('8000'))
        self.assertEqual(shop.get_pending_balance, D('8000'))

    def test_the_payable_tile_agrees_with_the_expense_expression(self):
        """The model and the Profit page must not describe one bill two ways."""
        shop, _bill = self._impossible_bill()
        shop.refresh_from_db()
        self.assertEqual(engine.financial_position()['payable_supplier'],
                         shop.get_pending_balance)


class ThreeDatesThreeJobsTests(AnalysisBase):
    """
    THE SCENARIO THE OWNER ASKED ABOUT, end to end.

    A Supplies Shop delivers, the workshop pays in instalments over following
    months, and mechanics draw the stock down all the while — so one physical
    delivery carries THREE different dates. Each has exactly one job and they
    never overlap:

        bill date     the cost enters the equation (Total Expenses)
        draw date     the cost enters the margin view (Inventory margin)
        payment date  moves the payable ONLY — it never touches profit

    That last one is the one that looks wrong and is right: paying a supplier
    converts a liability into cash out. It changes what you owe, not what you
    earned. `SupplierPayment` appears nowhere in `analysis_engine`, and these
    tests are what keeps it that way.
    """

    def setUp(self):
        super().setUp()
        self.shop = SupplierShop.objects.create(name='Bulk Oils')
        cat = Category.objects.create(name='Engine Oil')
        self.item = Item.objects.create(name='Castrol 5W-30', category=cat,
                                        average_stock=D('50'))
        self.m1 = self.today.replace(day=1) - timedelta(days=1)      # last month
        self.m1 = self.m1.replace(day=1)
        self.m2 = self.today.replace(day=1)                          # this month

        # MONTH 1 — the delivery. 40 units at ₹500 = ₹20,000 billed, unpaid.
        self.bill = SupplierRestockBill.objects.create(
            supplier=self.shop, bill_date=self.m1)
        SupplierRestockItem.objects.create(
            bill=self.bill, item=self.item, quantity=D('40'), total_price=D('20000'))

    def _window(self, day):
        return day, _month_end(day)

    def _report(self, day):
        s, e = self._window(day)
        return engine.build_profit_report(s, e)

    def _draw(self, day, qty, charged):
        card = self.make_card(bill='0', when=day)
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Castrol 5W-30',
            source=JobCardSpareItem.SOURCE_INVENTORY, item=self.item,
            quantity=D(qty), total_price=D(charged))
        card.update_totals()
        return card

    def test_a_delivery_nobody_has_used_yet_costs_NOTHING(self):
        """
        Month 1 took a ₹20,000 delivery and fitted none of it. Buying stock
        turns cash into goods on a shelf; it is not a cost until the goods are
        used. So month 1's expense is ₹0 and the ₹20,000 sits in the payable
        and on the shelf, where it belongs.
        """
        r1 = self._report(self.m1)
        self.assertEqual(
            [l['amount'] for l in r1['expense_lines'] if l['key'] == 'inventory'][0], D('0'))
        self.assertEqual(engine.supplier_billed(*self._window(self.m1)), D('20000'))
        self.assertEqual(engine.financial_position()['payable_supplier'], D('20000'))

    def test_paying_the_instalment_changes_the_payable_and_not_the_profit(self):
        """
        THE ONE THAT LOOKS WRONG AND IS RIGHT. An instalment is a liability
        turning into cash out; it is not a cost being incurred a second time.
        """
        before = self._report(self.m2)
        owed_before = engine.financial_position()['payable_supplier']

        SupplierPayment.objects.create(supplier=self.shop, amount=D('12000'),
                                       date=self.today)
        self.shop.update_totals()

        after = self._report(self.m2)
        self.assertEqual(after['profit'], before['profit'],
                         'a supplier instalment moved the profit')
        self.assertEqual(after['expense_total'], before['expense_total'])
        self.assertEqual(engine.financial_position()['payable_supplier'],
                         owed_before - D('12000'))

    def test_the_cost_lands_in_the_month_the_stock_was_USED(self):
        """
        70% drawn in month 2, against a bill dated month 1. The cost belongs to
        month 2, with the revenue those parts earned — which is the whole
        point of the basis. Month 1 carries nothing.
        """
        self._draw(self.m2 + timedelta(days=2), '28', '22400')

        r1, r2 = self._report(self.m1), self._report(self.m2)
        line = lambda r: [l['amount'] for l in r['expense_lines']
                          if l['key'] == 'inventory'][0]

        self.assertEqual(line(r1), D('0'))
        self.assertEqual(line(r2), D('14000'))      # 28 units x ₹500

        # And BOTH months close with no reconciling line, because both halves
        # of the page now charge stock at the same moment.
        for r in (r1, r2):
            self.assertEqual(r['earnings']['profit'], r['profit'])
            self.assertEqual([x['key'] for x in r['earnings']['spend']],
                             ['salary', 'cashbook'])

    def test_the_remaining_stock_is_still_on_the_shelf_and_still_valued(self):
        """The 30% nobody has used yet is an asset, not a loss."""
        self._draw(self.m2 + timedelta(days=2), '28', '22400')
        pos = engine.financial_position()
        self.assertEqual(pos['stock_value'], D('6000'))       # 12 units x ₹500
        self.assertEqual(pos['uncosted_products'], 0)

    def test_a_second_delivery_before_the_first_is_paid_keeps_both_straight(self):
        """
        The loop the owner described: they come again before the last bill is
        settled. The payable carries both; neither delivery is an expense until
        it is used; the average cost is a full date-ordered replay, so the earlier
        draw is NOT re-priced by the later delivery.
        """
        self._draw(self.m2 + timedelta(days=2), '28', '22400')
        second = SupplierRestockBill.objects.create(
            supplier=self.shop, bill_date=self.m2 + timedelta(days=5))
        SupplierRestockItem.objects.create(
            bill=second, item=self.item, quantity=D('40'), total_price=D('40000'))

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.get_pending_balance, D('60000'))

        # The month-2 draw was priced off month-1 receipts and stays there.
        drawn = JobCardSpareItem.objects.get(source=JobCardSpareItem.SOURCE_INVENTORY)
        drawn.refresh_from_db()
        self.assertEqual(drawn.unit_price, D('500'),
                         'a later delivery re-priced an earlier draw')
        r2 = self._report(self.m2)
        self.assertEqual(r2['earnings']['profit'], r2['profit'])


class WhatWeOweAndWhatWeHoldSitTogetherTests(AnalysisBase):
    """
    The owner's question, in their words: "we have to pay Supplies Shops
    ₹1,00,000, but we have ₹1,20,000 worth of stock in the workshop." Both
    figures existed and lived on two different pages — the payable on Profit,
    the stock value in Deep Analysis — so the comparison could not be made.

    ⚠ THEY ARE STATED, NEVER NETTED. There is no accounting identity between
    them: the payable covers every unpaid bill whether or not those goods are
    still on the shelf, and the shelf holds goods from bills long since paid.
    A "net" figure would be arithmetic on two numbers that do not belong to
    each other.
    """

    def setUp(self):
        super().setUp()
        shop = SupplierShop.objects.create(name='Bulk Oils')
        cat = Category.objects.create(name='Engine Oil')
        self.item = Item.objects.create(name='Castrol 5W-30', category=cat,
                                        average_stock=D('50'))
        bill = SupplierRestockBill.objects.create(supplier=shop, bill_date=self.today)
        SupplierRestockItem.objects.create(
            bill=bill, item=self.item, quantity=D('40'), total_price=D('20000'))
        SupplierPayment.objects.create(supplier=shop, amount=D('8000'), date=self.today)
        shop.update_totals()

        # A JOB CARD, because a stock purchase is no longer "activity". Buying
        # stock is not an expense, so a period containing only a delivery has a
        # turnover and an expense total of ₹0 — and the page correctly shows its
        # "Nothing recorded" empty state, which hides every card including this
        # one. That is right, and it means a fixture testing what the page
        # PRINTS has to contain real work.
        card = self.make_card(bill='6000', received='6000')
        card.labour_amount = D('6000')
        card.save()

    def _tiles(self):
        return {t['label']: t for t in engine.financial_position()['tiles']}

    def test_both_figures_are_on_the_profit_page_together(self):
        tiles = self._tiles()
        self.assertEqual(tiles['We owe supplies shops']['amount'], D('12000'))
        self.assertEqual(tiles['Stock on the shelf']['amount'], D('20000'))

    def test_the_page_prints_them_and_says_the_stock_is_at_cost(self):
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('We owe supplies shops', html)
        self.assertIn('Stock on the shelf', html)
        self.assertIn('at what it cost', html)

    def test_nothing_nets_the_two(self):
        """A difference between them would be arithmetic on unrelated numbers."""
        pos = engine.financial_position()
        for key in pos:
            self.assertNotIn('net', key.lower())
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        # ₹8,000 is stock 20,000 − payable 12,000. It must appear nowhere.
        self.assertNotIn('₹8,000</div>', html)

    def test_the_shelf_and_the_inventory_section_quote_one_figure(self):
        from workshop.analysis_views import _insight_inventory
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(_insight_inventory(s, e)['stock_value']['value'],
                         engine.financial_position()['stock_value'])

    def test_an_uncosted_product_is_left_out_and_SAID_so_on_the_tile(self):
        """
        Left out because "we don't know" is the honest answer and ₹0 is a wrong
        one — but a shelf that reads low with nothing saying why is worse than
        either. Expect this on go-live day.
        """
        cat = Category.objects.get(name='Engine Oil')
        Item.objects.create(name='Opening Stock Oil', category=cat,
                            average_stock=D('20'), avg_cost=D('0'),
                            current_stock=D('8'))
        tile = self._tiles()['Stock on the shelf']
        self.assertEqual(tile['amount'], D('20000'))
        self.assertEqual(tile['uncosted_products'], 1)
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('not yet costed, left out', html)

    def test_a_shelf_that_went_negative_is_said_in_words_like_every_other_tile(self):
        """Overdrawn stock means Supplies Shop bills are missing, not that the
        workshop holds a negative asset. No tile on this card prints a minus."""
        Item.objects.filter(pk=self.item.pk).update(current_stock=D('-4'))
        tile = self._tiles()['Stock recorded short']
        self.assertEqual(tile['amount'], D('2000'))

        # SCOPED TO THE POSITION CARD, not the whole page. This fixture has a
        # ₹20,000 restock bill and no revenue, so the period is a genuine LOSS
        # and the hero prints −₹20,000 — which is correct and has its own test.
        # The rule being asserted is that no BALANCE prints a minus.
        # SPLIT ON THE SECTION COMMENT, not on the card's own heading. The
        # earnings card's pointer line names "Position Right Now" too, so
        # splitting on the phrase now slices at that sentence and hands back
        # everything AFTER it — which no longer contains the tiles.
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        card = html.split('<!-- ── Position right now')[1].split('pf-cta')[0]
        self.assertNotIn('₹-', card)
        self.assertIn('Stock recorded short', card)

    def test_the_expense_lines_say_what_date_they_are_counted_on(self):
        """
        Both shops are paid in instalments and both have a payment screen of
        their own, so a parts line beside a ledger showing a different figure
        paid this month invites exactly the wrong reading.
        """
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('Parts taken off the warehouse shelf', html)
        self.assertIn('Parts bought per job, not payments', html)
        # And the bill itself is nowhere in the expense list — it raised the
        # payable and the shelf, which is what a purchase does.
        self.assertNotIn('Restock bills', html)


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
class TheTwoSpareRoutesAreTwoSectionsTests(AnalysisBase):
    """
    The Spares section listed 'Castrol 5W-30' and 'Brake Pads - Front' in one
    table under one Cost column, with nothing saying which shelf each came off.
    Splitting the TABLES (2026-08-25) fixed most of it and left the headline
    above them merged, so a per-job trading margin was still being averaged
    against a shelf margin that depends on `avg_cost` being right.

    They are two different businesses. The Job Card edits them as two sections,
    the Live Report lists them as two sections, only a shop part has a shop, an
    ordering state and a payable behind it — and the OWNER names them
    separately when asked what the workshop earns from. So they are two
    sections, each with its own honest headline.

    The COST columns are not even the same kind of number: a shop line's cost
    is the line total as typed, a draw's is a weighted average times quantity.
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

    def _shop(self):
        from workshop.analysis_views import _insight_spare_parts
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_spare_parts(s, e)

    def _stock(self):
        from workshop.analysis_views import _insight_inventory
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_inventory(s, e)

    def test_a_shop_part_never_appears_in_the_stock_section(self):
        names = [r['item__name'] for r in self._stock()['rows']]
        self.assertEqual(names, ['Castrol 5W-30'])
        self.assertNotIn('Brake Pads - Front', names)

    def test_a_draw_never_appears_in_the_shop_section(self):
        self.assertEqual([r['name'] for r in self._shop()['rows']],
                         ['Brake Pads - Front'])

    def test_each_route_is_costed_by_its_own_rule(self):
        """The shop line cost 1,000 — the LINE TOTAL, not 1,000 x 2. The draw
        cost 500 a litre x 5."""
        self.assertEqual(self._shop()['totals']['cost'], D('1000'))
        self.assertEqual(self._stock()['totals']['cost'], D('2500'))

    def test_the_two_sections_add_back_to_every_part_fitted(self):
        """
        The split may reorganise the screen; it may not change the total. Both
        sections read `engine.parts_trading`, so this also pins that the two
        sides partition the spare rows exactly — no row counted twice, none
        dropped.
        """
        s, e, _k, _l = engine.resolve_period('this_month')
        every_row = engine._live_spares(s, e)
        shop, stock = self._shop()['totals'], self._stock()['totals']
        self.assertEqual(shop['revenue'] + stock['revenue'],
                         engine._sum(every_row, 'total_price'))
        self.assertEqual(shop['cost'] + stock['cost'],
                         engine._sum(every_row, engine.SPARE_COST))
        self.assertEqual(shop['lines'] + stock['lines'], every_row.count())

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
        rows = self._stock()['rows']
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
        names = [r['item__name'] for r in self._stock()['rows']]
        self.assertIn('Bosch Brake Oil DOT 4', names)
        self.assertNotIn('Bosch Brake Oil Dot 4', names)

    def test_an_uncosted_draw_is_counted_so_the_margin_can_be_doubted(self):
        """A draw with no cost reads as a FREE part and pushes the margin up —
        the one way this table is wrong without looking wrong."""
        JobCardSpareItem.objects.filter(source=JobCardSpareItem.SOURCE_INVENTORY)\
                                .update(unit_price=None)
        self.assertEqual(self._stock()['uncosted_draws'], 1)

    def test_a_shop_purchase_with_no_shop_is_named_on_the_shop_section(self):
        """
        It IS inside this section's cost — every SOURCE_SHOP row is — and it is
        NOT inside the Profit page's Spare Shops line, which splits it out as
        "Other Spare Purchases". Without the count on screen the two pages
        quote different spare-shop costs for one period and nothing says why.
        """
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Mystery Part',
            source=JobCardSpareItem.SOURCE_SHOP, shop=None,
            quantity=D('1'), unit_price=D('300'), total_price=D('400'))
        out = self._shop()
        self.assertEqual(out['no_shop'], 1)
        self.assertEqual(out['totals']['cost'], D('1300'))

    def test_both_sections_render(self):
        for key, must_say in (('spare_parts', 'Brake Pads - Front'),
                              ('inventory', 'Castrol 5W-30')):
            r = self.client.get(reverse('analysis_insight_section', args=[key]))
            self.assertEqual(r.status_code, 200, key)
            self.assertIn(must_say, r.content.decode(), key)

    def test_the_old_merged_section_is_gone(self):
        """A stale bookmark or a half-updated link must 404, not render an
        empty page that looks like a period with no parts in it."""
        r = self.client.get(reverse('analysis_insight_section', args=['spares']))
        self.assertEqual(r.status_code, 404)


class TheMostUsedChartIsItsOwnQuestionTests(AnalysisBase):
    """
    The merged section built its "Parts That Move" chart by re-sorting the
    fifteen rows it had already cut by PROFIT. So a cheap part fitted to every
    car in the workshop could not appear in a chart of what moves unless it
    also happened to be one of the fifteen biggest earners — the chart was
    answering "which of the top earners is used most" under a heading that says
    something else.

    It is its own query now, over the whole route, ordered by how often the
    part was used.
    """

    def setUp(self):
        super().setUp()
        card = self.make_card(bill='0')
        # One part that EARNS a lot and is used once.
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Turbocharger',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=D('1000'), total_price=D('90000'))
        # Enough distinct middling parts to fill the 15-row table, so the cheap
        # workhorse below is pushed off it entirely.
        for i in range(20):
            JobCardSpareItem.objects.create(
                job_card=card, spare_part_name=f'Filler Part {i:02d}',
                source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
                quantity=D('1'), unit_price=D('100'), total_price=D('900'))
        # The workhorse: used more than anything else, earns almost nothing.
        for _ in range(30):
            JobCardSpareItem.objects.create(
                job_card=card, spare_part_name='Washer',
                source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
                quantity=D('1'), unit_price=D('5'), total_price=D('6'))

    def _out(self):
        from workshop.analysis_views import _insight_spare_parts
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_spare_parts(s, e)

    def test_the_most_used_part_is_in_the_chart_even_though_it_earns_least(self):
        out = self._out()
        self.assertNotIn('Washer', [r['name'] for r in out['rows']],
                         'fixture is wrong — the workhorse must be off the table')
        self.assertEqual(out['movers'][0]['label'], 'Washer')
        self.assertEqual(out['movers'][0]['times'], 30)

    def test_the_chart_is_ordered_by_use_not_by_money(self):
        times = [r['times'] for r in self._out()['movers']]
        self.assertEqual(times, sorted(times, reverse=True))


class TheProfitIsAlsoSaidTheOwnersWayTests(AnalysisBase):
    """
    The owner does not think "turnover minus expenses". They think: labour,
    the margin on parts bought per job, the margin on parts off the shelf, and
    the odd bit of scrap income — less the running costs.

    Both are true of the same workshop, so the page states both. The whole
    safety of the second one is that IT LANDS ON THE SAME PROFIT: an owner who
    subtracts the wage bill and the cashbook from a bare "Gross earnings" would
    land somewhere else, because the two views disagree about WHEN warehouse
    stock is expensed.
    """

    def setUp(self):
        super().setUp()
        cat = Category.objects.create(name='Engine Oil')
        self.item = Item.objects.create(name='Castrol 5W-30', category=cat,
                                        average_stock=D('20'), avg_cost=D('500'))
        self.card = self.make_card(bill='20000', discount='1500', received='18500')
        self.card.labour_amount = D('8000')
        self.card.save()
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Brake Pads',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('2'), unit_price=D('3000'), total_price=D('7000'))
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Castrol 5W-30',
            source=JobCardSpareItem.SOURCE_INVENTORY, item=self.item,
            quantity=D('5'), unit_price=D('500'), total_price=D('5000'))
        CashbookEntry.objects.create(entry_type='INCOME', category='Scrap',
                                     amount=D('2000'), date=self.today)
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Electricity',
                                     amount=D('3000'), date=self.today)

    def _report(self):
        s, e, _k, _l = engine.resolve_period('this_month')
        return engine.build_profit_report(s, e)

    def test_the_breakdown_lands_on_the_headline_profit(self):
        rep = self._report()
        self.assertEqual(rep['earnings']['profit'], rep['profit'])

    def test_it_still_reconciles_when_a_discount_was_given(self):
        """
        The discount is the easy thing to leave out, and the identity does not
        close without it: it is given on the WHOLE bill, so it belongs to
        neither the labour line nor either margin and has to be its own row.
        """
        rep = self._report()
        self.assertEqual(rep['bills']['discount'], D('1500'))
        keys = [r['key'] for r in rep['earnings']['earn']]
        self.assertIn('discount', keys)
        self.assertEqual(rep['earnings']['profit'], rep['profit'])

    def test_it_still_reconciles_when_a_shop_purchase_has_no_shop(self):
        """
        `unattributed_spare_expense` is its own EXPENSE line on the equation and
        is already inside the shop MARGIN here, because `parts_trading` costs
        every SOURCE_SHOP row whether or not a shop was named. Deducting it a
        second time would understate profit by that amount.
        """
        JobCardSpareItem.objects.create(
            job_card=self.card, spare_part_name='Mystery Part',
            source=JobCardSpareItem.SOURCE_SHOP, shop=None,
            quantity=D('1'), unit_price=D('900'), total_price=D('1200'))
        self.card.update_totals()
        rep = self._report()
        self.assertTrue(any(l['key'] == 'other_spares' for l in rep['expense_lines']))
        self.assertEqual(rep['earnings']['profit'], rep['profit'])

    def test_the_four_income_streams_are_the_ones_the_owner_named(self):
        earn = {r['key'] for r in self._report()['earnings']['earn']}
        self.assertEqual(earn,
                         {'labour', 'spare_margin', 'stock_margin',
                          'cashbook_income', 'discount'})

    def test_labour_comes_off_the_card_not_off_its_job_lines(self):
        """
        `JobCardLabourItem.amount` is a dormant column — work is quoted whole
        and the figure lives on the card. Summing the lines would report every
        card created since that change as ₹0 of labour.
        """
        # `amount` is the dormant column, written here on purpose: if labour
        # ever went back to summing the lines, this row would make the figure
        # 9,500 instead of 8,000 and the test would catch it.
        JobCardLabourItem.objects.create(job_card=self.card,
                                         job_description='Oil change',
                                         amount=D('1500'))
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertEqual(engine.labour_revenue(s, e), D('8000'))

    def test_there_is_NO_reconciling_line_between_the_two_halves(self):
        """
        THE POINT OF THE WHOLE CARD.

        It first shipped with a "stock movement" line at the bottom, converting
        between an equation that charged a Supplies Shop BILL and a card that
        charged stock when it was USED. It reconciled to the rupee and the
        owner's verdict was "I am more confused now" — a page that has to
        explain itself to itself is a page nobody trusts.

        Both halves now charge stock at the same moment, so `gross − salary −
        cashbook` IS the profit. If a third row ever reappears here, the two
        bases have drifted apart and that is the bug.
        """
        supplier = SupplierShop.objects.create(name='Bulk Oils')
        bill = SupplierRestockBill.objects.create(supplier=supplier, bill_date=self.today)
        # `per_unit_price` is a read-only PROPERTY — the stored column is the
        # line total, which is what the discount apportionment divides.
        SupplierRestockItem.objects.create(bill=bill, item=self.item,
                                           quantity=D('40'), total_price=D('20000'))
        rep = self._report()
        self.assertEqual([r['key'] for r in rep['earnings']['spend']],
                         ['salary', 'cashbook'])
        self.assertEqual(
            rep['earnings']['gross'] - rep['salary']['total'] - rep['cashbook']['total'],
            rep['profit'])

    def test_a_big_delivery_does_not_move_the_profit_at_all(self):
        """
        The lumpiness that made this basis worth changing. A ₹20,000 delivery
        nobody has used is cash converted into goods, not a cost — so the month
        it lands in reads exactly as it would have without it.
        """
        before = self._report()['profit']
        supplier = SupplierShop.objects.create(name='Bulk Oils')
        bill = SupplierRestockBill.objects.create(supplier=supplier, bill_date=self.today)
        SupplierRestockItem.objects.create(bill=bill, item=self.item,
                                           quantity=D('40'), total_price=D('20000'))
        self.assertEqual(self._report()['profit'], before)

    def test_no_row_in_the_breakdown_ever_prints_a_negative_rupee_figure(self):
        rep = self._report()
        for row in rep['earnings']['earn'] + rep['earnings']['spend']:
            self.assertGreaterEqual(row['amount'], 0, row['label'])

    def test_the_comparison_report_skips_it_rather_than_computing_it_twice(self):
        """`disclosures=False` is the footnote-only path — the comparison
        report reads nothing but turnover and profit."""
        s, e, _k, _l = engine.resolve_period('this_month')
        self.assertIsNone(engine.build_profit_report(s, e, disclosures=False)['earnings'])

    def test_the_page_prints_it_and_the_two_profits_agree(self):
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('What Earned The Profit', html)
        self.assertIn('Gross Earnings', html)
        rep = self._report()
        # The same figure has to appear for the hero and for the breakdown's
        # last line, or the page contradicts itself in the owner's own words.
        from workshop.templatetags.custom_filters import inr
        self.assertGreaterEqual(html.count(inr(rep['profit'])), 2)

    def test_the_subtitle_names_the_figure_rather_than_describing_itself(self):
        """
        It read "Same profit, by what earned it" — true, and only legible once
        you already knew what the card was for. Printing the profit itself says
        the same thing in a form that needs no explaining: the reader can see
        it matches the hero above.
        """
        from workshop.templatetags.custom_filters import inr
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        rep = self._report()
        self.assertIn(f"The same ₹{inr(rep['profit'])}, broken down", html)
        self.assertNotIn('Same profit, by what earned it', html)

    def test_both_deductions_are_real_running_costs(self):
        """
        There is no sub-heading over them, and now nothing that would need one:
        both rows below Gross Earnings are money that genuinely went out this
        period. The row that was neither — the stock-basis conversion — is gone
        with the basis mismatch that created it.
        """
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertNotIn('pf-block', html)
        for row in self._report()['earnings']['spend']:
            self.assertTrue(row['negative'], f"{row['label']} is not a cost")


class OneWordOneMeaningAcrossBothPagesTests(AnalysisBase):
    """
    FOUR different figures were all called "Profit" across two pages an owner
    reads in one sitting: the bottom line, a car's gross profit, a mechanic's,
    and a parts trading margin. `test_it_is_never_called_plain_profit` already
    fixed the car profile; its neighbours had drifted.

      Profit        the bottom line — the Profit page's word alone
      Gross profit  revenue − parts cost (car profiles, mechanics)
      Margin        parts sold − parts cost (spare parts, inventory, shops)
    """

    def setUp(self):
        super().setUp()
        card = self.make_card(bill='5000')
        card.labour_amount = D('2000')
        card.save()
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Pads',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=D('2000'), total_price=D('3000'))

    def _section(self, key):
        return self.client.get(
            reverse('analysis_insight_section', args=[key])).content.decode()

    def test_a_mechanics_figure_is_called_GROSS_profit(self):
        """The identical calculation a car profile prints, where the word is
        already fixed. Wages are not in it and cannot be."""
        html = self._section('mechanics')
        self.assertIn('Gross profit', html)
        self.assertNotIn('<th class="num">Profit</th>', html)

    def test_a_parts_trading_figure_is_called_MARGIN(self):
        for key in ('spare_parts', 'inventory', 'shops'):
            html = self._section(key)
            self.assertIn('Margin', html, key)
            self.assertNotIn('<th class="num">Profit</th>', html, key)

    def test_PAID_means_cash_and_SPEND_means_cost_in_every_section(self):
        """
        The second word-collision on these pages, and the one an owner hits
        first. Spare Parts labelled its COST tile "Paid to shops" while Shops
        labels actual CASH OUT "Paid to spare shops" - on the demo data those
        read 1.85L and 6L, so the same word carried two meanings and two
        figures on one screen. Worse, the Shops section's own footnote defines
        Paid as cash, so the page contradicted its own glossary.

        The two are deliberately different numbers - shops are settled in
        instalments, so what was bought and what was paid rarely land in one
        month - which means the WORD is the only thing telling them apart.

        Scans the templates rather than a rendered page, so a section added
        later cannot reintroduce it.
        """
        import glob
        import os
        import re

        offenders = []
        for path in sorted(glob.glob(
                'workshop/templates/workshop/analysis/sections/*.html')):
            body = io.open(path, encoding='utf-8').read()
            # Strip {% comment %} blocks - they discuss the rule by name.
            body = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}',
                          '', body, flags=re.S)
            for label in re.findall(r'<div class="k">([^<]{0,40}?)</div>', body):
                if 'paid' not in label.lower():
                    continue
                # A "Paid" tile must render a cash field, never a cost/spend one.
                if os.path.basename(path) != 'shops.html':
                    offenders.append((os.path.basename(path), label.strip()))

        self.assertEqual(
            offenders, [],
            'Only the Shops section may label a figure "Paid", because only it '
            'reports cash that actually left. These tiles say Paid outside it: '
            f'{offenders}')

    def test_no_insight_section_uses_the_bare_word_as_a_column(self):
        """
        Scans the templates rather than one rendered page, so a section added
        later cannot quietly reintroduce a fourth meaning of "Profit" on a
        screen the owner reads beside the real one.
        """
        import glob
        offenders = [
            path for path in glob.glob(
                'workshop/templates/workshop/analysis/sections/*.html')
            if '<th class="num">Profit</th>' in
               open(path, encoding='utf-8').read()
        ]
        self.assertEqual(offenders, [],
                         'an insight section is calling something plain "Profit" again')


class TheCashbookBreakdownLivesInDeepAnalysisTests(AnalysisBase):
    """
    It was the one open-ended drill-down on a page whose rule is that it has
    none — a collapsed tail and a "Show all" button between the owner and the
    position tiles, on a page read for one thing.

    Nothing was lost: the Cashbook page lists ENTRIES and has never totalled
    them by category, so this is the only place that view exists.
    """

    def setUp(self):
        super().setUp()
        for i, (cat, amt) in enumerate([('Rent', '25000'), ('Electricity', '4000'),
                                        ('Tea', '300'), ('Tools', '1200')]):
            CashbookEntry.objects.create(entry_type='EXPENSE', category=cat,
                                         amount=D(amt), date=self.today)
        CashbookEntry.objects.create(entry_type='INCOME', category='Scrap',
                                     amount=D('2200'), date=self.today)

    def _out(self):
        from workshop.analysis_views import _insight_cashbook
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_cashbook(s, e)

    def test_the_section_totals_what_the_profit_page_expenses(self):
        s, e, _k, _l = engine.resolve_period('this_month')
        rep = engine.build_profit_report(s, e)
        cashbook_line = [l for l in rep['expense_lines'] if l['key'] == 'cashbook'][0]
        self.assertEqual(self._out()['expense_total'], cashbook_line['amount'])

    def test_the_rows_add_up_to_the_total_with_nothing_capped_away(self):
        out = self._out()
        self.assertEqual(sum(r['total'] for r in out['expense_rows']),
                         out['expense_total'])
        self.assertEqual(len(out['expense_rows']), 4)

    def test_the_income_side_is_broken_down_too(self):
        """Scrap and black oil are the whole of it, and it existed nowhere as a
        breakdown before — the Profit page only ever showed one figure."""
        out = self._out()
        self.assertEqual([r['category'] for r in out['income_rows']], ['Scrap'])
        self.assertEqual(out['income_total'], D('2200'))

    def test_the_section_renders_every_category(self):
        html = self.client.get(
            reverse('analysis_insight_section', args=['cashbook'])).content.decode()
        for cat in ('Rent', 'Electricity', 'Tea', 'Tools', 'Scrap'):
            self.assertIn(cat, html)

    def test_the_profit_page_no_longer_lists_them(self):
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertNotIn('Show all', html)
        self.assertNotIn('pf-cat-hidden', html)

    def test_but_the_WAGE_WARNING_stayed_on_the_profit_page(self):
        """
        The one line in that card that said the profit figure above it may be
        WRONG — the same money counted from Salary & Advance and again from a
        cashbook category. A warning that changes what the headline means has
        to live beside the headline.
        """
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Staff Salaries',
                                     amount=D('40000'), date=self.today)
        month = self.today.replace(day=1)
        pay = SalaryPayment.objects.create(month=month)
        SalaryPaymentLine.objects.create(
            payment=pay, staff=self.mech, salary_used=D('20000'),
            leave_days=0, advance_used=D('0'), net_amount=D('20000'))
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('Wages may be counted twice', html)
        self.assertIn('Staff Salaries', html)


class ThePageCarriesNoDrillDownsTests(AnalysisBase):
    """
    The Profit page's rule is that it carries none, and it was carrying two:
    the Cashbook category list (open-ended) and the Salary & Advance card (four
    rows explaining one expense line). Keeping one and deleting the other would
    have been the page applying its own rule to whichever was noticed.

    Both left, to DIFFERENT places, and that is the point: the cashbook
    breakdown existed nowhere else so it became a Deep Analysis section; wages
    already have a whole module at `/salary-advance/`, so a ninth insight
    section would have been a thinner second copy of it.
    """

    def setUp(self):
        super().setUp()
        self.card = self.make_card(bill='60000', received='60000')
        self.card.labour_amount = D('60000')
        self.card.save()
        month = self.today.replace(day=1)
        SalaryAdvance.objects.create(staff=self.mech, amount=D('9000'), date=self.today)
        pay = SalaryPayment.objects.create(month=month)
        SalaryPaymentLine.objects.create(
            payment=pay, staff=self.mech, salary_used=D('25000'),
            leave_days=0, advance_used=D('9000'), net_amount=D('16000'))

    def _html(self):
        return self.client.get(reverse('analysis_dashboard')).content.decode()

    def test_the_salary_card_is_gone(self):
        html = self._html()
        for row in ('Total Wage Cost', 'Paid at settlement',
                    'Advances within settled months'):
            self.assertNotIn(row, html, f'the salary drill-down is back: {row}')

    def test_but_the_wage_cost_still_explains_itself_on_the_expense_line(self):
        """
        THE ONE FACT WORTH KEEPING. Salary is the only expense line here whose
        composition is not self-evident and which reads like a double count:
        the wage cost is NET PLUS ADVANCES. An owner seeing ₹25,000 here and a
        ₹16,000 settlement in Salary & Advance has to be able to tell the
        ₹9,000 difference is advances already handed over, not an error.

        It replaced "1 month settled" — a count of months, which said nothing
        about the figure beside it.
        """
        html = self._html()
        self.assertIn('₹16,000 settled + ₹9,000 advances', html)
        self.assertNotIn('1 month settled', html)

    def test_an_unsettled_month_keeps_the_warning_instead(self):
        """A bigger fact than how the counted part splits: on an unsettled
        month the wage bill is missing from the figure altogether."""
        SalaryPayment.objects.all().delete()
        html = self._html()
        self.assertIn('not settled', html)
        self.assertNotIn('settled + ₹', html)

    def test_a_month_with_no_advances_says_nothing_extra(self):
        """Nothing to explain — net IS the wage cost — so the line falls back
        to the plain count rather than printing 'x settled + ₹0 advances'."""
        SalaryPaymentLine.objects.all().update(advance_used=D('0'), net_amount=D('25000'))
        SalaryAdvance.objects.all().delete()
        html = self._html()
        self.assertNotIn('advances</span>', html)
        self.assertIn('1 month settled', html)


class TheExpenseListNeedsNoFootnoteTests(AnalysisBase):
    """
    The Expenses card carried a note: "Parts worth ₹1,88,000 came off warehouse
    stock and are not charged here — they were paid for earlier, on a Supplies
    Shop bill." Every word of it was true, and it should never have needed
    saying.

    It existed because the card charged a Supplies Shop BILL while the card
    below it charged stock when it was USED, so roughly a third of the parts
    fitted had their cost in neither of the four lines — and the honest reading
    of the total, with nothing said, was that profit was overstated by that
    much. The note was there to stop that reading.

    Both halves now charge parts when they are fitted. There is no gap left to
    explain, so there is nothing to explain it with.
    """

    def setUp(self):
        super().setUp()
        cat = Category.objects.create(name='Engine Oil')
        item = Item.objects.create(name='Castrol 5W-30', category=cat,
                                   average_stock=D('20'), avg_cost=D('500'))
        card = self.make_card(bill='9000')
        card.labour_amount = D('4000')
        card.save()
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Castrol 5W-30',
            source=JobCardSpareItem.SOURCE_INVENTORY, item=item,
            quantity=D('5'), unit_price=D('500'), total_price=D('5000'))

    def test_the_note_is_gone(self):
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertNotIn('came off warehouse', html)
        self.assertNotIn('not charged here', html)

    def test_because_the_warehouse_parts_ARE_charged_now(self):
        """What the note used to excuse is simply an expense line."""
        s, e, _k, _l = engine.resolve_period('this_month')
        rep = engine.build_profit_report(s, e)
        line = [l for l in rep['expense_lines'] if l['key'] == 'inventory'][0]
        self.assertEqual(line['label'], 'Inventory Used')
        self.assertEqual(line['amount'], D('2500'))       # 5 x ₹500
        self.assertEqual(rep['expense_total'], D('2500'))

    def test_the_line_says_it_is_not_about_bills_or_payments(self):
        """
        Both parts lines name their basis, because both shops are paid in
        instalments and both have a payment screen of their own — so a ledger
        showing a different figure this month invites the wrong reading.
        """
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('Parts taken off the warehouse shelf', html)
        self.assertIn('Parts bought per job, not payments', html)


class TheFleetBoxesReadAsOneSplitTests(AnalysisBase):
    """
    Five stat boxes, two different kinds of number, and nothing saying so. Four
    of them are the period's work SPLIT between fleet and walk-in; the fifth is
    a live count of accounts the date filter never touches. Read as a flat row,
    "Fleet accounts 2" sat first and the two walk-in boxes looked like unrelated
    facts rather than the other half of the two beside them.
    """

    def setUp(self):
        super().setUp()
        self.payer = BulkPayer.objects.create(customer_name='Malabar Cabs')
        for _ in range(3):
            self.make_card(bill='10000', received='10000', bulk_payer=self.payer)
        for _ in range(7):
            self.make_card(bill='10000', received='10000')

    def _out(self):
        from workshop.analysis_views import _insight_fleet
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_fleet(s, e)

    def test_the_fleet_boxes_carry_their_share_of_the_whole(self):
        out = self._out()
        self.assertEqual(out['fleet_jobs'], 3)
        self.assertEqual(out['walkin_jobs'], 7)
        self.assertEqual(out['total_jobs'], 10)
        self.assertEqual(out['fleet_job_pct'], 30.0)
        self.assertAlmostEqual(out['fleet_revenue_pct'], 30.0, places=6)

    def test_the_share_is_of_CAR_BILLS_not_of_turnover(self):
        """
        The denominator is fleet + walk-in revenue, which is car bills only.
        Turnover on the Profit page also carries cashbook income, so calling
        this a share of turnover would be a share of a figure it was not
        divided by — the arithmetic right and the word wrong.
        """
        CashbookEntry.objects.create(entry_type='INCOME', category='Scrap',
                                     amount=D('50000'), date=self.today)
        out = self._out()
        self.assertEqual(out['fleet_job_pct'], 30.0)
        self.assertAlmostEqual(out['fleet_revenue_pct'], 30.0, places=6,
                               msg='scrap income moved a share of CAR BILLS')
        html = self.client.get(
            reverse('analysis_insight_section', args=['fleet'])).content.decode()
        self.assertIn('car bills', html)
        self.assertNotIn('of turnover', html)
        # The walk-in boxes are gone: this is the FLEET section, and walk-in
        # revenue was the largest figure on it.
        self.assertNotIn('Walk-in revenue', html)

    def test_the_account_count_says_it_ignores_the_filter(self):
        """The only figure in the section the date range does not touch."""
        html = self.client.get(
            reverse('analysis_insight_section', args=['fleet'])).content.decode()
        self.assertIn('active now, not filtered', html)


class AFleetAccountThatOwesIsAlwaysListedTests(AnalysisBase):
    """
    `rows` is built from job cards IN THE WINDOW while "Balance now" is a live
    figure spanning the account's whole history — so an account that brought no
    cars in this period vanished from the table, taking its debt off the only
    screen that lists fleet balances. The Profit page's fleet line still counted
    it, so an owner adding up this column got less than the tile said.
    """

    def setUp(self):
        super().setUp()
        self.quiet = BulkPayer.objects.create(customer_name='Quiet Fleet')
        # Billed last year, never settled, and nothing since.
        self.make_card(bill='40000', received='15000', bulk_payer=self.quiet,
                       when=self.today - timedelta(days=400),
                       payment_status='PARTIAL')
        self.busy = BulkPayer.objects.create(customer_name='Busy Fleet')
        self.make_card(bill='10000', received='10000', bulk_payer=self.busy)

    def _rows(self):
        from workshop.analysis_views import _insight_fleet
        s, e, _k, _l = engine.resolve_period('this_month')
        return {r['bulk_payer__customer_name']: r
                for r in _insight_fleet(s, e)['rows']}

    def test_the_quiet_account_is_still_there_with_its_debt(self):
        rows = self._rows()
        self.assertIn('Quiet Fleet', rows)
        self.assertEqual(rows['Quiet Fleet']['owed'], D('25000'))
        self.assertEqual(rows['Quiet Fleet']['jobs'], 0)
        self.assertTrue(rows['Quiet Fleet']['no_jobs_here'])

    def test_its_activity_columns_are_blank_rather_than_zero(self):
        """
        Zero billed with 100% collected reads as "billed nothing and collected
        it all", which is a claim about a period this account was not in.
        """
        html = self.client.get(
            reverse('analysis_insight_section', args=['fleet'])).content.decode()
        self.assertIn('brought no cars in', html)
        self.assertIn('Quiet Fleet', html)

    def test_the_column_now_adds_up_to_the_profit_pages_fleet_line(self):
        """The two screens quote one number for one debt."""
        rows = self._rows()
        total = sum((r['owed'] for r in rows.values()), D('0'))
        self.assertEqual(total, engine.financial_position()['fleet_due'])


class SpendAndPaidAreTwoQuestionsTests(AnalysisBase):
    """
    An owner reading a profit figure asks "then where is the money?" within
    seconds. The answer is not a term subtracted from profit — profit and cash
    differ by five things at once — so the cash figure lives beside the shops it
    concerns, and the Profit page carries a pointer instead of a number.
    """

    def setUp(self):
        super().setUp()
        card = self.make_card(bill='5000')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Pads',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=D('3000'), total_price=D('5000'))
        SpareShopPayment.objects.create(shop=self.shop, amount=D('1200'))

        self.supplier = SupplierShop.objects.create(name='Bulk Oils')
        cat = Category.objects.create(name='Engine Oil')
        item = Item.objects.create(name='Castrol 5W-30', category=cat,
                                   average_stock=D('20'))
        bill = SupplierRestockBill.objects.create(supplier=self.supplier,
                                                 bill_date=self.today)
        SupplierRestockItem.objects.create(bill=bill, item=item, quantity=D('10'),
                                           total_price=D('9000'))
        SupplierPayment.objects.create(supplier=self.supplier, amount=D('4000'),
                                       date=self.today)

    def _out(self):
        from workshop.analysis_views import _insight_shops
        s, e, _k, _l = engine.resolve_period('this_month')
        return _insight_shops(s, e)

    def test_cash_paid_is_reported_beside_what_was_spent(self):
        out = self._out()
        self.assertEqual(out['spare_total'], D('3000'))     # what the work cost
        self.assertEqual(out['spare_paid'], D('1200'))      # what left the drawer
        self.assertEqual(out['supplier_paid'], D('4000'))

    def test_neither_paid_figure_touches_the_profit(self):
        """
        A payment settles a liability. Adding it to the expense list would
        charge the workshop for one delivery twice — once when it was used and
        again when it was paid for.
        """
        s, e, _k, _l = engine.resolve_period('this_month')
        before = engine.build_profit_report(s, e)['expense_total']
        SpareShopPayment.objects.create(shop=self.shop, amount=D('900'))
        SupplierPayment.objects.create(supplier=self.supplier, amount=D('2500'),
                                       date=self.today)
        self.assertEqual(engine.build_profit_report(s, e)['expense_total'], before)

    def test_both_sides_are_cut_by_the_day_the_money_moved(self):
        """
        This replaces a tripwire that asserted `SpareShopPayment` had NO `date`
        column — written so that the day it grew one, the choice got revisited
        deliberately rather than by accident. It grew one, and this is that
        revisit: both ledgers are now cut by `date`, never `created_at`.

        The assertion is the BEHAVIOUR, not the schema. A payment keyed today
        and dated into last month must fall OUT of this month on both sides —
        which is the whole point of the column, and the one thing a filter
        quietly left on `created_at` would get wrong. Every payment these
        objects create is keystroke-stamped NOW, so a `created_at` filter would
        count all four.
        """
        last_month = self.today.replace(day=1) - timedelta(days=1)

        # Dated into this window: must count.
        SpareShopPayment.objects.create(shop=self.shop, amount=D('700'),
                                        date=self.today)
        SupplierPayment.objects.create(supplier=self.supplier, amount=D('300'),
                                       date=self.today)
        # Back-dated out of it: must not, on either side.
        SpareShopPayment.objects.create(shop=self.shop, amount=D('5000'),
                                        date=last_month)
        SupplierPayment.objects.create(supplier=self.supplier, amount=D('9000'),
                                       date=last_month)

        out = self._out()
        self.assertEqual(out['spare_paid'], D('1900'))      # 1200 + 700
        self.assertEqual(out['supplier_paid'], D('4300'))   # 4000 + 300

    def test_the_two_sides_stay_two_figures_even_on_one_basis(self):
        """
        The basis is shared now, so the ORIGINAL reason for splitting them is
        gone. They stay split for a different one: a spare shop and a Supplies
        Shop are two trades on two instalment rhythms, and the rest of this
        section is already organised that way. Both tiles say the same thing
        about their date, because they now mean the same thing by it.
        """
        html = self.client.get(
            reverse('analysis_insight_section', args=['shops'])).content.decode()
        self.assertEqual(html.count('cash out, by payment date'), 2)
        self.assertNotIn('cash out, by entry date', html)

    def test_the_profit_page_points_at_the_position_card_not_at_a_cash_figure(self):
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('not cash in hand', html)
        self.assertIn('Position Right Now', html)


class MoneyInIsGreenMoneyOutIsRedTests(AnalysisBase):
    """
    The earnings card's right-hand column reads straight down: green in, red
    out. The colour sits on the AMOUNT, not the label, because the two cost
    rows already carry theirs there - putting it on the label above would make
    the two halves of one card disagree about where colour lives.
    """

    def setUp(self):
        super().setUp()
        card = self.make_card(bill='20000', discount='1500', received='18500')
        card.labour_amount = D('12000')
        card.save()
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Pads',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=D('5000'), total_price=D('8000'))
        CashbookEntry.objects.create(entry_type='INCOME', category='Scrap',
                                     amount=D('2000'), date=self.today)
        CashbookEntry.objects.create(entry_type='EXPENSE', category='Rent',
                                     amount=D('3000'), date=self.today)

    def _card(self):
        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        return html.split('Where the profit came from')[1].split('Trend')[0]

    def test_every_earning_row_is_green_and_every_cost_row_is_red(self):
        import re
        classes = re.findall(r'class="amt (plus|minus)"', self._card())
        s, e, _k, _l = engine.resolve_period('this_month')
        earn = engine.build_profit_report(s, e)['earnings']
        # One class per row, in page order: the earn block then the spend block.
        expected = ['minus' if r['negative'] else 'plus'
                    for r in earn['earn'] + earn['spend']]
        self.assertEqual(classes, expected)
        self.assertIn('plus', classes)
        self.assertIn('minus', classes)

    def test_a_discount_stays_RED_even_though_it_sits_in_the_earning_half(self):
        """It is money never earned, so it is coloured for the direction it
        goes, not the half it lives in."""
        s, e, _k, _l = engine.resolve_period('this_month')
        earn = engine.build_profit_report(s, e)['earnings']['earn']
        discount = [r for r in earn if r['key'] == 'discount'][0]
        self.assertTrue(discount['negative'])

    def test_gross_earnings_is_deliberately_not_green(self):
        """A structural waypoint, not a fifth thing that earned money - and
        with green above and green below, a green subtotal between them leaves
        nothing for the eye to land on."""
        gross = self._card().split('Gross Earnings')[1][:200]
        self.assertNotIn('amt plus', gross)


class TheVehiclesSectionSaysWhatItRunsOnTests(AnalysisBase):
    """
    It used to print the customer coverage - "filled in on 0 of 47 job cards
    here" - which reads as a shortfall to go and fix. It is not: a customer name
    is optional by design and this workshop mostly does not record one, because
    a car is identified by its plate. Reporting the count invited an owner to
    chase staff into filling boxes that change nothing on this screen.
    """

    def setUp(self):
        super().setUp()
        self.make_card(bill='5000')

    def _html(self):
        return self.client.get(
            reverse('analysis_insight_section', args=['vehicles'])).content.decode()

    def test_it_states_the_rule_instead_of_a_coverage_count(self):
        html = self._html()
        self.assertIn('works off the', html)
        self.assertIn('registration number', html)
        self.assertNotIn('job cards here', html)

    def test_the_figures_do_not_move_when_a_customer_name_is_added(self):
        """The claim the note makes, asserted rather than trusted."""
        from workshop.analysis_views import _insight_vehicles
        s, e, _k, _l = engine.resolve_period('this_month')
        before = _insight_vehicles(s, e)
        JobCard.objects.all().update(customer_name='Someone', customer_contact='999')
        after = _insight_vehicles(s, e)
        for key in ('top_vehicles', 'brands', 'total_cards', 'distinct_vehicles',
                    'repeat_vehicles', 'repeat_pct', 'avg_visits'):
            self.assertEqual(before[key], after[key], key)


class TheShelfIsValuedHonestlyOrNotAtAllTests(AnalysisBase):
    """
    The Inventory section reports what LEFT the shelf, so what is still ON it
    belongs beside that — and it is what the Profit page's "Stock added to the
    shelf" line is adding to.

    ⚠ Unknown cost on an `Item` is `avg_cost == 0`, NOT NULL: the column is
    `default=0, null=False`. An `isnull` filter would match nothing and quietly
    value opening stock that has never had a supplier bill behind it at ₹0 —
    reporting it as worthless rather than as unknown.
    """

    def setUp(self):
        super().setUp()
        cat = Category.objects.create(name='Engine Oil')
        self.costed = Item.objects.create(name='Castrol 5W-30', category=cat,
                                          average_stock=D('20'), avg_cost=D('500'),
                                          current_stock=D('10'))
        self.uncosted = Item.objects.create(name='Opening Stock Oil', category=cat,
                                            average_stock=D('20'), avg_cost=D('0'),
                                            current_stock=D('8'))

    def _value(self):
        # In the ENGINE, not a view: it is money math, and the Profit page's
        # position tile and the Inventory section both read it — two copies
        # would be two answers to "what is the stock worth" on two screens an
        # owner reads together.
        return engine.warehouse_stock_value()

    def test_a_product_with_no_cost_is_counted_not_valued_at_zero(self):
        out = self._value()
        self.assertEqual(out['value'], D('5000'))
        self.assertEqual(out['uncosted_products'], 1)

    def test_a_zero_stock_product_is_not_reported_as_a_gap(self):
        """Nothing on the shelf means nothing to put a cost against — listing
        it would make the caveat permanent and therefore unread."""
        self.uncosted.current_stock = D('0')
        self.uncosted.save()
        self.assertEqual(self._value()['uncosted_products'], 0)

    def test_negative_stock_is_left_negative(self):
        """It is allowed by design and it means a Supplies Shop bill is
        missing, so flooring it would delete the signal."""
        self.costed.current_stock = D('-2')
        self.costed.save()
        self.assertEqual(self._value()['value'], D('-1000'))


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

    def test_a_purchase_with_no_shop_is_disclosed_rather_than_dropped(self):
        """
        This section groups BY shop, so a row with no shop has no group to sit
        in and falls out of the total — while the Spare Parts section counts
        every SOURCE_SHOP row and therefore reports MORE spent on the same
        parts in the same period. Two screens disagreeing about one figure with
        nothing saying why is what the disclosure prevents.
        """
        from workshop.analysis_views import _insight_shops, _insight_spare_parts
        card = self.make_card(bill='0')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Pads',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=D('1000'), total_price=D('1500'))
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Mystery Part',
            source=JobCardSpareItem.SOURCE_SHOP, shop=None,
            quantity=D('1'), unit_price=D('400'), total_price=D('600'))

        s, e, _k, _l = engine.resolve_period('this_month')
        shops = _insight_shops(s, e)
        self.assertEqual(shops['spare_total'], D('1000'))
        self.assertEqual(shops['unattributed'], D('400'))
        # The disclosure is exactly the difference between the two sections.
        self.assertEqual(shops['spare_total'] + shops['unattributed'],
                         _insight_spare_parts(s, e)['totals']['cost'])

    def test_the_page_says_so_when_there_is_one(self):
        from django.urls import reverse as _rev
        card = self.make_card(bill='0')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Mystery Part',
            source=JobCardSpareItem.SOURCE_SHOP, shop=None,
            quantity=D('1'), unit_price=D('400'), total_price=D('600'))
        html = self.client.get(_rev('analysis_insight_section', args=['shops'])).content.decode()
        self.assertIn('no shop recorded', html)

    def test_an_ordinary_period_says_nothing_about_it(self):
        """Normally there are none, and a permanent notice is an unread one."""
        from django.urls import reverse as _rev
        card = self.make_card(bill='0')
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Pads',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=D('1000'), total_price=D('1500'))
        html = self.client.get(_rev('analysis_insight_section', args=['shops'])).content.decode()
        self.assertNotIn('no shop recorded', html)


class BothPartRoutesDiscloseAnUncostedPartTests(AnalysisBase):
    """
    `SPARE_COST` costs a NULL `unit_price` at ₹0 on BOTH routes, so on both a
    part with no price recorded reads as FREE and pushes profit UP by exactly
    that much. It is the one way this page can be wrong without looking wrong.

    Only the warehouse route was counted. `uncosted_draw_count` filtered
    `source=INVENTORY`, so an uncosted SHOP part was silently ₹0 and the page
    still reported "0 uncosted" - measured on the demo data as July's Spare
    Shops expense running ₹1,000 short with nothing saying so.

    Both are counted now. These tests assert the SYMMETRY rather than either
    number, because the failure worth catching is the two drifting apart again.
    """

    def _shop_part(self, card, price):
        return JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Brake Disc',
            source=JobCardSpareItem.SOURCE_SHOP, shop=self.shop,
            quantity=D('1'), unit_price=price, total_price=D('2000'))

    def _window(self):
        return engine.resolve_period('this_month')[:2]

    def test_an_unpriced_shop_part_is_counted(self):
        self._shop_part(self.make_card(bill='2000'), None)
        s, e = self._window()

        self.assertEqual(engine.uncosted_shop_count(s, e), 1)
        self.assertEqual(engine.build_profit_report(s, e)['uncosted_shop'], 1)

    def test_a_priced_shop_part_is_not_counted(self):
        self._shop_part(self.make_card(bill='2000'), D('1200'))
        s, e = self._window()

        self.assertEqual(engine.uncosted_shop_count(s, e), 0)

    def test_the_two_routes_are_counted_separately_not_together(self):
        """
        A shop gap must not inflate the warehouse count or the reverse - the
        remedies differ (key the shop's bill vs add a Supplies Shop bill), so
        a reader has to be able to tell which one they are looking at.
        """
        card = self.make_card(bill='4000')
        self._shop_part(card, None)
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Engine Oil',
            source=JobCardSpareItem.SOURCE_INVENTORY,
            quantity=D('1'), unit_price=None, total_price=D('2000'))
        s, e = self._window()

        self.assertEqual(engine.uncosted_shop_count(s, e), 1)
        self.assertEqual(engine.uncosted_draw_count(s, e), 1)

    def test_the_unpriced_part_really_does_cost_zero(self):
        """
        The reason the count has to exist: the row IS charged at ₹0, so profit
        is genuinely overstated rather than merely unexplained.
        """
        card = self.make_card(bill='2000')
        s, e = self._window()
        before = engine.build_profit_report(s, e)['expense_total']

        self._shop_part(card, None)
        after = engine.build_profit_report(s, e)['expense_total']

        self.assertEqual(after, before, 'an unpriced shop part added no cost')
        self.assertEqual(engine.build_profit_report(s, e)['uncosted_shop'], 1)

    def test_both_warnings_reach_the_page(self):
        card = self.make_card(bill='4000')
        self._shop_part(card, None)
        JobCardSpareItem.objects.create(
            job_card=card, spare_part_name='Engine Oil',
            source=JobCardSpareItem.SOURCE_INVENTORY,
            quantity=D('1'), unit_price=None, total_price=D('2000'))

        html = self.client.get(reverse('analysis_dashboard')).content.decode()
        self.assertIn('no price recorded', html)      # the shop half
        self.assertIn('no cost recorded', html)       # the warehouse half
