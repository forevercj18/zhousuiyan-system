from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_reservation_delivery_address'),
    ]

    operations = [
        migrations.AddField(
            model_name='sku',
            name='image_key',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='七牛图片Key'),
        ),
        migrations.AddField(
            model_name='skuimage',
            name='image_key',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='七牛图片Key'),
        ),
        migrations.AlterField(
            model_name='skuimage',
            name='image',
            field=models.FileField(blank=True, null=True, upload_to='sku_images/', verbose_name='图片'),
        ),
    ]
