"""
Labour becomes ONE charge per job card instead of a price per job line.

The whole point of this migration is that **no bill may change value**. Every
card's existing per-line amounts are summed into the new `labour_amount`, so
`total_bill_amount` — which used to be spares + Sum(labour lines) and is now
spares + labour_amount — lands on exactly the number it already held. The old
column is left in place and merely re-described; dropping it would throw away
the only record of how a historical card was priced.

The backfill is a single UPDATE with a correlated subquery rather than a Python
loop over the rows: there are tens of thousands of job cards, and the loop
version is one query each against a database in Singapore.
"""

from decimal import Decimal

from django.db import migrations, models
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce


def sum_line_amounts_into_the_card(apps, schema_editor):
    JobCard = apps.get_model('workshop', 'JobCard')
    JobCardLabourItem = apps.get_model('workshop', 'JobCardLabourItem')

    money = DecimalField(max_digits=12, decimal_places=2)
    per_card = (
        JobCardLabourItem.objects
        .filter(job_card=OuterRef('pk'))
        .values('job_card')
        .annotate(total=Sum('amount'))
        .values('total')
    )
    JobCard.objects.update(
        labour_amount=Coalesce(
            Subquery(per_card, output_field=money),
            Value(Decimal('0'), output_field=money),
            output_field=money,
        )
    )


def clear_the_card_level_charge(apps, schema_editor):
    """
    Reverse. The per-line amounts were never deleted, so going back simply stops
    reading the new column — there is nothing to reconstruct.
    """
    apps.get_model('workshop', 'JobCard').objects.update(labour_amount=Decimal('0'))


class Migration(migrations.Migration):

    dependencies = [
        ('workshop', '0065_cashbookentry_workshop_ca_categor_dff6bc_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobcard',
            name='labour_amount',
            field=models.DecimalField(blank=True, decimal_places=2, default=0, help_text='Total labour charge for every job on this card (entered once, not per line)', max_digits=12),
        ),
        migrations.RunPython(
            sum_line_amounts_into_the_card,
            clear_the_card_level_charge,
        ),
        migrations.AlterField(
            model_name='jobcardlabouritem',
            name='amount',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='DORMANT — pre-2026-08-04 per-line charge. Superseded by JobCard.labour_amount.', max_digits=10, null=True),
        ),
    ]
