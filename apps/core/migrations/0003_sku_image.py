from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_transfer_allocation'),
    ]

    operations = [
        migrations.AddField(
            model_name='sku',
            name='image',
            field=models.FileField(blank=True, null=True, upload_to='sku_images/', verbose_name='SKU图片'),
        ),
    ]

