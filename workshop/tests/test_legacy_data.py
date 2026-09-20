"""
LEGACY DATA — the go-live starting position.

Opening Stock (what was on the shelf) and Opening Balances (what each shop was
owed) are typed once on go-live day and must then behave exactly like the
system's own stock and debts — without moving a single profit or cash figure.
The rules are in workshop/views/legacy.py, the two `opening_balance` columns
and `inventory.OpeningStock`.
"""
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    Category, Item, OpeningStock, ShopCatalogItem, SupplierPayment,
    SupplierRestockBill, SupplierRestockItem, SupplierShop,
)
from workshop import analysis_engine as engine
from workshop.models import (
    JobCard, JobCardSpareItem, LegacyDataLock, SpareShop, SpareShopPayment,
)

FAB = '<button type="submit" class="lg-fab" aria-label="Save"'
INVENTORY = JobCardSpareItem.SOURCE_INVENTORY
SHOP = JobCardSpareItem.SOURCE_SHOP


def _user(role):
    user = User.objects.create_user(username=f'lg_{role.lower()}', password='pw')
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


class LegacyBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.oil = Item.objects.create(category=Category.objects.create(name='Engine Oil'),
                                       name='Castrol Edge 5W30', average_stock=D('20'))
        self.air = Item.objects.create(category=Category.objects.create(name='Filters'),
                                       name='Air Filter A4', average_stock=D('4'))
        self.fluid = SupplierShop.objects.create(name='Fluid Manjeri')
        self.biljo = SpareShop.objects.create(name='Biljo')
        self.owner = _user('Owner')
        self.client.force_login(self.owner)
        self._cards = 0

    # ── helpers ────────────────────────────────────────────────────────────
    def count(self, **rows):
        """POST the Opening Stock page. `rows` maps an item attribute name on
        self to (quantity, cost); every other product posts empty boxes."""
        data = {}
        for item in Item.objects.all():
            data[f'qty_{item.pk}'] = ''
            data[f'cost_{item.pk}'] = ''
        for attr, (qty, cost) in rows.items():
            item = getattr(self, attr)
            data[f'qty_{item.pk}'] = qty
            data[f'cost_{item.pk}'] = cost
        return self.client.post(reverse('opening_stock'), data, follow=True)

    def owe(self, spare='', supply=''):
        return self.client.post(reverse('opening_balances'), {
            f'spare_{self.biljo.pk}': spare, f'supply_{self.fluid.pk}': supply,
        }, follow=True)

    def draw(self, item, qty, days_ago=0):
        self._cards += 1
        card = JobCard.objects.create(registration_number=f'KL 10 AA {1000 + self._cards}',
                                      admitted_date=self.today - timedelta(days=days_ago))
        return JobCardSpareItem.objects.create(job_card=card, source=INVENTORY, item=item,
                                               quantity=D(str(qty)), total_price=D('0'))

    def bought(self, price, fitted=True):
        """A spare bought from Biljo — on a job card, or unassigned."""
        card = None
        if fitted:
            self._cards += 1
            card = JobCard.objects.create(registration_number=f'KL 10 AB {1000 + self._cards}',
                                          admitted_date=self.today)
        return JobCardSpareItem.objects.create(job_card=card, source=SHOP, shop=self.biljo,
                                               spare_part_name='Wheel bearing front left',
                                               quantity=D('1'), unit_price=D(str(price)))

    def fresh(self, obj):
        obj.refresh_from_db()
        return obj

    def figures(self):
        """Every profit and cash figure for this month, to prove nothing moved."""
        start, end = self.today.replace(day=1), self.today
        report = engine.build_profit_report(start, end)
        cash = engine.cash_position(start, end)
        return report['turnover'], report['expense_total'], report['profit'], cash['total_in'], cash['total_out']


class OpeningStockTests(LegacyBase):

    def test_it_raises_the_shelf_and_sets_the_cost(self):
        self.count(oil=('38', '500'))
        self.assertEqual(self.fresh(self.oil).current_stock, D('38'))
        self.assertEqual(self.oil.avg_cost, D('500.00'))
        self.assertEqual(OpeningStock.objects.get().quantity, D('38'))

    def test_it_creates_no_shop_balance(self):
        self.count(oil=('38', '500'))
        self.assertEqual(self.fresh(self.fluid).get_pending_balance, 0)
        self.assertEqual(self.fresh(self.biljo).get_pending_balance, 0)
        position = engine.financial_position()
        self.assertEqual(position['payable_total'], 0)
        self.assertEqual(position['stock_value'], D('19000'))

    def test_a_part_fitted_from_it_is_costed_at_the_typed_cost(self):
        self.count(oil=('38', '500'))
        self.assertEqual(self.draw(self.oil, 4).unit_price, D('500.00'))
        self.assertEqual(self.fresh(self.oil).current_stock, D('34'))

    def test_a_part_fitted_BEFORE_the_count_was_typed_is_still_costed(self):
        # Opening stock is always the first delivery, whatever day it is typed.
        early = self.draw(self.oil, 4)
        self.assertIsNone(early.unit_price)
        self.count(oil=('38', '500'))
        self.assertEqual(self.fresh(early).unit_price, D('500.00'))
        self.assertEqual(self.fresh(self.oil).current_stock, D('34'))

    def test_a_later_bill_blends_with_what_is_left(self):
        self.count(oil=('38', '500'))
        # Yesterday: on the SAME day the replay takes a delivery before a draw
        # (stock arrives before it can be used), which is not this case.
        first = self.draw(self.oil, 28, days_ago=1)          # 10 L left at ₹500
        bill = SupplierRestockBill.objects.create(supplier=self.fluid, bill_date=self.today)
        SupplierRestockItem.objects.create(bill=bill, item=self.oil,
                                           quantity=D('20'), total_price=D('10400'))  # ₹520 each
        # (10 × 500 + 20 × 520) ÷ 30
        self.assertEqual(self.fresh(self.oil).avg_cost, D('513.33'))
        self.assertEqual(self.oil.current_stock, D('30'))
        # A part fitted before that bill keeps the cost it was fitted at.
        self.assertEqual(self.fresh(first).unit_price, D('500.00'))
        # And the bill is a real debt, as always — the opening stock added none.
        self.assertEqual(self.fresh(self.fluid).get_pending_balance, D('10400'))

    def test_a_corrected_count_moves_the_shelf_by_the_difference(self):
        self.count(oil=('38', '500'))
        self.draw(self.oil, 4)
        self.count(oil=('40', '500'))
        self.assertEqual(self.fresh(self.oil).current_stock, D('36'))

    def test_a_corrected_cost_re_prices_parts_already_used(self):
        self.count(oil=('38', '500'))
        used = self.draw(self.oil, 4)
        self.count(oil=('38', '450'))
        self.assertEqual(self.fresh(used).unit_price, D('450.00'))

    def test_clearing_a_count_takes_the_stock_back(self):
        self.count(oil=('38', '500'), air=('6', '850'))
        self.count(air=('6', '850'))                         # oil boxes left empty
        self.assertFalse(OpeningStock.objects.filter(item=self.oil).exists())
        self.assertEqual(self.fresh(self.oil).current_stock, D('0'))
        self.assertEqual(self.fresh(self.air).current_stock, D('6'))

    def test_a_zero_count_is_no_row(self):
        self.count(oil=('0', '500'))
        self.assertFalse(OpeningStock.objects.exists())

    def test_the_cost_is_required(self):
        response = self.count(oil=('38', ''))
        self.assertFalse(OpeningStock.objects.exists())
        self.assertEqual(self.fresh(self.oil).current_stock, D('0'))
        self.assertContains(response, 'Type the cost per unit')

    def test_one_bad_row_saves_nothing_at_all(self):
        response = self.count(oil=('38', '500'), air=('6', 'abc'))
        self.assertFalse(OpeningStock.objects.exists())
        self.assertContains(response, 'Nothing was saved')
        # What was typed comes back exactly as typed.
        self.assertContains(response, 'value="abc"')
        self.assertContains(response, 'value="38"')

    def test_a_figure_is_refused_rather_than_rounded(self):
        for qty, cost in (('1.555', '500'), ('38', '1,000'), ('-2', '500'), ('38', '0')):
            with self.subTest(qty=qty, cost=cost):
                self.count(oil=(qty, cost))
                self.assertFalse(OpeningStock.objects.exists())

    def test_a_product_not_on_the_page_is_left_alone(self):
        self.count(air=('6', '850'))
        # A page opened before the air filter existed posts no boxes for it.
        self.client.post(reverse('opening_stock'), {
            f'qty_{self.oil.pk}': '38', f'cost_{self.oil.pk}': '500'})
        self.assertEqual(self.fresh(self.air).current_stock, D('6'))
        self.assertEqual(OpeningStock.objects.count(), 2)

    def test_typing_it_moves_no_profit_or_cash_figure(self):
        before = self.figures()
        self.count(oil=('38', '500'), air=('6', '850'))
        self.assertEqual(self.figures(), before)

    def test_a_product_holding_opening_stock_is_never_deleted_from_a_catalog(self):
        # `remove_shop_catalog_item` only deletes an orphan product with NO
        # stock; opening stock is stock, so the product is deactivated instead
        # and the count survives.
        catalog = ShopCatalogItem.objects.create(shop=self.fluid, item=self.oil)
        self.count(oil=('38', '500'))
        self.client.post(reverse('remove_shop_catalog_item', args=[self.fluid.pk, catalog.pk]))
        self.assertTrue(Item.objects.filter(pk=self.oil.pk).exists())
        self.assertEqual(OpeningStock.objects.get().quantity, D('38.00'))
        self.assertEqual(self.fresh(self.oil).current_stock, D('38'))

    def test_the_page_shows_the_count_and_its_worth(self):
        response = self.count(oil=('38', '500'), air=('6', '850'))
        self.assertEqual(response.context['counted'], 2)
        self.assertEqual(response.context['worth'], D('24100'))     # 19,000 + 5,100


    def test_the_round_save_sits_inside_the_form_on_both_screens(self):
        # The Job Card's own control, and the long-list reason: what is typed
        # lives in the browser until Save. Outside the form it would submit
        # nothing.
        for name in ('opening_stock', 'opening_balances'):
            with self.subTest(name=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn(FAB, html)
                opened = html.index('<form method="post" class="lg-form"')
                self.assertLess(opened, html.index(FAB))
                self.assertLess(html.index(FAB), html.index('</form>', opened))


class OpeningBalanceTests(LegacyBase):

    def test_it_is_saved_exactly_as_typed_and_every_balance_follows(self):
        self.owe(spare='245000', supply='85000')
        self.assertEqual(self.fresh(self.biljo).opening_balance, D('245000'))
        self.assertEqual(self.biljo.get_pending_balance, D('245000'))
        self.assertEqual(self.fresh(self.fluid).get_pending_balance, D('85000'))
        position = engine.financial_position()
        self.assertEqual(position['payable_spare'], D('245000'))
        self.assertEqual(position['payable_supplier'], D('85000'))

    def test_the_screen_takes_nothing_off_for_unassigned_spares(self):
        # The owner's rule: the PERSON subtracts them, the screen computes
        # nothing. Biljo's book says 2,50,000; the bearing is already in.
        self.bought(5000, fitted=False)
        self.owe(spare='245000')
        self.assertEqual(self.fresh(self.biljo).opening_balance, D('245000'))
        self.assertEqual(self.biljo.get_pending_balance, D('250000'))

    def test_a_later_purchase_adds_on_top(self):
        self.owe(spare='245000')
        self.bought(3000)
        self.assertEqual(self.fresh(self.biljo).get_pending_balance, D('248000'))

    def test_payments_pay_the_opening_balance_before_any_part(self):
        self.owe(spare='10000')
        self.bought(2000)
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('6000'))
        page = self.client.get(reverse('spare_shop_detail', args=[self.biljo.pk]), {'filter': 'all'})
        self.assertEqual(page.context['items'][0].covered_status, 'UNPAID')
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('6000'))   # 12,000 paid
        page = self.client.get(reverse('spare_shop_detail', args=[self.biljo.pk]), {'filter': 'all'})
        self.assertEqual(page.context['items'][0].covered_status, 'COVERED')

    def test_a_supplies_shop_pays_it_off_first_too(self):
        self.owe(supply='10000')
        bill = SupplierRestockBill.objects.create(supplier=self.fluid, bill_date=self.today)
        SupplierRestockItem.objects.create(bill=bill, item=self.oil, quantity=D('8'), total_price=D('4000'))
        SupplierPayment.objects.create(supplier=self.fluid, amount=D('8000'))
        self.fresh(self.fluid).update_totals()
        detail = reverse('supplier_shop_detail', args=[self.fluid.pk])
        self.assertEqual(self.client.get(detail, {'filter': 'all'}).context['bills'][0].covered_status, 'UNPAID')
        chunk = self.client.get(reverse('ajax_supplier_bills', args=[self.fluid.pk]))
        self.assertEqual(chunk.context['bills'][0].covered_status, 'UNPAID')
        SupplierPayment.objects.create(supplier=self.fluid, amount=D('6000'))  # 14,000 paid
        self.fresh(self.fluid).update_totals()
        self.assertEqual(self.client.get(detail, {'filter': 'all'}).context['bills'][0].covered_status, 'COVERED')

    def test_the_shop_page_says_what_is_left_and_stops_at_zero(self):
        self.owe(spare='10000', supply='10000')
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('6000'))
        SupplierPayment.objects.create(supplier=self.fluid, amount=D('6000'))
        self.fresh(self.fluid).update_totals()
        for url in (reverse('spare_shop_detail', args=[self.biljo.pk]),
                    reverse('supplier_shop_detail', args=[self.fluid.pk])):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('<p class="opening-left">', html)
                self.assertIn('4,000</b>&nbsp;left', html)

        SpareShopPayment.objects.create(shop=self.biljo, amount=D('4000'))
        SupplierPayment.objects.create(supplier=self.fluid, amount=D('4000'))
        self.fresh(self.fluid).update_totals()
        for url in (reverse('spare_shop_detail', args=[self.biljo.pk]),
                    reverse('supplier_shop_detail', args=[self.fluid.pk])):
            with self.subTest(url=url, paid_off=True):
                self.assertNotContains(self.client.get(url), '<p class="opening-left">')

    def test_a_shop_with_none_never_shows_the_line(self):
        html = self.client.get(reverse('spare_shop_detail', args=[self.biljo.pk])).content.decode()
        self.assertNotIn('<p class="opening-left">', html)

    def test_the_whole_ledger_print_keeps_it_for_ever_and_a_dated_print_never_has_it(self):
        self.owe(spare='10000')
        self.bought(2000)
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('12000'))    # all paid off
        url = reverse('spare_shop_print', args=[self.biljo.pk])
        whole = self.client.get(url)
        self.assertContains(whole, 'Opening Balance (before the system)')
        self.assertEqual(whole.context['total_balance'], 0)       # adds up only WITH it
        for dated in ({'filter': 'this_month'},
                      {'filter': 'custom', 'start_date': self.today.isoformat(),
                       'end_date': self.today.isoformat()}):
            with self.subTest(**dated):
                self.assertNotContains(self.client.get(url, dated), 'Opening Balance (before the system)')

    def test_a_print_of_a_shop_owed_only_the_opening_balance_still_shows_the_totals(self):
        self.owe(spare='10000')
        whole = self.client.get(reverse('spare_shop_print', args=[self.biljo.pk]))
        self.assertContains(whole, 'Balance to Pay')
        self.assertEqual(whole.context['total_balance'], D('10000'))

    def test_a_shop_still_owing_it_cannot_be_archived(self):
        self.owe(spare='10000', supply='10000')
        self.client.post(reverse('spare_shop_delete', args=[self.biljo.pk]))
        self.client.post(reverse('deactivate_supplier_shop', args=[self.fluid.pk]))
        self.assertFalse(self.fresh(self.biljo).is_trashed)
        self.assertTrue(self.fresh(self.fluid).is_active)

    def test_an_empty_box_means_nothing_owed_and_bad_figures_are_refused(self):
        self.owe(spare='10000', supply='5000')
        self.owe(spare='', supply='5000')
        self.assertEqual(self.fresh(self.biljo).opening_balance, 0)
        self.assertEqual(self.biljo.get_pending_balance, 0)
        for bad in ('-500', '2,45,000', 'abc', '10.555'):
            with self.subTest(bad=bad):
                response = self.owe(spare=bad, supply='9000')
                self.assertContains(response, 'Nothing was saved')
                self.assertEqual(self.fresh(self.fluid).opening_balance, D('5000'))

    def test_typing_it_moves_no_profit_or_cash_figure(self):
        before = self.figures()
        self.owe(spare='245000', supply='85000')
        self.assertEqual(self.figures(), before)

    def test_paying_it_off_is_cash_out_on_the_day_and_still_no_expense(self):
        self.owe(spare='10000')
        turnover, expenses, profit, cash_in, cash_out = self.figures()
        SpareShopPayment.objects.create(shop=self.biljo, amount=D('4000'))
        after = self.figures()
        self.assertEqual(after[:3], (turnover, expenses, profit))
        self.assertEqual(after[4], cash_out + D('4000'))


class WhoCanOpenItTests(LegacyBase):

    def test_office_and_floor_are_refused_on_both_screens(self):
        for role in ('Office', 'Floor'):
            self.client.force_login(_user(role))
            for name in ('opening_stock', 'opening_balances'):
                with self.subTest(role=role, name=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 403)
                    self.assertEqual(self.client.post(reverse(name), {
                        f'spare_{self.biljo.pk}': '999', f'qty_{self.oil.pk}': '9',
                        f'cost_{self.oil.pk}': '9'}).status_code, 403)
        self.assertEqual(self.fresh(self.biljo).opening_balance, 0)
        self.assertFalse(OpeningStock.objects.exists())

    def test_a_visitor_is_sent_to_sign_in(self):
        self.client.logout()
        response = self.client.get(reverse('opening_stock'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_the_menu_carries_ONE_legacy_data_row_for_office_and_owner(self):
        # The owner's call: one row, and the three screens live on its page.
        for role in ('Owner', 'Office'):
            with self.subTest(role=role):
                if role == 'Office':
                    self.client.force_login(_user('Office'))
                # A page with no Legacy Data links of its own, so every one
                # found is the menu's.
                menu = self.client.get(reverse('cashbook')).content.decode()
                self.assertEqual(menu.count(f'href="{reverse("legacy_home")}"'), 1)
                for name in ('old_bill_list', 'opening_stock', 'opening_balances'):
                    self.assertNotIn(f'href="{reverse(name)}"', menu)

    def test_the_page_offers_what_each_role_can_open(self):
        page = self.client.get(reverse('legacy_home')).content.decode()
        for name in ('old_bill_list', 'opening_stock', 'opening_balances'):
            self.assertIn(f'href="{reverse(name)}"', page)

        self.client.force_login(_user('Office'))
        page = self.client.get(reverse('legacy_home')).content.decode()
        self.assertIn(f'href="{reverse("old_bill_list")}"', page)
        self.assertNotIn(f'href="{reverse("opening_stock")}"', page)
        self.assertNotIn(f'href="{reverse("opening_balances")}"', page)

        self.client.force_login(_user('Floor'))
        self.assertEqual(self.client.get(reverse('legacy_home')).status_code, 403)

    def test_each_screen_leads_back_to_the_page(self):
        for name in ('old_bill_list', 'opening_stock', 'opening_balances'):
            with self.subTest(name=name):
                self.assertContains(self.client.get(reverse(name)),
                                    f'<a href="{reverse("legacy_home")}" class="pg-back">')


@override_settings(LEGACY_DATA_LOCKED=True)
class TheGoLiveLockTests(LegacyBase):
    """`LEGACY_DATA_LOCKED` on Railway: after go-live day both screens are the
    record — figures only, and every POST refused, owners included."""

    def setUp(self):
        super().setUp()
        OpeningStock.objects.create(item=self.oil, quantity=D('38'), unit_cost=D('500'))
        self.biljo.opening_balance = D('245000')
        self.biljo.save(update_fields=['opening_balance'])
        self.biljo.update_totals()

    def test_both_screens_show_the_figures_and_no_boxes(self):
        for name in ('opening_stock', 'opening_balances'):
            with self.subTest(name=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn('Locked since go-live', html)
                self.assertNotIn('class="lg-in"', html)
                self.assertNotIn('<button type="submit" class="lg-save-btn">', html)
                self.assertNotIn(FAB, html)          # nothing to save, nothing to press
        stock = self.client.get(reverse('opening_stock')).content.decode()
        self.assertIn('Castrol Edge 5W30', stock)
        self.assertNotIn('Air Filter A4', stock)          # never counted, so not listed
        self.assertIn('245,000', self.client.get(reverse('opening_balances')).content.decode())

    def test_a_post_is_refused_even_for_an_owner_and_nothing_moves(self):
        response = self.count(oil=('99', '1'), air=('5', '100'))
        self.assertContains(response, 'Locked since go-live')
        row = OpeningStock.objects.get()
        self.assertEqual((row.quantity, row.unit_cost), (D('38.00'), D('500.00')))
        self.assertEqual(self.fresh(self.oil).current_stock, D('38'))

        self.owe(spare='1', supply='999')
        self.assertEqual(self.fresh(self.biljo).opening_balance, D('245000'))
        self.assertEqual(self.biljo.get_pending_balance, D('245000'))
        self.assertEqual(self.fresh(self.fluid).opening_balance, 0)

    def test_the_page_marks_both_locked_and_old_bills_stays_open(self):
        page = self.client.get(reverse('legacy_home')).content.decode()
        self.assertEqual(page.count('aria-label="Locked"'), 2)
        self.assertEqual(self.client.get(reverse('old_bill_add')).status_code, 200)


class TheLockIsOffUnlessSetTests(LegacyBase):

    def test_it_starts_unlocked_and_says_nothing_about_a_lock(self):
        self.assertFalse(settings.LEGACY_DATA_LOCKED)
        self.assertNotIn('aria-label="Locked"', self.client.get(reverse('legacy_home')).content.decode())
        self.assertNotContains(self.client.get(reverse('opening_stock')), 'Locked since go-live')


class TheLockButtonTests(LegacyBase):
    """
    The lock an owner presses. It is a ROW, not a host setting, so it travels
    with the data: a backup restored anywhere, or the whole system moved, is
    still locked — and if it ever did come back open, the same button locks it
    again. Nothing in the app can undo it.
    """

    def test_an_owner_locks_it_and_both_screens_go_read_only(self):
        self.count(oil=('38', '500'))
        self.owe(spare='245000')

        response = self.client.post(reverse('legacy_lock'), follow=True)
        self.assertContains(response, 'Legacy Data is locked')
        self.assertEqual(LegacyDataLock.objects.get().locked_by, self.owner)

        for name in ('opening_stock', 'opening_balances'):
            with self.subTest(name=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn('Locked since go-live', html)
                self.assertNotIn('class="lg-in"', html)

        # And the figures are frozen, POST or no POST.
        self.count(oil=('1', '1'))
        self.owe(spare='1')
        self.assertEqual(OpeningStock.objects.get().quantity, D('38.00'))
        self.assertEqual(self.fresh(self.biljo).opening_balance, D('245000'))

    def test_pressing_it_again_changes_nothing(self):
        self.client.post(reverse('legacy_lock'))
        first = LegacyDataLock.objects.get()
        response = self.client.post(reverse('legacy_lock'), follow=True)
        self.assertContains(response, 'already locked')
        self.assertEqual(LegacyDataLock.objects.count(), 1)
        self.assertEqual(LegacyDataLock.objects.get().locked_at, first.locked_at)

    def test_only_an_owner_can_press_it_and_only_by_posting(self):
        self.assertEqual(self.client.get(reverse('legacy_lock')).status_code, 405)
        for role in ('Office', 'Floor'):
            with self.subTest(role=role):
                self.client.force_login(_user(role))
                self.assertEqual(self.client.post(reverse('legacy_lock')).status_code, 403)
        self.assertFalse(LegacyDataLock.objects.exists())

    def test_the_page_offers_it_only_while_unlocked_and_only_to_an_owner(self):
        page = self.client.get(reverse('legacy_home')).content.decode()
        self.assertIn('id="lgLockBtn"', page)
        self.assertIn(f'action="{reverse("legacy_lock")}"', page)

        self.client.force_login(_user('Office'))
        self.assertNotIn('id="lgLockBtn"', self.client.get(reverse('legacy_home')).content.decode())

        self.client.force_login(self.owner)
        self.client.post(reverse('legacy_lock'))
        page = self.client.get(reverse('legacy_home')).content.decode()
        self.assertNotIn('id="lgLockBtn"', page)          # no way back in the app
        self.assertIn('lg-locked-note', page)
        self.assertIn(self.owner.username, page)          # who locked it, and when

    def test_only_the_server_can_unlock_it(self):
        self.count(oil=('38', '500'))
        self.client.post(reverse('legacy_lock'))

        call_command('unlock_legacy_data', stdout=StringIO())          # dry run
        self.assertTrue(LegacyDataLock.objects.exists())
        self.assertContains(self.client.get(reverse('opening_stock')), 'Locked since go-live')

        call_command('unlock_legacy_data', yes=True, stdout=StringIO())
        self.assertFalse(LegacyDataLock.objects.exists())
        page = self.client.get(reverse('opening_stock')).content.decode()
        self.assertNotIn('Locked since go-live', page)
        self.assertIn('class="lg-in"', page)               # editable again
        self.count(oil=('40', '500'))
        self.assertEqual(OpeningStock.objects.get().quantity, D('40.00'))

    @override_settings(LEGACY_DATA_LOCKED=True)
    def test_the_host_switch_locks_on_its_own_and_the_command_cannot_lift_it(self):
        call_command('unlock_legacy_data', yes=True, stdout=StringIO())
        self.assertContains(self.client.get(reverse('opening_stock')), 'Locked since go-live')


class ThePurgeClearsItTests(LegacyBase):

    def test_the_pre_go_live_purge_clears_the_opening_stock(self):
        OpeningStock.objects.create(item=self.oil, quantity=D('38'), unit_cost=D('500'))
        call_command('purge_business_data', yes=True, stdout=StringIO())
        self.assertFalse(OpeningStock.objects.exists())
        self.assertFalse(Item.objects.exists())

    def test_it_clears_the_lock_too_so_a_purged_system_can_be_typed_again(self):
        LegacyDataLock.objects.create(locked_by=self.owner)
        call_command('purge_business_data', yes=True, stdout=StringIO())
        self.assertFalse(LegacyDataLock.objects.exists())
