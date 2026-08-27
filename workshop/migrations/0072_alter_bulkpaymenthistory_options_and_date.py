"""
Give a fleet payment the day the money moved.

The third and last ledger in this app to get one. `inventory.SupplierPayment`
has had a `date` column since day one and `SpareShopPayment` gained it in
`0071`; `BulkPaymentHistory` carried only `created_at` (`auto_now_add`), so a
fleet payment was filed under the day it was KEYED.

It is the worst of the three by size. A fleet settles in lump sums on its own
rhythm — the collector comes, cash changes hands, and the office keys it when
it gets to it — and these are the largest single receipts the workshop takes.
Nothing filtered fleet payments by date before, so the defect was invisible;
the moment any cash figure is cut by period it would file a six-figure receipt
in the wrong month.

The backfill takes each row's `created_at` as its best available answer,
converted to the workshop's own calendar day (`TIME_ZONE` is Asia/Kolkata while
the server may run in UTC, so the naive `.date()` of a UTC timestamp reports
the previous day for the whole of an IST morning). That is an approximation by
construction — the keystroke date is exactly what this column exists to stop
trusting — which is why it belongs before go-live, while nothing but demo data
is being approximated.
"""

import django.utils.timezone
from django.db import migrations, models
from django.utils import timezone


def backfill_payment_dates(apps, schema_editor):
    """Existing rows: the keystroke day, on the workshop's calendar."""
    BulkPaymentHistory = apps.get_model('workshop', 'BulkPaymentHistory')
    updated = []
    for payment in BulkPaymentHistory.objects.all().only('id', 'created_at', 'date'):
        stamp = payment.created_at
        if stamp is None:
            continue
        payment.date = (timezone.localtime(stamp) if timezone.is_aware(stamp) else stamp).date()
        updated.append(payment)
    if updated:
        BulkPaymentHistory.objects.bulk_update(updated, ['date'], batch_size=500)


def unbackfill(apps, schema_editor):
    """Nothing to undo — the column goes with the reverse of the AddField."""


class Migration(migrations.Migration):

    dependencies = [
        ('workshop', '0071_alter_spareshoppayment_options_spareshoppayment_date'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='bulkpaymenthistory',
            options={'ordering': ['-date', '-created_at']},
        ),
        migrations.AddField(
            model_name='bulkpaymenthistory',
            name='date',
            field=models.DateField(db_index=True, default=django.utils.timezone.now, help_text='The day the money actually moved.'),
        ),
        migrations.RunPython(backfill_payment_dates, unbackfill),
    ]
