"""
seed_demo_meeting — One-shot seeder for the client-meeting demo.

    python manage.py seed_demo_meeting

Resets inventory categories/products and creates 16 live job cards.
"""
from decimal import Decimal
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from inventory.models import Category, Item
from workshop.models import (
    Mechanic, JobCard, JobCardConcern, JobCardSpareItem,
    JobCardLabourItem, CarBrand, CarModel,
)


class Command(BaseCommand):
    help = 'Seed demo data for the client meeting (inventory + 16 job cards)'

    def handle(self, *args, **options):
        self.stdout.write('\n=== PART 1: Resetting Inventory ===')
        created_items = self._seed_inventory()
        self.stdout.write(f'  Total: {len(created_items)} inventory products created')

        self.stdout.write('\n=== PART 2: Creating 16 Job Cards ===')
        self._seed_jobcards(created_items)

        self.stdout.write(self.style.SUCCESS(
            '\nDone! Inventory reset + 16 job cards created.'
            '\n   Set car colours manually via the UI.'
            '\n   No customers, notes, or photos.\n'
        ))

    # ------------------------------------------------------------------
    def _seed_inventory(self):
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
        with transaction.atomic():
            # Unlink any INVENTORY spares so Item.delete() doesn't hit PROTECT
            JobCardSpareItem.objects.filter(source='INVENTORY').update(item=None)
            Item.objects.all().delete()
            Category.objects.all().delete()

            for cat_name, products in CATEGORIES_AND_PRODUCTS.items():
                cat = Category.objects.create(name=cat_name)
                for prod_name, current, avg in products:
                    item = Item.objects.create(
                        category=cat,
                        name=prod_name,
                        current_stock=current,
                        average_stock=avg,
                        avg_cost=Decimal('250.00'),
                    )
                    created_items[prod_name] = item
                self.stdout.write(f'  [OK] {cat_name}: {len(products)} products')

        return created_items

    # ------------------------------------------------------------------
    def _seed_jobcards(self, created_items):
        # Mechanics
        mechanics = {}
        for name in ['Amlah', 'Hijaz', 'Sabith']:
            m, created = Mechanic.objects.get_or_create(
                name=name,
                defaults={'role': 'MECHANIC', 'is_active': True},
            )
            mechanics[name] = m

        # Brands / models in the master list
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

        # The 16 cars
        CARS = [
            {'brand': 'Bmw', 'model': '320d',              'reg': 'KL 10 BC 1000', 'mech': 'Amlah'},
            {'brand': 'Bmw', 'model': '530d',              'reg': 'KL 10 BC 1002', 'mech': 'Hijaz'},
            {'brand': 'Bmw', 'model': 'X3 xDrive20d',      'reg': 'KL 10 BC 1004', 'mech': 'Sabith'},
            {'brand': 'Bmw', 'model': 'M340i',             'reg': 'KL 10 BC 1006', 'mech': 'Amlah'},
            {'brand': 'Audi', 'model': 'A4 2.0 TDI',       'reg': 'KL 10 BC 1008', 'mech': 'Hijaz'},
            {'brand': 'Audi', 'model': 'Q5 2.0 TDI',       'reg': 'KL 10 BC 1010', 'mech': 'Sabith'},
            {'brand': 'Audi', 'model': 'A6 3.0 TDI',       'reg': 'KL 10 BC 1012', 'mech': 'Amlah'},
            {'brand': 'Audi', 'model': 'Q7 3.0 TDI',       'reg': 'KL 10 BC 1014', 'mech': 'Hijaz'},
            {'brand': 'Porsche', 'model': 'Cayenne 3.0 Diesel', 'reg': 'KL 10 BC 1016', 'mech': 'Sabith'},
            {'brand': 'Porsche', 'model': 'Macan 2.0T',         'reg': 'KL 10 BC 1018', 'mech': 'Amlah'},
            {'brand': 'Porsche', 'model': 'Macan S',             'reg': 'KL 10 BC 1020', 'mech': 'Hijaz'},
            {'brand': 'Porsche', 'model': 'Taycan',              'reg': 'KL 10 BC 1022', 'mech': 'Sabith'},
            {'brand': 'Mercedes-Benz', 'model': 'C220d',    'reg': 'KL 10 BC 1024', 'mech': 'Amlah'},
            {'brand': 'Mercedes-Benz', 'model': 'E350d',    'reg': 'KL 10 BC 1026', 'mech': 'Hijaz'},
            {'brand': 'Mercedes-Benz', 'model': 'GLE 350d', 'reg': 'KL 10 BC 1028', 'mech': 'Sabith'},
            {'brand': 'Mercedes-Benz', 'model': 'A200',     'reg': 'KL 10 BC 1030', 'mech': 'Amlah'},
        ]

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
                'AdBlue level warning — See workshop message',
                'COMAND screen flickering intermittently',
                'Diesel particulate filter pressure differential high',
                'Power steering assist reduced warning at low speed',
                'Rear air suspension compressor running frequently',
            ],
        }

        JOBS = {
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

        # (category, product_name, qty, unit_cost, customer_price)
        INV = {
            'Bmw': [
                ('Castrol 5W-30',                        Decimal('6.5'), Decimal('850'),  Decimal('4200')),
                ('BMW 320d (N47) - HU 6004 x',           Decimal('1'),   Decimal('380'),  Decimal('750')),
                ('BMW 3 Series (F30) - CUK 25 001',      Decimal('1'),   Decimal('420'),  Decimal('850')),
                ('Liqui Moly Brake Cleaner',              Decimal('1'),   Decimal('180'),  Decimal('350')),
                ('Pink Coolant',                          Decimal('1'),   Decimal('220'),  Decimal('500')),
            ],
            'Audi': [
                ('Liqui Moly 5W-30',                     Decimal('5.5'), Decimal('920'),  Decimal('4500')),
                ('Audi 2.0 TDI (EA189) - HU 719/7 x',   Decimal('1'),   Decimal('410'),  Decimal('800')),
                ('Audi A4 (B8) / Q5 - CUK 2450',        Decimal('1'),   Decimal('380'),  Decimal('780')),
                ('Maruti Brake Cleaner',                  Decimal('1'),   Decimal('120'),  Decimal('250')),
                ('Blue Coolant',                          Decimal('2'),   Decimal('200'),  Decimal('450')),
            ],
            'Porsche': [
                ('Mobil 1 5W-30',                        Decimal('8'),   Decimal('1100'), Decimal('6500')),
                ('Porsche Cayenne 3.0D - HU 8005 z',    Decimal('1'),   Decimal('520'),  Decimal('1200')),
                ('Porsche Cayenne 3.0D - C 39 002',     Decimal('1'),   Decimal('480'),  Decimal('1100')),
                ('Brembo Brake Oil DOT 4 LV',            Decimal('1'),   Decimal('650'),  Decimal('1400')),
                ('Liqui Moly Brake Cleaner',              Decimal('2'),   Decimal('180'),  Decimal('350')),
            ],
            'Mercedes-Benz': [
                ('Castrol 5W-40',                        Decimal('7'),   Decimal('900'),  Decimal('4800')),
                ('MB C220d (OM651) - HU 7010 z',         Decimal('1'),   Decimal('450'),  Decimal('900')),
                ('MB C-Class (W205) - CUK 26 023',       Decimal('1'),   Decimal('400'),  Decimal('820')),
                ('MB C220d (OM651) - C 35 005',          Decimal('1'),   Decimal('380'),  Decimal('780')),
                ('Bosch Brake Oil DOT 4',                Decimal('1'),   Decimal('280'),  Decimal('600')),
            ],
        }

        SPARES = {
            'Bmw': [
                ('Valve cover gasket kit (N47)',            Decimal('1'), Decimal('2800'), Decimal('4500')),
                ('Intake manifold swirl flap repair kit',   Decimal('1'), Decimal('1800'), Decimal('3200')),
                ('Brake wear sensor — front',               Decimal('2'), Decimal('350'),  Decimal('700')),
                ('Serpentine belt tensioner',                Decimal('1'), Decimal('1400'), Decimal('2600')),
                ('Thermostat with housing (N47)',            Decimal('1'), Decimal('1600'), Decimal('2800')),
            ],
            'Audi': [
                ('DSG mechatronic seal kit',                Decimal('1'), Decimal('2200'), Decimal('3800')),
                ('Thermostat housing assembly (EA189)',      Decimal('1'), Decimal('1500'), Decimal('2600')),
                ('Control arm bushing kit — front',         Decimal('1'), Decimal('1800'), Decimal('3200')),
                ('AGM start-stop battery 70Ah',             Decimal('1'), Decimal('8500'), Decimal('12000')),
                ('Coolant expansion tank with cap',         Decimal('1'), Decimal('900'),  Decimal('1600')),
            ],
            'Porsche': [
                ('Front brake disc pair — 350mm',           Decimal('1'), Decimal('12000'), Decimal('18000')),
                ('Rear brake disc pair — 330mm',            Decimal('1'), Decimal('9500'),  Decimal('14500')),
                ('Air suspension strut — rear right',       Decimal('1'), Decimal('28000'), Decimal('42000')),
                ('O2 sensor bank 1 upstream',               Decimal('1'), Decimal('4200'),  Decimal('7500')),
                ('Sunroof drain tube set',                  Decimal('1'), Decimal('800'),   Decimal('1500')),
            ],
            'Mercedes-Benz': [
                ('AdBlue heater element (W205)',            Decimal('1'), Decimal('4500'),  Decimal('7800')),
                ('AdBlue level sensor module',              Decimal('1'), Decimal('3200'),  Decimal('5500')),
                ('DPF differential pressure sensor',        Decimal('1'), Decimal('2800'),  Decimal('4600')),
                ('Steering rack position sensor',           Decimal('1'), Decimal('3500'),  Decimal('6000')),
                ('Air suspension valve block',              Decimal('1'), Decimal('6800'),  Decimal('11000')),
            ],
        }

        LABOUR = {
            'Bmw':           [Decimal('8500'), Decimal('7200'), Decimal('9800'), Decimal('6500')],
            'Audi':          [Decimal('7800'), Decimal('8500'), Decimal('6800'), Decimal('9200')],
            'Porsche':       [Decimal('15000'), Decimal('12500'), Decimal('18000'), Decimal('11000')],
            'Mercedes-Benz': [Decimal('9500'), Decimal('8800'), Decimal('7500'), Decimal('10200')],
        }

        today = date.today()
        brand_index = {}

        with transaction.atomic():
            for i, car in enumerate(CARS):
                brand = car['brand']
                idx = brand_index.get(brand, 0)
                brand_index[brand] = idx + 1

                admitted = today - timedelta(days=(15 - i) % 5)

                jc = JobCard(
                    admitted_date=admitted,
                    brand_name=brand,
                    model_name=car['model'],
                    registration_number=car['reg'],
                    mileage='100500',
                    lead_mechanic=mechanics[car['mech']],
                    completed=False,
                    labour_amount=LABOUR[brand][idx],
                )
                jc.save()

                # 5 Concerns
                for text in CONCERNS[brand]:
                    JobCardConcern.objects.create(job_card=jc, concern_text=text, status='PENDING')

                # 5 Jobs Performed
                for desc in JOBS[brand]:
                    JobCardLabourItem.objects.create(job_card=jc, job_description=desc)

                # 5 Inventory Items
                for prod_name, qty, cost, cust in INV[brand]:
                    inv_item = created_items.get(prod_name)
                    JobCardSpareItem.objects.create(
                        job_card=jc,
                        spare_part_name=prod_name,
                        source='INVENTORY',
                        item=inv_item,
                        status='RECEIVED',
                        quantity=qty,
                        unit_price=cost,
                        total_price=cust,
                    )

                # 5 Spare Parts (shop-sourced)
                for name, qty, cost, cust in SPARES[brand]:
                    JobCardSpareItem.objects.create(
                        job_card=jc,
                        spare_part_name=name,
                        source='SHOP',
                        status='RECEIVED',
                        quantity=qty,
                        unit_price=cost,
                        total_price=cust,
                    )

                jc.update_totals()
                self.stdout.write(
                    f'  [OK] {jc.bill_number} -- {brand} {car["model"]} '
                    f'[{car["reg"]}] -> {car["mech"]}  |  '
                    f'Rs.{jc.total_bill_amount:,.0f}'
                )
