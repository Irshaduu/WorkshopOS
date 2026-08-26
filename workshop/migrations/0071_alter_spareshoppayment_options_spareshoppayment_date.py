"""
Give a spare-shop payment the day the money moved.

`SpareShopPayment` carried only `created_at` (`auto_now_add`), so a shop
settled on the 30th and keyed on the 3rd was reported under the wrong month by
this shop's own Last Month filter and by the Deep Analysis shops section, with
no way to correct it. Its sibling `inventory.SupplierPayment` has had a `date`
column since day one.

The backfill takes each existing row's `created_at` as its best available
answer, converted to the workshop's own calendar day (`TIME_ZONE` is
Asia/Kolkata while the server may run in UTC, so the naive `.date()` of a UTC
timestamp reports the previous day for the whole of an IST morning). That is
an approximation by construction — the keystroke date is exactly what this
column exists to stop trusting — which is why the change belongs before
go-live, while nothing but demo data is being approximated.
"""

import django.utils.timezone
from django.db import migrations, models
from django.utils import timezone


def backfill_payment_dates(apps, schema_editor):
    """Existing rows: the keystroke day, on the workshop's calendar."""
    SpareShopPayment = apps.get_model('workshop', 'SpareShopPayment')
    updated = []
    for payment in SpareShopPayment.objects.all().only('id', 'created_at', 'date'):
        stamp = payment.created_at
        if stamp is None:
            continue
        payment.date = (timezone.localtime(stamp) if timezone.is_aware(stamp) else stamp).date()
        updated.append(payment)
    if updated:
        SpareShopPayment.objects.bulk_update(updated, ['date'], batch_size=500)


def unbackfill(apps, schema_editor):
    """Nothing to undo — the column goes with the reverse of the AddField."""


class Migration(migrations.Migration):

    dependencies = [
        ('workshop', '0070_jobcard_photos'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='spareshoppayment',
            options={'ordering': ['-date', '-created_at']},
        ),
        migrations.AddField(
            model_name='spareshoppayment',
            name='date',
            field=models.DateField(db_index=True, default=django.utils.timezone.now, help_text='The day the money actually moved.'),
        ),
        migrations.RunPython(backfill_payment_dates, unbackfill),
    ]
