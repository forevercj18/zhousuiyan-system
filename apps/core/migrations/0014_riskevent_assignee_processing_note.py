from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_approvaltask_current_review_count_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='riskevent',
            name='assignee',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='risk_events_assigned', to=settings.AUTH_USER_MODEL, verbose_name='负责人'),
        ),
        migrations.AddField(
            model_name='riskevent',
            name='processing_note',
            field=models.TextField(blank=True, verbose_name='处理备注'),
        ),
    ]
