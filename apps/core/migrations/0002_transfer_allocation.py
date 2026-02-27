from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TransferAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.IntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)], verbose_name='分配数量')),
                ('target_event_date', models.DateField(verbose_name='目标预定日期')),
                ('window_start', models.DateField(verbose_name='锁窗口开始')),
                ('window_end', models.DateField(verbose_name='锁窗口结束')),
                ('distance_score', models.DecimalField(decimal_places=4, default=0, max_digits=8, verbose_name='地址距离分值')),
                ('status', models.CharField(choices=[('locked', '已锁定'), ('released', '已释放'), ('consumed', '已消耗')], default='locked', max_length=20, verbose_name='状态')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('sku', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.sku', verbose_name='SKU')),
                ('source_order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transfer_allocations_source', to='core.order', verbose_name='来源订单')),
                ('target_order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transfer_allocations_target', to='core.order', verbose_name='目标订单')),
            ],
            options={
                'verbose_name': '转寄分配锁',
                'verbose_name_plural': '转寄分配锁',
                'db_table': 'transfer_allocations',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='transferallocation',
            index=models.Index(fields=['source_order', 'sku', 'status'], name='transfer_al_source__fcb8bc_idx'),
        ),
        migrations.AddIndex(
            model_name='transferallocation',
            index=models.Index(fields=['target_order', 'status'], name='transfer_al_target__b72d17_idx'),
        ),
        migrations.AddIndex(
            model_name='transferallocation',
            index=models.Index(fields=['target_event_date'], name='transfer_al_target__bd9c91_idx'),
        ),
    ]

