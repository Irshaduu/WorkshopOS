"""
seed_live_jobcards - cars on the floor right now, for the demo.

    python manage.py seed_live_jobcards --yes

`seed_meeting_data` settles every card it writes, so the dashboard, the Live
Report and the operations board all come out empty. This adds cards in the state
neither other seeder leaves behind: admitted, filled in, NOT completed and NOT
billed - so the demo can press Completed and settle from the Invoice.

Same uniform card as seed_meeting_data (5 shop spares x 1,500 + 4 draws 6,500 +
labour 8,000 = 22,000), same plates, so the dataset stays checkable. The MONEY
is uniform; the concerns are not -- see `concern_states`.

Additive and re-runnable: it purges nothing. --replace drops the active cards on
its own plates first; without it a clash stops the run rather than breaking the
one-active-card rule.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Item, SupplierShop, SupplierRestockBill, SupplierRestockItem,
    SupplierPayment,
)
from workshop.models import (
    JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem,
    Mechanic, SpareShop,
)
from workshop import settlement

D = Decimal

# -- The pattern, copied from seed_meeting_data so the two cannot disagree ----
CARDS = 10
MILEAGE = '85000'
LABOUR = D('8000')

# Plate n is `KL 10 AA (1000 + n)` and its brand/model/colour follow the same
# two expressions the history uses, so card 1000 is the SAME CAR coming back.
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
# The oldest cars are finished: every concern FIXED, so settlement.unfilled()
# comes back empty and the settle dialog opens AMBER. That is the flow the demo
# presses "Complete & settle" on, and an unfixed concern would turn it red.
#
# The rest are still being worked on, because the Live Report's floor board
# lists the concerns still OPEN on each car -- ten finished cars left it saying
# "All concerns fixed" ten times over, which reads as a broken feature rather
# than a quiet workshop. One WORKING per unfinished car: a mechanic is on one
# thing at a time.
READY_CARDS = 4


def concern_states(n):
    """(text, status) for card `n`'s concerns, oldest card first.

    The longer a car has been in, the more of it is done, so the gradient runs
    with `admitted_date` and the cars ready to hand over are the oldest ones.
    Card 4 keeps 3 of 5 fixed; card 9 was admitted today and has none.
    """
    if n < READY_CARDS:
        fixed = len(CONCERNS)
    else:
        fixed = max(0, 3 - (n - READY_CARDS + 1) // 2)

    out = []
    for i, text in enumerate(CONCERNS):
        if i < fixed:
            status = 'FIXED'
        elif i == fixed:
            status = 'WORKING'
        else:
            status = 'PENDING'
        out.append((text, status))
    return out


JOBS = [
    'Engine Oil replaced',
    'Brake Pads - Front replaced',
    'Brake Disc - Front replaced',
    'Drive Belt replaced',
    'Wheel alignment and balancing done',
]

SPARES = ['Brake Pads - Front', 'Brake Disc - Front', 'Wiper Blades',
          'Drive Belt', 'Fuel Filter']
SPARE_COST = D('1000')      # the shop's LINE TOTAL, never a rate
SPARE_PRICE = D('1500')     # what the customer pays

#   name -> (qty per card, customer total for the line, standing unit cost)
DRAWS = {
    'Castrol 5W-30':             (D('5'), D('4000'), D('500')),
    'Blue Coolant':              (D('1'), D('1000'), D('600')),
    'Bosch Brake Oil DOT 4':     (D('1'), D('1000'), D('600')),
    'Liqui Moly Brake Cleaner':  (D('1'), D('500'),  D('300')),
}

# Taken in before the cars arrive, so the draws do not push the shelf negative
# — which in this system means a Supplies Shop bill is missing. Sized to land
# back at roughly `average_stock` once all ten cards have drawn.
RESTOCK = {
    'Castrol 5W-30':            D('20'),
    'Blue Coolant':             D('10'),
    'Bosch Brake Oil DOT 4':    D('10'),
    'Liqui Moly Brake Cleaner': D('10'),
}

# What one card must come to. Asserted after writing.
CARD_TOTAL = (SPARE_PRICE * len(SPARES)
              + sum(price for _q, price, _c in DRAWS.values())
              + LABOUR)


class Command(BaseCommand):
    help = ("Add fully-filled LIVE job cards — admitted and worked on, ready to "
            "press Completed and settle. Matches the seeded dataset's pattern.")

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true',
                            help="Actually run. Without it, only the plan is printed.")
        parser.add_argument('--count', type=int, default=CARDS,
                            help=f"How many live cards to create (default {CARDS}).")
        parser.add_argument('--replace', action='store_true',
                            help="Delete any existing ACTIVE card on these plates "
                                 "first. Without it, a conflict stops the run.")
        parser.add_argument('--no-restock', action='store_true',
                            help="Skip the delivery. The shelf will go negative.")

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        from django.db import connection

        self.count = max(1, opts['count'])
        # localdate(), never date.today() — the server may run in UTC.
        self.today = timezone.localdate()
        # Five days back, two cars a day. Nothing is admitted TODAY: a spare's
        # received_date is admitted + 1, and a future date is refused
        # (workshop/spare_dates.py).
        self.spread = 5
        self.first_day = self.today - timedelta(days=self.spread)

        self.stdout.write(f"\nDatabase : {connection.settings_dict['ENGINE'].split('.')[-1]} "
                          f"/ {connection.settings_dict['NAME']}")
        self.stdout.write(f"Window   : admitted {self.first_day} .. "
                          f"{self.today - timedelta(days=1)}")
        self.stdout.write(f"Plan     : {self.count} LIVE cards (not completed, "
                          f"unpaid) at Rs.{CARD_TOTAL:,.0f} each = "
                          f"Rs.{CARD_TOTAL * self.count:,.0f}")

        plates = [f"KL 10 AA {1000 + n}" for n in range(self.count)]
        clashes = list(JobCard.objects.filter(
            registration_number__in=plates, completed=False, is_deleted=False))
        if clashes and not opts['replace']:
            self.stdout.write(self.style.ERROR(
                "\nRefusing to run — these plates already have an ACTIVE job card, "
                "and this workshop allows only one at a time:"))
            for jc in clashes:
                self.stdout.write(f"    {jc.bill_number}  {jc.registration_number}  "
                                  f"admitted {jc.admitted_date}")
            self.stdout.write(self.style.WARNING(
                "\nRe-run with --replace to delete them and start over.\n"))
            return

        if not opts['yes']:
            self.stdout.write(self.style.WARNING(
                "\nDRY RUN — nothing changed. Re-run with --yes to apply.\n"))
            return

        if clashes:
            self._replace(clashes)
        if not opts['no_restock']:
            self._restock()
        cards = self._job_cards(plates)
        self._verify(cards)

    # ------------------------------------------------------------------
    def _replace(self, clashes):
        """Drop the previous run's live cards.

        Safe in bulk: `JobCardSpareItem` carries post_delete receivers, so Django
        never fast-deletes those rows and the cascade hands the stock back.
        """
        self.stdout.write(f"\n[0] Replacing {len(clashes)} existing active card(s)")
        shops = list(SpareShop.objects.all())
        with transaction.atomic():
            JobCard.objects.filter(pk__in=[c.pk for c in clashes]).delete()
        for shop in shops:
            shop.update_totals()

    # ------------------------------------------------------------------
    def _restock(self):
        """One delivery per Supplies Shop, dated the day before the first car.

        At each product's STANDING unit cost, so `avg_cost` does not move and
        these draws cost what every other card's did. Paid in full the same day,
        so the supplies payable is untouched.
        """
        self.stdout.write("\n[1] Supplies shop delivery")
        when = self.first_day - timedelta(days=1)

        by_name = {i.name: i for i in Item.objects.all()}
        missing = [n for n in RESTOCK if n not in by_name]
        if missing:
            self.stdout.write(self.style.ERROR(f"      MISSING PRODUCTS: {missing}"))

        # Read which shop sells what off the existing bills rather than
        # restating the category map.
        lines = {}
        for name, qty in RESTOCK.items():
            item = by_name.get(name)
            if not item:
                continue
            shop = (SupplierShop.objects
                    .filter(bills__items__item=item)
                    .order_by('-bills__bill_date').first())
            if shop is None:
                shop = SupplierShop.objects.filter(is_active=True).first()
            if shop is None:
                self.stdout.write(self.style.ERROR(
                    "      NO SUPPLIES SHOP — cannot record the delivery."))
                return
            lines.setdefault(shop, []).append((item, qty, qty * DRAWS[name][2]))

        for shop, rows in lines.items():
            with transaction.atomic():
                bill = SupplierRestockBill.objects.create(
                    supplier=shop, bill_date=when, discount_amount=D('0'))
                for item, qty, total in rows:
                    SupplierRestockItem.objects.create(
                        bill=bill, item=item, quantity=qty, total_price=total)
                bill.update_totals()
                bill.refresh_from_db()
                SupplierPayment.objects.create(
                    supplier=shop,
                    amount=bill.total_amount,
                    payment_method='TRANSFER',
                    date=when,
                    note=f"Paid on delivery — {bill.bill_date}",
                )
            self.stdout.write(
                f"      {shop.name}: {len(rows)} product(s), "
                f"Rs.{bill.total_amount:,.0f} billed and paid on {when}")

    # ------------------------------------------------------------------
    def _job_cards(self, plates):
        """The cards themselves — everything filled in, nothing settled."""
        self.stdout.write(f"\n[2] {self.count} live job cards")

        mechs = {m.name: m for m in Mechanic.objects.filter(name__in=MECHANICS)}
        mech_list = [mechs[n] for n in MECHANICS if n in mechs]
        shops = list(SpareShop.objects.filter(is_trashed=False).order_by('id'))
        by_name = {i.name: i for i in Item.objects.all()}

        if not mech_list:
            self.stdout.write(self.style.ERROR(
                "      NO MECHANICS — every card would be flagged 'no mechanic'."))
        if not shops:
            self.stdout.write(self.style.ERROR(
                "      NO SPARE SHOPS — every shop spare would be flagged 'no shop'."))

        made = []
        for n in range(self.count):
            brand, model = FLEET[n % len(FLEET)]
            reg = plates[n]
            # Two cars a day, oldest first. received_date is admitted + 1, which
            # is what keeps the newest pair inside today.
            admitted = self.first_day + timedelta(days=n // 2)
            received = min(admitted + timedelta(days=1), self.today)

            with transaction.atomic():
                card = JobCard.objects.create(
                    registration_number=reg,
                    brand_name=brand,
                    model_name=model,
                    car_color=COLOURS[n % len(COLOURS)],
                    mileage=MILEAGE,
                    admitted_date=admitted,
                    lead_mechanic=mech_list[n % len(mech_list)] if mech_list else None,
                    labour_amount=LABOUR,
                )

                # See `concern_states`: the oldest cards are finished and
                # settle cleanly, the newer ones still carry open work for the
                # Live Report's floor board to list.
                JobCardConcern.objects.bulk_create([
                    JobCardConcern(job_card=card, concern_text=text, status=status)
                    for text, status in concern_states(n)
                ])
                JobCardLabourItem.objects.bulk_create([
                    JobCardLabourItem(job_card=card, job_description=j, amount=D('0'))
                    for j in JOBS
                ])

                # Shop spares. The shop rotates with the card AND the row, so all
                # three ledgers fill evenly.
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
                        received_date=received,
                    )

                # Warehouse draws. `unit_price` is left alone on purpose —
                # JobCardSpareItem.save() snapshots Item.avg_cost onto it, and
                # that snapshot is the whole cost side of the inventory route.
                for name, (qty, price, _unit) in DRAWS.items():
                    item = by_name.get(name)
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

            made.append(card)
            mech_name = card.lead_mechanic.name if card.lead_mechanic else '-'
            self.stdout.write(
                f"      {card.bill_number}  {brand} {model} [{reg}]  "
                f"{mech_name:<7} admitted {admitted}  "
                f"Rs.{card.total_bill_amount:,.0f}")

        for shop in shops:
            shop.update_totals()
        return made

    # ------------------------------------------------------------------
    def _verify(self, cards):
        """Ask `settlement.unfilled()` what the settle dialog asks.

        TWO expectations since the cards stopped being uniform. The first
        `READY_CARDS` must come back completely empty -- those are the ones the
        demo settles, and the dialog has to open amber. The rest are allowed
        exactly ONE kind of gap, an unfixed concern, because that is the work
        the floor board exists to list; anything else unfilled on them (a
        missing price, no mechanic) is still a fault.
        """
        self.stdout.write("\n[3] Ready to complete and invoice?")

        ok = True
        ready = 0
        for n, card in enumerate(cards):
            card = (JobCard.objects
                    .prefetch_related('concerns', 'labours', 'spares')
                    .get(pk=card.pk))
            holes = settlement.unfilled(card)
            expected_open = n >= READY_CARDS

            # Everything except the concerns, on every card either way.
            other = holes.card + holes.spares + holes.inventory
            if other:
                ok = False
                rows = '; '.join(f"{p.name} — {p.missing}"
                                 for p in holes.spares + holes.inventory)
                self.stdout.write(self.style.ERROR(
                    f"      {card.bill_number}: {holes.count} gap(s) "
                    f"{holes.card_missing} {rows}".rstrip()))
            if holes.concerns and not expected_open:
                ok = False
                self.stdout.write(self.style.ERROR(
                    f"      {card.bill_number}: should be ready to settle, but "
                    f"{len(holes.concerns)} concern(s) are still open"))
            if not holes.concerns and expected_open:
                ok = False
                self.stdout.write(self.style.ERROR(
                    f"      {card.bill_number}: should still carry open work "
                    f"for the floor board, but every concern is fixed"))
            if not holes:
                ready += 1
            if card.total_bill_amount != CARD_TOTAL:
                ok = False
                self.stdout.write(self.style.ERROR(
                    f"      {card.bill_number}: total is "
                    f"Rs.{card.total_bill_amount:,.0f}, expected "
                    f"Rs.{CARD_TOTAL:,.0f}"))

        if ok:
            self.stdout.write(self.style.SUCCESS(
                f"      {ready} of {len(cards)} cards settle clean, "
                f"{len(cards) - ready} still being worked. "
                f"Rs.{CARD_TOTAL:,.0f} each, "
                f"Rs.{CARD_TOTAL * len(cards):,.0f} total."))
            self.stdout.write(
                "      On the clean ones the settle dialog opens AMBER, saying only "
                "that the car\n      is not marked Completed — which is true, and is "
                "what 'Complete & settle' is for.\n"
                "      The rest carry open concerns, which is what the Live Report's "
                "floor board\n      lists and what the settle dialog turns RED for.")

        # Reported either way: a negative reading means a Supplies Shop bill is
        # missing, and should not be left for Low Stock to surface later.
        self.stdout.write("\n[4] Warehouse shelf")
        for name in DRAWS:
            item = Item.objects.filter(name=name).first()
            if not item:
                continue
            style = self.style.ERROR if item.current_stock < 0 else self.style.SUCCESS
            self.stdout.write(style(
                f"      {name:<28} {item.current_stock:>8}  "
                f"(normally {item.average_stock}, cost Rs.{item.avg_cost})"))
        self.stdout.write('')
