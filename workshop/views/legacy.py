"""
LEGACY DATA — the position the workshop was already in when the system started.

The system goes live in a RUNNING workshop: parts are already on the shelf, and
money is already owed to every spare shop and Supplies Shop. Neither can come
in through the daily screens — stock only moves through a Supplies Shop bill,
and a shop's balance is built from its bills and parts — so two go-live screens
type the starting position once, and the everyday workflow runs on from it.

  * OPENING STOCK — per product, how many are on the shelf and what one cost.
    Raises the shelf through the stock signals and creates NO shop balance: on
    go-live day the shelf and the shop balances have no connection, because the
    instalments and the usage had long gone their own ways.
  * OPENING BALANCES — per shop, what its own book says the workshop owes,
    stored EXACTLY AS TYPED. The person entering it adds any unassigned spares
    first and takes their price off this figure by hand; the screen computes
    nothing, on the owner's decision.

Neither is an expense, a payment or cash, so no profit or cash figure moves —
`analysis_engine.py` never reads either. A part taken from opening stock is
charged when it is fitted, at the cost typed here, which is the rule for every
warehouse draw; an opening balance is paid off with the ordinary Record a
Payment, which counts as cash out on the day it is paid, as it should.

OWNER ONLY, both screens and every POST. The drawer carries ONE "Legacy Data"
row (the owner's call: these are go-live screens and should not cost the menu
four lines for ever), which opens `legacy_home` — a page holding Old Bills
(its own screen, views/old_bills.py, Office and Owner) and, for an owner, these
two.
"""
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models.functions import Lower
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from inventory.models import Item, OpeningStock, SupplierShop

from ..decorators import is_owner, office_required, owner_required
from ..models import LegacyDataLock, SpareShop
from ..money import parse_money


def _read(raw, model, field_name, allow_zero=False):
    """
    One typed box as a Decimal, or None when it cannot be used.

    The bound comes from the column (`workshop/money.py`), and a figure with
    MORE THAN TWO DECIMALS is refused rather than rounded — the column holds
    two, and rounding would save a number nobody typed. A comma is refused as
    everywhere else in the app; the message says to type the figure without.
    """
    value = parse_money(raw, model, field_name, allow_zero=allow_zero)
    if value is None or Decimal(str(raw).strip()) != value:
        return None
    if not allow_zero and value <= 0:
        return None
    return value


# THE GO-LIVE LOCK. Once locked, both screens render their figures read-only and
# REFUSE EVERY POST, owners included: the whole point is that nothing inside the
# app can change the starting position after go-live day. The refusal is in the
# view, never only in the template — a crafted POST meets it too.
#
# TWO WAYS TO BE LOCKED, and the ORDER of them is the design:
#   1. `LegacyDataLock` — a ROW, set by an owner from the page. The primary one,
#      because it travels with the data: a backup restored anywhere, or the whole
#      system moved to another host, is still locked. A host-side switch alone
#      would leave a moved system silently OPEN — the owner's point, 2026-09-20.
#   2. `settings.LEGACY_DATA_LOCKED` — the Railway switch, kept as a spare: it
#      locks without anybody pressing anything, which is worth having when the
#      owners are not at a screen. It cannot UNLOCK anything.
# Either one locks. Unlocking needs the server: `manage.py unlock_legacy_data`
# clears the row, and the switch is unset on the host.
LOCKED_MESSAGE = ("Locked since go-live — these are the figures the system started "
                  "from, and they can't be changed here.")


def _locked():
    return LegacyDataLock.is_locked() or bool(getattr(settings, 'LEGACY_DATA_LOCKED', False))


@office_required
def legacy_home(request):
    """The one drawer row's page: the three Legacy Data screens as the drawer's
    own rows, and the button that locks the two go-live ones for good.

    Office sees Old Bills alone — the two go-live screens are `@owner_required`,
    and a door somebody can see but not open is worse than no door.
    """
    owner = is_owner(request.user)
    row = LegacyDataLock.objects.select_related('locked_by').first()
    counted = OpeningStock.objects.all()
    owed = (sum((s.opening_balance for s in SpareShop.objects.filter(is_trashed=False)), Decimal('0'))
            + sum((s.opening_balance for s in SupplierShop.objects.filter(is_active=True)), Decimal('0')))
    worth = sum((r.quantity * r.unit_cost for r in counted), Decimal('0'))
    return render(request, 'workshop/legacy/legacy_home.html', {
        'can_open_go_live': owner,
        'locked': _locked(),
        'lock_row': row,
        # What the last confirmation states before it freezes anything, so the
        # owner reads the figures rather than the promise. Zero on both is
        # allowed — a workshop can genuinely owe nothing and hold no stock, and
        # refusing to lock would trap it — so the card SAYS SO instead.
        'counted': counted.count(),
        'worth': worth,
        'owed': owed,
        'nothing_entered': not counted.exists() and owed == 0,
    })


@owner_required
@require_POST
def legacy_lock(request):
    """Lock Opening Stock and Opening Balances, for good.

    POST only, Owner only, and idempotent: pressing it on an already locked
    section changes nothing rather than restamping who locked it. There is no
    matching unlock view, deliberately — see `LegacyDataLock`.
    """
    if LegacyDataLock.objects.exists():
        messages.info(request, "Legacy Data is already locked.")
    else:
        LegacyDataLock.objects.create(locked_by=request.user)
        messages.success(request, "Legacy Data is locked. The go-live figures can no "
                                  "longer be changed from the app.")
    return redirect('legacy_home')


def _plain(value):
    """A stored Decimal as a person would type it: 38.00 → "38", 1.50 → "1.5"."""
    return format(value.normalize(), 'f') if value is not None else ''


@owner_required
def opening_stock(request):
    """What was on the shelf on go-live day, one row per product."""
    items = list(Item.objects.select_related('category')
                 .order_by(Lower('category__name'), Lower('name')))
    saved = {row.item_id: row for row in OpeningStock.objects.all()}
    typed, problems = {}, {}
    locked = _locked()

    if request.method == 'POST' and locked:
        messages.error(request, LOCKED_MESSAGE)
        return redirect('opening_stock')

    if request.method == 'POST':
        plan = []          # (item, None to clear | (quantity, unit_cost))
        for item in items:
            q_key, c_key = f'qty_{item.pk}', f'cost_{item.pk}'
            # A product added after this page was opened has no boxes in the
            # payload. That is not "cleared" — it is untouched.
            if q_key not in request.POST and c_key not in request.POST:
                continue
            q_raw = request.POST.get(q_key, '').strip()
            c_raw = request.POST.get(c_key, '').strip()
            typed[item.pk] = (q_raw, c_raw)

            if not q_raw and not c_raw:
                plan.append((item, None))
                continue
            if not q_raw:
                problems[item.pk] = "How many are on the shelf?"
                continue
            qty = _read(q_raw, OpeningStock, 'quantity', allow_zero=True)
            if qty is None:
                problems[item.pk] = "The quantity must be a number, with at most two decimals."
                continue
            if qty == 0:                     # nothing on the shelf: no row at all
                plan.append((item, None))
                continue
            if not c_raw:
                problems[item.pk] = "Type the cost per unit — the last price you paid for it."
                continue
            cost = _read(c_raw, OpeningStock, 'unit_cost')
            if cost is None:
                problems[item.pk] = ("The cost must be a rupee amount above 0, without commas "
                                     "and with at most two decimals.")
                continue
            plan.append((item, (qty, cost)))

        if problems:
            # All or nothing: half a count saved is harder to reason about than
            # none, and every box typed comes back exactly as it was typed.
            n = len(problems)
            messages.error(request, f"Nothing was saved — {n} product{'s need' if n != 1 else ' needs'} "
                                    f"a look, marked below.")
        else:
            changed = 0
            with transaction.atomic():
                for item, wanted in plan:
                    row = saved.get(item.pk)
                    if wanted is None:
                        if row is not None:
                            row.delete()        # the signal takes the stock back off
                            changed += 1
                    elif row is None:
                        OpeningStock.objects.create(item=item, quantity=wanted[0], unit_cost=wanted[1])
                        changed += 1
                    elif (row.quantity, row.unit_cost) != wanted:
                        # Saved only when it actually changed: a corrected cost
                        # re-prices every part already used from this stock.
                        row.quantity, row.unit_cost = wanted
                        row.save()
                        changed += 1
            if changed:
                rows = OpeningStock.objects.all()
                worth = sum((r.quantity * r.unit_cost for r in rows), Decimal('0'))
                n = rows.count()
                messages.success(request, f"Opening stock saved — {n} product{'' if n == 1 else 's'} "
                                          f"counted, worth ₹{worth:,.0f} on the shelf.")
            else:
                messages.info(request, "Nothing had changed.")
            return redirect('opening_stock')

    groups, counted, worth = [], 0, Decimal('0')
    for item in items:
        row = saved.get(item.pk)
        if locked and row is None:
            continue                # a locked record lists what was counted, nothing else
        if row is not None:
            counted += 1
            worth += row.quantity * row.unit_cost
        q, c = typed.get(item.pk, (_plain(row.quantity), _plain(row.unit_cost)) if row else ('', ''))
        name = item.category.name if item.category_id else ''
        if not groups or groups[-1]['name'] != name:
            groups.append({'name': name, 'rows': []})
        groups[-1]['rows'].append({
            'item': item, 'qty': q, 'cost': c,
            'worth': row.quantity * row.unit_cost if row is not None else None,
            'problem': problems.get(item.pk, ''),
        })

    return render(request, 'workshop/legacy/opening_stock.html', {
        'groups': groups, 'product_count': len(items), 'counted': counted, 'worth': worth,
        'locked': locked,
    })


@owner_required
def opening_balances(request):
    """What each shop's own book said the workshop owed on go-live day."""
    spare = list(SpareShop.objects.filter(is_trashed=False).order_by(Lower('name')))
    supplies = list(SupplierShop.objects.filter(is_active=True).order_by(Lower('name')))
    sections = (('spare', 'Spare Shops', 'bi-gear-wide-connected', spare),
                ('supply', 'Supplies Shops', 'bi-truck', supplies))
    typed, problems = {}, {}
    locked = _locked()

    if request.method == 'POST' and locked:
        messages.error(request, LOCKED_MESSAGE)
        return redirect('opening_balances')

    if request.method == 'POST':
        plan = []          # (shop, value)
        for key, _label, _icon, shops in sections:
            for shop in shops:
                box = f'{key}_{shop.pk}'
                if box not in request.POST:      # a shop added after the page opened
                    continue
                raw = request.POST.get(box, '').strip()
                typed[box] = raw
                if not raw:                      # an empty box means nothing owed
                    plan.append((shop, Decimal('0')))
                    continue
                value = _read(raw, type(shop), 'opening_balance', allow_zero=True)
                if value is None:
                    problems[box] = ("Type a rupee amount of 0 or more, without commas "
                                     "and with at most two decimals.")
                    continue
                plan.append((shop, value))

        if problems:
            n = len(problems)
            messages.error(request, f"Nothing was saved — {n} box{'es need' if n != 1 else ' needs'} "
                                    f"a look, marked below.")
        else:
            changed = 0
            with transaction.atomic():
                for shop, value in plan:
                    if shop.opening_balance != value:
                        shop.opening_balance = value
                        shop.save(update_fields=['opening_balance'])
                        # The balance on every screen reads the cached total,
                        # and this is what rebuilds it.
                        shop.update_totals()
                        changed += 1
            if changed:
                owed = sum((s.opening_balance for _k, _l, _i, shops in sections for s in shops), Decimal('0'))
                messages.success(request, f"Opening balances saved — ₹{owed:,.0f} owed to shops "
                                          f"from before the system.")
            else:
                messages.info(request, "Nothing had changed.")
            return redirect('opening_balances')

    blocks = []
    for key, label, icon, shops in sections:
        rows = []
        for shop in shops:
            box = f'{key}_{shop.pk}'
            saved_value = _plain(shop.opening_balance) if shop.opening_balance else ''
            balance = shop.get_pending_balance
            rows.append({
                'shop': shop, 'box': box,
                'value': typed.get(box, saved_value),
                # A shop paid ahead is in CREDIT, said in words — never a minus
                # sign, the Profit page's own rule for these balances.
                'owed_now': abs(balance), 'in_credit': balance < 0,
                'problem': problems.get(box, ''),
            })
        blocks.append({'key': key, 'label': label, 'icon': icon, 'rows': rows,
                       'total': sum((s.opening_balance for s in shops), Decimal('0'))})

    return render(request, 'workshop/legacy/opening_balances.html', {
        'blocks': blocks,
        'grand_total': sum((b['total'] for b in blocks), Decimal('0')),
        'locked': locked,
    })
