from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_permissiontemplate'),
    ]

    operations = [
        migrations.CreateModel(
            name='Reservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reservation_no', models.CharField(max_length=50, unique=True, verbose_name='预定单号')),
                ('customer_wechat', models.CharField(max_length=100, verbose_name='微信号')),
                ('customer_name', models.CharField(blank=True, max_length=100, verbose_name='客户姓名')),
                ('customer_phone', models.CharField(blank=True, max_length=20, verbose_name='联系电话')),
                ('city', models.CharField(blank=True, max_length=100, verbose_name='意向城市')),
                ('quantity', models.IntegerField(default=1, verbose_name='数量')),
                ('event_date', models.DateField(verbose_name='预定日期')),
                ('deposit_amount', models.DecimalField(decimal_places=2, default='0.00', max_digits=10, verbose_name='订金金额')),
                ('status', models.CharField(choices=[('pending_info', '待补信息'), ('ready_to_convert', '可转正式订单'), ('converted', '已转订单'), ('cancelled', '已取消'), ('refunded', '已退款')], default='pending_info', max_length=20, verbose_name='状态')),
                ('notes', models.TextField(blank=True, verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('converted_order', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='source_reservation', to='core.order', verbose_name='关联正式订单')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_reservations', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('sku', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reservations', to='core.sku', verbose_name='意向款式')),
            ],
            options={
                'verbose_name': '预定单',
                'verbose_name_plural': '预定单',
                'db_table': 'reservations',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['reservation_no'], name='reservations_reserva_047526_idx'),
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['status'], name='reservations_status_232fe2_idx'),
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['event_date'], name='reservations_event_d_3b99c0_idx'),
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['customer_wechat'], name='reservations_custome_b90189_idx'),
        ),
        migrations.AlterField(
            model_name='financetransaction',
            name='order',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='finance_transactions', to='core.order', verbose_name='订单'),
        ),
        migrations.AddField(
            model_name='financetransaction',
            name='reservation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='finance_transactions', to='core.reservation', verbose_name='预定单'),
        ),
        migrations.AlterField(
            model_name='financetransaction',
            name='transaction_type',
            field=models.CharField(choices=[('reservation_deposit_received', '收预定订金'), ('reservation_deposit_refund', '退预定订金'), ('reservation_deposit_applied', '预定订金转押金'), ('deposit_received', '收押金'), ('balance_received', '收尾款'), ('deposit_refund', '退押金'), ('penalty_charge', '扣罚'), ('manual_adjust', '人工调整')], max_length=30, verbose_name='交易类型'),
        ),
        migrations.AddIndex(
            model_name='financetransaction',
            index=models.Index(fields=['reservation', 'transaction_type'], name='finance_tra_reserva_7edc4a_idx'),
        ),
    ]
