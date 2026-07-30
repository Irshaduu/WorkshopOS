"""
Management command: seed_dummy_data
------------------------------------
Seeds realistic dev/demo data on top of the existing master data
(CarBrand/CarModel/SparePart, loaded via load_master_data).

Creates:
  - 3 Spare Shops, 3 Supplier Shops, 5 Categories, 9 products
  - A monthly restock bill per supplier shop, plus periodic part-payments.
    Restocking is monthly rather than once up front because continuous
    consumption over a multi-year range would otherwise drive warehouse stock
    thousands of units negative.
  - 2 Fleet Accounts, settled by a lump payment each quarter through the real
    cascade (oldest-first, PENDING -> PARTIAL -> BULK_PAID)
  - Monthly cashbook entries (rent, salaries, utilities, misc income)
  - Job cards across the requested range, each with concerns, spares (mixed
    warehouse stock and external spare-shop purchases), labour, completion and
    billing that follows the real rules: normal customers settle once at
    pickup with any shortfall booked as discount; fleet cards are settled only
    by the cascade.
  - Returning customers: a share of each day's cars are repeat visits by
    vehicles already in the pool, always at least 30 days after their last
    visit, so the one-active-job-card-per-plate rule is never contended.

Every write goes through the ORM so model signals fire (stock sync,
update_totals). Work is committed one month at a time rather than in a single
transaction, so a long remote run never holds one enormous open transaction.

Uses only already-registered Mechanic/Assistant Mechanic staff, and creates
the roster itself only if none exists.

Usage:
    python manage.py seed_dummy_data
    python manage.py seed_dummy_data --start 2021-07-26 --end 2026-07-25
    python manage.py seed_dummy_data --cards-per-day 3
"""

import json
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.db.models import F, ExpressionWrapper, DecimalField, Sum

from workshop.models import (
    Mechanic, SpareShop, SpareShopPayment, BulkPayer, BulkPaymentHistory,
    CarBrand, CarModel, SparePart, ConcernSolution, CashbookEntry,
    JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem,
    SalaryAdvance, SalaryPayment, SalaryPaymentLine,
)
from inventory.models import (
    Category, Item, SupplierShop, ShopCatalogItem,
    SupplierRestockBill, SupplierRestockItem, SupplierPayment,
)


# =============================================================================
# Reference data
# =============================================================================

SPARE_SHOP_NAMES = ["Kochi Auto Spares", "Malabar Spare Parts", "Trivandrum Motor Spares"]

SUPPLIER_SHOPS_DATA = [
    ("Kerala Auto Distributors", [
        ("Fluids", "Engine Oil", 40),
        ("Filters", "Oil Filter", 30),
        ("Filters", "Air Filter", 25),
    ]),
    ("Cochin Brake & Clutch Traders", [
        ("Brake Parts", "Brake Pads – Front", 20),
        ("Brake Parts", "Brake Pads – Rear", 20),
        ("Brake Parts", "Brake Disc – Front", 15),
    ]),
    ("Malabar Electricals & Accessories", [
        ("Electricals", "Battery", 12),
        ("Electricals", "Spark Plug", 30),
        ("Wipers & Accessories", "Wiper Blades", 20),
    ]),
]

# product -> (unit cost to workshop, customer-facing unit price)
WAREHOUSE_ITEM_PRICES = {
    "Engine Oil": (420, 600),
    "Oil Filter": (280, 420),
    "Air Filter": (320, 480),
    "Brake Pads – Front": (2200, 3000),
    "Brake Pads – Rear": (2000, 2800),
    "Brake Disc – Front": (3200, 4200),
    "Battery": (5500, 6800),
    "Spark Plug": (450, 700),
    "Wiper Blades": (650, 950),
}

FLEET_ACCOUNTS = ["Kerala Green Cabs", "Spice Coast Corporate Fleet"]

# Staff created only when the roster is empty (a fresh database).
# Amlah is seeded retired on purpose: it exercises the deactivated-staff path
# (excluded from the job-card picker, sorted below active staff).
# name, role, is_active, monthly salary
DEFAULT_STAFF = [
    ("Mohammed", Mechanic.ROLE_MECHANIC, True, 32000),
    ("Hijaz", Mechanic.ROLE_MECHANIC, True, 28000),
    ("Amlah", Mechanic.ROLE_MECHANIC, False, 26000),
    ("Soo", Mechanic.ROLE_ASSISTANT_MECHANIC, True, 19000),
    ("Irshu", Mechanic.ROLE_OFFICE_STAFF, True, 24000),
    ("Kaka", Mechanic.ROLE_GENERAL_HELPER, True, 15000),
]

BRAND_WEIGHT = {
    "BMW": 3, "Audi": 3, "Mercedes-Benz": 3, "Volvo": 3, "Land Rover": 3,
    "Jaguar": 3, "Lexus": 3, "Volkswagen": 3,
    "Porsche": 2, "Mini Cooper": 2,
    "Aston Martin": 1, "Bentley": 1, "Maserati": 1, "Rolls-Royce": 1,
}

CAR_COLORS = [
    "White", "White", "White", "Black", "Black", "Black", "Silver", "Silver",
    "Grey", "Grey", "Blue", "Dark Blue", "Red", "Brown", "Dark Brown",
    "Light Blue", "Green", "Dark Green", "Yellow", "Light Green",
]

CONCERNS_POOL = [
    "Periodic service due", "Engine oil and filter change",
    "Brake pads worn, needs replacement", "AC not cooling properly",
    "Strange noise from front suspension", "Battery not holding charge",
    "Wheel alignment and balancing required", "Clutch slipping while driving",
    "Coolant leak near radiator", "Headlight bulb not working",
    "Power steering making noise", "Timing belt due for replacement",
    "Vibration at high speed", "Fuel efficiency dropped noticeably",
    "AC compressor noise on start", "Check engine light on",
    "Water pump leaking", "Suspension bottoming out on bumps",
    "Infotainment screen freezing", "Sunroof not closing fully",
]

LABOUR_POOL = [
    ("General Service Labour", 800, 2000),
    ("Wheel Alignment & Balancing", 600, 1200),
    ("AC Gas Refill & Service", 1000, 2200),
    ("Brake Service Labour", 500, 1200),
    ("Diagnostic Check", 400, 900),
    ("Engine Tune-up", 900, 2000),
    ("Clutch Replacement Labour", 1500, 3200),
    ("Denting & Painting (minor)", 1800, 4000),
    ("Suspension Overhaul Labour", 1200, 2800),
    ("Electrical Fault Finding", 500, 1400),
]

# Deliberately NO salary line here. Wages are seeded through Salary & Advance
# (see _seed_salary), which is where the Profit page reads them from. A cashbook
# entry named like wages is exactly what that page warns about as a possible
# double count, so seeding one would leave the demo permanently flagged.
CASHBOOK_MONTHLY = [
    ("EXPENSE", "Workshop Rent", 45000, 45000),
    ("EXPENSE", "Electricity Bill", 8000, 16000),
    ("EXPENSE", "Water & Utilities", 1500, 3500),
    ("EXPENSE", "Workshop Consumables", 4000, 12000),
]
CASHBOOK_OCCASIONAL = [
    ("EXPENSE", "Tool Purchase", 3000, 28000),
    ("EXPENSE", "Equipment Maintenance", 2500, 15000),
    ("EXPENSE", "Staff Welfare", 2000, 9000),
    ("INCOME", "Scrap Sale", 3000, 14000),
    ("INCOME", "Miscellaneous Income", 2000, 11000),
]

FIRST_NAMES = [
    "Anoop", "Rejith", "Sreekutty", "Muhammed", "Fathima", "Devika", "Arjun", "Nithya",
    "Sajeev", "Priya", "Vishnu", "Anjali", "Rahul", "Meera", "Sanjay", "Divya", "Kiran",
    "Lakshmi", "Manoj", "Reshma", "Naveen", "Athira", "Prasad", "Sruthi", "Vinod",
    "Aiswarya", "Suresh", "Neethu", "Ajay", "Deepa", "Shameer", "Nisha", "Faisal",
    "Gopika", "Harish", "Jaseem", "Kavya", "Linto", "Midhun", "Nandana",
]
LAST_NAMES = [
    "Nair", "Menon", "Pillai", "Varma", "Kumar", "Thomas", "Jose", "Iqbal", "Rahman",
    "Krishnan", "Das", "Mathew", "Joseph", "Nambiar", "Panicker", "Shenoy", "Kurup",
]

KERALA_DISTRICTS = ["01", "02", "03", "05", "06", "07", "08", "09", "10", "11", "14"]
PLATE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

DEFAULT_START = date(2021, 7, 26)
DEFAULT_END = date(2026, 7, 25)

# Chance a given slot is a returning vehicle rather than a brand-new one.
RETURNING_RATE = 0.45
MIN_REVISIT_GAP_DAYS = 30


class Command(BaseCommand):
    help = "Seed realistic dummy data: shops, products, restocks, fleet accounts, cashbook and job cards."

    def add_arguments(self, parser):
        parser.add_argument('--start', type=str, default=DEFAULT_START.isoformat(),
                            help=f"First admitted date, YYYY-MM-DD (default {DEFAULT_START})")
        parser.add_argument('--end', type=str, default=DEFAULT_END.isoformat(),
                            help=f"Last admitted date, YYYY-MM-DD (default {DEFAULT_END})")
        parser.add_argument('--cards-per-day', type=int, default=3,
                            help="Job cards created per day (default 3)")
        parser.add_argument('--seed', type=int, default=2026,
                            help="Random seed for reproducible output (default 2026)")

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        try:
            start = date.fromisoformat(options['start'])
            end = date.fromisoformat(options['end'])
        except ValueError as exc:
            raise CommandError(f"Invalid date: {exc}")

        if end < start:
            raise CommandError("--end must not be earlier than --start.")

        per_day = options['cards_per_day']
        if per_day < 1:
            raise CommandError("--cards-per-day must be at least 1.")

        random.seed(options['seed'])

        if JobCard.objects.filter(admitted_date__range=(start, end)).exists():
            raise CommandError(
                f"Job cards already exist between {start} and {end}. "
                f"Run `purge_business_data --yes` first if you want to re-seed."
            )

        # Validate every prerequisite BEFORE writing anything, so a missing
        # dependency can't leave half-built foundations behind.
        brand_model_pool = self._build_brand_model_pool()
        warehouse_names = list(WAREHOUSE_ITEM_PRICES.keys())
        external_spares = self._build_external_spare_pool(warehouse_names)

        total_days = (end - start).days + 1
        self.stdout.write(
            f"Seeding {start} to {end} ({total_days:,} days x {per_day}/day "
            f"= {total_days * per_day:,} job cards)\n"
        )

        # --- Foundations (one short transaction) ---
        with transaction.atomic():
            mechanics = self._ensure_staff()
            spare_shops = self._create_spare_shops()
            supplier_shops, shop_items = self._create_supplier_shops_and_products()
            fleet_accounts = self._create_fleet_accounts()
            self._seed_concern_pool()

        # name -> Item. A warehouse draw points at its product by FK now, so the
        # seeder has to resolve the real row rather than just naming it and
        # trusting the old name-match to find it.
        warehouse_items = {
            item.name: item
            for items in shop_items.values() for item in items
        }

        # The last month is deliberately left unsettled (see _seed_salary), so
        # the range end has to be known inside the per-month close.
        self._range_end = end

        # --- Main loop, committed one month at a time ---
        vehicles = {}          # reg -> dict(brand, model, customer, contact, last_visit)
        used_regs = set()
        created = 0
        month_cursor = None
        month_batch_start = None

        current = start
        while current <= end:
            month_key = (current.year, current.month)
            if month_key != month_cursor:
                if month_cursor is not None:
                    self._close_month(
                        month_batch_start, current - timedelta(days=1),
                        supplier_shops, shop_items, spare_shops, fleet_accounts,
                    )
                month_cursor = month_key
                month_batch_start = current
                self._open_month(current, supplier_shops, shop_items)

            with transaction.atomic():
                for _ in range(per_day):
                    self._create_one_job_card(
                        current, vehicles, used_regs, brand_model_pool, mechanics,
                        spare_shops, warehouse_items, external_spares, fleet_accounts,
                    )
                    created += 1

            if created % (per_day * 90) == 0:
                self.stdout.write(f"  ... {created:,} job cards (through {current})")

            current += timedelta(days=1)

        # Close the final month
        self._close_month(
            month_batch_start, end, supplier_shops, shop_items,
            spare_shops, fleet_accounts,
        )

        # Fleet totals are denormalized and only recomputed when a payment runs
        # (quarterly). Cards added since the last one would leave
        # total_billed_amount stale, so refresh every account at the end.
        with transaction.atomic():
            for payer in fleet_accounts:
                payer.update_totals()

        self.stdout.write(self.style.SUCCESS(
            f"\n[DONE] {created:,} job cards, {len(vehicles):,} distinct vehicles, "
            f"{len(spare_shops)} spare shops, {len(supplier_shops)} supplier shops, "
            f"{len(fleet_accounts)} fleet accounts ({start} to {end})."
        ))

    # ------------------------------------------------------------------
    # Foundations
    # ------------------------------------------------------------------

    def _ensure_staff(self):
        if not Mechanic.objects.exists():
            for name, role, is_active, salary in DEFAULT_STAFF:
                Mechanic.objects.create(name=name, role=role, is_active=is_active,
                                        current_salary=Decimal(salary))
            self.stdout.write(f"Staff roster created: {len(DEFAULT_STAFF)}")

        mechanics = list(Mechanic.objects.filter(
            is_active=True, role__in=Mechanic.JOBCARD_ELIGIBLE_ROLES
        ))
        if not mechanics:
            raise CommandError("No active Mechanic/Assistant Mechanic available to assign job cards to.")
        return mechanics

    def _create_spare_shops(self):
        shops = []
        for i, name in enumerate(SPARE_SHOP_NAMES):
            shop, _ = SpareShop.objects.get_or_create(
                name=name,
                defaults={"phone": f"9{800000000 + i * 1111}", "address": f"{name}, Kerala"},
            )
            shops.append(shop)
        return shops

    def _create_supplier_shops_and_products(self):
        supplier_shops, shop_items = [], {}
        for i, (shop_name, products) in enumerate(SUPPLIER_SHOPS_DATA):
            shop, _ = SupplierShop.objects.get_or_create(
                name=shop_name,
                defaults={"phone": f"9{700000000 + i * 1111}", "address": f"{shop_name}, Kerala"},
            )
            supplier_shops.append(shop)
            shop_items[shop.id] = []
            for cat_name, item_name, avg_stock in products:
                category, _ = Category.objects.get_or_create(
                    name__iexact=cat_name, defaults={"name": cat_name}
                )
                item, _ = Item.objects.get_or_create(
                    category=category, name=item_name,
                    defaults={"average_stock": Decimal(avg_stock)},
                )
                ShopCatalogItem.objects.get_or_create(shop=shop, item=item)
                shop_items[shop.id].append(item)
        return supplier_shops, shop_items

    def _create_fleet_accounts(self):
        return [BulkPayer.objects.get_or_create(customer_name=n)[0] for n in FLEET_ACCOUNTS]

    def _seed_concern_pool(self):
        for text in CONCERNS_POOL:
            if not ConcernSolution.objects.filter(concern__iexact=text).exists():
                ConcernSolution.objects.create(concern=text)

    def _build_brand_model_pool(self):
        pool = []
        for brand in CarBrand.objects.all():
            weight = BRAND_WEIGHT.get(brand.name, 1)
            for model in CarModel.objects.filter(brand=brand):
                pool.extend([(brand.name, model.name)] * weight)
        if not pool:
            raise CommandError("No CarBrand/CarModel data found — run load_master_data first.")
        return pool

    def _build_external_spare_pool(self, warehouse_names):
        names = list(SparePart.objects.exclude(name__in=warehouse_names).values_list("name", flat=True))
        return names or ["Assorted Spare Part"]

    # ------------------------------------------------------------------
    # Monthly bookends
    # ------------------------------------------------------------------

    def _open_month(self, month_start, supplier_shops, shop_items):
        """Monthly restock, sized to demand: top each product back up toward the
        quantity the workshop normally keeps, the way a real reorder works.

        A fixed monthly quantity would compound — over 60 months it buries the
        warehouse in stock nobody consumed. Ordering only the shortfall keeps
        levels hovering around average_stock and lets products dip into
        genuine low-stock between restocks.
        """
        with transaction.atomic():
            for shop in supplier_shops:
                lines = []
                for item in shop_items[shop.id]:
                    # current_stock moves via signals as job cards consume it
                    item.refresh_from_db(fields=['current_stock'])
                    target = item.average_stock * Decimal(str(round(random.uniform(1.2, 1.7), 2)))
                    shortfall = target - item.current_stock
                    if shortfall <= 0:
                        continue  # still well stocked — nothing to order
                    qty = Decimal(int(shortfall))
                    if qty > 0:
                        lines.append((item, qty))

                if not lines:
                    continue

                bill = SupplierRestockBill.objects.create(supplier=shop, bill_date=month_start)
                for item, qty in lines:
                    cost, _ = WAREHOUSE_ITEM_PRICES[item.name]
                    SupplierRestockItem.objects.create(
                        bill=bill, item=item, quantity=qty,
                        total_price=(Decimal(cost) * qty).quantize(Decimal("0.01")),
                    )
                bill.update_totals()


    def _seed_salary(self, month_start, month_end):
        """
        Advances handed out through the month, then the month-end settlement.

        The most recent month is deliberately left UNSETTLED. That is the normal
        state of a live workshop mid-month, and it exercises the branch in
        `analysis_engine.salary_expense` that counts loose advances in months with
        no settlement yet — cash already out of the drawer that a settled month
        would otherwise account for.

        Net pay is computed by importing the app's own `_compute_net` rather than
        restating salary − leave − advance here. Two copies of one formula would
        be free to drift, and seeded data that disagreed with the settlement screen
        would be worse than no seeded data at all.
        """
        from workshop.views.salary_advance import _compute_net

        staff = list(Mechanic.objects.filter(is_active=True)
                     .exclude(current_salary__isnull=True))
        if not staff:
            return

        span = max((month_end - month_start).days, 0)
        for person in staff:
            if random.random() < 0.45:
                SalaryAdvance.objects.create(
                    staff=person,
                    amount=Decimal(random.choice([1000, 2000, 2500, 3000, 5000])),
                    date=month_start + timedelta(days=random.randint(0, span)),
                    note="Advance",
                )

        if month_end >= self._range_end:
            return

        payment, _ = SalaryPayment.objects.get_or_create(month=month_start.replace(day=1))
        for person in staff:
            leave = Decimal(random.choice(["0", "0", "0", "0.5", "1", "2"]))
            advance_used = SalaryAdvance.objects.filter(
                staff=person, date__gte=month_start, date__lte=month_end
            ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
            SalaryPaymentLine.objects.update_or_create(
                payment=payment, staff=person,
                defaults={
                    "salary_used": person.current_salary,
                    "leave_days": leave,
                    "advance_used": advance_used,
                    "net_amount": _compute_net(person.current_salary, leave, advance_used),
                },
            )

    def _close_month(self, month_start, month_end, supplier_shops, shop_items,
                     spare_shops, fleet_accounts):
        """End-of-month settlements: supplier and spare-shop payments, cashbook
        entries, and a quarterly fleet cascade payment."""
        with transaction.atomic():
            # Supplier part-payments — leaves a running balance, like real trade credit.
            for shop in supplier_shops:
                outstanding = shop.total_billed_amount - shop.total_paid_amount
                if outstanding > 0:
                    pay = (outstanding * Decimal(str(round(random.uniform(0.55, 0.9), 2)))).quantize(Decimal("0.01"))
                    if pay > 0:
                        SupplierPayment.objects.create(
                            supplier=shop, amount=pay,
                            payment_method=random.choice(["CASH", "UPI", "TRANSFER"]),
                            date=month_end, note="Monthly settlement",
                        )
                        shop.update_totals()

            # Spare shop part-payments against parts bought that month.
            for shop in spare_shops:
                shop.update_totals()
                pending = shop.total_purchased_amount - shop.total_paid_amount
                if pending > 0:
                    pay = (pending * Decimal(str(round(random.uniform(0.6, 0.95), 2)))).quantize(Decimal("0.01"))
                    if pay > 0:
                        SpareShopPayment.objects.create(
                            shop=shop, amount=pay,
                            payment_method=random.choice(["CASH", "UPI", "TRANSFER"]),
                            note="Monthly settlement",
                        )

            self._create_cashbook_entries(month_start, month_end)
            self._seed_salary(month_start, month_end)

        # Fleet settlement once a quarter, through the real cascade.
        if month_end.month % 3 == 0:
            for payer in fleet_accounts:
                self._run_fleet_payment(payer)

    def _create_cashbook_entries(self, month_start, month_end):
        for entry_type, category, lo, hi in CASHBOOK_MONTHLY:
            day = min(random.randint(1, 5), (month_end - month_start).days + 1)
            CashbookEntry.objects.create(
                entry_type=entry_type, category=category,
                amount=Decimal(random.randint(lo, hi)),
                payment_method=random.choice(["CASH", "UPI", "TRANSFER"]),
                date=month_start + timedelta(days=day - 1),
                description=f"{category} for {month_start.strftime('%B %Y')}",
            )
        for entry_type, category, lo, hi in random.sample(CASHBOOK_OCCASIONAL, random.randint(0, 2)):
            span = (month_end - month_start).days
            CashbookEntry.objects.create(
                entry_type=entry_type, category=category,
                amount=Decimal(random.randint(lo, hi)),
                payment_method=random.choice(["CASH", "UPI", "TRANSFER"]),
                date=month_start + timedelta(days=random.randint(0, max(span, 0))),
            )

    # ------------------------------------------------------------------
    # Job cards
    # ------------------------------------------------------------------

    def _random_reg(self, used):
        while True:
            reg = ("KL" + random.choice(KERALA_DISTRICTS)
                   + random.choice(PLATE_LETTERS) + random.choice(PLATE_LETTERS)
                   + str(random.randint(1000, 9999)))
            if reg not in used:
                used.add(reg)
                return reg

    def _random_customer(self):
        contact = random.choice("6789") + "".join(str(random.randint(0, 9)) for _ in range(9))
        return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}", contact

    def _pick_vehicle(self, admitted_date, vehicles, used_regs, brand_model_pool):
        """Either a returning vehicle (last serviced comfortably in the past) or
        a brand-new one."""
        if vehicles and random.random() < RETURNING_RATE:
            cutoff = admitted_date - timedelta(days=MIN_REVISIT_GAP_DAYS)
            eligible = [r for r, v in vehicles.items() if v['last_visit'] <= cutoff]
            if eligible:
                reg = random.choice(eligible)
                vehicles[reg]['last_visit'] = admitted_date
                return reg, vehicles[reg]

        reg = self._random_reg(used_regs)
        brand, model = random.choice(brand_model_pool)
        name, contact = self._random_customer()
        vehicles[reg] = {
            'brand': brand, 'model': model, 'customer': name,
            'contact': contact, 'last_visit': admitted_date,
            'color': random.choice(CAR_COLORS),
        }
        return reg, vehicles[reg]

    def _create_one_job_card(self, admitted_date, vehicles, used_regs, brand_model_pool,
                             mechanics, spare_shops, warehouse_items, external_spares,
                             fleet_accounts):
        reg, v = self._pick_vehicle(admitted_date, vehicles, used_regs, brand_model_pool)

        jobcard = JobCard.objects.create(
            admitted_date=admitted_date,
            brand_name=v['brand'],
            model_name=v['model'],
            registration_number=reg,
            mileage=str(random.randint(8000, 145000)),
            customer_name=v['customer'],
            customer_contact=v['contact'],
            lead_mechanic=random.choice(mechanics),
            car_color=v['color'],
        )

        for text in random.sample(CONCERNS_POOL, random.randint(1, 3)):
            JobCardConcern.objects.create(job_card=jobcard, concern_text=text, status="FIXED")

        for _ in range(random.randint(0, 3)):
            qty = Decimal(random.choice([1, 1, 1, 2]))
            if random.random() < 0.55:
                # Warehouse draw. No shop, no ordering workflow, and no typed cost:
                # the model snapshots Item.avg_cost, which the monthly restock bills
                # have already established by the time any draw happens.
                name = random.choice(list(warehouse_items))
                item = warehouse_items[name]
                _cost, price = WAREHOUSE_ITEM_PRICES[name]
                fields = {}
                if random.random() < 0.3:
                    # Office sometimes enters a per-unit customer rate; more often
                    # they skip it and type the total straight in.
                    fields['customer_rate'] = Decimal(price)
                else:
                    fields['total_price'] = (Decimal(price) * qty).quantize(Decimal("0.01"))
                JobCardSpareItem.objects.create(
                    job_card=jobcard,
                    source=JobCardSpareItem.SOURCE_INVENTORY,
                    item=item, spare_part_name=name, quantity=qty, **fields,
                )
            else:
                name = random.choice(external_spares)
                cost = random.randint(400, 6000)
                price = int(cost * random.uniform(1.25, 1.5))
                shop = random.choice(spare_shops)
                JobCardSpareItem.objects.create(
                    job_card=jobcard,
                    source=JobCardSpareItem.SOURCE_SHOP,
                    spare_part_name=name, quantity=qty,
                    unit_price=Decimal(cost),
                    total_price=(Decimal(price) * qty).quantize(Decimal("0.01")),
                    status="RECEIVED", shop=shop, shop_name=shop.name,
                    ordered_date=admitted_date, received_date=admitted_date,
                )

        for desc, lo, hi in random.sample(LABOUR_POOL, random.randint(1, 2)):
            JobCardLabourItem.objects.create(
                job_card=jobcard, job_description=desc, amount=Decimal(random.randint(lo, hi))
            )

        jobcard.completed = True
        jobcard.completed_date = admitted_date + timedelta(days=random.randint(1, 4))

        if fleet_accounts and random.random() < 0.08:
            # Fleet card: left for the cascade to settle, never billed directly.
            jobcard.bulk_payer = random.choice(fleet_accounts)
            jobcard.payment_status = "PENDING"
            jobcard.received_amount = Decimal("0")
            jobcard.discount_amount = Decimal("0")
            jobcard.save()
        else:
            total = jobcard.total_bill_amount
            if random.random() < 0.05:
                # Genuinely unpaid at pickup.
                jobcard.received_amount = Decimal("0")
                jobcard.payment_status = "PENDING"
                jobcard.discount_amount = Decimal("0")
            else:
                if random.random() < 0.25 and total > 0:
                    shortfall = (total * Decimal(str(round(random.uniform(0.05, 0.15), 3)))).quantize(Decimal("0.01"))
                    received = total - shortfall
                else:
                    received = total
                jobcard.received_amount = received
                jobcard.payment_status = "PAID"
                # Shortfall is booked as discount — the deliberate rule for
                # normal customers, who settle exactly once at pickup.
                jobcard.discount_amount = max(Decimal("0"), total - received)
                jobcard.paid_date = timezone.make_aware(
                    datetime.combine(jobcard.completed_date, time(14, 30))
                )
            jobcard.payment_method = random.choice(["CASH", "UPI", "CARD", "TRANSFER"])
            jobcard.save()

        return jobcard

    # ------------------------------------------------------------------
    # Fleet cascade — mirrors bulk_payer_pay()
    # ------------------------------------------------------------------

    def _run_fleet_payment(self, payer):
        with transaction.atomic():
            pending = list(
                payer.job_cards.filter(payment_status__in=["PENDING", "PARTIAL"])
                .annotate(balance_amount=ExpressionWrapper(
                    F("total_bill_amount") - F("received_amount"), output_field=DecimalField()))
                .order_by("admitted_date", "pk")
            )
            outstanding = sum((j.balance_amount for j in pending), Decimal("0"))
            if outstanding <= 0:
                return

            # Fleets pay most but not all of the quarter's balance.
            lump_sum = (outstanding * Decimal(str(round(random.uniform(0.6, 0.92), 2)))).quantize(Decimal("0.01"))
            if lump_sum <= 0:
                return

            advance_used = payer.advance_balance
            remaining = lump_sum + advance_used
            payer.advance_balance = Decimal("0")

            jobs_updated, details = 0, []
            for job in pending:
                if remaining <= 0:
                    break
                balance = job.balance_amount
                if balance <= 0:
                    continue
                if remaining >= balance:
                    paid = balance
                    job.received_amount += balance
                    job.payment_status = "BULK_PAID"
                    job.discount_amount = Decimal("0")
                    job.paid_date = timezone.make_aware(
                        datetime.combine(job.completed_date or job.admitted_date, time(15, 0))
                    )
                    remaining -= balance
                else:
                    paid = remaining
                    job.received_amount += remaining
                    job.payment_status = "PARTIAL"
                    remaining = Decimal("0")
                job.payment_method = "TRANSFER"
                job.save()
                jobs_updated += 1
                details.append({
                    "job_id": job.pk, "reg": job.registration_number,
                    "car": f"{job.brand_name} {job.model_name}",
                    "paid": str(paid), "status": job.payment_status,
                })

            new_advance = remaining if remaining > 0 else Decimal("0")
            payer.advance_balance = new_advance
            payer.save(update_fields=["advance_balance"])

            BulkPaymentHistory.objects.create(
                bulk_payer=payer, amount=lump_sum, payment_method="TRANSFER",
                jobs_affected=jobs_updated,
                details=json.dumps({
                    "jobs": details,
                    "advance_used": str(advance_used),
                    "advance_stored": str(new_advance),
                }),
            )
            payer.update_totals()
