from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_riskevent_assignee_processing_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="customer_wechat",
            field=models.CharField(blank=True, max_length=100, verbose_name="微信号"),
        ),
    ]
