"""
Demo data seeder for client meeting.
Run: python manage.py shell < scratchpad/seed_demo_data.py

Two jobs:
  1. Reset Inventory Categories & Products (Items) — keeping SpareShop/SupplierShop data intact
  2. Create 16 live Job Cards with realistic data
"""
import os, sys, django
from decimal import Decimal
from datetime import date, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'formulad_workshop.settings.development')
django.setup()

from django.db import transaction
from inventory.models import Category, Item
from workshop.models import (
    Mechanic, JobCard, JobCardConcern, JobCardSpareItem,
    JobCardLabourItem, CarBrand, CarModel, SparePart,
)

# ============================================================================
# PART 1 — INVENTORY CATEGORIES & PRODUCTS
# ============================================================================
# Strategy: delete all existing Items (breaks no SpareShop FK — Item is only
# FK'd from ShopCatalogItem and SupplierRestockItem, not SpareShop itself).
# Then delete all Categories and recreate fresh.

print("\n=== PART 1: Resetting Inventory ===")

with transaction.atomic():
    # Clear existing items and categories
    # SupplierRestockItem -> Item (CASCADE via bill), ShopCatalogItem -> Item (CASCADE)
    # JobCardSpareItem -> Item (PROTECT) — clear the FK on any existing job card spares
    # that point at inventory items, so the delete doesn't fail.
    from workshop.models import JobCardSpareItem as JCSI
    JCSI.objects.filter(source='INVENTORY').update(item=None)

    Item.objects.all().delete()
    Category.objects.all().delete()

    CATEGORIES_AND_PRODUCTS = {
        'Engine Oil': [
            ('Castrol 5W-30', 12, 20),
            ('Mobil 1 5W-30', 8, 15),
            ('Liqui Moly 5W-30', 6, 12),
            ('Liqui Moly 5W-40', 5, 10),
            ('Castrol 5W-40', 7, 14),
        ],
        'Oil Filter': [
            ('BMW 320d (N47) - HU 6004 x', 4, 8),
            ('BMW 320d LCI (B47) - HU 6014 z', 3, 6),
            ('MB C220d (OM651) - HU 7010 z', 5, 8),
            ('MB E350d (OM642) - HU 821 x', 2, 5),
            ('Audi 2.0 TDI (EA189) - HU 719/7 x', 3, 6),
            ('Audi 3.0 TDI (V6) - HU 8005 z', 2, 4),
            ('Porsche Cayenne 3.0D - HU 8005 z', 2, 4),
            ('Porsche Macan 2.0T - HU 6002 z', 2, 4),
        ],
        'Air Filter': [
            ('BMW 320d (N47) - C 27 009', 3, 6),
            ('BMW 320d LCI (B47) - C 28 125', 3, 5),
            ('MB C220d (OM651) - C 35 005', 4, 7),
            ('MB E350d (OM642) - C 35 003', 2, 5),
            ('Audi A4 2.0 TDI (EA189) - C 32 130', 3, 6),
            ('Audi A6/Q7 3.0 TDI - C 16 114/2 x', 2, 4),
            ('Porsche Cayenne 3.0D - C 39 002', 2, 4),
            ('Porsche Macan 2.0T/3.0T - C 30 030', 2, 4),
        ],
        'Cabin Filter': [
            ('BMW 3 Series (F30) - CUK 25 001', 3, 6),
            ('BMW 5 Series (F10) - CUK 2533-2', 2, 4),
            ('MB C-Class (W205) - CUK 26 023', 3, 5),
            ('MB E-Class (W212) - CUK 29 005', 2, 4),
            ('Audi A4 (B8) / Q5 - CUK 2450', 3, 5),
            ('Audi A6 (C7) - CUK 2641', 2, 4),
            ('Porsche Cayenne (92A) - CUK 2847', 2, 3),
            ('Porsche Macan - CUK 2450', 2, 3),
        ],
        'Brake Cleaner': [
            ('Liqui Moly Brake Cleaner', 6, 12),
            ('Maruti Brake Cleaner', 8, 15),
        ],
        'Coolant': [
            ('Pink Coolant', 5, 10),
            ('Blue Coolant', 4, 8),
        ],
        'Brake Oil': [
            ('Brembo Brake Oil DOT 4 LV', 3, 6),
            ('Bosch Brake Oil DOT 4', 4, 8),
            ('Motul Brake Oil DOT 4 LV', 3, 5),
            ('Liqui Moly Brake Oil DOT 4', 3, 6),
        ],
    }

    created_items = {}
    for cat_name, products in CATEGORIES_AND_PRODUCTS.items():
        cat = Category.objects.create(name=cat_name)
        for prod_name, current, avg in products:
            item = Item.objects.create(
                category=cat,
                name=prod_name,
                current_stock=current,
                average_stock=avg,
                avg_cost=Decimal('250.00'),  # dummy cost
            )
            created_items[prod_name] = item
        print(f"  ✓ {cat_name}: {len(products)} products")

    print(f"  Total: {len(created_items)} inventory products created")


# ============================================================================
# PART 2 — 16 LIVE JOB CARDS
# ============================================================================
print("\n=== PART 2: Creating 16 Job Cards ===")

# Ensure mechanics exist
with transaction.atomic():
    mechanic_names = ['Amlah', 'Hijaz', 'Sabith']
    mechanics = {}
    for name in mechanic_names:
        m, created = Mechanic.objects.get_or_create(
            name=name,
            defaults={'role': 'MECHANIC', 'is_active': True}
        )
        mechanics[name] = m
        if created:
            print(f"  ✓ Created mechanic: {name}")

# Ensure brands and models exist in the master list
with transaction.atomic():
    brand_models = {
        'Bmw': ['320d', '530d', 'X3 xDrive20d', 'M340i'],
        'Audi': ['A4 2.0 TDI', 'Q5 2.0 TDI', 'A6 3.0 TDI', 'Q7 3.0 TDI'],
        'Porsche': ['Cayenne 3.0 Diesel', 'Macan 2.0T', 'Macan S', 'Taycan'],
        'Mercedes-Benz': ['C220d', 'E350d', 'GLE 350d', 'A200'],
    }
    for brand_name, models_list in brand_models.items():
        brand, _ = CarBrand.objects.get_or_create(name=brand_name)
        for model_name in models_list:
            CarModel.objects.get_or_create(brand=brand, name=model_name)

# The 16 cars — 4 per brand, assigned round-robin to mechanics
CARS = [
    # BMW
    {'brand': 'Bmw', 'model': '320d',           'reg': 'KL 10 BC 1000', 'mechanic': 'Amlah'},
    {'brand': 'Bmw', 'model': '530d',           'reg': 'KL 10 BC 1002', 'mechanic': 'Hijaz'},
    {'brand': 'Bmw', 'model': 'X3 xDrive20d',   'reg': 'KL 10 BC 1004', 'mechanic': 'Sabith'},
    {'brand': 'Bmw', 'model': 'M340i',          'reg': 'KL 10 BC 1006', 'mechanic': 'Amlah'},
    # Audi
    {'brand': 'Audi', 'model': 'A4 2.0 TDI',    'reg': 'KL 10 BC 1008', 'mechanic': 'Hijaz'},
    {'brand': 'Audi', 'model': 'Q5 2.0 TDI',    'reg': 'KL 10 BC 1010', 'mechanic': 'Sabith'},
    {'brand': 'Audi', 'model': 'A6 3.0 TDI',    'reg': 'KL 10 BC 1012', 'mechanic': 'Amlah'},
    {'brand': 'Audi', 'model': 'Q7 3.0 TDI',    'reg': 'KL 10 BC 1014', 'mechanic': 'Hijaz'},
    # Porsche
    {'brand': 'Porsche', 'model': 'Cayenne 3.0 Diesel', 'reg': 'KL 10 BC 1016', 'mechanic': 'Sabith'},
    {'brand': 'Porsche', 'model': 'Macan 2.0T',         'reg': 'KL 10 BC 1018', 'mechanic': 'Amlah'},
    {'brand': 'Porsche', 'model': 'Macan S',             'reg': 'KL 10 BC 1020', 'mechanic': 'Hijaz'},
    {'brand': 'Porsche', 'model': 'Taycan',              'reg': 'KL 10 BC 1022', 'mechanic': 'Sabith'},
    # Mercedes-Benz
    {'brand': 'Mercedes-Benz', 'model': 'C220d',    'reg': 'KL 10 BC 1024', 'mechanic': 'Amlah'},
    {'brand': 'Mercedes-Benz', 'model': 'E350d',    'reg': 'KL 10 BC 1026', 'mechanic': 'Hijaz'},
    {'brand': 'Mercedes-Benz', 'model': 'GLE 350d', 'reg': 'KL 10 BC 1028', 'mechanic': 'Sabith'},
    {'brand': 'Mercedes-Benz', 'model': 'A200',     'reg': 'KL 10 BC 1030', 'mechanic': 'Amlah'},
]

# Realistic concerns per brand
CONCERNS = {
    'Bmw': [
        'Engine oil leak from valve cover gasket',
        'Rough idle at cold start — vibration felt through steering',
        'DPF regeneration warning light on dashboard',
        'AC blower making rattling noise at speed 3+',
        'Brake pad wear sensor triggered — front axle',
    ],
    'Audi': [
        'Oil consumption higher than normal — 1L per 3000km',
        'DSG gearbox jerking during 2nd to 3rd shift',
        'Coolant level dropping — no visible external leak',
        'Suspension creaking noise over speed bumps',
        'Start-stop system not engaging in traffic',
    ],
    'Porsche': [
        'PDK transmission hesitation from standstill',
        'Brake dust excessive on front wheels',
        'Air suspension ride height uneven — right side low',
        'Engine check light — intermittent, clears on restart',
        'Panoramic sunroof wind noise at highway speed',
    ],
    'Mercedes-Benz': [
        'AdBlue level warning — "See workshop" message',
        'COMAND screen flickering intermittently',
        'Diesel particulate filter pressure differential high',
        'Power steering assist reduced warning at low speed',
        'Rear air suspension compressor running frequently',
    ],
}

# Jobs performed per brand
JOBS_PERFORMED = {
    'Bmw': [
        'Engine oil and filter change — Castrol 5W-30, 6.5L',
        'Valve cover gasket replacement with new bolts',
        'DPF forced regeneration and adaptation reset',
        'Cabin air filter replacement — CUK 25 001',
        'Front brake pad replacement with sensor',
    ],
    'Audi': [
        'Engine oil and filter service — Liqui Moly 5W-30, 5.5L',
        'DSG transmission fluid and filter change — 7-speed',
        'Coolant system pressure test and thermostat replacement',
        'Front lower control arm bushings replaced — both sides',
        'Start-stop battery coding and adaptation',
    ],
    'Porsche': [
        'Engine oil and filter change — Mobil 1 5W-40, 8.2L',
        'Front and rear brake disc and pad replacement',
        'Air suspension strut replacement — right rear',
        'Engine diagnostic scan — O2 sensor bank 1 replaced',
        'Sunroof drain tubes cleared and resealed',
    ],
    'Mercedes-Benz': [
        'AdBlue tank heater and level sensor replacement',
        'COMAND head unit software update and module reset',
        'DPF back-pressure sensor replaced and forced regen',
        'Power steering rack recalibration via XENTRY',
        'Air suspension compressor relay and valve block service',
    ],
}

# Inventory items to draw (5 per card) — matched to brands
INVENTORY_ITEMS_MAP = {
    'Bmw': [
        ('Engine Oil', 'Castrol 5W-30',                        Decimal('6.5'), Decimal('850'),  Decimal('4200')),
        ('Oil Filter', 'BMW 320d (N47) - HU 6004 x',           Decimal('1'),   Decimal('380'),  Decimal('750')),
        ('Cabin Filter', 'BMW 3 Series (F30) - CUK 25 001',    Decimal('1'),   Decimal('420'),  Decimal('850')),
        ('Brake Cleaner', 'Liqui Moly Brake Cleaner',           Decimal('1'),   Decimal('180'),  Decimal('350')),
        ('Coolant', 'Pink Coolant',                             Decimal('1'),   Decimal('220'),  Decimal('500')),
    ],
    'Audi': [
        ('Engine Oil', 'Liqui Moly 5W-30',                     Decimal('5.5'), Decimal('920'),  Decimal('4500')),
        ('Oil Filter', 'Audi 2.0 TDI (EA189) - HU 719/7 x',   Decimal('1'),   Decimal('410'),  Decimal('800')),
        ('Cabin Filter', 'Audi A4 (B8) / Q5 - CUK 2450',      Decimal('1'),   Decimal('380'),  Decimal('780')),
        ('Brake Cleaner', 'Maruti Brake Cleaner',               Decimal('1'),   Decimal('120'),  Decimal('250')),
        ('Coolant', 'Blue Coolant',                             Decimal('2'),   Decimal('200'),  Decimal('450')),
    ],
    'Porsche': [
        ('Engine Oil', 'Mobil 1 5W-30',                         Decimal('8'),   Decimal('1100'), Decimal('6500')),
        ('Oil Filter', 'Porsche Cayenne 3.0D - HU 8005 z',     Decimal('1'),   Decimal('520'),  Decimal('1200')),
        ('Air Filter', 'Porsche Cayenne 3.0D - C 39 002',      Decimal('1'),   Decimal('480'),  Decimal('1100')),
        ('Brake Oil', 'Brembo Brake Oil DOT 4 LV',             Decimal('1'),   Decimal('650'),  Decimal('1400')),
        ('Brake Cleaner', 'Liqui Moly Brake Cleaner',           Decimal('2'),   Decimal('180'),  Decimal('350')),
    ],
    'Mercedes-Benz': [
        ('Engine Oil', 'Castrol 5W-40',                         Decimal('7'),   Decimal('900'),  Decimal('4800')),
        ('Oil Filter', 'MB C220d (OM651) - HU 7010 z',         Decimal('1'),   Decimal('450'),  Decimal('900')),
        ('Cabin Filter', 'MB C-Class (W205) - CUK 26 023',     Decimal('1'),   Decimal('400'),  Decimal('820')),
        ('Air Filter', 'MB C220d (OM651) - C 35 005',          Decimal('1'),   Decimal('380'),  Decimal('780')),
        ('Brake Oil', 'Bosch Brake Oil DOT 4',                  Decimal('1'),   Decimal('280'),  Decimal('600')),
    ],
}

# Spare parts (from shops) — 5 per card
SPARE_PARTS_MAP = {
    'Bmw': [
        ('Valve cover gasket kit (N47)',       Decimal('1'), Decimal('2800'), Decimal('4500')),
        ('Intake manifold swirl flap repair kit', Decimal('1'), Decimal('1800'), Decimal('3200')),
        ('Brake wear sensor — front',          Decimal('2'), Decimal('350'),  Decimal('700')),
        ('Serpentine belt tensioner',           Decimal('1'), Decimal('1400'), Decimal('2600')),
        ('Thermostat with housing (N47)',       Decimal('1'), Decimal('1600'), Decimal('2800')),
    ],
    'Audi': [
        ('DSG mechatronic seal kit',           Decimal('1'), Decimal('2200'), Decimal('3800')),
        ('Thermostat housing assembly (EA189)', Decimal('1'), Decimal('1500'), Decimal('2600')),
        ('Control arm bushing kit — front',    Decimal('1'), Decimal('1800'), Decimal('3200')),
        ('AGM start-stop battery 70Ah',        Decimal('1'), Decimal('8500'), Decimal('12000')),
        ('Coolant expansion tank with cap',    Decimal('1'), Decimal('900'),  Decimal('1600')),
    ],
    'Porsche': [
        ('Front brake disc pair — 350mm',      Decimal('1'), Decimal('12000'), Decimal('18000')),
        ('Rear brake disc pair — 330mm',       Decimal('1'), Decimal('9500'),  Decimal('14500')),
        ('Air suspension strut — rear right',  Decimal('1'), Decimal('28000'), Decimal('42000')),
        ('O2 sensor bank 1 upstream',          Decimal('1'), Decimal('4200'),  Decimal('7500')),
        ('Sunroof drain tube set',             Decimal('1'), Decimal('800'),   Decimal('1500')),
    ],
    'Mercedes-Benz': [
        ('AdBlue heater element (W205)',       Decimal('1'), Decimal('4500'),  Decimal('7800')),
        ('AdBlue level sensor module',         Decimal('1'), Decimal('3200'),  Decimal('5500')),
        ('DPF differential pressure sensor',   Decimal('1'), Decimal('2800'),  Decimal('4600')),
        ('Steering rack position sensor',      Decimal('1'), Decimal('3500'),  Decimal('6000')),
        ('Air suspension valve block',         Decimal('1'), Decimal('6800'),  Decimal('11000')),
    ],
}

# Labour amounts per brand (realistic for luxury)
LABOUR_AMOUNTS = {
    'Bmw':           [Decimal('8500'), Decimal('7200'), Decimal('9800'), Decimal('6500')],
    'Audi':          [Decimal('7800'), Decimal('8500'), Decimal('6800'), Decimal('9200')],
    'Porsche':       [Decimal('15000'), Decimal('12500'), Decimal('18000'), Decimal('11000')],
    'Mercedes-Benz': [Decimal('9500'), Decimal('8800'), Decimal('7500'), Decimal('10200')],
}

today = date.today()

with transaction.atomic():
    brand_index = {}  # track car index within each brand (0-3)
    
    for i, car in enumerate(CARS):
        brand = car['brand']
        model = car['model']
        reg = car['reg']
        mech_name = car['mechanic']

        # Track which car within this brand (0, 1, 2, 3)
        idx = brand_index.get(brand, 0)
        brand_index[brand] = idx + 1

        # Stagger admitted dates over last few days for realism
        admitted = today - timedelta(days=(15 - i) % 5)

        jc = JobCard(
            admitted_date=admitted,
            brand_name=brand,
            model_name=model,
            registration_number=reg,
            mileage='100500',
            lead_mechanic=mechanics[mech_name],
            completed=False,
            labour_amount=LABOUR_AMOUNTS[brand][idx],
        )
        jc.save()  # triggers clean() for normalization + bill_number generation

        # 5 Concerns
        for concern_text in CONCERNS[brand]:
            JobCardConcern.objects.create(
                job_card=jc,
                concern_text=concern_text,
                status='PENDING',
            )

        # 5 Jobs Performed
        for job_desc in JOBS_PERFORMED[brand]:
            JobCardLabourItem.objects.create(
                job_card=jc,
                job_description=job_desc,
            )

        # 5 Inventory Items (source=INVENTORY)
        for cat_name, prod_name, qty, unit_cost, cust_price in INVENTORY_ITEMS_MAP[brand]:
            inv_item = created_items.get(prod_name)
            JobCardSpareItem.objects.create(
                job_card=jc,
                spare_part_name=prod_name,
                source='INVENTORY',
                item=inv_item,
                status='RECEIVED',
                quantity=qty,
                unit_price=unit_cost,
                total_price=cust_price,
            )

        # 5 Spare Parts (source=SHOP, no shop FK — just names)
        for part_name, qty, cost, cust_price in SPARE_PARTS_MAP[brand]:
            JobCardSpareItem.objects.create(
                job_card=jc,
                spare_part_name=part_name,
                source='SHOP',
                status='RECEIVED',
                quantity=qty,
                unit_price=cost,
                total_price=cust_price,
            )

        # Refresh totals
        jc.update_totals()

        print(f"  ✓ {jc.bill_number} — {brand} {model} [{reg}] → {mech_name}  |  ₹{jc.total_bill_amount:,.0f}")

print(f"\n✅ Done! {len(CARS)} job cards created.")
print("   → Car colors: set manually via the UI as requested.")
print("   → No customers, notes, or photos — as requested.\n")
