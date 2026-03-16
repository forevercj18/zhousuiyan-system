from decimal import Decimal

from django.db import migrations, models


def sync_legacy_source_order_no(apps, schema_editor):
    Order = apps.get_model('core', 'Order')
    for order in Order.objects.filter(source_order_no='', xianyu_order_no__gt=''):
        order.source_order_no = order.xianyu_order_no
        if not order.order_source:
            order.order_source = 'xianyu'
        order.save(update_fields=['source_order_no', 'order_source'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_reservation_owner'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='order_source',
            field=models.CharField(choices=[('wechat', '微信成交'), ('xianyu', '闲鱼'), ('xiaohongshu', '小红书'), ('other', '其他')], default='wechat', max_length=20, verbose_name='订单来源'),
        ),
        migrations.AddField(
            model_name='order',
            name='return_pickup_status',
            field=models.CharField(choices=[('not_required', '无需叫件'), ('pending_schedule', '待安排取件'), ('scheduled', '已安排取件'), ('picked_up', '已上门取件'), ('completed', '已完成'), ('cancelled', '已取消')], default='not_required', max_length=20, verbose_name='包回邮叫件状态'),
        ),
        migrations.AddField(
            model_name='order',
            name='return_service_fee',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10, verbose_name='包回邮服务费'),
        ),
        migrations.AddField(
            model_name='order',
            name='return_service_payment_channel',
            field=models.CharField(blank=True, choices=[('xianyu', '闲鱼'), ('xiaohongshu', '小红书'), ('wechat', '微信'), ('offline', '线下')], max_length=20, verbose_name='包回邮收款渠道'),
        ),
        migrations.AddField(
            model_name='order',
            name='return_service_payment_reference',
            field=models.CharField(blank=True, max_length=100, verbose_name='包回邮支付参考号'),
        ),
        migrations.AddField(
            model_name='order',
            name='return_service_payment_status',
            field=models.CharField(choices=[('unpaid', '未收款'), ('paid', '已收款'), ('refunded', '已退款')], default='unpaid', max_length=20, verbose_name='包回邮收款状态'),
        ),
        migrations.AddField(
            model_name='order',
            name='return_service_type',
            field=models.CharField(choices=[('none', '无'), ('customer_self_return', '客户自寄回'), ('platform_return_included', '包回邮服务')], default='none', max_length=30, verbose_name='回寄服务类型'),
        ),
        migrations.AddField(
            model_name='order',
            name='source_order_no',
            field=models.CharField(blank=True, max_length=100, verbose_name='平台单号'),
        ),
        migrations.AlterField(
            model_name='financetransaction',
            name='transaction_type',
            field=models.CharField(choices=[('reservation_deposit_received', '收预定订金'), ('reservation_deposit_refund', '退预定订金'), ('reservation_deposit_applied', '预定订金转押金'), ('deposit_received', '收押金'), ('balance_received', '收尾款'), ('deposit_refund', '退押金'), ('return_service_received', '收包回邮服务费'), ('return_service_refund', '退包回邮服务费'), ('penalty_charge', '扣罚'), ('manual_adjust', '人工调整')], max_length=30, verbose_name='交易类型'),
        ),
        migrations.AddIndex(
            model_name='order',
            index=models.Index(fields=['order_source'], name='orders_order_source_952ff0_idx'),
        ),
        migrations.AddIndex(
            model_name='order',
            index=models.Index(fields=['source_order_no'], name='orders_source_order_no_c15027_idx'),
        ),
        migrations.RunPython(sync_legacy_source_order_no, migrations.RunPython.noop),
    ]
