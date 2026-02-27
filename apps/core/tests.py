from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    AuditLog,
    Order,
    OrderItem,
    Part,
    PartsMovement,
    PurchaseOrder,
    PurchaseOrderItem,
    SKU,
    SystemSettings,
    Transfer,
)
from .services import OrderService, PartsService, ProcurementService


User = get_user_model()


class CoreServicesTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='tester',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )

        for key, value in (
            ('ship_lead_days', '2'),
            ('return_offset_days', '1'),
            ('buffer_days', '1'),
            ('max_transfer_gap_days', '3'),
        ):
            SystemSettings.objects.create(key=key, value=value)

        self.sku = SKU.objects.create(
            code='SKU-T001',
            name='测试套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('50.00'),
            stock=2,
            is_active=True,
        )

        self.part = Part.objects.create(
            name='测试部件',
            spec='标准',
            category='accessory',
            unit='个',
            current_stock=5,
            safety_stock=1,
            is_active=True,
        )

    def test_create_order_success(self):
        event_date = date.today() + timedelta(days=7)
        order = OrderService.create_order(
            data={
                'customer_name': '张三',
                'customer_phone': '13800000000',
                'delivery_address': '测试地址',
                'event_date': event_date,
                'rental_days': 1,
                'items': [{'sku_id': self.sku.id, 'quantity': 1}],
            },
            user=self.user,
        )

        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.total_amount, Decimal('150.00'))
        self.assertTrue(AuditLog.objects.filter(target=order.order_no, action='create').exists())

    def test_create_order_raises_when_inventory_insufficient(self):
        event_date = date.today() + timedelta(days=10)
        occupied_order = Order.objects.create(
            customer_name='已占用客户',
            customer_phone='13900000000',
            delivery_address='占用地址',
            event_date=event_date,
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=occupied_order,
            sku=self.sku,
            quantity=2,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('300.00'),
        )

        with self.assertRaises(ValueError):
            OrderService.create_order(
                data={
                    'customer_name': '李四',
                    'customer_phone': '13700000000',
                    'delivery_address': '测试地址2',
                    'event_date': event_date,
                    'rental_days': 1,
                    'items': [{'sku_id': self.sku.id, 'quantity': 1}],
                },
                user=self.user,
            )

    def test_parts_inbound_and_outbound_updates_stock(self):
        PartsService.inbound(self.part.id, 3, 'DOC-IN-1', '测试入库', self.user)
        self.part.refresh_from_db()
        self.assertEqual(self.part.current_stock, 8)

        PartsService.outbound(self.part.id, 2, 'DOC-OUT-1', '测试出库', self.user)
        self.part.refresh_from_db()
        self.assertEqual(self.part.current_stock, 6)
        self.assertEqual(PartsMovement.objects.filter(part=self.part).count(), 2)

    def test_procurement_mark_stocked_updates_part_inventory(self):
        po = PurchaseOrder.objects.create(
            channel='online',
            supplier='测试供应商',
            order_date=date.today(),
            status='arrived',
            created_by=self.user,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            part=self.part,
            part_name=self.part.name,
            spec=self.part.spec,
            unit=self.part.unit,
            quantity=4,
            unit_price=Decimal('10.00'),
            subtotal=Decimal('40.00'),
        )

        ProcurementService.mark_as_stocked(po.id, self.user)
        po.refresh_from_db()
        self.part.refresh_from_db()

        self.assertEqual(po.status, 'stocked')
        self.assertEqual(self.part.current_stock, 9)


class CoreViewsFlowTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='flow_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.login(username='flow_admin', password='test123')

        for key, value in (
            ('ship_lead_days', '2'),
            ('return_offset_days', '1'),
            ('buffer_days', '1'),
            ('max_transfer_gap_days', '3'),
        ):
            SystemSettings.objects.create(key=key, value=value)

        self.part = Part.objects.create(
            name='流程部件',
            spec='F1',
            category='accessory',
            unit='个',
            current_stock=10,
            safety_stock=2,
            is_active=True,
        )
        self.sku = SKU.objects.create(
            code='SKU-FLOW-1',
            name='流程套餐',
            category='主题套餐',
            rental_price=Decimal('200.00'),
            deposit=Decimal('80.00'),
            stock=5,
            is_active=True,
        )

    def test_order_full_status_flow(self):
        order = Order.objects.create(
            customer_name='全流程客户',
            customer_phone='13511111111',
            delivery_address='全流程地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='pending',
            total_amount=Decimal('280.00'),
            balance=Decimal('280.00'),
            created_by=self.user,
        )

        self.client.post(reverse('order_mark_confirmed', kwargs={'order_id': order.id}), {'deposit_paid': '80'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'confirmed')

        self.client.post(reverse('order_mark_delivered', kwargs={'order_id': order.id}), {'ship_tracking': 'SHIP-1'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')

        self.client.post(
            reverse('order_mark_returned', kwargs={'order_id': order.id}),
            {'return_tracking': 'RET-1', 'balance_paid': '200'}
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'returned')

        self.client.post(reverse('order_mark_completed', kwargs={'order_id': order.id}))
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    def test_workbench_mark_delivered(self):
        order = Order.objects.create(
            customer_name='流转客户',
            customer_phone='13500000000',
            delivery_address='地址',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('order_mark_delivered', kwargs={'order_id': order.id}),
            {'ship_tracking': 'SF123'}
        )
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')

    def test_workbench_mark_completed_from_delivered(self):
        order = Order.objects.create(
            customer_name='完成客户',
            customer_phone='13500000001',
            delivery_address='地址2',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
            total_amount=Decimal('100.00'),
            balance=Decimal('20.00'),
            deposit_paid=Decimal('80.00'),
        )

        resp = self.client.post(
            reverse('order_mark_completed', kwargs={'order_id': order.id}),
            {'return_tracking': 'RT001'}
        )
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    def test_purchase_order_create_with_items(self):
        resp = self.client.post(
            reverse('purchase_order_create'),
            {
                'channel': 'online',
                'supplier': '测试供应商',
                'order_date': date.today().isoformat(),
                'arrival_date': (date.today() + timedelta(days=1)).isoformat(),
                'part_id[]': [str(self.part.id)],
                'quantity[]': ['2'],
                'unit_price[]': ['12.5'],
                'notes': '测试采购创建',
            }
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        po = PurchaseOrder.objects.first()
        self.assertEqual(po.items.count(), 1)
        self.assertEqual(po.total_amount, Decimal('25.0'))

    def test_purchase_order_status_flow(self):
        po = PurchaseOrder.objects.create(
            channel='online',
            supplier='状态流采购单',
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
            quantity=1,
            unit_price=Decimal('8.00'),
            subtotal=Decimal('8.00'),
        )

        self.client.post(reverse('purchase_order_mark_ordered', kwargs={'po_id': po.id}))
        po.refresh_from_db()
        self.assertEqual(po.status, 'ordered')

        self.client.post(reverse('purchase_order_mark_arrived', kwargs={'po_id': po.id}))
        po.refresh_from_db()
        self.assertEqual(po.status, 'arrived')

        before_stock = self.part.current_stock
        self.client.post(reverse('purchase_order_mark_stocked', kwargs={'po_id': po.id}))
        po.refresh_from_db()
        self.part.refresh_from_db()
        self.assertEqual(po.status, 'stocked')
        self.assertEqual(self.part.current_stock, before_stock + 1)

    def test_transfer_create_and_complete_flow(self):
        order_from = Order.objects.create(
            customer_name='转寄回收',
            customer_phone='13611111111',
            delivery_address='A地址',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
            ship_date=date.today(),
            return_date=date.today() + timedelta(days=2),
        )
        order_to = Order.objects.create(
            customer_name='转寄发货',
            customer_phone='13622222222',
            delivery_address='B地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
            ship_date=date.today() + timedelta(days=2),
            return_date=date.today() + timedelta(days=5),
        )
        OrderItem.objects.create(order=order_from, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('280.00'))
        OrderItem.objects.create(order=order_to, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('280.00'))

        self.client.post(
            reverse('transfer_create'),
            {'order_from_id': order_from.id, 'order_to_id': order_to.id, 'sku_id': self.sku.id}
        )
        transfer = Transfer.objects.get(order_from=order_from, order_to=order_to, sku=self.sku)
        self.assertEqual(transfer.status, 'pending')

        self.client.post(reverse('transfer_complete', kwargs={'transfer_id': transfer.id}))
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'completed')
