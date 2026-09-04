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
  - SalaryAdvance, SalaryPayment (+ SalaryPaymentLine via CASCADE)
  - CashbookEntry
  - OwnerWithdrawal
  - RentRate, RentDeposit
  - DeletionLog

⚠ THREE TABLES WERE MISSING FROM THIS LIST UNTIL 2026-09-04, all three added
to the app after this command was written, and all three real money. This is
the command the go-live runbook says to run against production before the
workshop starts using the system — so anything it forgets is DEMO MONEY that
survives into the real books. `OwnerWithdrawal` feeds `cash_position()`, and
since rent moved onto its own expense line `RentRate` + `RentDeposit` feed the
PROFIT EQUATION: on the development data that was ₹12,60,000 of fabricated rent
and ₹12,32,500 of fabricated cash out, left behind by a purge that reported
success.
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
    JobCardPhoto,
    SpareShop, SpareShopPayment, BulkPayer, BulkPaymentHistory,
    Mechanic, CashbookEntry, DeletionLog,
    SalaryAdvance, SalaryPayment, SalaryPaymentLine,
    OwnerWithdrawal, RentRate, RentDeposit,
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
            ("Job card photos", JobCardPhoto),
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
            ("Salary payment lines", SalaryPaymentLine),
            ("Salary payments (monthly settlements)", SalaryPayment),
            ("Salary advances", SalaryAdvance),
            ("Staff roster (Mechanic)", Mechanic),
            ("Owner withdrawals", OwnerWithdrawal),
            # Deposits before the rate: `RentRate.effective_from` is unique and
            # nothing points at it, so the order is only for the report reading
            # in the same shape as every other pair here.
            ("Rent deposits", RentDeposit),
            ("Rent rates", RentRate),
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
            # The objects in the bucket are queued by the post_delete signal on
            # JobCardPhoto, which fires for querysets and cascades alike — so
            # there is nothing to do here beyond deleting in the right order.
            # Photos are first in `targets` so they go before the job cards
            # would cascade them away.
            for label, model, n in counts:
                if n:
                    model.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(f"\n[DONE] {total:,} rows deleted."))
        photo_count = next((n for _, model, n in counts if model is JobCardPhoto), 0)
        if photo_count:
            self.stdout.write(
                f"{photo_count:,} photo object(s) queued for removal from storage — "
                f"run `manage.py sweep_photo_blobs --yes` to collect them."
            )
