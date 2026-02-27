from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Order, Part, PurchaseOrder, PurchaseOrderItem, SKU, Transfer


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
        self.assertEqual(self.client.get(reverse('api_transfers')).status_code, 200)
