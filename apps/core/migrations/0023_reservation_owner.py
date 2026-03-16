from django.db import migrations, models
import django.db.models.deletion


def backfill_reservation_owner(apps, schema_editor):
    Reservation = apps.get_model('core', 'Reservation')
    for reservation in Reservation.objects.filter(owner__isnull=True, created_by__isnull=False):
        reservation.owner_id = reservation.created_by_id
        reservation.save(update_fields=['owner'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_reservation_and_finance_transaction_updates'),
    ]

    operations = [
        migrations.AddField(
            model_name='reservation',
            name='owner',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='owned_reservations', to='core.user', verbose_name='当前负责人'),
        ),
        migrations.RunPython(backfill_reservation_owner, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['owner', 'status'], name='reservations_owner_id_5e7d4e_idx'),
        ),
    ]
