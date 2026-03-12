from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import reverse

from apps.core.models import SKU, Order, OrderItem
from apps.core.services import OrderService


class Command(BaseCommand):
    help = '核心业务冒烟：创建测试订单并跑通订单状态闭环，并校验关键页面/API可用性。'

    def add_arguments(self, parser):
        parser.add_argument('--keep-data', action='store_true', help='保留冒烟数据（默认执行后清理）')
        parser.add_argument('--skip-http-check', action='store_true', help='跳过页面/API连通性检查')

    def _assert_http_ok(self, client, url, name):
        resp = client.get(url)
        if resp.status_code != 200:
            raise CommandError(f'[{name}] 访问失败: {url} -> {resp.status_code}')
        self.stdout.write(self.style.SUCCESS(f"[HTTP-OK] {name}: {url}"))

    def handle(self, *args, **options):
        keep_data = bool(options.get('keep_data'))
        skip_http_check = bool(options.get('skip_http_check'))
        User = get_user_model()

        user, _ = User.objects.get_or_create(
            username='smoke_runner',
            defaults={
                'role': 'admin',
                'is_staff': True,
                'is_superuser': True,
            }
        )
        user.set_password('smoke123')
        user.save(update_fields=['password'])

        sku = SKU.objects.create(
            code=f'SMOKE-{date.today().strftime("%m%d")}',
            name='冒烟测试套餐',
            category='主题套餐',
            rental_price=Decimal('99.00'),
            deposit=Decimal('50.00'),
            stock=1,
            is_active=True,
        )
        order_data = {
            'customer_name': '冒烟客户',
            'customer_phone': '13900009999',
            'delivery_address': '广东省深圳市福田区冒烟路1号',
            'event_date': date.today() + timedelta(days=7),
            'rental_days': 1,
            'notes': 'smoke_flow',
            'items': [{
                'sku_id': sku.id,
                'quantity': 1,
            }],
        }
        order = OrderService.create_order(order_data, user)
        order = OrderService.confirm_order(order.id, Decimal('50.00'), user)
        order = OrderService.mark_as_delivered(order.id, 'SMOKE-SHIP-001', user)
        order = OrderService.mark_as_returned(order.id, 'SMOKE-RET-001', Decimal('99.00'), user)
        order = OrderService.complete_order(order.id, user)

        self.stdout.write(f"[OK] 冒烟通过：{order.order_no} -> {order.status}")

        if not skip_http_check:
            client = Client()
            if not client.login(username='smoke_runner', password='smoke123'):
                raise CommandError('冒烟账号登录失败，无法执行页面/API连通性检查')

            page_checks = [
                ('工作台', reverse('dashboard')),
                ('订单中心', reverse('orders_list')),
                ('转寄中心', reverse('transfers_list')),
                ('转寄推荐回放', reverse('transfer_recommendation_logs')),
                ('审批中心', reverse('approvals_list')),
                ('财务对账', reverse('finance_reconciliation')),
                ('运维中心', reverse('ops_center')),
            ]
            for name, url in page_checks:
                self._assert_http_ok(client, url, name)

            api_checks = [
                ('运维告警API', reverse('api_ops_alerts')),
                ('风险事件API', reverse('api_risk_events')),
                ('审批任务API', reverse('api_approvals')),
                ('财务对账API', reverse('api_finance_reconciliation')),
                ('转寄回放API', reverse('api_transfer_recommendation_logs')),
            ]
            for name, url in api_checks:
                self._assert_http_ok(client, url, name)
            self.stdout.write(self.style.SUCCESS('[OK] 页面/API连通性检查通过'))

        if not keep_data:
            OrderItem.objects.filter(order=order).delete()
            order.delete()
            sku.delete()
            self.stdout.write('[CLEANUP] 已清理冒烟数据')
