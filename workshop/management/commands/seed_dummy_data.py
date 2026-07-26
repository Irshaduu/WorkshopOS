"""
Management command: seed_dummy_data
------------------------------------
Seeds realistic dev/demo data on top of the existing master data
(CarBrand/CarModel/SparePart, already loaded via load_master_data):

  - 3 Spare Shops (workshop.SpareShop)
  - 3 Supplier Shops (inventory.SupplierShop), 5 Categories, 3 products each
  - One restock bill + one running-balance payment per Supplier Shop
  - 2 Fleet Accounts (BulkPayer), with a partial bulk payment run against each
  - Job Cards for every day from 1 May 2026 to 25 Jul 2026, 3/day (258 total),
    each fully worked (concerns/spares/labour), delivered, billed per the
    real discount-as-shortfall rule for normal customers, a handful left
    unpaid, and a slice assigned to Fleet Accounts. ~15 registration numbers
    recur 2-3 times (repeat customers), always with a >=15 day gap so the
    prior visit's completed_date is well before the next admitted_date -
    consistent with the one-active-job-card-per-plate rule even though it
    isn't literally re-checked here (every seeded card is created already
    completed).

Uses only the 3 already-registered Mechanic/Assistant Mechanic staff -
does not create any Mechanic rows.

Usage:
    python manage.py seed_dummy_data
"""

import json
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.db.models import F, ExpressionWrapper, DecimalField

from workshop.models import (
    Mechanic, SpareShop, BulkPayer, BulkPaymentHistory,
    CarBrand, CarModel, SparePart, ConcernSolution,
    JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem,
)
from inventory.models import (
    Category, Item, SupplierShop, ShopCatalogItem,
    SupplierRestockBill, SupplierRestockItem, SupplierPayment,
)


# =============================================================================
# Fixed reference data
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
# name -> (unit cost to shop, customer-facing unit price)
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

KERALA_DISTRICTS = ["01", "02", "03", "05", "06", "07", "08", "09", "10", "11", "14"]
PLATE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

START_DATE = date(2026, 5, 1)
END_DATE = date(2026, 7, 25)


class Command(BaseCommand):
    help = "Seed realistic dummy data: shops, categories, products, bills, fleet accounts, and ~3 months of job cards."

    def handle(self, *args, **options):
        random.seed(2026)

        mechanics = list(Mechanic.objects.filter(is_active=True, role__in=Mechanic.JOBCARD_ELIGIBLE_ROLES))
        if not mechanics:
            raise CommandError("No active Mechanic/Assistant Mechanic found — register staff first.")

        if JobCard.objects.filter(admitted_date__range=(START_DATE, END_DATE)).exists():
            raise CommandError(
                f"Job cards already exist between {START_DATE} and {END_DATE} — "
                "aborting to avoid duplicating data. Remove them first if you want to re-seed."
            )

        with transaction.atomic():
            spare_shops = self._create_spare_shops()
            supplier_shops, warehouse_items = self._create_supplier_shops_and_products()
            self._create_restock_bills_and_payments(supplier_shops, warehouse_items)
            fleet_accounts = self._create_fleet_accounts()
            self._seed_autocomplete_pool()
            brand_model_pool = self._build_brand_model_pool()
            spare_name_pool = self._build_external_spare_pool(warehouse_items)

            job_cards_created = self._create_job_cards(
                brand_model_pool, mechanics, spare_shops, warehouse_items,
                spare_name_pool, fleet_accounts,
            )

            self._settle_fleet_accounts(fleet_accounts)

        self.stdout.write(self.style.SUCCESS(
            f"\n[DONE] {len(spare_shops)} Spare Shops, {len(supplier_shops)} Supplier Shops "
            f"({len(warehouse_items)} products), {len(fleet_accounts)} Fleet Accounts, "
            f"{job_cards_created} Job Cards ({START_DATE} to {END_DATE})."
        ))

    # ------------------------------------------------------------------
    # Foundational data
    # ------------------------------------------------------------------

    def _create_spare_shops(self):
        shops = []
        for i, name in enumerate(SPARE_SHOP_NAMES):
            shop, _ = SpareShop.objects.get_or_create(
                name=name,
                defaults={"phone": f"9{800000000 + i * 1111}", "address": f"{name}, Kerala"},
            )
            shops.append(shop)
        self.stdout.write(f"Spare Shops: {len(shops)}")
        return shops

    def _create_supplier_shops_and_products(self):
        supplier_shops = []
        warehouse_items = []  # list of Item instances, in the exact order of WAREHOUSE_ITEM_PRICES definition
        for i, (shop_name, products) in enumerate(SUPPLIER_SHOPS_DATA):
            shop, _ = SupplierShop.objects.get_or_create(
                name=shop_name,
                defaults={"phone": f"9{700000000 + i * 1111}", "address": f"{shop_name}, Kerala"},
            )
            supplier_shops.append(shop)
            for cat_name, item_name, avg_stock in products:
                category, _ = Category.objects.get_or_create(
                    name__iexact=cat_name, defaults={"name": cat_name}
                )
                item, _ = Item.objects.get_or_create(
                    category=category, name=item_name,
                    defaults={"average_stock": Decimal(avg_stock)},
                )
                ShopCatalogItem.objects.get_or_create(shop=shop, item=item)
                warehouse_items.append((shop, item))
        self.stdout.write(f"Supplier Shops: {len(supplier_shops)}, Products: {len(warehouse_items)}")
        return supplier_shops, warehouse_items

    def _create_restock_bills_and_payments(self, supplier_shops, warehouse_items):
        by_shop = {}
        for shop, item in warehouse_items:
            by_shop.setdefault(shop.id, []).append(item)

        for shop in supplier_shops:
            bill = SupplierRestockBill.objects.create(supplier=shop, bill_date=date(2026, 4, 22))
            for item in by_shop[shop.id]:
                cost, _ = WAREHOUSE_ITEM_PRICES[item.name]
                qty = Decimal(random.randint(25, 60))
                line_total = (Decimal(cost) * qty).quantize(Decimal("0.01"))
                SupplierRestockItem.objects.create(bill=bill, item=item, quantity=qty, total_price=line_total)
            bill.update_totals()

            payment_amount = (bill.total_amount * Decimal("0.6")).quantize(Decimal("0.01"))
            SupplierPayment.objects.create(
                supplier=shop, amount=payment_amount,
                payment_method=random.choice(["CASH", "UPI", "TRANSFER"]),
                date=date(2026, 4, 24), note="Initial stock payment",
            )
            shop.update_totals()
        self.stdout.write("Restock bills + payments created for all Supplier Shops")

    def _create_fleet_accounts(self):
        accounts = []
        for name in FLEET_ACCOUNTS:
            payer, _ = BulkPayer.objects.get_or_create(customer_name=name)
            accounts.append(payer)
        self.stdout.write(f"Fleet Accounts: {len(accounts)}")
        return accounts

    def _seed_autocomplete_pool(self):
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

    def _build_external_spare_pool(self, warehouse_items):
        warehouse_names = {item.name for _, item in warehouse_items}
        names = list(SparePart.objects.exclude(name__in=warehouse_names).values_list("name", flat=True))
        return names

    # ------------------------------------------------------------------
    # Job card generation
    # ------------------------------------------------------------------

    def _random_reg_number(self, used):
        while True:
            reg = "KL" + random.choice(KERALA_DISTRICTS) + \
                random.choice(PLATE_LETTERS) + random.choice(PLATE_LETTERS) + \
                str(random.randint(1000, 9999))
            if reg not in used:
                used.add(reg)
                return reg

    def _random_customer(self):
        first = random.choice([
            "Anoop", "Rejith", "Sreekutty", "Muhammed", "Fathima", "Devika", "Arjun", "Nithya",
            "Sajeev", "Priya", "Vishnu", "Anjali", "Rahul", "Meera", "Sanjay", "Divya", "Kiran",
            "Lakshmi", "Manoj", "Reshma", "Naveen", "Athira", "Prasad", "Sruthi", "Vinod",
            "Aiswarya", "Suresh", "Neethu", "Ajay", "Deepa", "Shameer", "Nisha",
        ])
        last = random.choice([
            "Nair", "Menon", "Pillai", "Varma", "Kumar", "Thomas", "Jose", "Iqbal", "Rahman",
            "Krishnan", "Das", "Mathew", "Joseph", "Nambiar", "Panicker",
        ])
        contact = random.choice("6789") + "".join(str(random.randint(0, 9)) for _ in range(9))
        return f"{first} {last}", contact

    def _plan_recurring_registrations(self, used_regs):
        """Pick ~15 plates that come back 2-3 times, spaced >=15 days apart
        so the previous visit's completed_date is well before the next
        admitted_date."""
        total_days = (END_DATE - START_DATE).days + 1
        day_to_regs = {}
        reg_profile = {}  # reg -> (brand, model, customer_name, customer_contact)

        for _ in range(15):
            reg = self._random_reg_number(used_regs)
            visits = random.choice([2, 2, 3])
            offsets = sorted(random.sample(range(total_days), visits))
            attempts = 0
            while any(offsets[i + 1] - offsets[i] < 15 for i in range(len(offsets) - 1)) and attempts < 20:
                offsets = sorted(random.sample(range(total_days), visits))
                attempts += 1
            for off in offsets:
                day_to_regs.setdefault(off, []).append(reg)
        return day_to_regs, reg_profile

    def _create_job_cards(self, brand_model_pool, mechanics, spare_shops,
                           warehouse_items, spare_name_pool, fleet_accounts):
        used_regs = set()
        day_to_recurring, reg_profile = self._plan_recurring_registrations(used_regs)
        warehouse_by_name = {item.name: (shop, item) for shop, item in warehouse_items}
        warehouse_names = list(warehouse_by_name.keys())

        total_days = (END_DATE - START_DATE).days + 1
        created = 0

        for day_offset in range(total_days):
            current_date = START_DATE + timedelta(days=day_offset)
            scheduled = day_to_recurring.get(day_offset, [])[:3]
            slots = list(scheduled) + [None] * (3 - len(scheduled))

            for slot_reg in slots:
                if slot_reg is None:
                    reg = self._random_reg_number(used_regs)
                    brand, model = random.choice(brand_model_pool)
                    customer_name, customer_contact = self._random_customer()
                else:
                    reg = slot_reg
                    if reg in reg_profile:
                        brand, model, customer_name, customer_contact = reg_profile[reg]
                    else:
                        brand, model = random.choice(brand_model_pool)
                        customer_name, customer_contact = self._random_customer()
                        reg_profile[reg] = (brand, model, customer_name, customer_contact)

                self._create_one_job_card(
                    reg, brand, model, customer_name, customer_contact, current_date,
                    mechanics, spare_shops, warehouse_by_name, warehouse_names,
                    spare_name_pool, fleet_accounts,
                )
                created += 1

            if created % 60 == 0:
                self.stdout.write(f"  ... {created} job cards created (through {current_date})")

        return created

    def _create_one_job_card(self, reg, brand, model, customer_name, customer_contact,
                              admitted_date, mechanics, spare_shops, warehouse_by_name,
                              warehouse_names, spare_name_pool, fleet_accounts):
        mechanic = random.choice(mechanics)
        color = random.choice(CAR_COLORS) if random.random() > 0.03 else "Other"
        mileage = str(random.randint(8000, 95000))

        jobcard = JobCard.objects.create(
            admitted_date=admitted_date,
            brand_name=brand,
            model_name=model,
            registration_number=reg,
            mileage=mileage,
            customer_name=customer_name,
            customer_contact=customer_contact,
            lead_mechanic=mechanic,
            car_color=color,
            car_color_other="Champagne Gold" if color == "Other" else None,
        )

        for text in random.sample(CONCERNS_POOL, random.randint(1, 3)):
            JobCardConcern.objects.create(job_card=jobcard, concern_text=text, status="FIXED")

        for _ in range(random.randint(0, 3)):
            qty = Decimal(random.choice([1, 1, 1, 2]))
            if random.random() < 0.55 and warehouse_names:
                name = random.choice(warehouse_names)
                cost, price = WAREHOUSE_ITEM_PRICES[name]
                JobCardSpareItem.objects.create(
                    job_card=jobcard, spare_part_name=name, quantity=qty,
                    unit_price=Decimal(cost), total_price=(Decimal(price) * qty).quantize(Decimal("0.01")),
                    status="RECEIVED", ordered_date=admitted_date, received_date=admitted_date,
                )
            else:
                name = random.choice(spare_name_pool)
                cost = random.randint(400, 6000)
                price = int(cost * random.uniform(1.25, 1.5))
                shop = random.choice(spare_shops)
                JobCardSpareItem.objects.create(
                    job_card=jobcard, spare_part_name=name, quantity=qty,
                    unit_price=Decimal(cost), total_price=(Decimal(price) * qty).quantize(Decimal("0.01")),
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
            payer = random.choice(fleet_accounts)
            jobcard.bulk_payer = payer
            jobcard.payment_status = "PENDING"
            jobcard.received_amount = Decimal("0")
            jobcard.discount_amount = Decimal("0")
            jobcard.save()
        else:
            total = jobcard.total_bill_amount
            if random.random() < 0.05:
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
                jobcard.discount_amount = max(Decimal("0"), total - received)
                # Settled at pickup — Paid Bills filters on paid_date, so a
                # seeded PAID bill without it would be invisible to every
                # date filter.
                jobcard.paid_date = timezone.make_aware(
                    datetime.combine(jobcard.completed_date, time(14, 30))
                )
            jobcard.payment_method = random.choice(["CASH", "UPI", "CARD", "TRANSFER"])
            jobcard.save()

        return jobcard

    # ------------------------------------------------------------------
    # Fleet cascade settlement
    # ------------------------------------------------------------------

    def _settle_fleet_accounts(self, fleet_accounts):
        for payer in fleet_accounts:
            payer.update_totals()
            pending_total = sum(
                (jc.total_bill_amount - jc.received_amount)
                for jc in payer.job_cards.filter(payment_status__in=["PENDING", "PARTIAL"])
            )
            if pending_total <= 0:
                continue
            pay_amount = (pending_total * Decimal("0.65")).quantize(Decimal("0.01"))
            self._run_bulk_payment(payer, pay_amount, "TRANSFER")

    def _run_bulk_payment(self, payer, lump_sum, payment_method):
        advance_used = payer.advance_balance
        remaining_funds = lump_sum + advance_used
        payer.advance_balance = Decimal("0")

        pending_cards = payer.job_cards.select_related(None).filter(
            payment_status__in=["PENDING", "PARTIAL"]
        ).annotate(
            balance_amount=ExpressionWrapper(
                F("total_bill_amount") - F("received_amount"), output_field=DecimalField()
            )
        ).order_by("admitted_date", "pk")

        jobs_updated = 0
        history_details = []
        for job in pending_cards:
            if remaining_funds <= 0:
                break
            balance = job.balance_amount
            if balance <= 0:
                continue
            if remaining_funds >= balance:
                paid_amount = balance
                job.received_amount += balance
                job.payment_status = "BULK_PAID"
                job.payment_method = payment_method
                job.discount_amount = Decimal("0")
                job.paid_date = timezone.make_aware(
                    datetime.combine(job.completed_date or job.admitted_date, time(15, 0))
                )
                remaining_funds -= balance
            else:
                paid_amount = remaining_funds
                job.received_amount += remaining_funds
                job.payment_status = "PARTIAL"
                job.payment_method = payment_method
                remaining_funds = Decimal("0")
            job.save()
            jobs_updated += 1
            history_details.append({
                "job_id": job.pk, "reg": job.registration_number,
                "car": f"{job.brand_name} {job.model_name}",
                "paid": str(paid_amount), "status": job.payment_status,
            })

        new_advance = remaining_funds if remaining_funds > Decimal("0") else Decimal("0")
        payer.advance_balance = new_advance
        payer.save(update_fields=["advance_balance"])

        BulkPaymentHistory.objects.create(
            bulk_payer=payer, amount=lump_sum, payment_method=payment_method,
            jobs_affected=jobs_updated,
            details=json.dumps({
                "jobs": history_details,
                "advance_used": str(advance_used),
                "advance_stored": str(new_advance),
            }),
        )
        payer.update_totals()
        self.stdout.write(f"  Bulk payment: {payer.customer_name} paid ₹{lump_sum}, {jobs_updated} job(s) affected")
