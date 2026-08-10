"""
Management command: load_master_data
-------------------------------------
Seeds the database with master Car Brands, Car Models, and Spare Parts.

The data is the Python list **below, in this file** — nothing is read from disk
at runtime. `master_data_export.md` at the repo root is the source record it was
transcribed from (the real list collected from the workshop), so editing that
file alone changes nothing here; both have to be kept in step.

Usage:
    python manage.py load_master_data
    python manage.py load_master_data --clear   # wipe existing data first
"""

from django.core.management.base import BaseCommand
from workshop.models import CarBrand, CarModel, SparePart


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------

CAR_DATA = {
    "Aston Martin": ["DB11", "DBS", "Vantage"],
    "Audi": ["A3", "A4", "A6", "A8", "Q3", "Q5", "Q7", "Q8", "R8"],
    "BMW": ["118d", "320d", "520d", "525d", "530d", "730Ld", "M3", "M5",
            "X1", "X3", "X4", "X5", "X7"],
    "Bentley": ["Bentayga", "Continental GT", "Flying Spur"],
    "Jaguar": ["E-Pace", "F-Pace", "XE", "XF", "XJ"],
    "Land Rover": ["Defender", "Discovery", "Discovery Sport", "Range Rover",
                   "Range Rover Evoque", "Range Rover Sport", "Range Rover Vogue"],
    "Lexus": ["ES", "LS", "LX", "NX", "RX"],
    "Maserati": ["Ghibli", "Levante", "Quattroporte"],
    "Mercedes-Benz": ["A180", "C220d", "E250", "GL 350", "GLA200",
                      "GLC 220", "GLE 250", "GLS 350", "S350"],
    "Mini Cooper": ["3-Door", "5-Door", "Clubman", "Countryman"],
    "Porsche": ["911", "Cayenne", "Macan", "Panamera", "Taycan"],
    "Rolls-Royce": ["Cullinan", "Ghost", "Phantom", "Wraith"],
    "Volkswagen": ["Passat", "Polo", "Tiguan", "Touareg", "Vento"],
    "Volvo": ["S60", "S90", "XC40", "XC60", "XC90"],
}

SPARE_PARTS = [
    "ABS Module",
    "ABS Sensor",
    "AC Blower Motor",
    "AC Compressor",
    "AC Filter",
    "AC Vent",
    "AHC Fluid",
    "Accumulator",
    "Air Filter",
    "Air Suspension Compressor",
    "Airmatic Compressor Bracket",
    "Alternator",
    "Alternator Pulley",
    "Anti Squeal Paste",
    "Axle boot",
    "Balance End Cap",
    "Balance Rod Bushes",
    "Battery",
    "Battery Sensor",
    "Bonnet Shock",
    "Brake Booster",
    "Brake Caliper",
    "Brake Cleaner",
    "Brake Disc – Front",
    "Brake Disc – Rear",
    "Brake Fluid",
    "Brake Hose",
    "Brake Master Cylinder",
    "Brake Pad Grease",
    "Brake Pads – Front",
    "Brake Pads – Rear",
    "Brake Rotors – Front",
    "Brake Wear Sensor",
    "Cabin Filter",
    "Camshaft Position Sensor",
    "Center Bearing",
    "Charge Pressure O-Ring",
    "Cleaning Element",
    "Condenser",
    "Control Arm (Complete)",
    "Control Arm Ball Joint",
    "Coolant",
    "Coolant Elbow",
    "Coolant Pipe",
    "Cowl Panel",
    "Crankshaft Position Sensor",
    "Cylinder Head Cover Gasket",
    "DPF Clamp & Gasket",
    "Dashboard Trim Clips",
    "Dicky Shock",
    "Dicky Switch",
    "Diesel Filter",
    "Diesel Hose",
    "Differential Bush – Front",
    "Differential Fluid",
    "Differential Oil Seal",
    "Dipstick",
    "Distilled Water",
    "Door Handle – Inner",
    "Door Handle – Outer",
    "Door Hinges",
    "Door Lock Actuator",
    "Drive Belt",
    "Drive Shaft",
    "Dust Filter",
    "ECU (Engine Control Unit)",
    "EGR Cooler Hose",
    "EGR Gasket",
    "EGR Vacuum Hose",
    "Engine & Transmission Mount",
    "Engine Coolant Temperature Sensor",
    "Engine Flush",
    "Engine Mount",
    "Engine Oil",
    "Engine Oil Cooler",
    "Engine Oil Shell 5W 30",
    "Evaporator",
    "Expansion Tank Cap",
    "Fender Lining",
    "Flywheel",
    "Front Diff Fluid",
    "Front Propeller Shaft",
    "Front Shock Absorber",
    "Front Strut Kit",
    "Fuel Filter",
    "Fuel Tank Screw Cap Seal",
    "Fuse Box",
    "Fuse – 40A",
    "Gear Oil",
    "Glow Plug",
    "HLA",
    "Head Gasket",
    "Headlight Bulb",
    "Headlight Module",
    "Hose Clip",
    "Hypoid Axle Fluid",
    "Idler Pulley",
    "Ignition Coil",
    "Injector Seal",
    "Instrument Panel",
    "Key Shell",
    "Level Sensor Seal",
    "Lower Arm",
    "Lower Ball Joint",
    "Lower Control Arm Bracket",
    "Lower Control Arm Bush",
    "MAF Sensor",
    "MAP Sensor",
    "Manifold Gasket",
    "Mirror Indicator",
    "O Ring",
    "Oil Filter",
    "Oil Level Sensor",
    "Oil Temperature Sensor",
    "PCV Hose",
    "Parking Sensor",
    "Parking Sensor Module",
    "Pilot Bearing",
    "Piston",
    "Pollen Filter",
    "Power Steering Fluid",
    "Power Steering Hose",
    "Power Window Switch",
    "Propeller Shaft",
    "Push Rod",
    "Rack End",
    "Radiator",
    "Radiator Hose",
    "Rear Bellow",
    "Rear Logo",
    "Rear Shock Absorber",
    "Rear Strut Mount",
    "Rear Tail Light Bulb",
    "Refrigerant",
    "Relay",
    "Reservoir Tank",
    "Return Hose",
    "Reverse Bulb",
    "Safety Cleaner",
    "Seat Belt Cover",
    "Seat Belt Cover – Rear",
    "Seat Folding Handle",
    "Seat Motor",
    "Shock Absorber Mount – Front",
    "Shock Absorber Mount – Rear",
    "Shock Mount Kit",
    "Shock absorbers rear",
    "Silicone Sealant",
    "Spark Plug",
    "Stabilizer Bar",
    "Stabilizer Link",
    "Starting Motor",
    "Steering Intermediate Shaft",
    "Steering Rack",
    "Steering Rack Boot",
    "Stem Seal",
    "Subframe Bush",
    "Sump Gasket",
    "Sunroof Button",
    "Sunroof Motor",
    "Sunshield Lock",
    "Sway Bar Bush",
    "Swirl Flap",
    "Tailgate Control Module",
    "Tensioner Bearing",
    "Thermostat",
    "Thermostat Housing",
    "Thread Locker",
    "Thrust Arm Bush",
    "Thrust Control Arms",
    "Tie Rod",
    "Tie Rod End Ball Joint",
    "Timing Belt Kit",
    "Transfer Case Chain",
    "Transfer Case Fluid",
    "Transmission Filter",
    "Transmission Fluid",
    "Transmission Mount",
    "Transmission Oil Pan Gasket",
    "Transmission Pump",
    "Turbo Core",
    "Turbo Gasket",
    "Turbo Sensors",
    "Upper Arm",
    "Valve",
    "Valve Block",
    "Valve Cover Gasket",
    "Water Pump",
    "Wheel Alignment",
    "Wheel Bearing",
    "Wheel Bearing – Rear",
    "Wheel Bolt",
    "Wheel Hub Bearing – Left",
    "Wheel Speed Sensor – Front",
    "Wheel Speed Sensor – Rear",
    "Window Regulator",
    "Windshield Washer Fluid",
    "Windshield Washer Pump",
    "Wiper Blades",
]


class Command(BaseCommand):
    help = "Seeds CarBrand, CarModel, and SparePart tables from master_data_export.md"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing CarBrand, CarModel, and SparePart records before seeding.",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write(self.style.WARNING("Clearing existing master data..."))
            SparePart.objects.all().delete()
            CarModel.objects.all().delete()
            CarBrand.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("  → Cleared."))

        # ── Car Brands & Models ──────────────────────────────────────────────
        self.stdout.write("\nSeeding Car Brands & Models...")
        brand_created = brand_skipped = model_created = model_skipped = 0

        for brand_name, models_list in CAR_DATA.items():
            brand, created = CarBrand.objects.get_or_create(name=brand_name)
            if created:
                brand_created += 1
            else:
                brand_skipped += 1

            for model_name in models_list:
                _, mc = CarModel.objects.get_or_create(brand=brand, name=model_name)
                if mc:
                    model_created += 1
                else:
                    model_skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"  >> Brands:  {brand_created} created, {brand_skipped} already existed"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  >> Models:  {model_created} created, {model_skipped} already existed"
            )
        )

        # ── Spare Parts ──────────────────────────────────────────────────────
        self.stdout.write("\nSeeding Spare Parts...")
        part_created = part_skipped = 0

        for part_name in SPARE_PARTS:
            _, created = SparePart.objects.get_or_create(name=part_name)
            if created:
                part_created += 1
            else:
                part_skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"  >> Parts:   {part_created} created, {part_skipped} already existed"
            )
        )

        # ── Summary ──────────────────────────────────────────────────────────
        self.stdout.write(
            self.style.SUCCESS(
                f"\n[DONE]  "
                f"{brand_created} brands, {model_created} models, {part_created} spare parts added."
            )
        )
