from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone

from .middleware import AuditLogMiddleware
from .models import (
    AuditLog,
    InventoryUnit,
    UnitMovement,
    Order,
    OrderItem,
    Part,
    PartsMovement,
    PurchaseOrder,
    PurchaseOrderItem,
    SKU,
    SystemSettings,
    Transfer,
    TransferAllocation,
)
from .services import OrderService, PartsService, ProcurementService
from .utils import (
    get_transfer_match_candidates,
    build_transfer_allocation_plan,
    build_transfer_pool_rows,
    check_sku_availability,
)


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
        self.assertEqual(order.total_amount, Decimal('100.00'))
        self.assertTrue(AuditLog.objects.filter(target=order.order_no, action='create').exists())

    def test_create_order_transfer_allocation_should_auto_create_transfer_task(self):
        source_order = Order.objects.create(
            customer_name='来源客户',
            customer_phone='13711112222',
            delivery_address='广东省广州市天河区体育西路1号',
            event_date=date.today(),
            rental_days=1,
            ship_date=date.today() - timedelta(days=2),
            return_date=date.today() + timedelta(days=2),
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=source_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('100.00'),
        )
        order = OrderService.create_order(
            data={
                'customer_name': '目标客户',
                'customer_phone': '13899990000',
                'delivery_address': '广东省广州市越秀区中山一路2号',
                'event_date': date.today() + timedelta(days=7),
                'rental_days': 1,
                'items': [{'sku_id': self.sku.id, 'quantity': 1}],
            },
            user=self.user,
        )
        self.assertTrue(TransferAllocation.objects.filter(target_order=order, status='locked').exists())
        transfer = Transfer.objects.get(order_to=order, order_from=source_order, sku=self.sku, status='pending')
        self.assertEqual(transfer.quantity, 1)
        self.assertEqual(transfer.gap_days, (order.event_date - source_order.event_date).days)

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
            subtotal=Decimal('200.00'),
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

    def test_create_order_force_warehouse_should_not_lock_transfer(self):
        source_order = Order.objects.create(
            customer_name='来源客户',
            customer_phone='13600000001',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=source_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('100.00'),
        )

        event_date = date.today() + timedelta(days=8)
        order = OrderService.create_order(
            data={
                'customer_name': '仓库发货客户',
                'customer_phone': '13811110000',
                'delivery_address': '广东省深圳市南山区',
                'event_date': event_date,
                'rental_days': 1,
                'items': [{
                    'sku_id': self.sku.id,
                    'quantity': 1,
                    'transfer_source_order_id': source_order.id,
                    'force_warehouse': True,
                }],
            },
            user=self.user,
        )

        self.assertEqual(order.status, 'pending')
        self.assertFalse(TransferAllocation.objects.filter(target_order=order).exists())

    def test_create_order_force_warehouse_still_checks_stock(self):
        self.sku.stock = 0
        self.sku.save(update_fields=['stock'])

        source_order = Order.objects.create(
            customer_name='来源客户',
            customer_phone='13600000002',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=source_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('100.00'),
        )

        event_date = date.today() + timedelta(days=8)
        with self.assertRaises(ValueError):
            OrderService.create_order(
                data={
                    'customer_name': '仓库发货库存不足客户',
                    'customer_phone': '13811110001',
                    'delivery_address': '广东省深圳市南山区',
                    'event_date': event_date,
                    'rental_days': 1,
                    'items': [{
                        'sku_id': self.sku.id,
                        'quantity': 1,
                        'force_warehouse': True,
                    }],
                },
                user=self.user,
            )

    def test_check_sku_availability_should_not_release_when_source_completed_but_target_active(self):
        source_order = Order.objects.create(
            customer_name='来源客户',
            customer_phone='13600000001',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='completed',
            created_by=self.user,
        )
        target_order = Order.objects.create(
            customer_name='目标客户',
            customer_phone='13600000002',
            delivery_address='广东省深圳市南山区',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=source_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('100.00'),
        )
        OrderItem.objects.create(
            order=target_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('100.00'),
        )
        TransferAllocation.objects.create(
            source_order=source_order,
            target_order=target_order,
            sku=self.sku,
            quantity=1,
            target_event_date=target_order.event_date,
            window_start=target_order.event_date - timedelta(days=5),
            window_end=target_order.event_date + timedelta(days=5),
            status='consumed',
            created_by=self.user,
        )

        result = check_sku_availability(self.sku.id, 1)
        self.assertEqual(result['occupied'], 1)
        self.assertEqual(result['available_count'], 1)

    def test_transfer_candidate_sorting_by_target_plus_buffer_then_distance(self):
        sku = SKU.objects.create(
            code='SKU-TX-1',
            name='转寄套餐1',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        # 目标地址在广州，目标日期+buffer(默认1)更接近 event_date=+2 的来源单
        source_a = Order.objects.create(
            customer_name='来源A',
            customer_phone='13000000001',
            delivery_address='上海市浦东新区世纪大道100号',  # 距离广州更远
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_a, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        # 来源B：日期更远但地址更近，按新规则应排在来源A后
        source_b = Order.objects.create(
            customer_name='来源B',
            customer_phone='13000000002',
            delivery_address='广州市天河区体育西路101号',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_b, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        # 来源C：与来源A同日期，测试次排序（距离）应优于来源A
        source_c = Order.objects.create(
            customer_name='来源C',
            customer_phone='13000000003',
            delivery_address='深圳市南山区科技园101号',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_c, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        target_date = date.today() + timedelta(days=8)
        candidates = get_transfer_match_candidates('广州市天河区体育西路2号', target_date, sku.id)
        self.assertGreaterEqual(len(candidates), 3)
        self.assertEqual(candidates[0]['source_order'].id, source_c.id)
        self.assertEqual(candidates[1]['source_order'].id, source_a.id)
        self.assertEqual(candidates[2]['source_order'].id, source_b.id)

    def test_transfer_candidate_date_gap_uses_buffer_days_setting(self):
        SystemSettings.objects.update_or_create(
            key='buffer_days',
            defaults={'value': '7'}
        )
        sku = SKU.objects.create(
            code='SKU-TX-BUF',
            name='转寄缓冲测试套餐',
            category='主题套餐',
            rental_price=Decimal('88.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        target_date = date.today() + timedelta(days=10)
        source = Order.objects.create(
            customer_name='来源缓冲',
            customer_phone='13060000001',
            delivery_address='广东省广州市天河区',
            event_date=target_date - timedelta(days=6),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('88.00'))

        candidates = get_transfer_match_candidates('广东省广州市越秀区', target_date, sku.id)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['source_order'].id, source.id)
        self.assertEqual(candidates[0]['date_gap_score'], 13)
        self.assertEqual(candidates[0]['buffer_days'], 7)

    def test_transfer_distance_parse_with_missing_city_suffix(self):
        sku = SKU.objects.create(
            code='SKU-TX-1A',
            name='转寄套餐1A',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        near_source = Order.objects.create(
            customer_name='近来源',
            customer_phone='13010000001',
            delivery_address='广东省揭阳市榕城区临江路',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        far_source = Order.objects.create(
            customer_name='远来源',
            customer_phone='13010000002',
            delivery_address='广东省广州市天河区体育西路',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=near_source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))
        OrderItem.objects.create(order=far_source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        candidates = get_transfer_match_candidates('广东揭阳榕城区东升路', date.today() + timedelta(days=8), sku.id)
        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0]['source_order'].id, near_source.id)
        self.assertIn(candidates[0]['distance_confidence'], ['high', 'medium'])

    def test_transfer_distance_parse_with_province_and_city_without_city_char(self):
        sku = SKU.objects.create(
            code='SKU-TX-1B',
            name='转寄套餐1B',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        near_source = Order.objects.create(
            customer_name='近来源',
            customer_phone='13020000001',
            delivery_address='福建省泉州市丰泽区东海街道',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        far_source = Order.objects.create(
            customer_name='远来源',
            customer_phone='13020000002',
            delivery_address='福建省福州市鼓楼区东街口',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=near_source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))
        OrderItem.objects.create(order=far_source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        candidates = get_transfer_match_candidates('福建省泉州晋江市世纪大道', date.today() + timedelta(days=8), sku.id)
        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0]['source_order'].id, near_source.id)
        self.assertIn(candidates[0]['distance_confidence'], ['high', 'medium'])

    def test_transfer_parse_city_should_not_fallback_to_province_capital(self):
        sku = SKU.objects.create(
            code='SKU-TX-CITY',
            name='转寄城市解析测试',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源单',
            customer_phone='13021000001',
            delivery_address='广东省广州市天河区体育西路',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        candidates = get_transfer_match_candidates('陕西省咸阳市彬州市西大街南二巷', date.today() + timedelta(days=8), sku.id)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['target_city'], '咸阳市')

    def test_transfer_parse_short_province_city_text(self):
        sku = SKU.objects.create(
            code='SKU-TX-SHORT',
            name='转寄短地址解析测试',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源单',
            customer_phone='13022000001',
            delivery_address='广东省广州市天河区体育西路',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        candidates = get_transfer_match_candidates('陕西咸阳彬州西大街', date.today() + timedelta(days=8), sku.id)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['target_province'], '陕西省')
        self.assertEqual(candidates[0]['target_city'], '咸阳市')

    def test_transfer_parse_short_text_for_non_coordinate_city(self):
        sku = SKU.objects.create(
            code='SKU-TX-SHORT2',
            name='转寄短地址解析测试2',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源单',
            customer_phone='13023000001',
            delivery_address='广东省广州市天河区体育西路',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        candidates = get_transfer_match_candidates('江苏苏州工业园区星湖街', date.today() + timedelta(days=8), sku.id)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['target_province'], '江苏省')
        self.assertEqual(candidates[0]['target_city'], '苏州市')

    def test_transfer_candidate_accepts_confirmed_source_status(self):
        sku = SKU.objects.create(
            code='SKU-TX-1C',
            name='转寄套餐1C',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        source_confirmed = Order.objects.create(
            customer_name='待发货来源',
            customer_phone='13030000001',
            delivery_address='广东省揭阳市榕城区',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_confirmed, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        target_date = date.today() + timedelta(days=8)
        candidates = get_transfer_match_candidates('广东揭阳榕城区东升路', target_date, sku.id)
        self.assertTrue(any(c['source_order'].id == source_confirmed.id for c in candidates))

    def test_transfer_candidate_accepts_pending_source_status(self):
        sku = SKU.objects.create(
            code='SKU-TX-1E',
            name='转寄套餐1E',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        source_pending = Order.objects.create(
            customer_name='待处理来源',
            customer_phone='13050000001',
            delivery_address='广东省揭阳市榕城区',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_pending, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        target_date = date.today() + timedelta(days=8)
        candidates = get_transfer_match_candidates('广东揭阳榕城区东升路', target_date, sku.id)
        self.assertTrue(any(c['source_order'].id == source_pending.id for c in candidates))

    def test_transfer_pool_row_generate_task_should_allow_only_when_target_delivered(self):
        sku = SKU.objects.create(
            code='SKU-TX-P1',
            name='候选池套餐',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=5,
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源候选',
            customer_phone='13051000001',
            delivery_address='广东省广州市天河区体育西路1号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标候选',
            customer_phone='13051000002',
            delivery_address='广东省广州市越秀区中山一路2号',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))
        OrderItem.objects.create(order=target, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        rows = build_transfer_pool_rows()
        row = next((r for r in rows if r['order'].id == target.id and r['item'].sku_id == sku.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row['current_source_type'], 'warehouse')
        self.assertEqual(row['recommended_source_type'], 'transfer')
        self.assertTrue(row['can_generate_task'])

        target.status = 'pending'
        target.save(update_fields=['status'])
        rows = build_transfer_pool_rows()
        row = next((r for r in rows if r['order'].id == target.id and r['item'].sku_id == sku.id), None)
        self.assertIsNotNone(row)
        self.assertFalse(row['can_generate_task'])

    def test_transfer_pool_row_should_mark_has_task_for_completed_transfer(self):
        sku = SKU.objects.create(
            code='SKU-TX-P2',
            name='候选池套餐2',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=5,
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源候选2',
            customer_phone='13052000001',
            delivery_address='广东省广州市天河区体育西路3号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标候选2',
            customer_phone='13052000002',
            delivery_address='广东省广州市越秀区中山一路3号',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))
        OrderItem.objects.create(order=target, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))
        Transfer.objects.create(
            order_from=source,
            order_to=target,
            sku=sku,
            quantity=1,
            gap_days=8,
            cost_saved=Decimal('100.00'),
            status='completed',
            created_by=self.user,
        )

        rows = build_transfer_pool_rows()
        row = next((r for r in rows if r['order'].id == target.id and r['item'].sku_id == sku.id), None)
        self.assertIsNotNone(row)
        self.assertTrue(row['has_pending_task'])
        self.assertFalse(row['can_generate_task'])

    def test_transfer_pool_row_should_show_current_source_for_delivered_target_with_consumed_allocation(self):
        sku = SKU.objects.create(
            code='SKU-TX-P3',
            name='候选池套餐3',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=5,
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源候选3',
            customer_phone='13053000001',
            delivery_address='广东省深圳市南山区科技园1号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标候选3',
            customer_phone='13053000002',
            delivery_address='广东省深圳市福田区车公庙2号',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))
        OrderItem.objects.create(order=target, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            distance_score=Decimal('1.0000'),
            status='consumed',
            created_by=self.user,
        )

        rows = build_transfer_pool_rows()
        row = next((r for r in rows if r['order'].id == target.id and r['item'].sku_id == sku.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row['current_source_type'], 'transfer')
        self.assertIn(source.order_no, row['current_source_text'])

    def test_transfer_candidate_rejects_exact_five_day_gap(self):
        sku = SKU.objects.create(
            code='SKU-TX-1D',
            name='转寄套餐1D',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=10,
            is_active=True,
        )
        target_date = date.today() + timedelta(days=8)
        source_exact_five_days = Order.objects.create(
            customer_name='5天差来源',
            customer_phone='13040000001',
            delivery_address='福建省泉州市丰泽区',
            event_date=target_date - timedelta(days=5),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_exact_five_days, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        candidates = get_transfer_match_candidates('福建省泉州晋江市', target_date, sku.id)
        self.assertFalse(any(c['source_order'].id == source_exact_five_days.id for c in candidates))

    def test_transfer_lock_prevents_duplicate_within_plus_minus_5_days(self):
        sku = SKU.objects.create(
            code='SKU-TX-2',
            name='转寄套餐2',
            category='主题套餐',
            rental_price=Decimal('90.00'),
            deposit=Decimal('30.00'),
            stock=0,  # 强制只能走转寄，便于验证锁机制
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源单',
            customer_phone='13100000000',
            delivery_address='广州市天河区体育西路1号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('90.00'))

        first_target_date = date.today() + timedelta(days=8)
        first = OrderService.create_order(
            data={
                'customer_name': '目标单1',
                'customer_phone': '13200000001',
                'delivery_address': '广州市天河区体育西路2号',
                'event_date': first_target_date,
                'rental_days': 1,
                'items': [{'sku_id': sku.id, 'quantity': 1}],
            },
            user=self.user,
        )
        self.assertTrue(TransferAllocation.objects.filter(target_order=first, status='locked').exists())

        # 第二单目标日期在 +/-5 天窗口内，且仓库库存为0，应被阻止（无法重复挂同来源）
        with self.assertRaises(ValueError):
            OrderService.create_order(
                data={
                    'customer_name': '目标单2',
                    'customer_phone': '13200000002',
                    'delivery_address': '广州市天河区体育西路3号',
                    'event_date': first_target_date + timedelta(days=2),
                    'rental_days': 1,
                    'items': [{'sku_id': sku.id, 'quantity': 1}],
                },
                user=self.user,
            )

    def test_transfer_preferred_source_order_is_prioritized(self):
        sku = SKU.objects.create(
            code='SKU-TX-3',
            name='转寄套餐3',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('30.00'),
            stock=0,
            is_active=True,
        )
        source_a = Order.objects.create(
            customer_name='来源A',
            customer_phone='13300000001',
            delivery_address='深圳市南山区科技园A',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        source_b = Order.objects.create(
            customer_name='来源B',
            customer_phone='13300000002',
            delivery_address='深圳市南山区科技园B',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_a, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('120.00'))
        OrderItem.objects.create(order=source_b, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('120.00'))

        plan = build_transfer_allocation_plan(
            delivery_address='深圳市南山区科技园C',
            target_event_date=date.today() + timedelta(days=8),
            sku_id=sku.id,
            quantity=1,
            preferred_source_order_id=source_b.id,
        )
        self.assertEqual(len(plan['allocations']), 1)
        self.assertEqual(plan['allocations'][0]['source_order_id'], source_b.id)

    def test_transfer_allocation_mark_consumed_when_order_delivered(self):
        sku = SKU.objects.create(
            code='SKU-TX-4',
            name='转寄套餐4',
            category='主题套餐',
            rental_price=Decimal('88.00'),
            deposit=Decimal('20.00'),
            stock=0,
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源单',
            customer_phone='13400000001',
            delivery_address='杭州市西湖区文三路1号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('88.00'))

        target = OrderService.create_order(
            data={
                'customer_name': '目标单',
                'customer_phone': '13400000002',
                'delivery_address': '杭州市西湖区文三路2号',
                'event_date': date.today() + timedelta(days=8),
                'rental_days': 1,
                'items': [{'sku_id': sku.id, 'quantity': 1}],
            },
            user=self.user,
        )
        OrderService.confirm_order(target.id, Decimal('20.00'), self.user)
        OrderService.mark_as_delivered(target.id, 'YT20260001', self.user)

        self.assertTrue(
            TransferAllocation.objects.filter(
                target_order=target,
                status='consumed'
            ).exists()
        )

    def test_update_order_rebuilds_transfer_allocation(self):
        sku = SKU.objects.create(
            code='SKU-TX-5',
            name='转寄套餐5',
            category='主题套餐',
            rental_price=Decimal('99.00'),
            deposit=Decimal('20.00'),
            stock=0,
            is_active=True,
        )
        source_a = Order.objects.create(
            customer_name='来源A',
            customer_phone='13600000001',
            delivery_address='成都市高新区天府大道100号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        source_b = Order.objects.create(
            customer_name='来源B',
            customer_phone='13600000002',
            delivery_address='成都市高新区天府大道200号',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_a, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('99.00'))
        OrderItem.objects.create(order=source_b, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('99.00'))

        order = OrderService.create_order(
            data={
                'customer_name': '编辑前',
                'customer_phone': '13600000003',
                'delivery_address': '成都市高新区天府大道300号',
                'event_date': date.today() + timedelta(days=8),
                'rental_days': 1,
                'items': [{'sku_id': sku.id, 'quantity': 1}],
            },
            user=self.user,
        )
        first_alloc = TransferAllocation.objects.filter(target_order=order, status='locked').first()
        self.assertIsNotNone(first_alloc)

        OrderService.update_order(
            order.id,
            {
                'customer_name': '编辑后',
                'customer_phone': '13600000003',
                'delivery_address': '成都市高新区天府大道300号',
                'event_date': date.today() + timedelta(days=8),
                'rental_days': 1,
                'items': [
                    {
                        'sku_id': sku.id,
                        'quantity': 1,
                        'transfer_source_order_id': source_b.id,
                    }
                ],
            },
            self.user
        )

        self.assertTrue(
            TransferAllocation.objects.filter(
                target_order=order,
                source_order=source_b,
                status='locked'
            ).exists()
        )
        self.assertTrue(
            TransferAllocation.objects.filter(
                id=first_alloc.id,
                status='released'
            ).exists()
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

    def test_order_mark_returned_should_block_for_transfer_source_order(self):
        source_order = Order.objects.create(
            customer_name='来源客户',
            customer_phone='13612345678',
            delivery_address='来源地址',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target_order = Order.objects.create(
            customer_name='目标客户',
            customer_phone='13699990000',
            delivery_address='目标地址',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=source_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('200.00'),
        )
        OrderItem.objects.create(
            order=target_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('200.00'),
        )
        TransferAllocation.objects.create(
            source_order=source_order,
            target_order=target_order,
            sku=self.sku,
            quantity=1,
            target_event_date=target_order.event_date,
            window_start=target_order.event_date - timedelta(days=5),
            window_end=target_order.event_date + timedelta(days=5),
            status='consumed',
            created_by=self.user,
        )

        self.client.post(
            reverse('order_mark_returned', kwargs={'order_id': source_order.id}),
            {'return_tracking': 'RET-BLOCK', 'balance_paid': '0'}
        )
        source_order.refresh_from_db()
        self.assertEqual(source_order.status, 'delivered')
        self.assertNotEqual(source_order.return_tracking, 'RET-BLOCK')

    def test_workbench_confirm_auto_deliver(self):
        order = Order.objects.create(
            customer_name='自动发货客户',
            customer_phone='13500000002',
            delivery_address='地址3',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='pending',
            total_amount=Decimal('200.00'),
            balance=Decimal('200.00'),
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('order_mark_confirmed', kwargs={'order_id': order.id}),
            {'deposit_paid': '80', 'ship_tracking': 'YT778899', 'auto_deliver': '1'}
        )
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertEqual(order.ship_tracking, 'YT778899')

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
        self.assertTrue(
            AuditLog.objects.filter(
                module='转寄',
                action='create',
                target=f'任务#{transfer.id}'
            ).exists()
        )

        self.client.post(
            reverse('transfer_complete', kwargs={'transfer_id': transfer.id}),
            {
                'tracking_no': 'YT-NEW-001',
            }
        )
        transfer.refresh_from_db()
        order_from.refresh_from_db()
        order_to.refresh_from_db()
        self.assertEqual(transfer.status, 'completed')
        self.assertEqual(order_to.status, 'delivered')
        self.assertEqual(order_to.ship_tracking, 'YT-NEW-001')
        self.assertEqual(order_from.status, 'completed')
        self.assertEqual(order_from.return_tracking, 'YT-NEW-001')

    def test_transfer_complete_should_require_tracking_number(self):
        order_from = Order.objects.create(
            customer_name='转寄回收2',
            customer_phone='13611112222',
            delivery_address='A2地址',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        order_to = Order.objects.create(
            customer_name='转寄发货2',
            customer_phone='13622223333',
            delivery_address='B2地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        transfer = Transfer.objects.create(
            order_from=order_from,
            order_to=order_to,
            sku=self.sku,
            quantity=1,
            gap_days=2,
            cost_saved=Decimal('100.00'),
            status='pending',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('transfer_complete', kwargs={'transfer_id': transfer.id}),
            {'tracking_no': ''},
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'pending')

    def test_transfer_recommend_should_skip_when_pending_task_exists(self):
        source = Order.objects.create(
            customer_name='来源',
            customer_phone='13600001111',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标',
            customer_phone='13600002222',
            delivery_address='广东省广州市越秀区',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        Transfer.objects.create(
            order_from=source,
            order_to=target,
            sku=self.sku,
            quantity=1,
            gap_days=7,
            cost_saved=Decimal('100.00'),
            status='pending',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('transfer_recommend'),
            {'rows[]': [f'{target.id}:{self.sku.id}']}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            TransferAllocation.objects.filter(
                target_order=target,
                sku=self.sku,
                status='locked'
            ).exists()
        )

    def test_transfer_recommend_should_only_update_allocation(self):
        source = Order.objects.create(
            customer_name='来源2',
            customer_phone='13600003333',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标2',
            customer_phone='13600004444',
            delivery_address='广东省广州市越秀区',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))

        resp = self.client.post(
            reverse('transfer_recommend'),
            {'rows[]': [f'{target.id}:{self.sku.id}']}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            TransferAllocation.objects.filter(
                source_order=source,
                target_order=target,
                sku=self.sku,
                status='locked'
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                module='转寄',
                action='update',
                target=target.order_no
            ).exists()
        )
        self.assertFalse(
            Transfer.objects.filter(
                order_to=target,
                sku=self.sku,
                status='pending'
            ).exists()
        )

    def test_transfer_generate_tasks_should_create_pending_task_for_transfer_allocation(self):
        source = Order.objects.create(
            customer_name='来源G',
            customer_phone='13600005555',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标G',
            customer_phone='13600006666',
            delivery_address='广东省广州市越秀区',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            distance_score=Decimal('1.0000'),
            status='locked',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('transfer_generate_tasks'),
            {'rows[]': [f'{target.id}:{self.sku.id}']}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Transfer.objects.filter(
                order_from=source,
                order_to=target,
                sku=self.sku,
                status='pending'
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                module='转寄',
                action='create',
                details__icontains='生成转寄任务'
            ).exists()
        )

    def test_transfer_generate_tasks_should_skip_when_current_is_warehouse(self):
        target = Order.objects.create(
            customer_name='目标W',
            customer_phone='13600007777',
            delivery_address='广东省佛山市南海区',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))

        resp = self.client.post(
            reverse('transfer_generate_tasks'),
            {'rows[]': [f'{target.id}:{self.sku.id}']}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            Transfer.objects.filter(
                order_to=target,
                sku=self.sku,
                status='pending'
            ).exists()
        )

    def test_transfer_generate_tasks_should_skip_when_target_not_delivered(self):
        source = Order.objects.create(
            customer_name='来源ND',
            customer_phone='13620000001',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标ND',
            customer_phone='13620000002',
            delivery_address='广东省广州市越秀区',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            distance_score=Decimal('1.0000'),
            status='locked',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('transfer_generate_tasks'),
            {'rows[]': [f'{target.id}:{self.sku.id}']},
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            Transfer.objects.filter(
                order_to=target,
                sku=self.sku,
                status='pending'
            ).exists()
        )

    def test_transfer_generate_tasks_should_skip_when_completed_task_exists(self):
        source = Order.objects.create(
            customer_name='来源CT',
            customer_phone='13630000001',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标CT',
            customer_phone='13630000002',
            delivery_address='广东省广州市越秀区',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            distance_score=Decimal('1.0000'),
            status='locked',
            created_by=self.user,
        )
        Transfer.objects.create(
            order_from=source,
            order_to=target,
            sku=self.sku,
            quantity=1,
            gap_days=8,
            cost_saved=Decimal('100.00'),
            status='completed',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('transfer_generate_tasks'),
            {'rows[]': [f'{target.id}:{self.sku.id}']},
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            Transfer.objects.filter(order_to=target, sku=self.sku).count(),
            1
        )

    def test_transfer_generate_tasks_should_switch_to_recommended_source_before_create_task(self):
        source_current = Order.objects.create(
            customer_name='当前来源',
            customer_phone='13610000001',
            delivery_address='广东省广州市天河区体育西路',
            event_date=date.today(),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        source_recommended = Order.objects.create(
            customer_name='推荐来源',
            customer_phone='13610000002',
            delivery_address='广东省广州市越秀区中山一路',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标R',
            customer_phone='13610000003',
            delivery_address='广东省广州市越秀区中山一路2号',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_current, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=source_recommended, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))

        TransferAllocation.objects.create(
            source_order=source_current,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            distance_score=Decimal('5.0000'),
            status='locked',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('transfer_generate_tasks'),
            {'rows[]': [f'{target.id}:{self.sku.id}']}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            TransferAllocation.objects.filter(
                source_order=source_recommended,
                target_order=target,
                sku=self.sku,
                status='locked'
            ).exists()
        )
        self.assertTrue(
            Transfer.objects.filter(
                order_from=source_recommended,
                order_to=target,
                sku=self.sku,
                status='pending'
            ).exists()
        )

    def test_api_check_duplicate_order_should_return_duplicates(self):
        existing = Order.objects.create(
            customer_name='重复客户',
            customer_phone='18800001111',
            delivery_address='广东省广州市天河区体育西路100号',
            event_date=date.today() + timedelta(days=10),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=existing,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('200.00'),
        )

        resp = self.client.get(
            reverse('api_check_duplicate_order'),
            {
                'customer_phone': '18800001111',
                'delivery_address': '广东省广州市天河区体育西路100号',
                'event_date': (date.today() + timedelta(days=10)).isoformat(),
                'sku_ids[]': [str(self.sku.id)],
            }
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['data']['has_duplicates'])
        self.assertEqual(payload['data']['duplicates'][0]['order_no'], existing.order_no)

    def test_api_check_duplicate_order_should_exclude_current_order(self):
        existing = Order.objects.create(
            customer_name='编辑客户',
            customer_phone='18800002222',
            delivery_address='福建省泉州市丰泽区刺桐路1号',
            event_date=date.today() + timedelta(days=11),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=existing,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('200.00'),
        )

        resp = self.client.get(
            reverse('api_check_duplicate_order'),
            {
                'customer_phone': '18800002222',
                'delivery_address': '福建省泉州市丰泽区刺桐路1号',
                'event_date': (date.today() + timedelta(days=11)).isoformat(),
                'sku_ids[]': [str(self.sku.id)],
                'exclude_order_id': str(existing.id),
            }
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertFalse(payload['data']['has_duplicates'])

    def test_dashboard_recent_orders_transfer_allocation_should_deduplicate_and_ignore_released(self):
        source = Order.objects.create(
            customer_name='来源D',
            customer_phone='18900000001',
            delivery_address='广东省深圳市南山区科技园',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标D',
            customer_phone='18900000002',
            delivery_address='广东省深圳市福田区车公庙',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            distance_score=Decimal('1.0000'),
            status='locked',
            created_by=self.user,
        )
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            distance_score=Decimal('1.0000'),
            status='released',
            created_by=self.user,
        )

        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        recent_orders = list(resp.context['recent_orders'])
        target_row = next((o for o in recent_orders if o.id == target.id), None)
        self.assertIsNotNone(target_row)
        self.assertEqual(len(target_row.transfer_allocations_display), 1)
        self.assertEqual(target_row.transfer_allocations_display[0]['order_no'], source.order_no)
        self.assertEqual(target_row.transfer_allocations_display[0]['quantity'], 1)

    def test_dashboard_should_render_role_risk_entries_and_quick_actions(self):
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        role_dashboard = resp.context['role_dashboard']
        self.assertTrue(any(item['url_name'] == 'order_create' for item in role_dashboard['quick_actions']))
        self.assertTrue(any(item['key'] == 'pending_orders' for item in role_dashboard['risk_entries']))
        self.assertTrue(any(item['key'] == 'low_stock_parts' and item['query'] == 'low=1' for item in role_dashboard['risk_entries']))

    def test_parts_inventory_low_filter_should_only_return_low_stock_parts(self):
        low_part = Part.objects.create(
            name='低库存部件',
            spec='L1',
            category='accessory',
            unit='个',
            current_stock=1,
            safety_stock=3,
            is_active=True,
        )
        normal_part = Part.objects.create(
            name='正常库存部件',
            spec='N1',
            category='accessory',
            unit='个',
            current_stock=5,
            safety_stock=3,
            is_active=True,
        )
        resp = self.client.get(reverse('parts_inventory_list') + '?low=1')
        self.assertEqual(resp.status_code, 200)
        ids = [part.id for part in resp.context['parts_page'].object_list]
        self.assertIn(low_part.id, ids)
        self.assertNotIn(normal_part.id, ids)

    def test_outbound_inventory_dashboard_should_expose_health_score(self):
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0001',
            status='in_transit',
            current_location_type='transit',
            is_active=True,
        )
        mv = UnitMovement.objects.create(
            unit=unit,
            event_type='TRANSFER_PENDING',
            status='warning',
            notes='测试健康分',
            operator=self.user,
        )
        UnitMovement.objects.filter(id=mv.id).update(event_time=timezone.now() - timedelta(days=8))

        resp = self.client.get(reverse('outbound_inventory_dashboard'))
        self.assertEqual(resp.status_code, 200)
        summary = resp.context['summary']
        self.assertIn('avg_outbound_health', summary)
        units = list(resp.context['units_page'].object_list)
        target = next((u for u in units if u.id == unit.id), None)
        self.assertIsNotNone(target)
        self.assertTrue(hasattr(target, 'health_score'))
        self.assertTrue(hasattr(target, 'health_level'))
        self.assertGreaterEqual(target.health_score, 0)
        self.assertLessEqual(target.health_score, 100)

    def test_outbound_inventory_export_should_contain_health_columns(self):
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0002',
            status='in_transit',
            current_location_type='transit',
            is_active=True,
        )
        UnitMovement.objects.create(
            unit=unit,
            event_type='TRANSFER_SHIPPED',
            status='normal',
            notes='导出健康字段',
            operator=self.user,
        )
        resp = self.client.get(reverse('outbound_inventory_export'))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8-sig')
        self.assertIn('健康分', content)
        self.assertIn('健康等级', content)
        self.assertIn(unit.unit_no, content)


class ActionPermissionGuardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='perm_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.manager = User.objects.create_user(
            username='perm_manager',
            password='test123',
            role='manager',
            is_superuser=False,
            is_staff=True,
        )
        self.warehouse_manager = User.objects.create_user(
            username='perm_wh_manager',
            password='test123',
            role='warehouse_manager',
            is_superuser=False,
            is_staff=True,
        )
        self.sku = SKU.objects.create(
            code='SKU-PERM-1',
            name='权限测试套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('50.00'),
            stock=5,
            is_active=True,
        )

    def test_manager_should_not_force_cancel_order(self):
        order = Order.objects.create(
            customer_name='待取消',
            customer_phone='13900000001',
            delivery_address='A',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='pending',
            created_by=self.admin,
        )
        self.client.login(username='perm_manager', password='test123')
        resp = self.client.post(reverse('order_cancel', kwargs={'order_id': order.id}), {'reason': 'test'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertContains(resp, 'order.force_cancel', status_code=200)

    def test_warehouse_manager_should_not_cancel_transfer_task(self):
        source = Order.objects.create(
            customer_name='来源单',
            customer_phone='13900000002',
            delivery_address='B',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.admin,
            ship_date=date.today() - timedelta(days=1),
            return_date=date.today() + timedelta(days=1),
        )
        target = Order.objects.create(
            customer_name='目标单',
            customer_phone='13900000003',
            delivery_address='C',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='confirmed',
            created_by=self.admin,
            ship_date=date.today() + timedelta(days=4),
            return_date=date.today() + timedelta(days=6),
        )
        transfer = Transfer.objects.create(
            order_from=source,
            order_to=target,
            sku=self.sku,
            quantity=1,
            gap_days=5,
            status='pending',
            created_by=self.admin,
        )
        self.client.login(username='perm_wh_manager', password='test123')
        resp = self.client.post(reverse('transfer_cancel', kwargs={'transfer_id': transfer.id}), follow=True)
        self.assertEqual(resp.status_code, 200)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'pending')
        self.assertContains(resp, 'transfer.cancel_task', status_code=200)


class AuditDiffLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='audit_diff_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.sku = SKU.objects.create(
            code='SKU-AUDIT-1',
            name='审计测试套餐',
            category='主题套餐',
            rental_price=Decimal('168.00'),
            deposit=Decimal('200.00'),
            stock=3,
            is_active=True,
        )
        self.client.login(username='audit_diff_admin', password='test123')

    def test_order_cancel_should_write_before_after_diff(self):
        order = Order.objects.create(
            customer_name='取消客户',
            customer_phone='13800000001',
            delivery_address='广东省深圳市南山区科技园',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='pending',
            notes='原备注',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price,
        )

        resp = self.client.post(
            reverse('order_cancel', kwargs={'order_id': order.id}),
            {'reason': '审计测试取消'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

        log = AuditLog.objects.filter(module='订单', target=order.order_no).order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertIn('before', payload)
        self.assertIn('after', payload)
        self.assertEqual(payload['before']['status'], 'pending')
        self.assertEqual(payload['after']['status'], 'cancelled')
        self.assertIn('status', payload.get('changed_fields', []))

    def test_transfer_cancel_should_write_before_after_diff(self):
        source = Order.objects.create(
            customer_name='来源',
            customer_phone='13800000002',
            delivery_address='A',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标',
            customer_phone='13800000003',
            delivery_address='B',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        transfer = Transfer.objects.create(
            order_from=source,
            order_to=target,
            sku=self.sku,
            quantity=1,
            gap_days=8,
            status='pending',
            created_by=self.user,
        )

        resp = self.client.post(reverse('transfer_cancel', kwargs={'transfer_id': transfer.id}), follow=True)
        self.assertEqual(resp.status_code, 200)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'cancelled')

        log = AuditLog.objects.filter(module='转寄', target=f'任务#{transfer.id}').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['before']['status'], 'pending')
        self.assertEqual(payload['after']['status'], 'cancelled')
        self.assertIn('status', payload.get('changed_fields', []))

    def test_settings_save_should_write_before_after_diff(self):
        SystemSettings.objects.update_or_create(key='ship_lead_days', defaults={'value': '2'})
        SystemSettings.objects.update_or_create(key='buffer_days', defaults={'value': '1'})

        resp = self.client.post(
            reverse('settings'),
            {
                'active_tab': 'order',
                'ship_lead_days': '3',
                'return_offset_days': '1',
                'buffer_days': '4',
                'max_transfer_gap_days': '5',
                'warehouse_sender_name': '仓库A',
                'warehouse_sender_phone': '18800000000',
                'warehouse_sender_address': '广东省深圳市南山区',
                'transfer_pending_timeout_hours': '24',
                'transfer_shipped_timeout_days': '3',
                'outbound_max_days_warn': '10',
                'outbound_max_hops_warn': '4',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        log = AuditLog.objects.filter(module='系统设置', target='settings').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['before']['ship_lead_days'], '2')
        self.assertEqual(payload['after']['ship_lead_days'], '3')
        self.assertIn('ship_lead_days', payload.get('changed_fields', []))

    def test_order_confirm_should_write_structured_diff(self):
        order = Order.objects.create(
            customer_name='确认客户',
            customer_phone='13800000009',
            delivery_address='广东省深圳市福田区',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price,
        )
        resp = self.client.post(
            reverse('order_mark_confirmed', kwargs={'order_id': order.id}),
            {'deposit_paid': '200.00'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'confirmed')
        log = AuditLog.objects.filter(module='订单', target=order.order_no).order_by('-created_at').first()
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '确认订单')
        self.assertEqual(payload['before']['status'], 'pending')
        self.assertEqual(payload['after']['status'], 'confirmed')

    def test_init_units_should_write_audit_log(self):
        InventoryUnit.objects.all().delete()
        resp = self.client.get(reverse('outbound_inventory_dashboard') + '?init_units=1', follow=True)
        self.assertEqual(resp.status_code, 200)
        log = AuditLog.objects.filter(module='在外库存', target='inventory_units.bootstrap').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '初始化单套库存编号')
        self.assertIn('created_units', payload.get('after', {}))


class AuditLogExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='audit_export_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.login(username='audit_export_admin', password='test123')

    def test_audit_logs_export_should_return_filtered_csv(self):
        AuditLog.objects.create(
            user=self.user,
            action='update',
            module='订单',
            target='ORD-EXPORT-1',
            details=json.dumps(
                {
                    'summary': '修改订单信息',
                    'before': {'status': 'pending'},
                    'after': {'status': 'confirmed'},
                    'changed_fields': ['status'],
                    'extra': {},
                },
                ensure_ascii=False,
            ),
            ip_address=None,
        )
        AuditLog.objects.create(
            user=self.user,
            action='create',
            module='采购',
            target='PO-EXPORT-1',
            details='创建采购单',
            ip_address=None,
        )

        resp = self.client.get(
            reverse('audit_logs'),
            {'module': '订单', 'export': '1'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        self.assertIn('attachment; filename=\"audit_logs.csv\"', resp['Content-Disposition'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('ORD-EXPORT-1', content)
        self.assertIn('修改订单信息', content)
        self.assertNotIn('PO-EXPORT-1', content)
        self.assertIn(',app,', content)
        self.assertIn('Before(JSON)', content)
        self.assertIn('After(JSON)', content)
        self.assertIn('Extra(JSON)', content)

    def test_audit_logs_export_changed_only_should_exclude_no_change_and_plain_text(self):
        AuditLog.objects.create(
            user=self.user,
            action='update',
            module='订单',
            target='ORD-CHANGED-1',
            details=json.dumps(
                {
                    'summary': '有变更',
                    'before': {'status': 'pending'},
                    'after': {'status': 'confirmed'},
                    'changed_fields': ['status'],
                    'extra': {},
                },
                ensure_ascii=False,
            ),
            ip_address=None,
        )
        AuditLog.objects.create(
            user=self.user,
            action='update',
            module='订单',
            target='ORD-NOCHANGE-1',
            details=json.dumps(
                {
                    'summary': '无变更',
                    'before': {'status': 'pending'},
                    'after': {'status': 'pending'},
                    'changed_fields': [],
                    'extra': {},
                },
                ensure_ascii=False,
            ),
            ip_address=None,
        )
        AuditLog.objects.create(
            user=self.user,
            action='create',
            module='订单',
            target='ORD-PLAIN-1',
            details='纯文本日志',
            ip_address=None,
        )

        resp = self.client.get(
            reverse('audit_logs'),
            {'module': '订单', 'structured_only': '1', 'changed_only': '1', 'export': '1'},
        )
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8-sig')
        self.assertIn('ORD-CHANGED-1', content)
        self.assertNotIn('ORD-NOCHANGE-1', content)
        self.assertNotIn('ORD-PLAIN-1', content)

    def test_audit_logs_view_risk_only_and_changed_sort_should_work(self):
        AuditLog.objects.create(
            user=self.user,
            action='status_change',
            module='订单',
            target='RISK-A',
            details=json.dumps(
                {
                    'summary': '状态变更A',
                    'before': {'status': 'pending'},
                    'after': {'status': 'confirmed'},
                    'changed_fields': ['status'],
                    'extra': {},
                },
                ensure_ascii=False,
            ),
            ip_address=None,
        )
        AuditLog.objects.create(
            user=self.user,
            action='status_change',
            module='订单',
            target='RISK-B',
            details=json.dumps(
                {
                    'summary': '状态变更B',
                    'before': {'a': 1, 'b': 1},
                    'after': {'a': 2, 'b': 3},
                    'changed_fields': ['a', 'b'],
                    'extra': {},
                },
                ensure_ascii=False,
            ),
            ip_address=None,
        )
        AuditLog.objects.create(
            user=self.user,
            action='create',
            module='订单',
            target='NON-RISK',
            details='创建日志',
            ip_address=None,
        )

        resp = self.client.get(
            reverse('audit_logs'),
            {
                'risk_only': '1',
                'structured_only': '1',
                'changed_only': '1',
                'sort_by': 'changed_desc',
            },
        )
        self.assertEqual(resp.status_code, 200)
        logs = list(resp.context['logs_page'].object_list)
        self.assertGreaterEqual(len(logs), 2)
        targets = [item.target for item in logs]
        self.assertIn('RISK-A', targets)
        self.assertIn('RISK-B', targets)
        self.assertNotIn('NON-RISK', targets)
        self.assertEqual(logs[0].target, 'RISK-B')

    def test_audit_logs_source_filter_should_work(self):
        AuditLog.objects.create(
            user=self.user,
            action='create',
            module='转寄',
            target='SRC-API-1',
            details=json.dumps(
                {
                    'summary': 'API日志',
                    'before': {},
                    'after': {'ok': True},
                    'changed_fields': ['ok'],
                    'extra': {'source': 'api'},
                },
                ensure_ascii=False,
            ),
            ip_address=None,
        )
        AuditLog.objects.create(
            user=self.user,
            action='update',
            module='订单',
            target='SRC-MW-1',
            details=json.dumps(
                {
                    'summary': '中间件日志',
                    'before': {},
                    'after': {},
                    'changed_fields': [],
                    'extra': {'source': 'middleware'},
                },
                ensure_ascii=False,
            ),
            ip_address=None,
        )

        resp = self.client.get(reverse('audit_logs'), {'source': 'api'})
        self.assertEqual(resp.status_code, 200)
        logs = list(resp.context['logs_page'].object_list)
        targets = [item.target for item in logs]
        self.assertIn('SRC-API-1', targets)
        self.assertNotIn('SRC-MW-1', targets)


class AuditMiddlewareStructuredTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='audit_mw_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.factory = RequestFactory()
        self.middleware = AuditLogMiddleware(lambda request: HttpResponse('ok'))

    def test_middleware_should_write_structured_audit_details(self):
        request = self.factory.post('/orders/create/', {'customer_name': 'A'})
        request.user = self.user
        response = HttpResponse('ok', status=200)

        self.middleware.process_response(request, response)

        log = AuditLog.objects.filter(module='订单', target='新订单').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload.get('summary'), '中间件记录请求操作')
        self.assertEqual(payload.get('extra', {}).get('source'), 'middleware')
        self.assertEqual(payload.get('extra', {}).get('path'), '/orders/create/')
        self.assertEqual(payload.get('extra', {}).get('status_code'), 200)


class ProcurementAuditDiffTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='proc_audit_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.part = Part.objects.create(
            name='审计部件',
            spec='A1',
            category='accessory',
            unit='个',
            current_stock=10,
            safety_stock=2,
            is_active=True,
        )

    def test_mark_as_ordered_should_write_structured_diff(self):
        po = PurchaseOrder.objects.create(
            channel='online',
            supplier='审计供应商',
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
            unit_price=Decimal('10.00'),
            subtotal=Decimal('20.00'),
        )
        ProcurementService.mark_as_ordered(po.id, self.user)
        po.refresh_from_db()
        self.assertEqual(po.status, 'ordered')
        log = AuditLog.objects.filter(module='采购', target=po.po_no).order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '采购单标记已下单')
        self.assertEqual(payload['before']['status'], 'draft')
        self.assertEqual(payload['after']['status'], 'ordered')
        self.assertIn('status', payload.get('changed_fields', []))

    def test_parts_outbound_should_write_structured_diff(self):
        PartsService.outbound(self.part.id, 3, 'DOC-AUDIT-OUT', '审计测试出库', self.user)
        self.part.refresh_from_db()
        self.assertEqual(self.part.current_stock, 7)
        log = AuditLog.objects.filter(module='部件', target=self.part.name).order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '部件出库')
        self.assertEqual(payload['before']['current_stock'], 10)
        self.assertEqual(payload['after']['current_stock'], 7)
        self.assertIn('current_stock', payload.get('changed_fields', []))


class AuditCoverageGuardTests(TestCase):
    def test_production_code_should_not_directly_write_auditlog_objects_create(self):
        """防回退：生产代码中禁止绕过 AuditService 直接写 AuditLog.objects.create。"""
        project_root = Path(__file__).resolve().parents[2]
        scan_targets = [
            project_root / 'apps' / 'core',
            project_root / 'apps' / 'api',
        ]
        allow_files = {
            str(project_root / 'apps' / 'core' / 'services' / 'audit_service.py'),
            str(project_root / 'apps' / 'core' / 'tests.py'),
            str(project_root / 'apps' / 'api' / 'tests.py'),
        }

        violations = []
        for base in scan_targets:
            for file_path in base.rglob('*.py'):
                if 'migrations' in file_path.parts:
                    continue
                if 'views_backup.py' in file_path.name or 'views_v2.py' in file_path.name:
                    continue
                file_str = str(file_path)
                if file_str in allow_files:
                    continue
                content = file_path.read_text(encoding='utf-8')
                if 'AuditLog.objects.create(' in content:
                    violations.append(file_str)

        self.assertEqual(
            violations,
            [],
            msg='发现直接 AuditLog.objects.create 调用，请统一改为 AuditService：\n' + '\n'.join(violations),
        )
