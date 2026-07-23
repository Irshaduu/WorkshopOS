from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Rename the JobCard 'delivered' status to 'completed' (and its paired
    'discharged_date' to 'completed_date'), including the composite dashboard
    index that references the renamed field.

    RenameField preserves the underlying column data and the field's own
    db_index; it also updates the field references inside Meta.indexes in the
    migration state, so no separate index rebuild is required.
    """

    dependencies = [
        ('workshop', '0047_bulkpayer_advance_balance'),
    ]

    operations = [
        migrations.RenameField(
            model_name='jobcard',
            old_name='delivered',
            new_name='completed',
        ),
        migrations.RenameField(
            model_name='jobcard',
            old_name='discharged_date',
            new_name='completed_date',
        ),
    ]
