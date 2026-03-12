from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_order_customer_wechat"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="xianyu_order_no",
            field=models.CharField(blank=True, max_length=100, verbose_name="闲鱼订单号"),
        ),
    ]
