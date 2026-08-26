"""
seed_meeting_data — the owner-meeting / manual-testing dataset.

    python manage.py seed_meeting_data --yes

WHAT MAKES THIS DIFFERENT FROM `seed_dummy_data`
------------------------------------------------
`seed_dummy_data` randomises everything to look like a real workshop. This one
is deliberately UNIFORM: every job card carries the same concerns, the same job
lines, the same parts and the same amounts. That is the whole point — every
figure on every screen can be checked by multiplying one card by the number of
cards, so "is the Profit page right?" stops being a matter of trust.

    one card  =  5 spares x 1,500  +  inventory 6,500  +  labour 8,000
              =  22,000

    150 cards =  33,00,000 turnover, and nothing on any screen may disagree.

WHAT IT KEEPS (never touched)
    Inventory categories & products   the taxonomy is already correct:
                                      Category = generic part, Item = branded
                                      SKU, which is what the printed invoice
                                      needs.
    Spare shops / Supplies shops      names and their ids
    Staff roster (Mechanic)           names, roles and salaries
    Master lists                      brands, models, spares, concerns
    Logins, groups, sessions

WHAT IT CLEARS
    Every financial record: job cards and their children, fleet accounts and
    payment history, spare-shop payments, supplier restock bills/items and
    payments, cashbook, salary settlements and advances, deletion history.
    Warehouse stock and avg_cost are reset to zero, because both are DERIVED
    from bills and draws and a bulk delete does not reliably wind them back.

ORDER IS LOAD-BEARING
    The opening restock bill is dated THREE DAYS BEFORE the first job card.
    `inventory/costing.py` replays receipts in DATE ORDER, so a draw dated on or
    before its first receipt has no cost basis and is stored NULL — which the
    Profit page then reports as "no cost recorded". Every product must be on the
    shelf, priced, before anything draws from it.

    Every restock bill for a given product uses the SAME unit price, so the
    weighted average is exactly that price and a draw's cost is arithmetic
    anybody can repeat.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Category, Item, ShopCatalogItem, SupplierShop,
    SupplierRestockBill, SupplierRestockItem, SupplierPayment,
)
from workshop.models import (
    JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem, JobCardPhoto,
    SpareShop, SpareShopPayment, BulkPayer, BulkPaymentHistory,
    Mechanic, CashbookEntry, DeletionLog,
    SalaryAdvance, SalaryPayment, SalaryPaymentLine,
    CarBrand, CarModel,
)

D = Decimal

# ── Shape of the dataset ──────────────────────────────────────────────────
DAYS = 100
CARDS = 150
PLATES = 60           # 150 / 60 -> 30 cars visit 3x, 30 visit 2x
MILEAGE = '85000'
LABOUR = D('8000')

# Twelve models, five plates each. Boxster and GLC are not in the master list
# and are added below rather than typed free-hand, so Car Profiles and the brand
# charts group them with everything else.
FLEET = [
    ('BMW', '320d'), ('BMW', '530d'), ('BMW', 'X3'),
    ('Audi', 'A4'), ('Audi', 'A6'), ('Audi', 'A8'),
    ('Porsche', 'Cayenne'), ('Porsche', 'Macan'), ('Porsche', 'Boxster'),
    ('Mercedes-Benz', 'C220d'), ('Mercedes-Benz', 'GLC'), ('Mercedes-Benz', 'A180'),
]
COLOURS = ['Black', 'White', 'Silver', 'Grey', 'Blue', 'Red']

MECHANICS = ['Amlah', 'Hijaz', 'Sabith']

CONCERNS = [
    'Periodic service due',
    'Engine oil and filter change',
    'Brake pads worn, needs replacement',
    'Vibration at high speed',
    'Wheel alignment and balancing required',
]

JOBS = [
    'Engine Oil replaced',
    'Brake Pads - Front replaced',
    'Brake Disc - Front replaced',
    'Drive Belt replaced',
    'Wheel alignment and balancing done',
]

# Shop-route spares. Uniform money on purpose: 1,000 is what the shop billed
# (the LINE TOTAL, never a rate), 1,500 is what the customer pays.
SPARES = ['Brake Pads - Front', 'Brake Disc - Front', 'Wiper Blades',
          'Drive Belt', 'Fuel Filter']
SPARE_COST = D('1000')
SPARE_PRICE = D('1500')

# Warehouse draws. Four products, and every one of them fits any car — an oil
# filter is model-specific, so putting a fixed one on every card would print a
# BMW part number on a Porsche's bill.
#   name -> (qty per card, customer total for the line, cost per unit)
DRAWS = {
    'Castrol 5W-30':             (D('5'), D('4000'), D('500')),
    'Blue Coolant':              (D('1'), D('1000'), D('600')),
    'Bosch Brake Oil DOT 4':     (D('1'), D('1000'), D('600')),
    'Liqui Moly Brake Cleaner':  (D('1'), D('500'),  D('300')),
}
OTHER_UNIT_COST = D('250')      # everything not consumed, stocked at its normal level

# Which Supplies Shop sells what. By name, so the demo reads correctly.
SUPPLIER_CATEGORIES = {
    'Lubricant':     ['Engine Oil'],
    'Fluid manjeri': ['Coolant', 'Brake Oil', 'Brake Cleaner'],
    'Ninoos':        ['Air Filter', 'Cabin Filter', 'Oil Filter'],
}

FLEET_ACCOUNTS = ['Malabar Cabs', 'Zenith Rentals']
FLEET_EVERY = 12        # every 12th card is billed to a fleet account

# What each shop is still owed after the seeded payments. A round number so
# "we owe spare shops" is checkable at a glance: 3 shops x 50,000 = 1,50,000.
SPARE_SHOP_BALANCE = D('50000')
SUPPLIER_BALANCE = D('25000')

CASHBOOK_MONTHLY = [('Rent', D('45000')), ('Electricity', D('12000')),
                    ('Water', D('1500'))]
CASHBOOK_INCOME = ('Scrap sale', D('5000'))
ADVANCE = D('3000')     # each of the three mechanics, once a month


class Command(BaseCommand):
    help = "Wipe every financial record and rebuild a uniform 100-day dataset."

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true',
                            help="Actually run. Without it, only the plan is printed.")

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        from django.db import connection
        self.today = timezone.localdate()
        self.start = self.today - timedelta(days=DAYS - 1)

        self.stdout.write(f"\nDatabase : {connection.settings_dict['ENGINE'].split('.')[-1]} "
                          f"/ {connection.settings_dict['NAME']}")
        self.stdout.write(f"Window   : {self.start} .. {self.today}  ({DAYS} days)")
        self.stdout.write(f"Plan     : {CARDS} cards over {PLATES} plates, "
                          f"{len(SPARES)} spares + {len(DRAWS)} draws + labour each")

        if not opts['yes']:
            self.stdout.write(self.style.WARNING(
                "\nDRY RUN — nothing changed. Re-run with --yes to apply.\n"))
            return

        self._purge()
        self._master_data()
        items = self._restock()
        self._job_cards(items)
        self._shop_payments()
        self._salary()
        self._cashbook()
        self._recount()

    # ------------------------------------------------------------------
    def _purge(self):
        """Clear the financial records, keep the reference data.

        Deliberately NOT `purge_business_data` — that one also drops the
        inventory catalog, the shops and the staff roster, which are exactly
        what is being kept here.
        """
        self.stdout.write("\n[1/6] Clearing financial records")
        for label, qs in [
            ("job card photos", JobCardPhoto.objects.all()),
            ("job card spares", JobCardSpareItem.objects.all()),
            ("job card labour", JobCardLabourItem.objects.all()),
            ("job card concerns", JobCardConcern.objects.all()),
            ("job cards", JobCard.objects.all()),
            ("fleet payment history", BulkPaymentHistory.objects.all()),
            ("fleet accounts", BulkPayer.objects.all()),
            ("spare shop payments", SpareShopPayment.objects.all()),
            ("supplier restock items", SupplierRestockItem.objects.all()),
            ("supplier restock bills", SupplierRestockBill.objects.all()),
            ("supplier payments", SupplierPayment.objects.all()),
            ("cashbook entries", CashbookEntry.objects.all()),
            ("salary payment lines", SalaryPaymentLine.objects.all()),
            ("salary settlements", SalaryPayment.objects.all()),
            ("salary advances", SalaryAdvance.objects.all()),
            ("deletion history", DeletionLog.objects.all()),
        ]:
            n = qs.count()
            if n:
                qs.delete()
            self.stdout.write(f"      {label:26} {n:>6}")

        # Stock and cost are DERIVED — from restock bills on the way in and job
        # card draws on the way out. Both sides have just been deleted, so both
        # must go back to zero; a bulk delete does not reliably wind a signal
        # back, and a leftover balance would make every count on the Low Stock
        # page wrong from the first minute.
        Item.objects.update(current_stock=0, avg_cost=0)
        SpareShop.objects.update(total_purchased_amount=0, total_paid_amount=0)
        SupplierShop.objects.update(total_billed_amount=0, total_paid_amount=0)
        self.stdout.write("      stock, cost and shop ledgers reset to zero")

    # ------------------------------------------------------------------
    def _master_data(self):
        """Make the master list agree with the cars about to be created.

        Two models are missing and one brand exists twice. `CarBrand.name` is
        unique but CASE-SENSITIVE, so 'Bmw' and 'BMW' both inserted at some
        point — and every report that groups by brand would show BMW twice.
        """
        self.stdout.write("\n[2/6] Master list")

        dup = CarBrand.objects.filter(name='Bmw').first()
        keep = CarBrand.objects.filter(name='BMW').first()
        if dup and keep:
            moved = 0
            for m in dup.models.all():
                if not keep.models.filter(name__iexact=m.name).exists():
                    m.brand = keep
                    m.save(update_fields=['brand'])
                    moved += 1
                else:
                    m.delete()
            dup.delete()
            self.stdout.write(f"      merged duplicate brand 'Bmw' into 'BMW' "
                              f"({moved} model(s) carried over)")

        for brand_name, model_name in FLEET:
            brand = CarBrand.objects.filter(name__iexact=brand_name).first()
            if not brand:
                brand = CarBrand.objects.create(name=brand_name)
            if not brand.models.filter(name__iexact=model_name).exists():
                CarModel.objects.create(brand=brand, name=model_name)
                self.stdout.write(f"      added model {brand_name} {model_name}")

    # ------------------------------------------------------------------
    def _restock(self):
        """Fill the shelves, and give every product a cost before anything draws.

        The opening bill is dated three days before the first job card. Costing
        replays receipts in date order, so a draw on or before its first receipt
        has no basis and is stored NULL — reported on the Profit page as "no
        cost recorded". Three days is simply comfortable clearance.
        """
        self.stdout.write("\n[3/6] Supplies shops — catalog and restock bills")

        shops = {s.name: s for s in SupplierShop.objects.all()}
        by_name = {i.name: i for i in Item.objects.select_related('category')}

        # Every product belongs to exactly one shop, by category.
        owner = {}
        for shop_name, cats in SUPPLIER_CATEGORIES.items():
            shop = shops.get(shop_name)
            if not shop:
                continue
            for item in Item.objects.filter(category__name__in=cats):
                owner[item.id] = shop
                ShopCatalogItem.objects.get_or_create(shop=shop, item=item,
                                                      defaults={'is_active': True})
        # Anything a category rule missed goes to the first shop, so no product
        # is left unsellable.
        fallback = next(iter(shops.values()))
        for item in Item.objects.all():
            if item.id not in owner:
                owner[item.id] = fallback
                ShopCatalogItem.objects.get_or_create(shop=fallback, item=item,
                                                      defaults={'is_active': True})

        # How much of each consumed product the 150 cards will take, plus a
        # buffer so the shelf ends positive rather than at exactly zero.
        needed = {name: qty * CARDS + qty * 10 for name, (qty, _p, _c) in DRAWS.items()}

        opening_date = self.start - timedelta(days=3)
        lines = {}          # shop -> [(item, qty, total_price)]

        for item in Item.objects.select_related('category'):
            if item.name in DRAWS:
                # 60% of the run up front, the rest in monthly top-ups below.
                qty = (needed[item.name] * D('0.6')).quantize(D('1'))
                unit = DRAWS[item.name][2]
            else:
                qty = item.average_stock or D('5')
                unit = OTHER_UNIT_COST
            lines.setdefault(owner[item.id], []).append((item, qty, qty * unit))

        made = self._bill(lines, opening_date)
        self.stdout.write(f"      opening bill{'s' if made > 1 else ''} on {opening_date}: {made}")

        # Monthly top-ups for the four consumed products only.
        for n, day_offset in enumerate((25, 55, 85), start=1):
            top = {}
            for name, (qty_per_card, _p, unit) in DRAWS.items():
                item = by_name[name]
                qty = (needed[name] * D('0.4') / 3).quantize(D('1'))
                top.setdefault(owner[item.id], []).append((item, qty, qty * unit))
            made = self._bill(top, self.start + timedelta(days=day_offset))
            self.stdout.write(f"      top-up {n} on {self.start + timedelta(days=day_offset)}: {made} bill(s)")

        return by_name

    def _bill(self, lines_by_shop, when):
        """One restock bill per shop for the given lines. Signals move the stock."""
        count = 0
        for shop, rows in lines_by_shop.items():
            if not rows:
                continue
            with transaction.atomic():
                bill = SupplierRestockBill.objects.create(
                    supplier=shop, bill_date=when, discount_amount=D('0'))
                for item, qty, total in rows:
                    SupplierRestockItem.objects.create(
                        bill=bill, item=item, quantity=qty, total_price=total)
                bill.update_totals()
            count += 1
        return count

    # ------------------------------------------------------------------
    def _job_cards(self, by_name):
        """150 identical cards, spread over the window, every one settled."""
        self.stdout.write("\n[4/6] Job cards")

        mechs = {m.name: m for m in Mechanic.objects.filter(name__in=MECHANICS)}
        mech_list = [mechs[n] for n in MECHANICS if n in mechs]
        shops = list(SpareShop.objects.order_by('id'))
        payers = [BulkPayer.objects.create(customer_name=n) for n in FLEET_ACCOUNTS]

        draw_items = {name: by_name[name] for name in DRAWS if name in by_name}
        missing = [n for n in DRAWS if n not in by_name]
        if missing:
            self.stdout.write(self.style.ERROR(f"      MISSING PRODUCTS: {missing}"))

        fleet_cards = {p.id: [] for p in payers}
        made = 0

        for n in range(CARDS):
            plate_no = n % PLATES
            brand, model = FLEET[plate_no % len(FLEET)]
            reg = f"KL 10 AA {1000 + plate_no}"
            admitted = self.start + timedelta(days=(n * DAYS) // CARDS)
            mech = mech_list[n % len(mech_list)] if mech_list else None

            payer = None
            if n % FLEET_EVERY == 0 and payers:
                payer = payers[(n // FLEET_EVERY) % len(payers)]

            with transaction.atomic():
                card = JobCard.objects.create(
                    registration_number=reg,
                    brand_name=brand,
                    model_name=model,
                    car_color=COLOURS[plate_no % len(COLOURS)],
                    mileage=MILEAGE,
                    admitted_date=admitted,
                    lead_mechanic=mech,
                    labour_amount=LABOUR,
                    bulk_payer=payer,
                )

                JobCardConcern.objects.bulk_create([
                    JobCardConcern(job_card=card, concern_text=c, status='FIXED')
                    for c in CONCERNS
                ])
                JobCardLabourItem.objects.bulk_create([
                    JobCardLabourItem(job_card=card, job_description=j, amount=D('0'))
                    for j in JOBS
                ])

                # Shop spares. The shop rotates with the card so all three
                # ledgers fill evenly — 150 x 5 = 750 rows, 250 per shop.
                for j, name in enumerate(SPARES):
                    JobCardSpareItem.objects.create(
                        job_card=card,
                        spare_part_name=name,
                        source=JobCardSpareItem.SOURCE_SHOP,
                        shop=shops[(n + j) % len(shops)] if shops else None,
                        quantity=D('1'),
                        unit_price=SPARE_COST,
                        total_price=SPARE_PRICE,
                        status='RECEIVED',
                        ordered_date=admitted,
                        received_date=admitted + timedelta(days=1),
                    )

                # Warehouse draws. `unit_price` is left alone on purpose —
                # JobCardSpareItem.save() snapshots Item.avg_cost onto it, and
                # that snapshot is the whole cost side of the inventory route.
                for name, (qty, price, _unit) in DRAWS.items():
                    item = draw_items.get(name)
                    if not item:
                        continue
                    JobCardSpareItem.objects.create(
                        job_card=card,
                        spare_part_name=item.name,
                        source=JobCardSpareItem.SOURCE_INVENTORY,
                        item=item,
                        quantity=qty,
                        total_price=price,
                    )

                card.update_totals()
                card.refresh_from_db()

                card.completed = True
                handover = admitted + timedelta(days=2)
                card.completed_date = handover            # DateField
                card.received_amount = card.total_bill_amount
                card.discount_amount = D('0')
                # paid_date is a DateTimeField. Handing it a bare date makes
                # Django build a NAIVE midnight and warn, so pin the moment in
                # IST the way seed_dummy_data already does — the counter is
                # open in the afternoon, and midnight would be the one instant
                # that flips across a day boundary in another timezone.
                card.paid_date = timezone.make_aware(
                    datetime.combine(handover, time(14, 30)))
                if payer:
                    card.payment_status = 'BULK_PAID'
                    fleet_cards[payer.id].append((card, admitted))
                else:
                    card.payment_status = 'PAID'
                    card.payment_method = ['CASH', 'UPI', 'CARD', 'TRANSFER'][n % 4]
                card.save()

            made += 1
            if made % 25 == 0:
                self.stdout.write(f"      {made}/{CARDS} cards")

        # Fleet accounts settle in full, one payment row per card, so the
        # section's own invariant holds:
        #     sum(card.received) + advance_balance == sum(history.amount)
        for payer in payers:
            rows = fleet_cards[payer.id]
            with transaction.atomic():
                for card, when in rows:
                    BulkPaymentHistory.objects.create(
                        bulk_payer=payer,
                        amount=card.total_bill_amount,
                        payment_method='TRANSFER',
                        jobs_affected=1,
                        details=f"Settled {card.registration_number} ({when})",
                    )
                payer.update_totals()
            self.stdout.write(f"      fleet '{payer.customer_name}': {len(rows)} card(s) settled")

        for shop in shops:
            shop.update_totals()
        self.stdout.write(f"      {made} cards created, spare-shop ledgers refreshed")

    # ------------------------------------------------------------------
    def _shop_payments(self):
        """Pay the shops down to a round remaining balance.

        Without this both ledgers read "purchased X, paid nothing", which is not
        a workshop anyone recognises, and the payment-history screens have
        nothing on them. The leftover is a round figure on purpose so "we owe
        spare shops" can be checked without opening a calculator.

        Paid in two instalments a month apart, so the history list itself has
        something to show.
        """
        self.stdout.write("\n[5/7] Shop payments")

        for shop in SpareShop.objects.all():
            shop.update_totals()
            shop.refresh_from_db()
            owing = shop.total_purchased_amount - SPARE_SHOP_BALANCE
            if owing <= 0:
                continue
            half = (owing / 2).quantize(D('1'))
            # Dated inside the window and spaced like the supplier instalments
            # below, so "a month apart" is true of the rows and not just of the
            # docstring. Undated, both would land on the day the seeder ran.
            for n, (amount, offset) in enumerate(
                    ((half, 30), (owing - half, 70)), start=1):
                SpareShopPayment.objects.create(
                    shop=shop, amount=amount, payment_method='TRANSFER',
                    date=self.start + timedelta(days=offset),
                    note=f"Instalment {n}")
            shop.update_totals()
            shop.refresh_from_db()
            self.stdout.write(f"      {shop.name:15} paid {shop.total_paid_amount:>10} "
                              f"balance {shop.get_pending_balance}")

        for shop in SupplierShop.objects.all():
            shop.update_totals()
            shop.refresh_from_db()
            owing = shop.total_billed_amount - SUPPLIER_BALANCE
            if owing <= 0:
                continue
            half = (owing / 2).quantize(D('1'))
            for n, (amount, offset) in enumerate(
                    ((half, 30), (owing - half, 70)), start=1):
                SupplierPayment.objects.create(
                    supplier=shop, amount=amount, payment_method='TRANSFER',
                    date=self.start + timedelta(days=offset),
                    note=f"Instalment {n}")
            shop.update_totals()
            shop.refresh_from_db()
            self.stdout.write(f"      {shop.name:15} paid {shop.total_paid_amount:>10} "
                              f"balance {shop.get_pending_balance}")

    # ------------------------------------------------------------------
    def _salary(self):
        """Three settled months, the current one left open.

        That is the workshop's real rhythm — a month is settled in the first
        days of the next one — and it is what the Profit page's unsettled-wages
        banner is built to report.
        """
        self.stdout.write("\n[5/6] Salary and advances")
        staff = list(Mechanic.objects.filter(is_active=True).exclude(current_salary=None))
        if not staff:
            self.stdout.write("      no staff with a salary — skipped")
            return

        mech_names = set(MECHANICS)
        months = []
        cursor = self.start.replace(day=1)
        while cursor <= self.today:
            months.append(cursor)
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

        current = self.today.replace(day=1)
        for month in months:
            # One advance per mechanic, mid-month.
            for m in staff:
                if m.name in mech_names:
                    SalaryAdvance.objects.create(
                        staff=m, amount=ADVANCE,
                        date=month + timedelta(days=14),
                        note='Mid-month advance')

            if month == current:
                continue        # the open month — settled next month, not now

            payment = SalaryPayment.objects.create(month=month)
            for m in staff:
                advance = ADVANCE if m.name in mech_names else D('0')
                SalaryPaymentLine.objects.create(
                    payment=payment, staff=m,
                    salary_used=m.current_salary,
                    leave_days=D('0'),
                    overtime_amount=D('0'),
                    advance_used=advance,
                    net_amount=m.current_salary - advance,
                )
        # Every earlier month is closed once a newer one is settled.
        settled = list(SalaryPayment.objects.order_by('month'))
        for p in settled[:-1]:
            p.superseded = True
            p.save(update_fields=['superseded'])
        self.stdout.write(f"      {len(settled)} month(s) settled, "
                          f"{current.strftime('%B %Y')} left open")

    # ------------------------------------------------------------------
    def _cashbook(self):
        """Fixed monthly running costs, so the breakdown is checkable by eye."""
        self.stdout.write("\n[6/6] Cashbook")
        cursor = self.start.replace(day=1)
        n = 0
        while cursor <= self.today:
            when = max(cursor + timedelta(days=4), self.start)
            for category, amount in CASHBOOK_MONTHLY:
                CashbookEntry.objects.create(
                    entry_type='EXPENSE', category=category, amount=amount,
                    payment_method='CASH', date=when,
                    description=f"{category} — {cursor.strftime('%B %Y')}")
                n += 1
            CashbookEntry.objects.create(
                entry_type='INCOME', category=CASHBOOK_INCOME[0],
                amount=CASHBOOK_INCOME[1], payment_method='CASH', date=when,
                description=f"Scrap and waste oil — {cursor.strftime('%B %Y')}")
            n += 1
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.stdout.write(f"      {n} entries")

    # ------------------------------------------------------------------
    def _recount(self):
        """Print what was built, so the run itself is the first verification."""
        from django.db.models import Sum
        self.stdout.write("\n" + "=" * 58)
        cards = JobCard.objects.count()
        billed = JobCard.objects.aggregate(t=Sum('total_bill_amount'))['t'] or 0
        received = JobCard.objects.aggregate(t=Sum('received_amount'))['t'] or 0
        self.stdout.write(f"  job cards          {cards}")
        self.stdout.write(f"  total billed       {billed}")
        self.stdout.write(f"  total received     {received}")
        self.stdout.write(f"  distinct vehicles  "
                          f"{JobCard.objects.values('registration_number').distinct().count()}")
        self.stdout.write(f"  spare rows         {JobCardSpareItem.objects.count()}")
        self.stdout.write(f"  restock bills      {SupplierRestockBill.objects.count()}")
        self.stdout.write(f"  salary months      {SalaryPayment.objects.count()}")
        self.stdout.write(f"  cashbook entries   {CashbookEntry.objects.count()}")
        self.stdout.write(self.style.SUCCESS("\n  Done.\n"))
