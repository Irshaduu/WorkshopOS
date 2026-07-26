"""
Management command: purge_business_data
----------------------------------------
Deletes ALL workshop business data while leaving login accounts and master
reference lists intact. Built as the reversal for `seed_dummy_data`, but it is
deliberately NOT "delete only the dummy rows" — nothing in the schema marks a
row as dummy, so a command claiming to tell them apart would be lying. It
clears the business tables wholesale; that is the honest, predictable
behaviour, and it is why it refuses to run without --yes.

KEPT (never touched):
  - User / Group / UserProfile / UserSession / FailedAttempt  (logins & security)
  - CarBrand / CarModel / SparePart / ConcernSolution         (master lists)

REMOVED:
  - JobCard (+ concerns, spares, labour via CASCADE)
  - SpareShop, SpareShopPayment
  - BulkPayer (Fleet Accounts), BulkPaymentHistory
  - Mechanic (staff roster)
  - CashbookEntry
  - DeletionLog
  - inventory: SupplierShop, SupplierRestockBill/Item, SupplierPayment,
               ShopCatalogItem, Item, Category

Usage:
    python manage.py purge_business_data            # dry run — shows counts only
    python manage.py purge_business_data --yes      # actually delete
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from workshop.models import (
    JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem,
    SpareShop, SpareShopPayment, BulkPayer, BulkPaymentHistory,
    Mechanic, CashbookEntry, DeletionLog,
)
from inventory.models import (
    Category, Item, ShopCatalogItem, SupplierShop,
    SupplierRestockBill, SupplierRestockItem, SupplierPayment,
)


class Command(BaseCommand):
    help = "Delete all workshop business data (keeps login accounts and master lists). Use --yes to confirm."

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', action='store_true',
            help="Actually perform the deletion. Without this flag the command only reports counts.",
        )

    def handle(self, *args, **options):
        # Ordered so children go before parents where CASCADE isn't relied on.
        targets = [
            ("Job card spares", JobCardSpareItem),
            ("Job card labour", JobCardLabourItem),
            ("Job card concerns", JobCardConcern),
            ("Job cards", JobCard),
            ("Fleet payment history", BulkPaymentHistory),
            ("Fleet accounts", BulkPayer),
            ("Spare shop payments", SpareShopPayment),
            ("Spare shops", SpareShop),
            ("Supplier restock items", SupplierRestockItem),
            ("Supplier restock bills", SupplierRestockBill),
            ("Supplier payments", SupplierPayment),
            ("Shop catalog entries", ShopCatalogItem),
            ("Supplier shops", SupplierShop),
            ("Inventory items", Item),
            ("Inventory categories", Category),
            ("Cashbook entries", CashbookEntry),
            ("Staff roster (Mechanic)", Mechanic),
            ("Deletion history", DeletionLog),
        ]

        counts = [(label, model, model.objects.count()) for label, model in targets]
        total = sum(n for _, _, n in counts)

        self.stdout.write("Rows that will be removed:")
        for label, _, n in counts:
            if n:
                self.stdout.write(f"  {n:>7,}  {label}")
        if total == 0:
            self.stdout.write(self.style.SUCCESS("\nNothing to delete — business tables are already empty."))
            return

        self.stdout.write(f"\n  {total:>7,}  TOTAL")
        self.stdout.write(self.style.WARNING(
            "\nLogin accounts, groups and master lists (brands/models/spares/concerns) are NOT touched."
        ))

        if not options['yes']:
            self.stdout.write(self.style.WARNING(
                "\nDry run — nothing deleted. Re-run with --yes to actually delete."
            ))
            return

        with transaction.atomic():
            for label, model, n in counts:
                if n:
                    model.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(f"\n[DONE] {total:,} rows deleted."))
