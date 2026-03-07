from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Order, Part, PurchaseOrder, PurchaseOrderItem, SKU, Transfer, TransferAllocation, OrderItem, AuditLog


User = get_user_model()


class ApiEndpointsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='api_tester',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.login(username='api_tester', password='test123')

        self.sku = SKU.objects.create(
            code='SKU-API-1',
            name='API套餐',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('60.00'),
            stock=3,
            is_active=True,
        )
        self.part = Part.objects.create(
            name='API部件',
            spec='A1',
            category='accessory',
            unit='个',
            current_stock=2,
            safety_stock=3,
            is_active=True,
        )
        Order.objects.create(
            id=1001,
            customer_name='API客户',
            customer_phone='13600000000',
            delivery_address='API地址',
            event_date=date.today(),
            rental_days=1,
            status='pending',
            created_by=self.user,
            total_amount=Decimal('180.00'),
            balance=Decimal('180.00'),
        )

    def test_orders_api_returns_database_records(self):
        resp = self.client.get(reverse('api_orders_list'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload['data']), 1)
        self.assertIn('customer_name', payload['data'][0])

    def test_skus_api_returns_database_records(self):
        resp = self.client.get(reverse('api_skus_list'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        codes = [item['code'] for item in payload['data']]
        self.assertIn('SKU-API-1', codes)

    def test_dashboard_stats_api_returns_real_counts(self):
        resp = self.client.get(reverse('api_dashboard_stats'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data']['total_orders'], 1)
        self.assertEqual(payload['data']['total_skus'], 1)
        self.assertEqual(payload['data']['low_stock_parts'], 1)
        self.assertIn('completed_orders', payload['data'])

    def test_dashboard_role_view_should_return_role_specific_payload(self):
        resp = self.client.get(reverse('api_dashboard_role_view'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data']['role'], 'admin')
        self.assertEqual(payload['data']['view_type'], 'business')
        self.assertTrue(any(card['key'] == 'total_revenue' for card in payload['data']['focus_cards']))
        self.assertTrue(any(action['url_name'] == 'order_create' for action in payload['data']['quick_actions']))
        self.assertTrue(any(risk['key'] == 'pending_orders' for risk in payload['data']['risk_entries']))
        self.assertTrue(any(risk['key'] == 'low_stock_parts' and risk['query'] == 'low=1' for risk in payload['data']['risk_entries']))

        warehouse_user = User.objects.create_user(
            username='api_wh',
            password='test123',
            role='warehouse_staff',
            is_superuser=False,
            is_staff=True,
        )
        self.client.logout()
        self.client.login(username='api_wh', password='test123')
        resp2 = self.client.get(reverse('api_dashboard_role_view'))
        self.assertEqual(resp2.status_code, 200)
        payload2 = resp2.json()
        self.assertTrue(payload2['success'])
        self.assertEqual(payload2['data']['role'], 'warehouse_staff')
        self.assertEqual(payload2['data']['view_type'], 'warehouse')
        self.assertTrue(any(card['key'] == 'warehouse_available_stock' for card in payload2['data']['focus_cards']))
        self.assertTrue(any(action['url_name'] == 'parts_inventory_list' for action in payload2['data']['quick_actions']))
        self.assertTrue(any(risk['key'] == 'pending_transfer_tasks' for risk in payload2['data']['risk_entries']))

    def test_dashboard_stats_should_ignore_allocations_from_inactive_source(self):
        OrderItem.objects.create(
            order=Order.objects.get(id=1001),
            sku=self.sku,
            quantity=2,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price * 2,
        )
        active_source = Order.objects.create(
            customer_name='source-active',
            customer_phone='13000000003',
            delivery_address='S2',
            event_date=date.today() - timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        source = Order.objects.create(
            customer_name='source-cancelled',
            customer_phone='13000000001',
            delivery_address='S',
            event_date=date.today(),
            rental_days=1,
            status='cancelled',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='target-pending',
            customer_phone='13000000002',
            delivery_address='T',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        TransferAllocation.objects.create(
            source_order=active_source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=1),
            window_end=target.event_date + timedelta(days=1),
            created_by=self.user,
            status='locked',
        )
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=1),
            window_end=target.event_date + timedelta(days=1),
            created_by=self.user,
            status='locked',
        )
        resp = self.client.get(reverse('api_dashboard_stats'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        # occupied_raw=2, transfer_allocated 只应计 active_source 的 1，仓库可用=3-(2-1)=2
        self.assertEqual(payload['data']['warehouse_available_stock'], 2)

    def test_order_status_action_apis(self):
        order = Order.objects.get(id=1001)

        resp = self.client.post(reverse('api_order_confirm', kwargs={'order_id': order.id}), {'deposit_paid': '60'})
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'confirmed')

        resp = self.client.post(reverse('api_order_mark_delivered', kwargs={'order_id': order.id}), {'ship_tracking': 'S1'})
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')

        resp = self.client.post(reverse('api_order_mark_returned', kwargs={'order_id': order.id}), {'return_tracking': 'R1', 'balance_paid': '120'})
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'returned')

        resp = self.client.post(reverse('api_order_complete', kwargs={'order_id': order.id}))
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    def test_api_mark_returned_should_block_for_transfer_source_order(self):
        source = Order.objects.create(
            customer_name='source-api',
            customer_phone='13100000001',
            delivery_address='A',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='target-api',
            customer_phone='13100000002',
            delivery_address='B',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=1),
            window_end=target.event_date + timedelta(days=1),
            created_by=self.user,
            status='locked',
        )
        resp = self.client.post(
            reverse('api_order_mark_returned', kwargs={'order_id': source.id}),
            {'return_tracking': 'R-API', 'balance_paid': '0'}
        )
        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertFalse(payload['success'])
        self.assertIn('转寄中心', payload['message'])

    def test_procurement_and_transfer_apis(self):
        po = PurchaseOrder.objects.create(
            channel='online',
            supplier='API供应商',
            order_date=date.today(),
            status='draft',
            created_by=self.user,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            part=self.part,
            part_name=self.part.name,
            spec=self.part.spec,
            unit=self.part.unit,
            quantity=2,
            unit_price=Decimal('5.00'),
            subtotal=Decimal('10.00'),
        )

        self.assertEqual(self.client.get(reverse('api_purchase_orders')).status_code, 200)
        self.assertEqual(self.client.post(reverse('api_purchase_order_mark_ordered', kwargs={'po_id': po.id})).status_code, 200)
        po.refresh_from_db()
        self.assertEqual(po.status, 'ordered')
        self.assertEqual(self.client.post(reverse('api_purchase_order_mark_arrived', kwargs={'po_id': po.id})).status_code, 200)
        po.refresh_from_db()
        self.assertEqual(po.status, 'arrived')
        self.assertEqual(self.client.post(reverse('api_purchase_order_mark_stocked', kwargs={'po_id': po.id})).status_code, 200)
        po.refresh_from_db()
        self.assertEqual(po.status, 'stocked')

        order_from = Order.objects.create(
            customer_name='API转寄回收',
            customer_phone='13811111111',
            delivery_address='A',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
            ship_date=date.today(),
            return_date=date.today() + timedelta(days=2),
        )
        order_to = Order.objects.create(
            customer_name='API转寄发货',
            customer_phone='13822222222',
            delivery_address='B',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
            ship_date=date.today() + timedelta(days=2),
            return_date=date.today() + timedelta(days=5),
        )

        resp = self.client.post(
            reverse('api_transfer_create'),
            {'order_from_id': order_from.id, 'order_to_id': order_to.id, 'sku_id': self.sku.id}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Transfer.objects.exists())
        transfer = Transfer.objects.first()
        self.assertTrue(
            AuditLog.objects.filter(
                module='转寄',
                target=f'任务#{transfer.id}',
                details__icontains='API创建转寄任务'
            ).exists()
        )
        self.assertEqual(self.client.get(reverse('api_transfers')).status_code, 200)

    def test_dashboard_stats_should_match_dashboard_page_core_fields(self):
        # 准备一点状态数据，覆盖核心统计字段
        Order.objects.create(
            customer_name='完成单',
            customer_phone='13700000011',
            delivery_address='X',
            event_date=date.today(),
            rental_days=1,
            status='completed',
            created_by=self.user,
            total_amount=Decimal('99.00'),
            balance=Decimal('0.00'),
        )
        Order.objects.create(
            customer_name='已发货单',
            customer_phone='13700000012',
            delivery_address='Y',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
            total_amount=Decimal('88.00'),
            balance=Decimal('88.00'),
        )

        page_resp = self.client.get(reverse('dashboard'))
        self.assertEqual(page_resp.status_code, 200)
        page_stats = page_resp.context['stats']

        api_resp = self.client.get(reverse('api_dashboard_stats'))
        self.assertEqual(api_resp.status_code, 200)
        api_stats = api_resp.json()['data']

        compare_fields = [
            'pending_orders',
            'delivered_orders',
            'completed_orders',
            'warehouse_available_stock',
            'transfer_available_count',
            'total_orders',
            'total_skus',
            'low_stock_parts',
        ]
        for field in compare_fields:
            self.assertEqual(page_stats[field], api_stats[field], f'字段口径不一致: {field}')

        self.assertEqual(Decimal(str(page_stats['total_revenue'])), Decimal(str(api_stats['total_revenue'])))
        self.assertEqual(Decimal(str(page_stats['pending_revenue'])), Decimal(str(api_stats['pending_revenue'])))
