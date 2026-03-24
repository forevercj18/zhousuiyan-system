from datetime import date, timedelta
from decimal import Decimal
import json
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import Sum
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone

from .middleware import AuditLogMiddleware
from .models import (
    AuditLog,
    InventoryUnit,
    InventoryUnitPart,
    UnitMovement,
    Order,
    OrderItem,
    Part,
    SKUComponent,
    PartsMovement,
    PurchaseOrder,
    PurchaseOrderItem,
    SKU,
    SystemSettings,
    Transfer,
    TransferAllocation,
    FinanceTransaction,
    RiskEvent,
    ApprovalTask,
    DataConsistencyCheckRun,
    TransferRecommendationLog,
    AssemblyOrder,
    AssemblyOrderItem,
    MaintenanceWorkOrder,
    MaintenanceWorkOrderItem,
    UnitDisposalOrder,
    UnitDisposalOrderItem,
    PartRecoveryInspection,
    PermissionTemplate,
    Reservation,
    WechatCustomer,
    WechatStaffBinding,
    SKUImage,
)
from .services import AuditService
from .services import OrderService, PartsService, ProcurementService
from .utils import (
    get_transfer_match_candidates,
    build_transfer_allocation_plan,
    build_transfer_pool_rows,
    check_sku_availability,
    get_dashboard_stats_payload,
    run_data_consistency_checks,
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
                'customer_wechat': 'zhangsan_wechat',
                'xianyu_order_no': 'xy123456',
                'delivery_address': '测试地址',
                'event_date': event_date,
                'rental_days': 1,
                'items': [{'sku_id': self.sku.id, 'quantity': 1}],
            },
            user=self.user,
        )

        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.customer_wechat, 'zhangsan_wechat')
        self.assertEqual(order.xianyu_order_no, 'xy123456')
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.total_amount, Decimal('100.00'))
        self.assertTrue(AuditLog.objects.filter(target=order.order_no, action='create').exists())

    def test_create_order_should_record_return_service_fields_and_finance(self):
        event_date = date.today() + timedelta(days=7)
        order = OrderService.create_order(
            data={
                'customer_name': '包回邮客户',
                'customer_phone': '13800009999',
                'customer_wechat': 'wx_return_service',
                'order_source': 'xiaohongshu',
                'source_order_no': 'xh-order-1001',
                'delivery_address': '测试地址-包回邮',
                'event_date': event_date,
                'rental_days': 1,
                'return_service_type': 'platform_return_included',
                'return_service_fee': '45.00',
                'return_service_payment_status': 'paid',
                'return_service_payment_channel': 'xiaohongshu',
                'return_service_payment_reference': 'xh-pay-001',
                'return_pickup_status': 'pending_schedule',
                'items': [{'sku_id': self.sku.id, 'quantity': 1}],
            },
            user=self.user,
        )

        self.assertEqual(order.order_source, 'xiaohongshu')
        self.assertEqual(order.source_order_no, 'xh-order-1001')
        self.assertEqual(order.return_service_type, 'platform_return_included')
        self.assertEqual(order.return_service_fee, Decimal('45.00'))
        self.assertEqual(order.return_service_payment_status, 'paid')
        self.assertEqual(order.return_service_payment_channel, 'xiaohongshu')
        self.assertEqual(order.return_service_payment_reference, 'xh-pay-001')
        self.assertEqual(order.return_pickup_status, 'pending_schedule')
        self.assertTrue(
            FinanceTransaction.objects.filter(
                order=order,
                transaction_type='return_service_received',
                amount=Decimal('45.00'),
                reference_no='xh-pay-001',
            ).exists()
        )

    def test_confirm_order_should_create_deposit_finance_transaction(self):
        order = Order.objects.create(
            customer_name='财务客户A',
            customer_phone='13812340001',
            delivery_address='财务地址A',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='pending',
            total_amount=Decimal('100.00'),
            balance=Decimal('100.00'),
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('100.00'),
        )

        OrderService.confirm_order(order.id, Decimal('50.00'), self.user)

        tx = FinanceTransaction.objects.filter(order=order).order_by('-id').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.transaction_type, 'deposit_received')
        self.assertEqual(tx.amount, Decimal('50.00'))

    def test_mark_returned_should_create_balance_finance_transaction(self):
        order = Order.objects.create(
            customer_name='财务客户B',
            customer_phone='13812340002',
            delivery_address='财务地址B',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='delivered',
            total_amount=Decimal('100.00'),
            balance=Decimal('100.00'),
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('100.00'),
        )

        OrderService.mark_as_returned(order.id, 'RET-FIN-001', Decimal('80.00'), self.user)

        tx = FinanceTransaction.objects.filter(order=order).order_by('-id').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.transaction_type, 'balance_received')
        self.assertEqual(tx.amount, Decimal('80.00'))

    def test_complete_order_should_create_deposit_refund_finance_transaction(self):
        order = Order.objects.create(
            customer_name='财务客户C',
            customer_phone='13812340003',
            delivery_address='财务地址C',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='returned',
            total_amount=Decimal('100.00'),
            deposit_paid=Decimal('50.00'),
            balance=Decimal('0.00'),
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('100.00'),
        )

        OrderService.complete_order(order.id, self.user)

        tx = FinanceTransaction.objects.filter(order=order).order_by('-id').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.transaction_type, 'deposit_refund')
        self.assertEqual(tx.amount, Decimal('50.00'))

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

    def test_effective_stock_should_prefer_active_units_over_legacy_stock_field(self):
        self.sku.stock = 99
        self.sku.save(update_fields=['stock'])
        for idx in range(3):
            InventoryUnit.objects.create(
                sku=self.sku,
                unit_no=f'ZSY-EFFECTIVE-{idx + 1:04d}',
                status='in_warehouse',
                current_location_type='warehouse',
                is_active=True,
            )
        InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-EFFECTIVE-SCRAP-0001',
            status='scrapped',
            current_location_type='warehouse',
            is_active=True,
        )

        self.sku.refresh_from_db()
        self.assertEqual(self.sku.effective_stock, 3)
        result = check_sku_availability(self.sku.id, 1)
        self.assertEqual(result['current_stock'], 3)
        self.assertEqual(result['available_count'], 3)

    def test_dashboard_stats_should_use_effective_stock_instead_of_raw_legacy_stock_field(self):
        self.sku.stock = 99
        self.sku.save(update_fields=['stock'])
        InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-DASH-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-DASH-0002',
            status='scrapped',
            current_location_type='warehouse',
            is_active=True,
        )
        payload = get_dashboard_stats_payload()
        self.assertEqual(payload['warehouse_available_stock'], 1)

    def test_dashboard_stats_should_fallback_to_legacy_stock_when_units_not_initialized(self):
        InventoryUnit.objects.filter(sku=self.sku).delete()
        self.sku.stock = 3
        self.sku.save(update_fields=['stock'])

        payload = get_dashboard_stats_payload()
        self.assertEqual(payload['warehouse_available_stock'], 3)

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
        self.assertEqual(row['task_status'], 'completed')
        self.assertEqual(row['can_recommend_reason'], '已存在已完成转寄任务，不可重推')
        self.assertEqual(row['can_generate_reason'], '已存在已完成转寄任务')
        self.assertFalse(row['can_generate_task'])

    def test_transfer_pool_row_should_allow_generate_again_when_task_cancelled(self):
        sku = SKU.objects.create(
            code='SKU-TX-P2-CANCEL',
            name='候选池套餐2取消',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=5,
            is_active=True
        )
        source = Order.objects.create(
            customer_name='来源候选2取消',
            customer_phone='13052000011',
            delivery_address='广东省广州市天河区体育西路31号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标候选2取消',
            customer_phone='13052000012',
            delivery_address='广东省广州市越秀区中山一路31号',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=source,
            sku=sku,
            quantity=1,
            rental_price=sku.rental_price,
            deposit=sku.deposit,
            subtotal=Decimal('80.00'),
        )
        OrderItem.objects.create(
            order=target,
            sku=sku,
            quantity=1,
            rental_price=sku.rental_price,
            deposit=sku.deposit,
            subtotal=Decimal('80.00'),
        )
        Transfer.objects.create(
            order_from=source,
            order_to=target,
            sku=sku,
            quantity=1,
            gap_days=8,
            cost_saved=Decimal('100.00'),
            status='cancelled',
            created_by=self.user,
        )

        rows = build_transfer_pool_rows()
        row = next((r for r in rows if r['order'].id == target.id and r['item'].sku_id == sku.id), None)
        self.assertIsNotNone(row)
        self.assertFalse(row['has_pending_task'])
        self.assertEqual(row['task_status'], 'cancelled')
        self.assertTrue(row['can_recommend'])
        self.assertEqual(row['can_recommend_reason'], '')
        self.assertTrue(row['can_generate_task'])
        self.assertEqual(row['can_generate_reason'], '')

    def test_transfer_pool_row_should_expose_generate_reason_when_target_not_delivered(self):
        sku = SKU.objects.create(
            code='SKU-TX-P4',
            name='候选池套餐4',
            category='主题套餐',
            rental_price=Decimal('80.00'),
            deposit=Decimal('20.00'),
            stock=5,
            is_active=True,
        )
        source = Order.objects.create(
            customer_name='来源候选4',
            customer_phone='13054000001',
            delivery_address='广东省广州市天河区体育西路4号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标候选4',
            customer_phone='13054000002',
            delivery_address='广东省广州市越秀区中山一路4号',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))
        OrderItem.objects.create(order=target, sku=sku, quantity=1, rental_price=sku.rental_price, deposit=sku.deposit, subtotal=Decimal('80.00'))

        rows = build_transfer_pool_rows()
        row = next((r for r in rows if r['order'].id == target.id and r['item'].sku_id == sku.id), None)
        self.assertIsNotNone(row)
        self.assertFalse(row['can_generate_task'])
        self.assertEqual(row['can_generate_reason'], '目标订单未发货，暂不可生成任务')

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
                'customer_wechat': 'before_wechat',
                'xianyu_order_no': 'xy-before',
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
                'customer_wechat': 'after_wechat',
                'xianyu_order_no': 'xy-after',
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
        order.refresh_from_db()
        self.assertEqual(order.customer_wechat, 'after_wechat')
        self.assertEqual(order.xianyu_order_no, 'xy-after')
        self.assertEqual(order.return_service_payment_status, 'unpaid')
        self.assertTrue(
            TransferAllocation.objects.filter(
                id=first_alloc.id,
                status='released'
            ).exists()
        )

    def test_update_order_should_create_return_service_refund_transaction(self):
        order = OrderService.create_order(
            data={
                'customer_name': '服务费编辑前',
                'customer_phone': '13600008888',
                'customer_wechat': 'edit-return-service',
                'delivery_address': '杭州市西湖区测试路1号',
                'event_date': date.today() + timedelta(days=8),
                'rental_days': 1,
                'order_source': 'wechat',
                'return_service_type': 'platform_return_included',
                'return_service_fee': '45.00',
                'return_service_payment_status': 'paid',
                'return_service_payment_channel': 'wechat',
                'return_service_payment_reference': 'wx-in-001',
                'return_pickup_status': 'pending_schedule',
                'items': [{'sku_id': self.sku.id, 'quantity': 1}],
            },
            user=self.user,
        )

        OrderService.update_order(
            order.id,
            {
                'customer_name': '服务费编辑后',
                'customer_phone': '13600008888',
                'customer_wechat': 'edit-return-service',
                'delivery_address': '杭州市西湖区测试路1号',
                'event_date': date.today() + timedelta(days=8),
                'rental_days': 1,
                'order_source': 'wechat',
                'return_service_type': 'platform_return_included',
                'return_service_fee': '45.00',
                'return_service_payment_status': 'refunded',
                'return_service_payment_channel': 'wechat',
                'return_service_payment_reference': 'wx-out-001',
                'return_pickup_status': 'cancelled',
                'items': [{'sku_id': self.sku.id, 'quantity': 1}],
            },
            self.user,
        )

        order.refresh_from_db()
        self.assertEqual(order.return_service_payment_status, 'refunded')
        self.assertEqual(order.return_pickup_status, 'cancelled')
        self.assertTrue(
            FinanceTransaction.objects.filter(
                order=order,
                transaction_type='return_service_refund',
                amount=Decimal('45.00'),
                reference_no='wx-out-001',
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

    def test_workbench_mark_delivered_should_require_tracking_number(self):
        order = Order.objects.create(
            customer_name='发货必填客户',
            customer_phone='13500000010',
            delivery_address='地址10',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('order_mark_delivered', kwargs={'order_id': order.id}),
            {'ship_tracking': ''}
        )
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'confirmed')

    def test_order_mark_returned_should_require_return_tracking_number(self):
        order = Order.objects.create(
            customer_name='归还必填客户',
            customer_phone='13500000011',
            delivery_address='地址11',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('order_mark_returned', kwargs={'order_id': order.id}),
            {'return_tracking': '', 'balance_paid': '0'}
        )
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')

    def test_order_detail_should_include_finance_transactions(self):
        order = Order.objects.create(
            customer_name='详情财务',
            customer_phone='13500009999',
            delivery_address='详情地址',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='pending',
            total_amount=Decimal('200.00'),
            balance=Decimal('200.00'),
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('200.00'),
        )
        OrderService.confirm_order(order.id, Decimal('80.00'), self.user)
        resp = self.client.get(reverse('order_detail', kwargs={'order_id': order.id}))
        self.assertEqual(resp.status_code, 200)
        txs = list(resp.context['finance_transactions'])
        self.assertGreaterEqual(len(txs), 1)
        self.assertEqual(txs[0].transaction_type, 'deposit_received')
        self.assertEqual(resp.context['expected_deposit_total'], Decimal('80.00'))

    def test_order_detail_should_render_return_service_update_form_for_delivered_order(self):
        order = Order.objects.create(
            customer_name='回邮详情客户',
            customer_phone='13500008888',
            delivery_address='回邮详情地址',
            event_date=date.today() + timedelta(days=5),
            ship_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )

        resp = self.client.get(reverse('order_detail', kwargs={'order_id': order.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '保存包回邮服务')
        self.assertContains(resp, '支持订单已发货后补录包回邮服务，系统会按收款状态自动生成包回邮服务费收入/退款流水。')

    def test_order_return_service_update_should_support_post_delivery_purchase(self):
        order = Order.objects.create(
            customer_name='补买包回邮客户',
            customer_phone='13500007777',
            delivery_address='补买包回邮地址',
            event_date=date.today() + timedelta(days=4),
            ship_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )

        resp = self.client.post(
            reverse('order_return_service_update', kwargs={'order_id': order.id}),
            {
                'return_service_type': 'platform_return_included',
                'return_service_fee': '45.00',
                'return_service_payment_status': 'paid',
                'return_service_payment_channel': 'wechat',
                'return_service_payment_reference': 'wx-after-delivery-001',
                'return_pickup_status': 'pending_schedule',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.return_service_type, 'platform_return_included')
        self.assertEqual(order.return_service_fee, Decimal('45.00'))
        self.assertEqual(order.return_service_payment_status, 'paid')
        self.assertEqual(order.return_service_payment_channel, 'wechat')
        self.assertEqual(order.return_service_payment_reference, 'wx-after-delivery-001')
        self.assertEqual(order.return_pickup_status, 'pending_schedule')
        self.assertTrue(
            FinanceTransaction.objects.filter(
                order=order,
                transaction_type='return_service_received',
                amount=Decimal('45.00'),
                reference_no='wx-after-delivery-001',
            ).exists()
        )

    def test_orders_list_should_use_configured_default_page_size(self):
        SystemSettings.objects.update_or_create(key='page_size_default', defaults={'value': '2'})
        for idx in range(3):
            order = Order.objects.create(
                customer_name=f'分页客户{idx}',
                customer_phone=f'1390000000{idx}',
                delivery_address='分页地址',
                event_date=date.today() + timedelta(days=idx + 1),
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

        resp = self.client.get(reverse('orders_list'))
        self.assertEqual(resp.status_code, 200)
        page = resp.context['orders_page']
        self.assertEqual(page.paginator.per_page, 2)
        self.assertEqual(len(page.object_list), 2)

    def test_orders_list_should_compute_shipping_timeliness_and_sort_by_risk(self):
        today = timezone.localdate()
        overdue = Order.objects.create(
            customer_name='超时单',
            customer_phone='13910000001',
            delivery_address='A地址',
            event_date=today + timedelta(days=1),
            ship_date=today - timedelta(days=1),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        warning = Order.objects.create(
            customer_name='预警单',
            customer_phone='13910000002',
            delivery_address='B地址',
            event_date=today + timedelta(days=6),
            ship_date=today + timedelta(days=3),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        normal = Order.objects.create(
            customer_name='正常单',
            customer_phone='13910000003',
            delivery_address='C地址',
            event_date=today + timedelta(days=15),
            ship_date=today + timedelta(days=10),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        shipped = Order.objects.create(
            customer_name='已发货单',
            customer_phone='13910000004',
            delivery_address='D地址',
            event_date=today - timedelta(days=1),
            ship_date=today - timedelta(days=3),
            rental_days=1,
            status='delivered',
            ship_tracking='SF123456',
            created_by=self.user,
        )
        for o in [overdue, warning, normal, shipped]:
            OrderItem.objects.create(
                order=o,
                sku=self.sku,
                quantity=1,
                rental_price=self.sku.rental_price,
                deposit=self.sku.deposit,
                subtotal=self.sku.rental_price,
            )

        resp = self.client.get(reverse('orders_list'))
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context['orders_page'].object_list)
        order_codes = [r.shipping_timeliness_code for r in rows[:4]]
        self.assertEqual(order_codes, ['overdue', 'warning', 'normal', 'shipped'])
        self.assertEqual(rows[0].shipping_timeliness_label, '🔴 已超时，请尽快发货')
        self.assertEqual(rows[1].shipping_timeliness_label, '🟠 即将超时（7天内）')
        self.assertEqual(rows[2].shipping_timeliness_label, '🟢 正常时效')
        self.assertEqual(rows[3].shipping_timeliness_label, '🔵 已发货')
        self.assertEqual(rows[3].shipping_remaining_days, 0)

    def test_orders_list_should_support_sla_filter(self):
        today = timezone.localdate()
        overdue = Order.objects.create(
            customer_name='超时筛选',
            customer_phone='13920000001',
            delivery_address='A地址',
            event_date=today + timedelta(days=1),
            ship_date=today - timedelta(days=2),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        shipped = Order.objects.create(
            customer_name='发货筛选',
            customer_phone='13920000002',
            delivery_address='B地址',
            event_date=today - timedelta(days=1),
            ship_date=today - timedelta(days=3),
            rental_days=1,
            status='delivered',
            ship_tracking='YT123456',
            created_by=self.user,
        )
        for o in [overdue, shipped]:
            OrderItem.objects.create(
                order=o,
                sku=self.sku,
                quantity=1,
                rental_price=self.sku.rental_price,
                deposit=self.sku.deposit,
                subtotal=self.sku.rental_price,
            )

        resp = self.client.get(reverse('orders_list') + '?sla=overdue')
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context['orders_page'].object_list)
        self.assertTrue(rows)
        self.assertTrue(all(r.shipping_timeliness_code == 'overdue' for r in rows))

    def test_reservation_create_should_record_finance_and_audit(self):
        resp = self.client.post(reverse('reservation_create'), {
            'customer_wechat': 'wx_rsv_001',
            'customer_name': '预定客户',
            'customer_phone': '',
            'city': '上海',
            'sku_id': str(self.sku.id),
            'quantity': '1',
            'event_date': (timezone.localdate() + timedelta(days=10)).strftime('%Y-%m-%d'),
            'deposit_amount': '50.00',
            'status': 'pending_info',
            'notes': '先收50订金',
        })
        self.assertEqual(resp.status_code, 302)
        reservation = Reservation.objects.get(customer_wechat='wx_rsv_001')
        self.assertEqual(reservation.deposit_amount, Decimal('50.00'))
        self.assertEqual(reservation.owner_id, self.user.id)
        self.assertTrue(
            FinanceTransaction.objects.filter(
                reservation=reservation,
                transaction_type='reservation_deposit_received',
                amount=Decimal('50.00'),
            ).exists()
        )
        self.assertTrue(AuditLog.objects.filter(module='预定单', target=reservation.reservation_no, action='create').exists())

    def test_reservation_refund_should_zero_out_deposit_and_create_finance_record(self):
        reservation = Reservation.objects.create(
            customer_wechat='wx_refund_001',
            customer_name='退款客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=8),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=self.user,
        )
        resp = self.client.post(reverse('reservation_refund', kwargs={'reservation_id': reservation.id}), {'reason': '客户取消'})
        self.assertEqual(resp.status_code, 302)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'refunded')
        self.assertEqual(reservation.deposit_amount, Decimal('0.00'))
        self.assertTrue(
            FinanceTransaction.objects.filter(
                reservation=reservation,
                transaction_type='reservation_deposit_refund',
                amount=Decimal('50.00'),
            ).exists()
        )

    def test_convert_reservation_to_order_should_apply_deposit_and_mark_converted(self):
        reservation = Reservation.objects.create(
            customer_wechat='wx_convert_001',
            customer_name='转单客户',
            customer_phone='13800138000',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=9),
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            created_by=self.user,
        )
        resp = self.client.post(reverse('order_create'), {
            'reservation_id': str(reservation.id),
            'customer_name': reservation.customer_name,
            'customer_phone': reservation.customer_phone,
            'customer_wechat': reservation.customer_wechat,
            'xianyu_order_no': '',
            'customer_email': '',
            'delivery_address': '上海市浦东新区测试路1号',
            'return_address': '',
            'event_date': reservation.event_date.strftime('%Y-%m-%d'),
            'rental_days': '1',
            'notes': '由预定单转入',
            'sku_id[]': str(self.sku.id),
            'quantity[]': '1',
            'transfer_source_order_id[]': '',
        })
        self.assertEqual(resp.status_code, 302)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'converted')
        self.assertIsNotNone(reservation.converted_order_id)
        order = reservation.converted_order
        self.assertEqual(order.deposit_paid, Decimal('50.00'))
        self.assertTrue(
            FinanceTransaction.objects.filter(
                order=order,
                transaction_type='reservation_deposit_applied',
                amount=Decimal('50.00'),
                reference_no=reservation.reservation_no,
            ).exists()
        )

    def test_finance_transactions_list_should_render_reservation_records(self):
        reservation = Reservation.objects.create(
            customer_wechat='wx_fin_001',
            customer_name='财务预定客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=5),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=self.user,
        )
        FinanceTransaction.objects.create(
            reservation=reservation,
            transaction_type='reservation_deposit_received',
            amount=Decimal('50.00'),
            created_by=self.user,
        )
        resp = self.client.get(reverse('finance_transactions_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reservation.reservation_no)
        self.assertContains(resp, '收预定订金')

    def test_finance_transactions_list_should_support_return_service_keyword_search(self):
        order = Order.objects.create(
            customer_name='包回邮财务客户',
            customer_phone='18800009999',
            delivery_address='深圳市南山区科技园',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            order_source='xiaohongshu',
            source_order_no='xh-order-search-001',
            return_service_type='platform_return_included',
            return_service_fee=Decimal('45.00'),
            return_service_payment_status='paid',
            return_service_payment_channel='xiaohongshu',
            return_service_payment_reference='xh-ref-001',
            return_pickup_status='pending_schedule',
            created_by=self.user,
        )
        FinanceTransaction.objects.create(
            order=order,
            transaction_type='return_service_received',
            amount=Decimal('45.00'),
            reference_no='xh-ref-001',
            created_by=self.user,
        )
        resp = self.client.get(reverse('finance_transactions_list'), {'keyword': 'xh-ref-001'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, order.order_no)
        self.assertContains(resp, '收包回邮服务费')

    def test_reservation_detail_should_render_finance_and_audit_records(self):
        reservation = Reservation.objects.create(
            customer_wechat='wx_detail_001',
            customer_name='详情客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=6),
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            notes='详情页验证',
            created_by=self.user,
        )
        FinanceTransaction.objects.create(
            reservation=reservation,
            transaction_type='reservation_deposit_received',
            amount=Decimal('50.00'),
            notes='详情流水',
            created_by=self.user,
        )
        AuditService.log_with_diff(
            user=self.user,
            action='update',
            module='预定单',
            target=reservation.reservation_no,
            summary='编辑预定单',
            before={'status': 'pending_info'},
            after={'status': 'ready_to_convert'},
        )
        resp = self.client.get(reverse('reservation_detail', kwargs={'reservation_id': reservation.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reservation.reservation_no)
        self.assertContains(resp, '详情流水')
        self.assertContains(resp, '编辑预定单')

    def test_reservation_detail_should_render_conflict_summary(self):
        target_date = timezone.localdate() + timedelta(days=10)
        Reservation.objects.create(
            customer_wechat='wx_conflict_001',
            customer_name='同日预定客户',
            sku=self.sku,
            quantity=2,
            event_date=target_date,
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            created_by=self.user,
        )
        reservation = Reservation.objects.create(
            customer_wechat='wx_conflict_002',
            customer_name='当前预定客户',
            sku=self.sku,
            quantity=1,
            event_date=target_date,
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=self.user,
        )
        resp = self.client.get(reverse('reservation_detail', kwargs={'reservation_id': reservation.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '档期提醒')
        self.assertContains(resp, '同款同日已有 1 张预定单')

    def test_dashboard_due_within_7_days_should_exclude_orders_with_tracking_no(self):
        today = timezone.localdate()
        warning_order = Order.objects.create(
            customer_name='7天内预警单',
            customer_phone='13920000011',
            delivery_address='预警地址',
            event_date=today + timedelta(days=8),
            ship_date=today + timedelta(days=2),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        tracked_order = Order.objects.create(
            customer_name='已录运单但未改状态',
            customer_phone='13920000012',
            delivery_address='运单地址',
            event_date=today + timedelta(days=9),
            ship_date=today + timedelta(days=3),
            rental_days=1,
            status='pending',
            ship_tracking='SF99887766',
            created_by=self.user,
        )
        for o in [warning_order, tracked_order]:
            OrderItem.objects.create(
                order=o,
                sku=self.sku,
                quantity=1,
                rental_price=self.sku.rental_price,
                deposit=self.sku.deposit,
                subtotal=self.sku.rental_price,
            )

        dashboard_resp = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard_resp.status_code, 200)
        role_dashboard = dashboard_resp.context['role_dashboard']
        due_card = next((card for card in role_dashboard['focus_cards'] if card['key'] == 'due_within_7_days_count'), None)
        self.assertIsNotNone(due_card)
        self.assertEqual(due_card['value'], 1)
        list_resp = self.client.get(reverse('orders_list') + '?sla=warning')
        self.assertEqual(list_resp.status_code, 200)
        rows = list(list_resp.context['orders_page'].object_list)
        self.assertTrue(any(row.id == warning_order.id for row in rows))
        self.assertFalse(any(row.id == tracked_order.id for row in rows))

    def test_dashboard_should_render_ready_reservations_card(self):
        Reservation.objects.create(
            customer_wechat='wx_ready_001',
            customer_name='待转客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=12),
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            created_by=self.user,
        )
        dashboard_resp = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard_resp.status_code, 200)
        role_dashboard = dashboard_resp.context['role_dashboard']
        ready_card = next((card for card in role_dashboard['focus_cards'] if card['key'] == 'ready_reservations_count'), None)
        self.assertIsNotNone(ready_card)
        self.assertEqual(ready_card['value'], 1)

    def test_reservations_bulk_update_status_should_only_update_safe_rows(self):
        editable = Reservation.objects.create(
            customer_wechat='wx_bulk_001',
            customer_name='批量客户1',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=15),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=self.user,
        )
        converted = Reservation.objects.create(
            customer_wechat='wx_bulk_002',
            customer_name='批量客户2',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=16),
            deposit_amount=Decimal('50.00'),
            status='converted',
            created_by=self.user,
        )
        resp = self.client.post(reverse('reservations_bulk_update_status'), {
            'ids[]': [str(editable.id), str(converted.id)],
            'status': 'ready_to_convert',
        })
        self.assertEqual(resp.status_code, 302)
        editable.refresh_from_db()
        converted.refresh_from_db()
        self.assertEqual(editable.status, 'ready_to_convert')
        self.assertEqual(converted.status, 'converted')
        self.assertTrue(
            AuditLog.objects.filter(
                module='预定单',
                target=editable.reservation_no,
                action='status_change',
            ).exists()
        )

    def test_reservations_bulk_transfer_owner_should_update_owner(self):
        new_owner = User.objects.create_user(
            username='cs_receiver',
            password='test123',
            role='customer_service',
            full_name='接手客服',
        )
        reservation = Reservation.objects.create(
            customer_wechat='wx_transfer_001',
            customer_name='转交客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=11),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=self.user,
            owner=self.user,
        )
        resp = self.client.post(reverse('reservations_bulk_transfer_owner'), {
            'ids[]': [str(reservation.id)],
            'owner_id': str(new_owner.id),
            'transfer_reason': '客服请假转交',
        })
        self.assertEqual(resp.status_code, 302)
        reservation.refresh_from_db()
        self.assertEqual(reservation.owner_id, new_owner.id)
        self.assertTrue(
            AuditLog.objects.filter(
                module='预定单',
                target=reservation.reservation_no,
                action='update',
            ).exists()
        )

    def test_reservations_list_should_render_status_summary_cards(self):
        Reservation.objects.create(
            customer_wechat='wx_sum_001',
            customer_name='汇总客户1',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=7),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=self.user,
        )
        Reservation.objects.create(
            customer_wechat='wx_sum_002',
            customer_name='汇总客户2',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=8),
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            created_by=self.user,
        )
        resp = self.client.get(reverse('reservations_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '待转正式订单')
        self.assertEqual(resp.context['status_summary']['pending_info'], 1)
        self.assertEqual(resp.context['status_summary']['ready_to_convert'], 1)

    def test_reservations_list_should_handle_null_owner_without_template_error(self):
        reservation = Reservation.objects.create(
            customer_wechat='wx_null_owner_001',
            customer_name='无负责人客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=9),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=self.user,
            owner=None,
        )

        resp = self.client.get(reverse('reservations_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reservation.reservation_no)
        self.assertContains(resp, '无负责人客户')

    def test_dashboard_followup_cards_should_only_count_current_owner_for_customer_service(self):
        service_user = User.objects.create_user(
            username='service_followup',
            password='test123',
            role='customer_service',
            permission_mode='custom',
            custom_modules=['dashboard', 'orders', 'reservations', 'calendar'],
            custom_actions=['view', 'create', 'update'],
        )
        other_service = User.objects.create_user(
            username='service_other',
            password='test123',
            role='customer_service',
        )
        Reservation.objects.create(
            customer_wechat='wx_today_owner',
            customer_name='今日联系客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=7),
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            created_by=service_user,
            owner=service_user,
        )
        Reservation.objects.create(
            customer_wechat='wx_overdue_other',
            customer_name='别人的逾期客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=5),
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            created_by=other_service,
            owner=other_service,
        )
        self.client.logout()
        self.client.login(username='service_followup', password='test123')
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        cards = resp.context['role_dashboard']['focus_cards']
        today_card = next(card for card in cards if card['key'] == 'today_followup_reservations_count')
        overdue_card = next(card for card in cards if card['key'] == 'overdue_followup_reservations_count')
        self.assertEqual(today_card['value'], 1)
        self.assertEqual(overdue_card['value'], 0)

    def test_reservations_list_journey_filter_should_only_show_converted_pending_shipment(self):
        pending_order = Order.objects.create(
            customer_name='待发货来源客户',
            customer_phone='13800000031',
            delivery_address='上海市测试路31号',
            event_date=timezone.localdate() + timedelta(days=8),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        delivered_order = Order.objects.create(
            customer_name='已发货来源客户',
            customer_phone='13800000032',
            delivery_address='上海市测试路32号',
            event_date=timezone.localdate() + timedelta(days=9),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        awaiting_shipment = Reservation.objects.create(
            customer_wechat='wx_journey_001',
            customer_name='转单待发货客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=8),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=pending_order,
            created_by=self.user,
            owner=self.user,
        )
        Reservation.objects.create(
            customer_wechat='wx_journey_002',
            customer_name='已发货履约客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=9),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=delivered_order,
            created_by=self.user,
            owner=self.user,
        )
        resp = self.client.get(reverse('reservations_list') + '?journey=awaiting_shipment')
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context['reservations_page'].object_list)
        self.assertEqual([row.id for row in rows], [awaiting_shipment.id])
        self.assertEqual(resp.context['status_summary']['awaiting_shipment'], 1)
        self.assertContains(resp, '已转单待发货')

    def test_dashboard_converted_pending_shipment_card_should_only_count_current_owner_for_customer_service(self):
        service_user = User.objects.create_user(
            username='service_convert_followup',
            password='test123',
            role='customer_service',
            permission_mode='custom',
            custom_modules=['dashboard', 'orders', 'reservations', 'calendar'],
            custom_actions=['view', 'create', 'update'],
        )
        other_service = User.objects.create_user(
            username='service_convert_other',
            password='test123',
            role='customer_service',
        )
        waiting_order = Order.objects.create(
            customer_name='待发货正式单',
            customer_phone='13800000041',
            delivery_address='上海市测试路41号',
            event_date=timezone.localdate() + timedelta(days=10),
            rental_days=1,
            status='confirmed',
            created_by=service_user,
        )
        other_waiting_order = Order.objects.create(
            customer_name='别人的待发货正式单',
            customer_phone='13800000042',
            delivery_address='上海市测试路42号',
            event_date=timezone.localdate() + timedelta(days=10),
            rental_days=1,
            status='pending',
            created_by=other_service,
        )
        Reservation.objects.create(
            customer_wechat='wx_convert_owner_1',
            customer_name='我的待发货来源单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=10),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=waiting_order,
            created_by=service_user,
            owner=service_user,
        )
        Reservation.objects.create(
            customer_wechat='wx_convert_owner_2',
            customer_name='别人的待发货来源单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=10),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=other_waiting_order,
            created_by=other_service,
            owner=other_service,
        )
        self.client.logout()
        self.client.login(username='service_convert_followup', password='test123')
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        cards = resp.context['role_dashboard']['focus_cards']
        followup_card = next(card for card in cards if card['key'] == 'converted_pending_shipment_reservations_count')
        self.assertEqual(followup_card['value'], 1)

    def test_reservations_list_journey_filter_should_support_overdue_shipment_and_balance_due(self):
        overdue_order = Order.objects.create(
            customer_name='超时待发货正式单',
            customer_phone='13800000051',
            delivery_address='上海市测试路51号',
            event_date=timezone.localdate() + timedelta(days=5),
            rental_days=1,
            ship_date=timezone.localdate() - timedelta(days=1),
            status='confirmed',
            balance=Decimal('120.00'),
            created_by=self.user,
        )
        balance_due_order = Order.objects.create(
            customer_name='待收尾款正式单',
            customer_phone='13800000052',
            delivery_address='上海市测试路52号',
            event_date=timezone.localdate() + timedelta(days=6),
            rental_days=1,
            ship_date=timezone.localdate() + timedelta(days=1),
            status='confirmed',
            balance=Decimal('88.00'),
            created_by=self.user,
        )
        cleared_order = Order.objects.create(
            customer_name='尾款已清正式单',
            customer_phone='13800000053',
            delivery_address='上海市测试路53号',
            event_date=timezone.localdate() + timedelta(days=6),
            rental_days=1,
            ship_date=timezone.localdate() + timedelta(days=1),
            status='confirmed',
            balance=Decimal('0.00'),
            created_by=self.user,
        )
        overdue_reservation = Reservation.objects.create(
            customer_wechat='wx_journey_overdue',
            customer_name='超时来源预定单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=5),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=overdue_order,
            created_by=self.user,
            owner=self.user,
        )
        balance_due_reservation = Reservation.objects.create(
            customer_wechat='wx_journey_balance',
            customer_name='尾款来源预定单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=6),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=balance_due_order,
            created_by=self.user,
            owner=self.user,
        )
        Reservation.objects.create(
            customer_wechat='wx_journey_cleared',
            customer_name='已结清来源预定单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=6),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=cleared_order,
            created_by=self.user,
            owner=self.user,
        )

        overdue_resp = self.client.get(reverse('reservations_list') + '?journey=awaiting_shipment_overdue')
        self.assertEqual(overdue_resp.status_code, 200)
        overdue_rows = list(overdue_resp.context['reservations_page'].object_list)
        self.assertEqual([row.id for row in overdue_rows], [overdue_reservation.id])
        self.assertEqual(overdue_resp.context['status_summary']['awaiting_shipment_overdue'], 1)
        self.assertContains(overdue_resp, '待发货超时')

        balance_resp = self.client.get(reverse('reservations_list') + '?journey=balance_due')
        self.assertEqual(balance_resp.status_code, 200)
        balance_rows = list(balance_resp.context['reservations_page'].object_list)
        self.assertEqual({row.id for row in balance_rows}, {overdue_reservation.id, balance_due_reservation.id})
        self.assertEqual(balance_resp.context['status_summary']['balance_due'], 2)
        self.assertContains(balance_resp, '待收尾款')

    def test_dashboard_converted_followup_cards_should_only_count_current_owner_for_customer_service(self):
        service_user = User.objects.create_user(
            username='service_convert_risk',
            password='test123',
            role='customer_service',
            permission_mode='custom',
            custom_modules=['dashboard', 'orders', 'reservations', 'calendar'],
            custom_actions=['view', 'create', 'update'],
        )
        other_service = User.objects.create_user(
            username='service_convert_risk_other',
            password='test123',
            role='customer_service',
        )
        overdue_order = Order.objects.create(
            customer_name='我的超时正式单',
            customer_phone='13800000061',
            delivery_address='上海市测试路61号',
            event_date=timezone.localdate() + timedelta(days=4),
            rental_days=1,
            ship_date=timezone.localdate() - timedelta(days=1),
            status='confirmed',
            balance=Decimal('66.00'),
            created_by=service_user,
        )
        other_order = Order.objects.create(
            customer_name='别人的超时正式单',
            customer_phone='13800000062',
            delivery_address='上海市测试路62号',
            event_date=timezone.localdate() + timedelta(days=4),
            rental_days=1,
            ship_date=timezone.localdate() - timedelta(days=1),
            status='confirmed',
            balance=Decimal('77.00'),
            created_by=other_service,
        )
        Reservation.objects.create(
            customer_wechat='wx_owner_risk_1',
            customer_name='我的超时来源单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=4),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=overdue_order,
            created_by=service_user,
            owner=service_user,
        )
        Reservation.objects.create(
            customer_wechat='wx_owner_risk_2',
            customer_name='别人的超时来源单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=4),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=other_order,
            created_by=other_service,
            owner=other_service,
        )
        self.client.logout()
        self.client.login(username='service_convert_risk', password='test123')
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        cards = resp.context['role_dashboard']['focus_cards']
        overdue_card = next(card for card in cards if card['key'] == 'converted_overdue_shipment_reservations_count')
        balance_card = next(card for card in cards if card['key'] == 'converted_balance_due_reservations_count')
        self.assertEqual(overdue_card['value'], 1)
        self.assertEqual(balance_card['value'], 1)

    def test_dashboard_should_render_reservation_owner_followup_panels_for_manager_view(self):
        owner_a = User.objects.create_user(
            username='reservation_owner_a',
            password='test123',
            role='customer_service',
            full_name='客服甲',
        )
        owner_b = User.objects.create_user(
            username='reservation_owner_b',
            password='test123',
            role='customer_service',
            full_name='客服乙',
        )
        overdue_order = Order.objects.create(
            customer_name='客服甲超时正式单',
            customer_phone='13800000071',
            delivery_address='上海市测试路71号',
            event_date=timezone.localdate() + timedelta(days=4),
            rental_days=1,
            ship_date=timezone.localdate() - timedelta(days=1),
            status='confirmed',
            balance=Decimal('20.00'),
            created_by=owner_a,
        )
        balance_order = Order.objects.create(
            customer_name='客服乙尾款正式单',
            customer_phone='13800000072',
            delivery_address='上海市测试路72号',
            event_date=timezone.localdate() + timedelta(days=6),
            rental_days=1,
            ship_date=timezone.localdate() + timedelta(days=1),
            status='confirmed',
            balance=Decimal('66.00'),
            created_by=owner_b,
        )
        Reservation.objects.create(
            customer_wechat='wx_panel_today',
            customer_name='客服甲今日联系单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=7),
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            created_by=owner_a,
            owner=owner_a,
        )
        Reservation.objects.create(
            customer_wechat='wx_panel_overdue',
            customer_name='客服甲逾期联系单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=5),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=owner_a,
            owner=owner_a,
        )
        Reservation.objects.create(
            customer_wechat='wx_panel_ship',
            customer_name='客服甲待发货超时单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=4),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=overdue_order,
            created_by=owner_a,
            owner=owner_a,
        )
        Reservation.objects.create(
            customer_wechat='wx_panel_balance',
            customer_name='客服乙待收尾款单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=6),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=balance_order,
            created_by=owner_b,
            owner=owner_b,
        )
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        panels = resp.context['role_dashboard']['reservation_owner_panels']
        self.assertEqual(len(panels), 2)
        first = next(panel for panel in panels if panel['owner_name'] == '客服甲')
        second = next(panel for panel in panels if panel['owner_name'] == '客服乙')
        self.assertEqual(first['today_count'], 1)
        self.assertEqual(first['overdue_count'], 1)
        self.assertEqual(first['overdue_shipment_count'], 1)
        self.assertEqual(first['balance_due_count'], 1)
        self.assertEqual(second['balance_due_count'], 1)
        self.assertContains(resp, '预定跟进负责人分布')
        self.assertContains(resp, '客服甲')
        self.assertContains(resp, '客服乙')

    def test_dashboard_should_render_reservation_owner_transfer_suggestions_for_manager_view(self):
        heavy_owner = User.objects.create_user(
            username='reservation_heavy_owner',
            password='test123',
            role='customer_service',
            full_name='繁忙客服',
        )
        light_owner = User.objects.create_user(
            username='reservation_light_owner',
            password='test123',
            role='customer_service',
            full_name='空闲客服',
        )
        overdue_order = Order.objects.create(
            customer_name='建议移交超时正式单',
            customer_phone='13800000081',
            delivery_address='上海市测试路81号',
            event_date=timezone.localdate() + timedelta(days=3),
            rental_days=1,
            ship_date=timezone.localdate() - timedelta(days=1),
            status='confirmed',
            created_by=heavy_owner,
        )
        balance_order = Order.objects.create(
            customer_name='建议移交尾款正式单',
            customer_phone='13800000082',
            delivery_address='上海市测试路82号',
            event_date=timezone.localdate() + timedelta(days=5),
            rental_days=1,
            ship_date=timezone.localdate() + timedelta(days=1),
            status='confirmed',
            balance=Decimal('99.00'),
            created_by=heavy_owner,
        )
        Reservation.objects.create(
            customer_wechat='wx_transfer_suggest_1',
            customer_name='逾期联系建议单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=5),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=heavy_owner,
            owner=heavy_owner,
        )
        Reservation.objects.create(
            customer_wechat='wx_transfer_suggest_2',
            customer_name='今日联系建议单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=7),
            deposit_amount=Decimal('50.00'),
            status='ready_to_convert',
            created_by=heavy_owner,
            owner=heavy_owner,
        )
        Reservation.objects.create(
            customer_wechat='wx_transfer_suggest_3',
            customer_name='待发货超时建议单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=3),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=overdue_order,
            created_by=heavy_owner,
            owner=heavy_owner,
        )
        Reservation.objects.create(
            customer_wechat='wx_transfer_suggest_4',
            customer_name='尾款建议单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=5),
            deposit_amount=Decimal('50.00'),
            status='converted',
            converted_order=balance_order,
            created_by=heavy_owner,
            owner=heavy_owner,
        )
        Reservation.objects.create(
            customer_wechat='wx_transfer_suggest_5',
            customer_name='空闲客服单',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=20),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=light_owner,
            owner=light_owner,
        )
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        suggestions = resp.context['role_dashboard']['reservation_owner_transfer_suggestions']
        self.assertTrue(suggestions)
        first = suggestions[0]
        self.assertEqual(first['source_owner_name'], '繁忙客服')
        self.assertEqual(first['target_owner_name'], '空闲客服')
        self.assertGreaterEqual(first['suggest_count'], 1)
        self.assertContains(resp, '负责人移交建议')
        self.assertContains(resp, '繁忙客服')
        self.assertContains(resp, '空闲客服')

    def test_dashboard_should_render_followup_banner_without_global_message_dialog(self):
        Reservation.objects.create(
            customer_wechat='wx_dashboard_followup_banner',
            customer_name='工作台提醒客户',
            sku=self.sku,
            quantity=1,
            event_date=timezone.localdate() + timedelta(days=5),
            deposit_amount=Decimal('50.00'),
            status='pending_info',
            created_by=self.user,
            owner=self.user,
        )

        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '预定跟进提醒')
        self.assertContains(resp, '今天不再提醒')
        self.assertContains(resp, '查看待办')
        self.assertNotContains(resp, 'pageMessagesData')

    def test_dashboard_should_expose_overdue_orders_card_and_match_orders_list_filter(self):
        today = timezone.localdate()
        overdue_order = Order.objects.create(
            customer_name='已超时订单',
            customer_phone='13920000021',
            delivery_address='超时地址',
            event_date=today + timedelta(days=2),
            ship_date=today - timedelta(days=1),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        tracked_overdue = Order.objects.create(
            customer_name='已录运单超时单',
            customer_phone='13920000022',
            delivery_address='超时运单地址',
            event_date=today + timedelta(days=2),
            ship_date=today - timedelta(days=2),
            rental_days=1,
            status='pending',
            ship_tracking='SF11223344',
            created_by=self.user,
        )
        for o in [overdue_order, tracked_overdue]:
            OrderItem.objects.create(
                order=o,
                sku=self.sku,
                quantity=1,
                rental_price=self.sku.rental_price,
                deposit=self.sku.deposit,
                subtotal=self.sku.rental_price,
            )

        dashboard_resp = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard_resp.status_code, 200)
        role_dashboard = dashboard_resp.context['role_dashboard']
        overdue_card = next((card for card in role_dashboard['focus_cards'] if card['key'] == 'overdue_orders_count'), None)
        self.assertIsNotNone(overdue_card)
        self.assertEqual(overdue_card['value'], 1)
        self.assertEqual(overdue_card['query'], 'sla=overdue')

        list_resp = self.client.get(reverse('orders_list') + '?sla=overdue')
        self.assertEqual(list_resp.status_code, 200)
        rows = list(list_resp.context['orders_page'].object_list)
        self.assertTrue(any(row.id == overdue_order.id for row in rows))
        self.assertFalse(any(row.id == tracked_overdue.id for row in rows))

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

        detail_resp = self.client.get(reverse('order_detail', kwargs={'order_id': source_order.id}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, '来源单占用')
        self.assertContains(detail_resp, '是（1）')
        self.assertContains(detail_resp, '转寄中心操作（来源占用中）')

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
        self.assertEqual(order.status, 'delivered')

    def test_order_mark_completed_should_only_allow_returned_status(self):
        order = Order.objects.create(
            customer_name='待完成客户',
            customer_phone='13500000011',
            delivery_address='地址4',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )

        resp = self.client.post(reverse('order_mark_completed', kwargs={'order_id': order.id}))
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'confirmed')

    def test_order_detail_should_hide_mark_completed_without_action_permission(self):
        cs = User.objects.create_user(
            username='flow_customer_service_returned',
            password='test123',
            role='customer_service',
            is_staff=True,
        )
        order = Order.objects.create(
            customer_name='详情权限客户',
            customer_phone='13500000021',
            delivery_address='地址6',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='returned',
            created_by=self.user,
        )
        client = Client()
        client.login(username='flow_customer_service_returned', password='test123')

        resp = client.get(reverse('order_detail', kwargs={'order_id': order.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, '标记完成')

    def test_orders_list_should_hide_mark_completed_without_action_permission(self):
        cs = User.objects.create_user(
            username='flow_customer_service_returned_list',
            password='test123',
            role='customer_service',
            is_staff=True,
        )
        order = Order.objects.create(
            customer_name='列表权限客户',
            customer_phone='13500000022',
            delivery_address='地址7',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='returned',
            created_by=self.user,
        )
        client = Client()
        client.login(username='flow_customer_service_returned_list', password='test123')

        resp = client.get(reverse('orders_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, f"markCompleted({order.id},")

    def test_orders_list_should_support_returned_status_filter(self):
        returned_order = Order.objects.create(
            customer_name='已归还筛选客户',
            customer_phone='13500000023',
            delivery_address='地址8',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='returned',
            created_by=self.user,
        )
        pending_order = Order.objects.create(
            customer_name='待处理筛选客户',
            customer_phone='13500000024',
            delivery_address='地址9',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )

        resp = self.client.get(reverse('orders_list'), {'status': 'returned'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, returned_order.order_no)
        self.assertNotContains(resp, pending_order.order_no)
        self.assertContains(resp, '<option value="returned" selected>已归还</option>', html=True)

    def test_orders_list_should_support_wechat_and_xianyu_keyword_search(self):
        target_order = Order.objects.create(
            customer_name='微信闲鱼客户',
            customer_phone='18800000001',
            customer_wechat='wx-search-001',
            xianyu_order_no='xy-order-001',
            order_source='xianyu',
            source_order_no='xy-order-001',
            delivery_address='地址10',
            event_date=date.today() + timedelta(days=4),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        other_order = Order.objects.create(
            customer_name='普通客户',
            customer_phone='18800000002',
            customer_wechat='wx-other-002',
            xianyu_order_no='xy-other-002',
            order_source='wechat',
            source_order_no='wx-order-002',
            delivery_address='地址11',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )

        resp_wechat = self.client.get(reverse('orders_list'), {'keyword': 'wx-search-001'})
        self.assertEqual(resp_wechat.status_code, 200)
        self.assertContains(resp_wechat, target_order.order_no)
        self.assertNotContains(resp_wechat, other_order.order_no)

        resp_xianyu = self.client.get(reverse('orders_list'), {'keyword': 'xy-order-001'})
        self.assertEqual(resp_xianyu.status_code, 200)
        self.assertContains(resp_xianyu, target_order.order_no)
        self.assertNotContains(resp_xianyu, other_order.order_no)

        target_order.return_service_type = 'platform_return_included'
        target_order.return_service_fee = Decimal('45.00')
        target_order.return_service_payment_status = 'paid'
        target_order.return_service_payment_channel = 'wechat'
        target_order.return_service_payment_reference = 'wx-pay-search-001'
        target_order.return_pickup_status = 'pending_schedule'
        target_order.save(update_fields=[
            'return_service_type',
            'return_service_fee',
            'return_service_payment_status',
            'return_service_payment_channel',
            'return_service_payment_reference',
            'return_pickup_status',
            'updated_at',
        ])

        resp_platform = self.client.get(reverse('orders_list'), {'keyword': 'xy-order-001'})
        self.assertEqual(resp_platform.status_code, 200)
        self.assertContains(resp_platform, target_order.order_no)

        resp_reference = self.client.get(reverse('orders_list'), {'keyword': 'wx-pay-search-001'})
        self.assertEqual(resp_reference.status_code, 200)
        self.assertContains(resp_reference, target_order.order_no)
        self.assertNotContains(resp_reference, other_order.order_no)

    def test_orders_list_should_filter_return_service_paid(self):
        target_order = Order.objects.create(
            customer_name='包回邮已付款客户',
            customer_phone='18800009991',
            delivery_address='地址20',
            event_date=date.today() + timedelta(days=4),
            rental_days=1,
            status='pending',
            return_service_type='platform_return_included',
            return_service_fee=Decimal('45.00'),
            return_service_payment_status='paid',
            return_service_payment_channel='wechat',
            return_service_payment_reference='wx-paid-filter-001',
            return_pickup_status='pending_schedule',
            created_by=self.user,
        )
        Order.objects.create(
            customer_name='包回邮未付款客户',
            customer_phone='18800009992',
            delivery_address='地址21',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='pending',
            return_service_type='platform_return_included',
            return_service_fee=Decimal('45.00'),
            return_service_payment_status='unpaid',
            return_pickup_status='pending_schedule',
            created_by=self.user,
        )

        resp = self.client.get(reverse('orders_list'), {'return_payment': 'paid'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, target_order.order_no)
        self.assertContains(resp, '包回邮￥45.00')

    def test_purchase_orders_nav_should_not_activate_orders_menu(self):
        resp = self.client.get(reverse('purchase_orders_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<a href="/procurement/purchase-orders/" class="nav-item active">', html=False)
        self.assertContains(resp, '<a href="/orders/" class="nav-item ">')

    def test_base_should_render_page_messages_as_hidden_dialog_payload(self):
        resp = self.client.post(reverse('orders_bulk_delete'), {}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="pageMessagesData"')
        self.assertContains(resp, 'page-message-item')
        self.assertNotContains(resp, '<div class="messages">', html=False)

    def test_user_edit_should_update_basic_fields(self):
        target = User.objects.create_user(
            username='edit_target',
            password='test123',
            role='warehouse_staff',
            full_name='原姓名',
            email='old@example.com',
            phone='13800001234',
        )

        resp = self.client.post(
            reverse('user_edit', kwargs={'user_id': target.id}),
            {
                'username': 'edit_target_new',
                'full_name': '新姓名',
                'role': 'manager',
                'permission_mode': 'custom',
                'custom_modules': ['orders', 'calendar', 'users'],
                'custom_actions': ['view', 'update'],
                'custom_action_permissions': ['transfer.complete_task', 'finance.manual_adjust'],
                'email': 'new@example.com',
                'phone': '13900005678',
                'password': '',
            },
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        target.refresh_from_db()
        self.assertEqual(target.username, 'edit_target_new')
        self.assertEqual(target.full_name, '新姓名')
        self.assertEqual(target.role, 'manager')
        self.assertEqual(target.permission_mode, 'custom')
        self.assertEqual(target.custom_modules, ['orders', 'calendar', 'users'])
        self.assertEqual(target.custom_actions, ['view', 'update'])
        self.assertEqual(target.custom_action_permissions, ['transfer.complete_task', 'finance.manual_adjust'])
        self.assertEqual(target.email, 'new@example.com')
        self.assertEqual(target.phone, '13900005678')
        log = AuditLog.objects.filter(module='用户管理', target='用户:edit_target_new', action='update').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '编辑用户')
        self.assertEqual(payload['before']['username'], 'edit_target')
        self.assertEqual(payload['after']['username'], 'edit_target_new')
        self.assertIn('permission_mode', payload['changed_fields'])
        self.assertIn('custom_modules', payload['changed_fields'])

    def test_user_create_should_add_new_user(self):
        resp = self.client.post(
            reverse('user_create'),
            {
                'username': 'new_user_case',
                'full_name': '新增用户',
                'role': 'customer_service',
                'permission_mode': 'custom',
                'custom_modules': ['orders', 'calendar', 'finance'],
                'custom_actions': ['view', 'create'],
                'custom_action_permissions': ['order.confirm_delivery'],
                'email': 'new_user@example.com',
                'phone': '13700001111',
                'password': 'test123',
            },
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        created = User.objects.get(username='new_user_case')
        self.assertEqual(created.full_name, '新增用户')
        self.assertEqual(created.role, 'customer_service')
        self.assertEqual(created.permission_mode, 'custom')
        self.assertEqual(created.custom_modules, ['orders', 'calendar', 'finance'])
        self.assertEqual(created.custom_actions, ['view', 'create'])
        self.assertEqual(created.custom_action_permissions, ['order.confirm_delivery'])
        self.assertEqual(created.email, 'new_user@example.com')
        self.assertEqual(created.phone, '13700001111')
        self.assertTrue(created.is_active)
        log = AuditLog.objects.filter(module='用户管理', target='用户:new_user_case', action='create').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '创建用户')
        self.assertEqual(payload['after']['username'], 'new_user_case')
        self.assertIn('username', payload['changed_fields'])

    def test_user_toggle_status_should_disable_target_user(self):
        target = User.objects.create_user(
            username='toggle_target',
            password='test123',
            role='warehouse_staff',
            full_name='切换用户',
            is_active=True,
        )

        resp = self.client.post(
            reverse('user_toggle_status', kwargs={'user_id': target.id}),
            {'enable': '0'},
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        log = AuditLog.objects.filter(module='用户管理', target='用户:toggle_target', action='status_change').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '用户状态变更为禁用')
        self.assertEqual(payload['before']['is_active'], True)
        self.assertEqual(payload['after']['is_active'], False)
        self.assertIn('is_active', payload['changed_fields'])

    def test_user_toggle_status_should_not_disable_current_user(self):
        resp = self.client.post(
            reverse('user_toggle_status', kwargs={'user_id': self.user.id}),
            {'enable': '0'},
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_custom_permissions_should_allow_orders_view_for_warehouse_staff_template(self):
        order = Order.objects.create(
            customer_name='自定义权限客户',
            customer_phone='13512340000',
            delivery_address='自定义权限地址',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='confirmed',
            total_amount=Decimal('168.00'),
            balance=Decimal('168.00'),
            created_by=self.user,
        )
        custom_user = User.objects.create_user(
            username='custom_perm_user',
            password='test123',
            role='warehouse_staff',
            permission_mode='custom',
            custom_modules=['orders'],
            custom_actions=['view'],
            custom_action_permissions=[],
            is_staff=True,
        )

        client = Client()
        self.assertTrue(client.login(username='custom_perm_user', password='test123'))
        resp = client.get(reverse('orders_list'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, order.order_no)

    def test_permission_template_create_should_add_template(self):
        resp = self.client.post(
            reverse('permission_template_create'),
            {
                'name': '客服扩展模板',
                'base_role': 'customer_service',
                'description': '客服可看财务',
                'modules': ['orders', 'calendar', 'finance'],
                'actions': ['view', 'update'],
                'action_permissions': ['finance.manual_adjust'],
            },
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        template = PermissionTemplate.objects.get(name='客服扩展模板')
        self.assertEqual(template.base_role, 'customer_service')
        self.assertEqual(template.modules, ['orders', 'calendar', 'finance'])
        self.assertEqual(template.actions, ['view', 'update'])
        self.assertEqual(template.action_permissions, ['finance.manual_adjust'])
        log = AuditLog.objects.filter(module='权限模板', target='模板:客服扩展模板', action='create').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '创建权限模板')
        self.assertEqual(payload['after']['name'], '客服扩展模板')

    def test_permission_template_edit_should_update_template(self):
        template = PermissionTemplate.objects.create(
            name='原模板',
            base_role='warehouse_staff',
            description='原说明',
            modules=['orders'],
            actions=['view'],
            action_permissions=[],
        )

        resp = self.client.post(
            reverse('permission_template_edit', kwargs={'template_id': template.id}),
            {
                'name': '新模板',
                'base_role': 'manager',
                'description': '新说明',
                'modules': ['orders', 'users'],
                'actions': ['view', 'create', 'update'],
                'action_permissions': ['transfer.complete_task'],
            },
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        template.refresh_from_db()
        self.assertEqual(template.name, '新模板')
        self.assertEqual(template.base_role, 'manager')
        self.assertEqual(template.description, '新说明')
        self.assertEqual(template.modules, ['orders', 'users'])
        self.assertEqual(template.actions, ['view', 'create', 'update'])
        self.assertEqual(template.action_permissions, ['transfer.complete_task'])
        log = AuditLog.objects.filter(module='权限模板', target='模板:新模板', action='update').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '编辑权限模板')
        self.assertEqual(payload['before']['name'], '原模板')
        self.assertEqual(payload['after']['name'], '新模板')
        self.assertIn('base_role', payload['changed_fields'])

    def test_permission_template_delete_should_remove_template(self):
        template = PermissionTemplate.objects.create(
            name='待删模板',
            base_role='warehouse_staff',
            modules=['orders'],
            actions=['view'],
            action_permissions=[],
        )

        resp = self.client.post(
            reverse('permission_template_delete', kwargs={'template_id': template.id}),
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PermissionTemplate.objects.filter(id=template.id).exists())
        log = AuditLog.objects.filter(module='权限模板', target='模板:待删模板', action='delete').order_by('-created_at').first()
        self.assertIsNotNone(log)
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '删除权限模板')
        self.assertEqual(payload['before']['name'], '待删模板')
        self.assertEqual(payload['after'], {})

    def test_users_list_should_render_permission_template_options(self):
        PermissionTemplate.objects.create(
            name='财务查看模板',
            base_role='manager',
            modules=['finance'],
            actions=['view'],
            action_permissions=[],
        )

        resp = self.client.get(reverse('users_list'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '权限模板库')
        self.assertContains(resp, '财务查看模板')
        self.assertContains(resp, 'name="permission_template"')

    def test_users_list_should_render_permission_preview_payload(self):
        preview_user = User.objects.create_user(
            username='preview_user',
            password='test123',
            role='warehouse_staff',
            permission_mode='custom',
            custom_modules=['orders', 'finance'],
            custom_actions=['view', 'update'],
            custom_action_permissions=['finance.manual_adjust'],
            is_staff=True,
        )

        resp = self.client.get(reverse('users_list'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="userPermissionPreviewData"')
        self.assertContains(resp, 'previewUserPermissions(')
        self.assertContains(resp, preview_user.username)
        preview_payload = resp.context['user_permission_previews'][str(preview_user.id)]
        self.assertIn('当前为自定义搭配权限；数据范围仍按基础角色模板生效', preview_payload['data_scopes'])
        self.assertEqual(preview_payload['baseline_role'], '仓库操作员')
        self.assertIn('订单中心', preview_payload['diffs']['modules']['added'])
        self.assertIn('产品管理', preview_payload['diffs']['modules']['removed'])
        self.assertIn('订单确认/发货', preview_payload['diffs']['action_permissions']['removed'])

    def test_users_list_should_render_recent_permission_audits(self):
        AuditLog.objects.create(
            user=self.user,
            action='update',
            module='用户管理',
            target='用户:audit_target',
            details=json.dumps({
                'summary': '编辑用户',
                'before': {'role': 'warehouse_staff'},
                'after': {'role': 'manager'},
                'changed_fields': ['role'],
                'extra': {'source': 'app'},
            }, ensure_ascii=False),
        )

        resp = self.client.get(reverse('users_list'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '最近权限变更记录')
        self.assertContains(resp, '用户:audit_target')
        self.assertContains(resp, '编辑用户')
        self.assertContains(resp, 'role')

    def test_customer_service_should_not_confirm_order_without_action_permission(self):
        cs = User.objects.create_user(
            username='flow_customer_service',
            password='test123',
            role='customer_service',
            is_staff=True,
        )
        order = Order.objects.create(
            customer_name='客服确认客户',
            customer_phone='13500000012',
            delivery_address='地址5',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        client = Client()
        client.login(username='flow_customer_service', password='test123')

        resp = client.post(
            reverse('order_mark_confirmed', kwargs={'order_id': order.id}),
            {'deposit_paid': '80'},
        )
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')

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
            deposit_paid=Decimal('80.00'),
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
        self.assertTrue(
            FinanceTransaction.objects.filter(
                order=order_from,
                transaction_type='deposit_refund',
                amount=Decimal('80.00'),
            ).exists()
        )

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
        self.assertContains(resp, 'id="transferCompleteFeedbackData"')
        session = self.client.session
        self.assertNotIn('transfer_complete_feedback', session)

    def test_transfer_complete_should_store_success_feedback_for_popup(self):
        order_from = Order.objects.create(
            customer_name='转寄成功来源',
            customer_phone='13611113333',
            delivery_address='来源成功地址',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            deposit_paid=Decimal('80.00'),
            created_by=self.user,
            ship_date=date.today(),
            return_date=date.today() + timedelta(days=2),
        )
        order_to = Order.objects.create(
            customer_name='转寄成功目标',
            customer_phone='13622224444',
            delivery_address='目标成功地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
            ship_date=date.today() + timedelta(days=2),
            return_date=date.today() + timedelta(days=5),
        )
        OrderItem.objects.create(order=order_from, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('280.00'))
        OrderItem.objects.create(order=order_to, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('280.00'))
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
            {'tracking_no': 'YT-SUCCESS-001'},
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="transferCompleteFeedbackData"')
        payload = json.loads(resp.context['transfer_complete_feedback_json'])
        self.assertEqual(payload['title'], '完成成功')
        self.assertIn('转寄任务已完成', payload['message'])

    def test_transfer_complete_should_store_error_feedback_for_popup(self):
        order_from = Order.objects.create(
            customer_name='转寄失败来源',
            customer_phone='13611114444',
            delivery_address='来源失败地址',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        order_to = Order.objects.create(
            customer_name='转寄失败目标',
            customer_phone='13622225555',
            delivery_address='目标失败地址',
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
            {'tracking_no': 'YT-ERROR-001'},
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="transferCompleteFeedbackData"')
        payload = json.loads(resp.context['transfer_complete_feedback_json'])
        self.assertEqual(payload['title'], '操作失败')
        self.assertIn('来源单状态为 待处理，无法执行归还完成', payload['message'])

    def test_transfer_complete_should_only_consume_matching_source_allocation(self):
        source_a = Order.objects.create(
            customer_name='来源A',
            customer_phone='13633330001',
            delivery_address='来源A地址',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        source_b = Order.objects.create(
            customer_name='来源B',
            customer_phone='13633330002',
            delivery_address='来源B地址',
            event_date=date.today() + timedelta(days=1),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标单',
            customer_phone='13633330003',
            delivery_address='目标地址',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source_a, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=source_b, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=2, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('400.00'))

        alloc_a = TransferAllocation.objects.create(
            source_order=source_a,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=2),
            window_end=target.event_date + timedelta(days=2),
            status='locked',
            created_by=self.user,
        )
        alloc_b = TransferAllocation.objects.create(
            source_order=source_b,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=2),
            window_end=target.event_date + timedelta(days=2),
            status='locked',
            created_by=self.user,
        )

        transfer = Transfer.objects.create(
            order_from=source_a,
            order_to=target,
            sku=self.sku,
            quantity=1,
            gap_days=3,
            cost_saved=Decimal('100.00'),
            status='pending',
            created_by=self.user,
        )

        self.client.post(
            reverse('transfer_complete', kwargs={'transfer_id': transfer.id}),
            {'tracking_no': 'YT-CONSUME-001'},
            follow=True
        )

        alloc_a.refresh_from_db()
        alloc_b.refresh_from_db()
        self.assertEqual(alloc_a.status, 'consumed')
        self.assertEqual(alloc_b.status, 'locked')

    def test_transfer_complete_should_split_allocation_when_partial_consume(self):
        source = Order.objects.create(
            customer_name='来源拆分',
            customer_phone='13644440001',
            delivery_address='来源拆分地址',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标拆分',
            customer_phone='13644440002',
            delivery_address='目标拆分地址',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=2, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('400.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=2, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('400.00'))

        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=2,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=2),
            window_end=target.event_date + timedelta(days=2),
            status='locked',
            created_by=self.user,
        )
        transfer = Transfer.objects.create(
            order_from=source,
            order_to=target,
            sku=self.sku,
            quantity=1,
            gap_days=3,
            cost_saved=Decimal('100.00'),
            status='pending',
            created_by=self.user,
        )

        self.client.post(
            reverse('transfer_complete', kwargs={'transfer_id': transfer.id}),
            {'tracking_no': 'YT-SPLIT-001'},
            follow=True
        )

        consumed_qty = TransferAllocation.objects.filter(
            source_order=source,
            target_order=target,
            sku=self.sku,
            status='consumed',
        ).aggregate(total=Sum('quantity'))['total'] or 0
        locked_qty = TransferAllocation.objects.filter(
            source_order=source,
            target_order=target,
            sku=self.sku,
            status='locked',
        ).aggregate(total=Sum('quantity'))['total'] or 0
        self.assertEqual(int(consumed_qty), 1)
        self.assertEqual(int(locked_qty), 1)

    def test_consistency_check_should_report_legacy_stock_mismatch(self):
        result = run_data_consistency_checks()
        self.assertGreaterEqual(result['warning_count'], 1)
        self.assertTrue(
            any(
                i.get('type') == 'legacy_stock_mismatch'
                and i.get('meta', {}).get('sku_id') == self.sku.id
                for i in result['issues']
            )
        )

    def test_consistency_check_should_report_transfer_locked_shortage(self):
        source = Order.objects.create(
            customer_name='巡检来源',
            customer_phone='13910000001',
            delivery_address='巡检来源地址',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='巡检目标',
            customer_phone='13910000002',
            delivery_address='巡检目标地址',
            event_date=date.today() + timedelta(days=6),
            rental_days=1,
            status='confirmed',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=2, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=2, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        Transfer.objects.create(
            order_from=source,
            order_to=target,
            sku=self.sku,
            quantity=2,
            gap_days=6,
            cost_saved=Decimal('100.00'),
            status='pending',
            created_by=self.user,
        )
        TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=2),
            window_end=target.event_date + timedelta(days=2),
            status='locked',
            created_by=self.user,
        )

        result = run_data_consistency_checks()
        self.assertGreaterEqual(result['error_count'], 1)
        self.assertTrue(
            any(i.get('type') == 'transfer_locked_shortage' for i in result['issues'])
        )

    def test_consistency_check_should_report_finance_reconciliation_mismatch(self):
        order = Order.objects.create(
            customer_name='巡检财务客户',
            customer_phone='13910000003',
            delivery_address='巡检财务地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            total_amount=Decimal('200.00'),
            deposit_paid=Decimal('100.00'),
            balance=Decimal('0.00'),
            status='completed',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku,
            quantity=1,
            rental_price=Decimal('200.00'),
            deposit=Decimal('100.00'),
            subtotal=Decimal('200.00'),
        )

        result = run_data_consistency_checks()
        self.assertTrue(any(i.get('type') == 'finance_reconciliation_mismatch' for i in result['issues']))
        self.assertGreaterEqual(int((result.get('type_counts') or {}).get('finance_reconciliation_mismatch', 0)), 1)

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

    def test_transfer_recommend_for_delivered_order_should_create_risk_event_when_source_changed(self):
        source = Order.objects.create(
            customer_name='来源风险',
            customer_phone='13600006666',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='completed',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标风险',
            customer_phone='13600007777',
            delivery_address='广东省广州市越秀区',
            event_date=date.today() + timedelta(days=7),
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
            status='locked',
            created_by=self.user,
        )

        resp = self.client.post(reverse('transfer_recommend'), {'rows[]': [f'{target.id}:{self.sku.id}']})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            RiskEvent.objects.filter(
                event_type='delivered_recommend_change',
                order=target,
                status='open',
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
        messages_list = [m.message for m in resp.context['messages']]
        self.assertTrue(any('已存在转寄任务（待执行或已完成）' in m for m in messages_list))

    def test_transfer_recommend_should_warn_when_completed_task_exists(self):
        source = Order.objects.create(
            customer_name='来源RC',
            customer_phone='13640000001',
            delivery_address='广东省广州市天河区',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        target = Order.objects.create(
            customer_name='目标RC',
            customer_phone='13640000002',
            delivery_address='广东省广州市越秀区',
            event_date=date.today() + timedelta(days=8),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        OrderItem.objects.create(order=source, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
        OrderItem.objects.create(order=target, sku=self.sku, quantity=1, rental_price=self.sku.rental_price, deposit=self.sku.deposit, subtotal=Decimal('200.00'))
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
            reverse('transfer_recommend'),
            {'rows[]': [f'{target.id}:{self.sku.id}']},
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        messages_list = [m.message for m in resp.context['messages']]
        self.assertTrue(any('已存在转寄任务（待执行或已完成），不可重推' in m for m in messages_list))

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

    def test_orders_and_dashboard_should_render_returned_status_label(self):
        returned_order = Order.objects.create(
            customer_name='归还状态客户',
            customer_phone='18900000003',
            delivery_address='广东省深圳市南山区科技园2号',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='returned',
            created_by=self.user,
        )
        OrderItem.objects.create(
            order=returned_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('200.00'),
        )

        list_resp = self.client.get(reverse('orders_list'), {'status': 'returned'})
        self.assertEqual(list_resp.status_code, 200)
        self.assertContains(list_resp, '<span class="emphasis-badge info-soft">已归还</span>', html=True)

        dashboard_resp = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard_resp.status_code, 200)
        self.assertContains(dashboard_resp, '<span class="badge text-bg-info">已归还</span>', html=True)

    def test_login_page_should_not_expose_default_credentials_hint(self):
        self.client.logout()

        resp = self.client.get(reverse('login'))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'admin123')
        self.assertNotContains(resp, '默认账号')

    def test_skus_list_should_render_and_consume_assembly_feedback_from_session(self):
        SKUComponent.objects.create(
            sku=self.sku,
            part=self.part,
            quantity_per_set=2,
            notes='主组件',
        )
        session = self.client.session
        session['sku_assembly_feedback'] = {
            'status': 'success',
            'sku_code': self.sku.code,
            'sku_name': self.sku.name,
            'quantity': 3,
            'assembly_no': 'ASM-TEST-001',
        }
        session.save()

        first_resp = self.client.get(reverse('skus_list'))

        self.assertEqual(first_resp.status_code, 200)
        self.assertContains(first_resp, 'id="skuAssemblyFeedbackData"')
        self.assertContains(first_resp, 'ASM-TEST-001')
        self.assertContains(first_resp, 'data-components-b64=')

        second_resp = self.client.get(reverse('skus_list'))

        self.assertEqual(second_resp.status_code, 200)
        self.assertNotContains(second_resp, 'id="skuAssemblyFeedbackData"')
        self.assertNotContains(second_resp, 'ASM-TEST-001')

    @override_settings(
        R2_ACCESS_KEY_ID='',
        R2_SECRET_ACCESS_KEY='',
        R2_BUCKET='',
        R2_ENDPOINT='',
        R2_PUBLIC_DOMAIN='',
        R2_ENABLED=False,
    )
    def test_skus_list_should_show_r2_warning_when_storage_not_ready(self):
        resp = self.client.get(reverse('skus_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Cloudflare R2 图片直传未就绪')
        self.assertContains(resp, 'R2_ACCESS_KEY_ID')

    def test_dashboard_should_render_role_risk_entries_and_quick_actions(self):
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        role_dashboard = resp.context['role_dashboard']
        self.assertTrue(any(item['url_name'] == 'order_create' for item in role_dashboard['quick_actions']))
        self.assertTrue(any(item['key'] == 'pending_orders' for item in role_dashboard['risk_entries']))
        self.assertTrue(any(item['key'] == 'low_stock_parts' and item['query'] == 'low=1' for item in role_dashboard['risk_entries']))
        self.assertTrue(any(card['key'] == 'pending_orders' and card.get('url_name') == 'orders_list' for card in role_dashboard['focus_cards']))
        self.assertTrue(any(card['key'] == 'due_within_7_days_count' and card.get('query') == 'sla=warning' for card in role_dashboard['focus_cards']))
        self.assertTrue(any(k['key'] == 'fulfillment_rate' for k in role_dashboard.get('kpi_entries', [])))

    def test_dashboard_should_support_admin_view_role_switch(self):
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='TEST-DASH-0001',
            status='in_warehouse',
            is_active=True,
        )
        maintenance = MaintenanceWorkOrder.objects.create(
            unit=unit,
            sku=self.sku,
            issue_desc='待处理维修',
            status='draft',
            created_by=self.user,
        )
        disposal = UnitDisposalOrder.objects.create(
            action_type='disassemble',
            unit=unit,
            sku=self.sku,
            status='completed',
            created_by=self.user,
            completed_by=self.user,
        )
        disposal_item = UnitDisposalOrderItem.objects.create(
            disposal_order=disposal,
            part=self.part,
            quantity=1,
            returned_quantity=0,
        )
        PartRecoveryInspection.objects.create(
            disposal_order=disposal,
            disposal_item=disposal_item,
            unit=unit,
            sku=self.sku,
            part=self.part,
            quantity=1,
            status='pending',
        )
        PartRecoveryInspection.objects.create(
            disposal_order=disposal,
            disposal_item=disposal_item,
            unit=unit,
            sku=self.sku,
            part=self.part,
            quantity=1,
            status='repair',
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=0,
            status='missing',
            is_active=True,
        )

        resp = self.client.get(reverse('dashboard') + '?view_role=warehouse_staff')
        self.assertEqual(resp.status_code, 200)
        role_dashboard = resp.context['role_dashboard']
        self.assertEqual(role_dashboard['role'], 'warehouse_staff')
        self.assertEqual(role_dashboard['view_type'], 'warehouse')
        self.assertTrue(any(card['key'] == 'warehouse_available_stock' for card in role_dashboard['focus_cards']))
        self.assertTrue(any(card['key'] == 'pending_recovery_inspections' and card['value'] == 1 for card in role_dashboard['focus_cards']))
        self.assertTrue(any(card['key'] == 'repair_recovery_inspections' and card['value'] == 1 for card in role_dashboard['focus_cards']))
        self.assertTrue(any(card['key'] == 'draft_maintenance_work_orders' and card['value'] == 1 for card in role_dashboard['focus_cards']))
        self.assertTrue(role_dashboard['warehouse_insights'])
        self.assertTrue(any(panel['title'] == '高频异常部件' for panel in role_dashboard['warehouse_insights']))
        self.assertTrue(any(panel['title'] == '回件待处理焦点' for panel in role_dashboard['warehouse_insights']))
        first_item = role_dashboard['warehouse_insights'][0]['items'][0]
        self.assertEqual(first_item['url_name'], 'warehouse_reports')
        self.assertIn('part_id=', first_item['query'])
        self.assertEqual(first_item['detail_url_name'], 'part_issue_pool')

    def test_warehouse_reports_should_render_summary_metrics(self):
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='TEST-REPORT-0001',
            status='in_warehouse',
            is_active=True,
        )
        assembly = AssemblyOrder.objects.create(
            sku=self.sku,
            quantity=2,
            status='completed',
            created_by=self.user,
            completed_at=timezone.now(),
        )
        disposal = UnitDisposalOrder.objects.create(
            action_type='disassemble',
            unit=unit,
            sku=self.sku,
            status='completed',
            created_by=self.user,
            completed_by=self.user,
            completed_at=timezone.now(),
        )
        disposal_item = UnitDisposalOrderItem.objects.create(
            disposal_order=disposal,
            part=self.part,
            quantity=1,
            returned_quantity=0,
        )
        PartRecoveryInspection.objects.create(
            disposal_order=disposal,
            disposal_item=disposal_item,
            unit=unit,
            sku=self.sku,
            part=self.part,
            quantity=1,
            status='pending',
        )
        work_order = MaintenanceWorkOrder.objects.create(
            unit=unit,
            sku=self.sku,
            issue_desc='报表测试维修',
            status='draft',
            created_by=self.user,
        )
        MaintenanceWorkOrderItem.objects.create(
            work_order=work_order,
            old_part=self.part,
            new_part=self.part,
            replace_quantity=2,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=2,
            actual_quantity=1,
            status='damaged',
            is_active=True,
        )

        resp = self.client.get(reverse('warehouse_reports'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '仓储报表')
        self.assertContains(resp, '高频损耗部件')
        self.assertContains(resp, self.part.name)
        self.assertContains(resp, reverse('part_issue_pool'))
        self.assertContains(resp, reverse('maintenance_work_orders_list'))
        self.assertContains(resp, reverse('part_recovery_inspections_list'))
        summary = resp.context['summary']
        self.assertEqual(summary['assembly_completed'], 1)
        self.assertEqual(summary['maintenance_draft'], 1)
        self.assertEqual(summary['disposal_completed'], 1)
        self.assertEqual(summary['recovery_pending'], 1)

    def test_warehouse_reports_should_support_sku_and_part_filters(self):
        other_sku = SKU.objects.create(
            code='SKU-REPORT-OTHER',
            name='其他报表套餐',
            category='主题套餐',
            rental_price=Decimal('99.00'),
            deposit=Decimal('20.00'),
            stock=0,
            is_active=True,
        )
        other_part = Part.objects.create(
            name='其他部件',
            spec='X1',
            category='accessory',
            unit='个',
            current_stock=5,
            safety_stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='TEST-REPORT-FILTER-0001',
            status='in_warehouse',
            is_active=True,
        )
        other_unit = InventoryUnit.objects.create(
            sku=other_sku,
            unit_no='TEST-REPORT-FILTER-0002',
            status='in_warehouse',
            is_active=True,
        )
        assembly_other = AssemblyOrder.objects.create(sku=other_sku, quantity=1, status='completed', created_by=self.user, completed_at=timezone.now())
        assembly_self = AssemblyOrder.objects.create(sku=self.sku, quantity=1, status='completed', created_by=self.user, completed_at=timezone.now())
        AssemblyOrderItem.objects.create(
            assembly_order=assembly_self,
            part=self.part,
            quantity_per_set=1,
            required_quantity=1,
            deducted_quantity=1,
        )
        AssemblyOrderItem.objects.create(
            assembly_order=assembly_other,
            part=other_part,
            quantity_per_set=1,
            required_quantity=1,
            deducted_quantity=1,
        )
        maintenance_self = MaintenanceWorkOrder.objects.create(unit=unit, sku=self.sku, issue_desc='当前SKU维修', status='draft', created_by=self.user)
        maintenance_other = MaintenanceWorkOrder.objects.create(unit=other_unit, sku=other_sku, issue_desc='其他SKU维修', status='draft', created_by=self.user)
        MaintenanceWorkOrderItem.objects.create(
            work_order=maintenance_self,
            old_part=self.part,
            new_part=self.part,
            replace_quantity=1,
        )
        MaintenanceWorkOrderItem.objects.create(
            work_order=maintenance_other,
            old_part=other_part,
            new_part=other_part,
            replace_quantity=1,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part, expected_quantity=1, actual_quantity=0, status='missing', is_active=True)
        InventoryUnitPart.objects.create(unit=other_unit, part=other_part, expected_quantity=1, actual_quantity=0, status='missing', is_active=True)

        resp = self.client.get(reverse('warehouse_reports'), {'sku_id': str(self.sku.id), 'part_id': str(self.part.id), 'range': '7'})
        self.assertEqual(resp.status_code, 200)
        summary = resp.context['summary']
        self.assertEqual(summary['assembly_completed'], 1)
        self.assertEqual(summary['maintenance_draft'], 1)
        self.assertEqual(list(resp.context['issue_top_parts'].values_list('id', flat=True)), [self.part.id])

    def test_warehouse_reports_export_should_return_csv(self):
        resp = self.client.get(reverse('warehouse_reports_export') + '?range=7')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        body = resp.content.decode('utf-8-sig')
        self.assertIn('模块,指标,日期,值', body)
        self.assertIn('汇总,累计完成装配', body)

    def test_warehouse_reports_export_should_respect_sku_and_part_filters(self):
        AssemblyOrder.objects.create(sku=self.sku, quantity=1, status='completed', created_by=self.user, completed_at=timezone.now())
        resp = self.client.get(reverse('warehouse_reports_export'), {'range': '7', 'sku_id': str(self.sku.id), 'part_id': str(self.part.id)})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8-sig')
        self.assertIn(f'筛选条件,SKU,{self.sku.code}', body)
        self.assertIn(f'筛选条件,部件,{self.part.name}', body)

    def test_risk_events_list_and_resolve_should_work(self):
        event = RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='medium',
            status='open',
            module='订单',
            title='测试风险事件',
            description='待关闭',
            detected_by=self.user,
        )
        resp = self.client.get(reverse('risk_events_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '测试风险事件')

        resp2 = self.client.post(
            reverse('risk_event_resolve', kwargs={'event_id': event.id}),
            {'note': '已处理'},
            follow=True
        )
        self.assertEqual(resp2.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.status, 'closed')
        self.assertTrue(
            AuditLog.objects.filter(module='风险事件', target=f'风险事件#{event.id}').exists()
        )

    def test_risk_event_claim_should_set_processing_and_assignee(self):
        event = RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='medium',
            status='open',
            module='订单',
            title='待认领风险事件',
            description='待处理',
            detected_by=self.user,
        )
        resp = self.client.post(
            reverse('risk_event_claim', kwargs={'event_id': event.id}),
            {'note': '我来跟进'},
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.status, 'processing')
        self.assertEqual(event.assignee_id, self.user.id)
        self.assertIn('我来跟进', event.processing_note)
        self.assertTrue(
            AuditLog.objects.filter(module='风险事件', target=f'风险事件#{event.id}', action='update').exists()
        )

    def test_risk_event_claim_closed_should_keep_closed(self):
        event = RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='medium',
            status='closed',
            module='订单',
            title='已关闭风险事件',
            description='done',
            detected_by=self.user,
        )
        resp = self.client.post(
            reverse('risk_event_claim', kwargs={'event_id': event.id}),
            {'note': '尝试认领'},
            follow=True
        )
        self.assertEqual(resp.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.status, 'closed')

    def test_risk_events_list_should_support_mine_only_and_export(self):
        other = User.objects.create_user(
            username='risk_other',
            password='test123',
            role='manager',
            is_staff=True,
        )
        mine = RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='medium',
            status='processing',
            module='订单',
            title='我负责事件',
            assignee=self.user,
            detected_by=self.user,
        )
        RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='medium',
            status='processing',
            module='订单',
            title='他人负责事件',
            assignee=other,
            detected_by=self.user,
        )
        resp = self.client.get(reverse('risk_events_list') + '?mine_only=1')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, mine.title)
        self.assertNotContains(resp, '他人负责事件')

        export_resp = self.client.get(reverse('risk_events_list') + '?mine_only=1&export=1')
        self.assertEqual(export_resp.status_code, 200)
        self.assertIn('text/csv', export_resp['Content-Type'])
        content = export_resp.content.decode('utf-8-sig')
        self.assertIn('我负责事件', content)
        self.assertNotIn('他人负责事件', content)

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

    def test_parts_inventory_buttons_should_render_data_attributes_for_quoted_values(self):
        quoted_part = Part.objects.create(
            name="O'Neil支架",
            spec="12'寸",
            category='packaging',
            unit='套',
            current_stock=2,
            safety_stock=1,
            location="A区'1架",
            is_active=True,
        )

        resp = self.client.get(reverse('parts_inventory_list'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'data-id="{quoted_part.id}"')
        self.assertContains(resp, 'data-name="O&#x27;Neil支架"', html=False)
        self.assertContains(resp, 'data-spec="12&#x27;寸"', html=False)
        self.assertContains(resp, 'data-location="A区&#x27;1架"', html=False)
        self.assertContains(resp, 'onclick="showEditModal(this)"', html=False)
        self.assertContains(resp, 'onclick="deletePart(this)"', html=False)

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

    def test_outbound_inventory_dashboard_should_include_part_summary(self):
        SKUComponent.objects.create(sku=self.sku, part=self.part, quantity_per_set=2)
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0003',
            status='in_transit',
            current_location_type='transit',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=2,
            actual_quantity=1,
            status='missing',
            is_active=True,
        )
        resp = self.client.get(reverse('outbound_inventory_dashboard'))
        self.assertEqual(resp.status_code, 200)
        units = list(resp.context['units_page'].object_list)
        target = next((u for u in units if u.id == unit.id), None)
        self.assertIsNotNone(target)
        self.assertEqual(target.part_summary['total'], 1)
        self.assertEqual(target.part_summary['missing'], 1)
        self.assertEqual(target.part_summary['issue'], 1)

    def test_outbound_inventory_dashboard_should_warn_when_part_issue_exists(self):
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0005',
            status='in_transit',
            current_location_type='transit',
            is_active=True,
        )
        UnitMovement.objects.create(
            unit=unit,
            event_type='TRANSFER_SHIPPED',
            status='normal',
            notes='部件异常预警测试',
            operator=self.user,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=0,
            status='missing',
            is_active=True,
        )

        resp = self.client.get(reverse('outbound_inventory_dashboard'))
        self.assertEqual(resp.status_code, 200)
        units = list(resp.context['units_page'].object_list)
        target = next((u for u in units if u.id == unit.id), None)
        self.assertIsNotNone(target)
        self.assertIn('部件异常1项', target.warn_reason)
        self.assertLess(target.health_score, 100)

    def test_outbound_inventory_unit_parts_update_should_persist(self):
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0004',
            status='in_transit',
            current_location_type='transit',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        resp = self.client.post(
            reverse('outbound_inventory_unit_parts_update', kwargs={'unit_id': unit.id}),
            {
                'part_id[]': [str(self.part.id)],
                'status[]': ['damaged'],
                'actual_quantity[]': ['0'],
                'notes[]': ['破损'],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        row = InventoryUnitPart.objects.get(unit=unit, part=self.part)
        self.assertEqual(row.status, 'damaged')
        self.assertEqual(row.actual_quantity, 0)
        self.assertEqual(row.notes, '破损')
        self.assertIsNotNone(row.last_checked_at)
        self.assertTrue(
            AuditLog.objects.filter(module='在外库存', target=unit.unit_no, action='update').exists()
        )

    def test_outbound_inventory_dashboard_part_issue_only_filter(self):
        healthy_unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0006',
            status='in_transit',
            current_location_type='transit',
            is_active=True,
        )
        issue_unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0007',
            status='in_transit',
            current_location_type='transit',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=healthy_unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=issue_unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=0,
            status='missing',
            is_active=True,
        )

        resp = self.client.get(reverse('outbound_inventory_dashboard') + '?part_issue_only=1')
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context['units_page'].object_list)
        ids = [u.id for u in rows]
        self.assertIn(issue_unit.id, ids)
        self.assertNotIn(healthy_unit.id, ids)

    def test_part_issue_pool_should_list_anomalies_and_draft_maintenance(self):
        damaged_unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0010',
            status='in_transit',
            current_location_type='customer',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=damaged_unit,
            part=self.part,
            expected_quantity=2,
            actual_quantity=1,
            status='damaged',
            notes='灯串损坏',
            is_active=True,
        )

        maintenance_unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0011',
            status='maintenance',
            current_location_type='warehouse',
            is_active=True,
        )
        draft_order = MaintenanceWorkOrder.objects.create(
            unit=maintenance_unit,
            sku=self.sku,
            issue_desc='待换布幔',
            status='draft',
            created_by=self.user,
        )
        draft_order.items.create(
            old_part=self.part,
            new_part=self.part,
            replace_quantity=1,
            notes='测试待处理',
        )

        resp = self.client.get(reverse('part_issue_pool'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, damaged_unit.unit_no)
        self.assertContains(resp, draft_order.work_order_no)
        self.assertEqual(resp.context['summary']['issue_units'], 1)
        self.assertEqual(resp.context['summary']['draft_maintenance'], 1)

    def test_part_issue_pool_should_filter_by_status(self):
        damaged_unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0012',
            status='in_transit',
            current_location_type='customer',
            is_active=True,
        )
        missing_unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKUFLOW0001-0013',
            status='in_transit',
            current_location_type='customer',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=damaged_unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=0,
            status='damaged',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=missing_unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=0,
            status='missing',
            is_active=True,
        )

        resp = self.client.get(reverse('part_issue_pool') + '?status=damaged')
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context['anomaly_page'].object_list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].unit_id, damaged_unit.id)

    def test_part_issue_pool_should_support_sku_and_part_filters(self):
        other_sku = SKU.objects.create(
            code='SKU-ISSUE-OTHER',
            name='其他异常套餐',
            category='主题套餐',
            rental_price=Decimal('88.00'),
            deposit=Decimal('20.00'),
            stock=0,
            is_active=True,
        )
        other_part = Part.objects.create(
            name='其他异常部件',
            spec='ISSUE-1',
            category='accessory',
            unit='个',
            current_stock=5,
            safety_stock=1,
            is_active=True,
        )
        damaged_unit = InventoryUnit.objects.create(sku=self.sku, unit_no='ZSY-SKUFLOW0001-0014', status='in_transit', current_location_type='customer', is_active=True)
        other_unit = InventoryUnit.objects.create(sku=other_sku, unit_no='ZSY-SKUFLOW0001-0015', status='in_transit', current_location_type='customer', is_active=True)
        InventoryUnitPart.objects.create(unit=damaged_unit, part=self.part, expected_quantity=1, actual_quantity=0, status='damaged', is_active=True)
        InventoryUnitPart.objects.create(unit=other_unit, part=other_part, expected_quantity=1, actual_quantity=0, status='damaged', is_active=True)

        resp = self.client.get(reverse('part_issue_pool'), {'sku_id': str(self.sku.id), 'part_id': str(self.part.id)})
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context['anomaly_page'].object_list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].unit_id, damaged_unit.id)

    def test_maintenance_work_orders_should_support_sku_and_part_filters(self):
        other_sku = SKU.objects.create(
            code='SKU-MAINT-OTHER',
            name='其他维修套餐',
            category='主题套餐',
            rental_price=Decimal('88.00'),
            deposit=Decimal('20.00'),
            stock=0,
            is_active=True,
        )
        other_part = Part.objects.create(
            name='其他维修部件',
            spec='M-1',
            category='accessory',
            unit='个',
            current_stock=5,
            safety_stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(sku=self.sku, unit_no='ZSY-SKUFLOW0001-0016', status='maintenance', current_location_type='warehouse', is_active=True)
        other_unit = InventoryUnit.objects.create(sku=other_sku, unit_no='ZSY-SKUFLOW0001-0017', status='maintenance', current_location_type='warehouse', is_active=True)
        order = MaintenanceWorkOrder.objects.create(unit=unit, sku=self.sku, issue_desc='当前部件维修', status='draft', created_by=self.user)
        other_order = MaintenanceWorkOrder.objects.create(unit=other_unit, sku=other_sku, issue_desc='其他部件维修', status='draft', created_by=self.user)
        order.items.create(old_part=self.part, new_part=self.part, replace_quantity=1)
        other_order.items.create(old_part=other_part, new_part=other_part, replace_quantity=1)

        resp = self.client.get(reverse('maintenance_work_orders_list'), {'sku_id': str(self.sku.id), 'part_id': str(self.part.id)})
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context['maintenance_orders_page'].object_list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, order.id)

    def test_part_recovery_inspections_should_support_sku_and_part_filters(self):
        other_sku = SKU.objects.create(
            code='SKU-REC-OTHER',
            name='其他回件套餐',
            category='主题套餐',
            rental_price=Decimal('88.00'),
            deposit=Decimal('20.00'),
            stock=0,
            is_active=True,
        )
        other_part = Part.objects.create(
            name='其他回件部件',
            spec='R-1',
            category='accessory',
            unit='个',
            current_stock=5,
            safety_stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(sku=self.sku, unit_no='ZSY-SKUFLOW0001-0018', status='in_warehouse', current_location_type='warehouse', is_active=True)
        other_unit = InventoryUnit.objects.create(sku=other_sku, unit_no='ZSY-SKUFLOW0001-0019', status='in_warehouse', current_location_type='warehouse', is_active=True)
        disposal = UnitDisposalOrder.objects.create(action_type='disassemble', unit=unit, sku=self.sku, status='completed', created_by=self.user, completed_by=self.user, completed_at=timezone.now())
        other_disposal = UnitDisposalOrder.objects.create(action_type='disassemble', unit=other_unit, sku=other_sku, status='completed', created_by=self.user, completed_by=self.user, completed_at=timezone.now())
        disposal_item = UnitDisposalOrderItem.objects.create(disposal_order=disposal, part=self.part, quantity=1, returned_quantity=0)
        other_item = UnitDisposalOrderItem.objects.create(disposal_order=other_disposal, part=other_part, quantity=1, returned_quantity=0)
        PartRecoveryInspection.objects.create(disposal_order=disposal, disposal_item=disposal_item, unit=unit, sku=self.sku, part=self.part, quantity=1, status='pending')
        PartRecoveryInspection.objects.create(disposal_order=other_disposal, disposal_item=other_item, unit=other_unit, sku=other_sku, part=other_part, quantity=1, status='pending')

        resp = self.client.get(reverse('part_recovery_inspections_list'), {'sku_id': str(self.sku.id), 'part_id': str(self.part.id)})
        self.assertEqual(resp.status_code, 200)
        rows = list(resp.context['inspections_page'].object_list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].unit_id, unit.id)

    def test_maintenance_work_orders_should_show_report_drilldown_hint(self):
        unit = InventoryUnit.objects.create(sku=self.sku, unit_no='ZSY-SKUFLOW0001-0020', status='maintenance', current_location_type='warehouse', is_active=True)
        order = MaintenanceWorkOrder.objects.create(unit=unit, sku=self.sku, issue_desc='报表下钻提示', status='draft', created_by=self.user)
        order.items.create(old_part=self.part, new_part=self.part, replace_quantity=1)

        resp = self.client.get(reverse('maintenance_work_orders_list'), {
            'sku_id': str(self.sku.id),
            'part_id': str(self.part.id),
            'from_report': '1',
            'range': '30',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '当前数据来自仓储报表下钻')
        self.assertContains(resp, reverse('warehouse_reports'))


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
        self.assertTrue(
            ApprovalTask.objects.filter(
                action_code='order.force_cancel',
                target_type='order',
                target_id=order.id,
                status='pending',
            ).exists()
        )

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
        self.assertTrue(
            ApprovalTask.objects.filter(
                action_code='transfer.cancel_task',
                target_type='transfer',
                target_id=transfer.id,
                status='pending',
            ).exists()
        )


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

    def test_order_cancel_delivered_should_be_rejected(self):
        order = Order.objects.create(
            customer_name='风险取消客户',
            customer_phone='13800000008',
            delivery_address='广东省深圳市南山区科技园',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            status='delivered',
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
            {'reason': '高风险取消测试'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertFalse(
            RiskEvent.objects.filter(
                event_type='delivered_order_cancel',
                order=order,
                status='open'
            ).exists()
        )
        self.assertFalse(
            ApprovalTask.objects.filter(
                action_code='order.force_cancel',
                target_type='order',
                target_id=order.id,
            ).exists()
        )
        self.assertContains(resp, '无法取消')

    def test_frequent_cancel_should_create_risk_event(self):
        for idx in range(3):
            order = Order.objects.create(
                customer_name=f'取消客户{idx}',
                customer_phone=f'1380000001{idx}',
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
            self.client.post(reverse('order_cancel', kwargs={'order_id': order.id}), {'reason': '频繁取消测试'}, follow=True)

        self.assertTrue(
            RiskEvent.objects.filter(event_type='frequent_cancel', status='open').exists()
        )

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

    def test_settings_run_consistency_check_should_not_write_settings_audit(self):
        before_count = AuditLog.objects.filter(module='系统设置', target='settings').count()
        before_runs = DataConsistencyCheckRun.objects.count()
        resp = self.client.post(
            reverse('settings'),
            {
                'active_tab': 'system',
                'run_consistency_check': '1',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        after_count = AuditLog.objects.filter(module='系统设置', target='settings').count()
        self.assertEqual(before_count, after_count)
        self.assertEqual(DataConsistencyCheckRun.objects.count(), before_runs + 1)

    def test_settings_should_reject_invalid_approval_required_count_map(self):
        SystemSettings.objects.update_or_create(
            key='approval_required_count_map',
            defaults={'value': '{"order.force_cancel": 2}'},
        )
        resp = self.client.post(
            reverse('settings'),
            {
                'active_tab': 'system',
                'approval_required_count_map': '{"order.force_cancel":"abc"}',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '审批层级策略映射配置无效')
        value = SystemSettings.objects.filter(key='approval_required_count_map').values_list('value', flat=True).first()
        self.assertEqual(value, '{"order.force_cancel": 2}')

    @override_settings(
        R2_ACCESS_KEY_ID='ak-test',
        R2_SECRET_ACCESS_KEY='sk-test',
        R2_BUCKET='bucket-test',
        R2_ENDPOINT='https://example.r2.cloudflarestorage.com',
        R2_PUBLIC_DOMAIN='https://pic.yanli.net.cn',
        R2_UPLOAD_PREFIX_SKU='sku-images/',
        R2_ENABLED=True,
    )
    def test_settings_should_render_r2_status_summary(self):
        resp = self.client.get(reverse('settings') + '?tab=system')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Cloudflare R2 图片存储状态')
        self.assertContains(resp, '已就绪')
        self.assertContains(resp, 'bucket-test')
        self.assertContains(resp, 'https://pic.yanli.net.cn')

    def test_settings_test_alert_notify_should_write_notification_audit(self):
        before_count = AuditLog.objects.filter(module='通知中心', target='通知测试').count()
        resp = self.client.post(
            reverse('settings'),
            {
                'active_tab': 'system',
                'test_alert_notify': '1',
                'alert_notify_enabled': '1',
                'alert_notify_min_severity': 'info',
                'alert_notify_webhook_url': '',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            AuditLog.objects.filter(module='通知中心', target='通知测试').count(),
            before_count + 1,
        )
        log = AuditLog.objects.filter(module='通知中心', target='通知测试').order_by('-created_at').first()
        payload = json.loads(log.details)
        self.assertEqual(payload['summary'], '发送告警通知')
        self.assertEqual(payload['extra'].get('source'), 'settings_test')
        self.assertEqual(payload['after'].get('status'), 'skipped')

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


class SKUBOMViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='sku_bom_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.part_a = Part.objects.create(
            name='部件A',
            spec='A1',
            category='accessory',
            unit='个',
            current_stock=10,
            safety_stock=1,
            is_active=True,
        )
        self.part_b = Part.objects.create(
            name='部件B',
            spec='B1',
            category='accessory',
            unit='个',
            current_stock=10,
            safety_stock=1,
            is_active=True,
        )

    def test_sku_create_should_persist_bom_components(self):
        resp = self.client.post(
            reverse('sku_create'),
            {
                'code': 'SKU-BOM-001',
                'name': 'BOM测试套餐',
                'category': '主题套餐',
                'rental_price': '168.00',
                'deposit': '200.00',
                'description': 'test',
                'component_part_id[]': [str(self.part_a.id), str(self.part_b.id)],
                'component_qty[]': ['2', '1'],
                'component_notes[]': ['主体', '辅件'],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        sku = SKU.objects.get(code='SKU-BOM-001')
        comps = SKUComponent.objects.filter(sku=sku).order_by('part_id')
        self.assertEqual(comps.count(), 2)
        self.assertEqual(comps[0].quantity_per_set, 2)
        self.assertEqual(comps[1].quantity_per_set, 1)
        self.assertEqual(InventoryUnit.objects.filter(sku=sku).count(), 0)
        self.assertEqual(InventoryUnitPart.objects.filter(unit__sku=sku, is_active=True).count(), 0)
        self.assertEqual(sku.stock, 0)

    def test_sku_edit_should_replace_bom_components(self):
        create_resp = self.client.post(
            reverse('sku_create'),
            {
                'code': 'SKU-BOM-EDIT',
                'name': '编辑BOM套餐',
                'category': '主题套餐',
                'rental_price': '99.00',
                'deposit': '50.00',
                'description': '',
                'component_part_id[]': [str(self.part_a.id)],
                'component_qty[]': ['1'],
                'component_notes[]': ['旧'],
            },
            follow=True,
        )
        self.assertEqual(create_resp.status_code, 200)
        sku = SKU.objects.get(code='SKU-BOM-EDIT')

        resp = self.client.post(
            reverse('sku_edit', kwargs={'sku_id': sku.id}),
            {
                'code': sku.code,
                'name': sku.name,
                'category': sku.category,
                'rental_price': '120.00',
                'deposit': '80.00',
                'description': '更新',
                'component_part_id[]': [str(self.part_b.id)],
                'component_qty[]': ['3'],
                'component_notes[]': ['新'],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        comps = SKUComponent.objects.filter(sku=sku)
        self.assertEqual(comps.count(), 1)
        comp = comps.first()
        self.assertEqual(comp.part_id, self.part_b.id)
        self.assertEqual(comp.quantity_per_set, 3)
        self.assertEqual(InventoryUnit.objects.filter(sku=sku).count(), 0)

    @override_settings(
        R2_ACCESS_KEY_ID='ak-test',
        R2_SECRET_ACCESS_KEY='sk-test',
        R2_BUCKET='bucket-test',
        R2_ENDPOINT='https://example.r2.cloudflarestorage.com',
        R2_PUBLIC_DOMAIN='https://pic.yanli.net.cn',
        R2_ENABLED=True,
        R2_UPLOAD_PREFIX_SKU='sku-images/',
    )
    def test_sku_upload_token_should_return_r2_payload(self):
        resp = self.client.post(reverse('sku_upload_token'), {'filename': 'cover.jpg'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['data']['key'].startswith('sku-images/'))
        self.assertEqual(data['data']['upload_method'], 'PUT')
        self.assertIn('X-Amz-Algorithm=', data['data']['upload_url'])

    def test_sku_create_should_save_image_key(self):
        resp = self.client.post(
            reverse('sku_create'),
            {
                'code': 'SKU-IMG-KEY-001',
                'name': 'R2图片套餐',
                'category': '主题套餐',
                'rental_price': '188.00',
                'deposit': '100.00',
                'description': 'r2',
                'image_key': 'sku-images/2026/03/demo-cover.jpg',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        sku = SKU.objects.get(code='SKU-IMG-KEY-001')
        self.assertEqual(sku.image_key, 'sku-images/2026/03/demo-cover.jpg')

    def test_sku_edit_should_update_image_key(self):
        sku = SKU.objects.create(
            code='SKU-IMG-EDIT',
            name='图片编辑套餐',
            category='主题套餐',
            rental_price=Decimal('99.00'),
            deposit=Decimal('50.00'),
            stock=0,
            image_key='sku-images/old-key.jpg',
        )
        resp = self.client.post(
            reverse('sku_edit', kwargs={'sku_id': sku.id}),
            {
                'code': sku.code,
                'name': sku.name,
                'category': sku.category,
                'rental_price': '120.00',
                'deposit': '60.00',
                'description': 'updated',
                'image_key': 'sku-images/2026/03/new-key.jpg',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        sku.refresh_from_db()
        self.assertEqual(sku.image_key, 'sku-images/2026/03/new-key.jpg')

    def test_sku_create_should_save_gallery_payload(self):
        resp = self.client.post(
            reverse('sku_create'),
            {
                'code': 'SKU-GALLERY-001',
                'name': '多图套餐',
                'category': '主题套餐',
                'rental_price': '188.00',
                'deposit': '100.00',
                'gallery_payload': json.dumps([
                    {'key': 'sku-images/2026/03/gallery-cover.jpg', 'sort_order': 0, 'is_cover': True},
                    {'key': 'sku-images/2026/03/gallery-side.jpg', 'sort_order': 1, 'is_cover': False},
                ]),
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        sku = SKU.objects.get(code='SKU-GALLERY-001')
        self.assertEqual(sku.images.count(), 2)
        self.assertTrue(sku.images.filter(image_key='sku-images/2026/03/gallery-cover.jpg', is_cover=True).exists())

    def test_sku_edit_should_replace_gallery_payload(self):
        sku = SKU.objects.create(
            code='SKU-GALLERY-EDIT',
            name='多图编辑套餐',
            category='主题套餐',
            rental_price=Decimal('99.00'),
            deposit=Decimal('50.00'),
            stock=0,
        )
        SKUImage.objects.create(sku=sku, image_key='sku-images/old-gallery.jpg', sort_order=0, is_cover=True)
        resp = self.client.post(
            reverse('sku_edit', kwargs={'sku_id': sku.id}),
            {
                'code': sku.code,
                'name': sku.name,
                'category': sku.category,
                'rental_price': '120.00',
                'deposit': '60.00',
                'description': 'updated',
                'gallery_payload': json.dumps([
                    {'key': 'sku-images/2026/03/new-cover.jpg', 'sort_order': 0, 'is_cover': True},
                ]),
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        sku.refresh_from_db()
        self.assertEqual(sku.images.count(), 1)
        self.assertTrue(sku.images.filter(image_key='sku-images/2026/03/new-cover.jpg', is_cover=True).exists())

    def test_sku_assemble_should_deduct_parts_and_create_units(self):
        sku = SKU.objects.create(
            code='SKU-ASM-001',
            name='装配套餐',
            category='主题套餐',
            rental_price=Decimal('199.00'),
            deposit=Decimal('88.00'),
            stock=0,
            is_active=True,
        )
        SKUComponent.objects.create(sku=sku, part=self.part_a, quantity_per_set=2, notes='主体')
        SKUComponent.objects.create(sku=sku, part=self.part_b, quantity_per_set=1, notes='辅件')

        resp = self.client.post(
            reverse('sku_assemble', kwargs={'sku_id': sku.id}),
            {'assembly_quantity': '3', 'assembly_notes': '首批装配'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        sku.refresh_from_db()
        self.part_a.refresh_from_db()
        self.part_b.refresh_from_db()

        self.assertEqual(sku.stock, 3)
        self.assertEqual(InventoryUnit.objects.filter(sku=sku, is_active=True).count(), 3)
        self.assertEqual(InventoryUnitPart.objects.filter(unit__sku=sku, is_active=True).count(), 6)
        self.assertEqual(self.part_a.current_stock, 4)
        self.assertEqual(self.part_b.current_stock, 7)
        assembly = AssemblyOrder.objects.get(sku=sku)
        self.assertEqual(assembly.status, 'completed')
        self.assertEqual(assembly.items.count(), 2)

    def test_maintenance_work_order_complete_should_replace_parts(self):
        sku = SKU.objects.create(
            code='SKU-MWO-001',
            name='维修套餐',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('50.00'),
            stock=0,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-MWO-001-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        damaged_part = Part.objects.create(
            name='损坏主体',
            spec='A2',
            category='accessory',
            unit='个',
            current_stock=0,
            safety_stock=0,
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=damaged_part,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )

        resp = self.client.post(
            reverse('maintenance_work_order_create', kwargs={'unit_id': unit.id}),
            {
                'issue_desc': '主体折损',
                'notes': '换新件',
                'old_part_id[]': [str(damaged_part.id)],
                'new_part_id[]': [str(self.part_b.id)],
                'replace_quantity[]': ['1'],
                'item_notes[]': ['替换辅件'],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        work_order = MaintenanceWorkOrder.objects.get(unit=unit)
        self.assertEqual(work_order.status, 'draft')
        unit.refresh_from_db()
        self.assertEqual(unit.status, 'maintenance')

        resp = self.client.post(
            reverse('maintenance_work_order_complete', kwargs={'work_order_id': work_order.id}),
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        work_order.refresh_from_db()
        unit.refresh_from_db()
        self.part_b.refresh_from_db()
        damaged_row = InventoryUnitPart.objects.get(unit=unit, part=damaged_part)
        replacement_row = InventoryUnitPart.objects.get(unit=unit, part=self.part_b)

        self.assertEqual(work_order.status, 'completed')
        self.assertEqual(unit.status, 'in_warehouse')
        self.assertEqual(self.part_b.current_stock, 9)
        self.assertEqual(damaged_row.status, 'missing')
        self.assertEqual(damaged_row.actual_quantity, 0)
        self.assertEqual(replacement_row.status, 'normal')
        self.assertEqual(replacement_row.actual_quantity, 1)

    def test_maintenance_work_order_should_block_duplicate_draft_for_same_unit(self):
        sku = SKU.objects.create(
            code='SKU-MWO-002',
            name='维修套餐2',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('50.00'),
            stock=0,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-MWO-002-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part_a,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        payload = {
            'issue_desc': '首次维修',
            'old_part_id[]': [str(self.part_a.id)],
            'new_part_id[]': [str(self.part_b.id)],
            'replace_quantity[]': ['1'],
            'item_notes[]': ['test'],
        }
        first = self.client.post(reverse('maintenance_work_order_create', kwargs={'unit_id': unit.id}), payload, follow=True)
        self.assertEqual(first.status_code, 200)
        second = self.client.post(reverse('maintenance_work_order_create', kwargs={'unit_id': unit.id}), payload, follow=True)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(MaintenanceWorkOrder.objects.filter(unit=unit).count(), 1)

    def test_assembly_order_cancel_should_restore_parts_and_disable_created_units(self):
        sku = SKU.objects.create(
            code='SKU-ASM-CANCEL',
            name='装配取消套餐',
            category='主题套餐',
            rental_price=Decimal('188.00'),
            deposit=Decimal('66.00'),
            stock=0,
            is_active=True,
        )
        SKUComponent.objects.create(sku=sku, part=self.part_a, quantity_per_set=1, notes='主体')
        SKUComponent.objects.create(sku=sku, part=self.part_b, quantity_per_set=1, notes='辅件')

        create_resp = self.client.post(
            reverse('sku_assemble', kwargs={'sku_id': sku.id}),
            {'assembly_quantity': '2', 'assembly_notes': '测试取消'},
            follow=True,
        )
        self.assertEqual(create_resp.status_code, 200)
        assembly = AssemblyOrder.objects.get(sku=sku)
        self.assertEqual(assembly.created_units.count(), 2)

        cancel_resp = self.client.post(
            reverse('assembly_order_cancel', kwargs={'assembly_id': assembly.id}),
            {'next': reverse('assembly_orders_list')},
            follow=True,
        )
        self.assertEqual(cancel_resp.status_code, 200)
        assembly.refresh_from_db()
        sku.refresh_from_db()
        self.part_a.refresh_from_db()
        self.part_b.refresh_from_db()
        self.assertEqual(assembly.status, 'cancelled')
        self.assertEqual(sku.stock, 0)
        self.assertEqual(self.part_a.current_stock, 10)
        self.assertEqual(self.part_b.current_stock, 10)
        self.assertEqual(InventoryUnit.objects.filter(source_assembly_order=assembly, is_active=True).count(), 0)

    def test_assembly_orders_export_should_respect_filters(self):
        sku = SKU.objects.create(
            code='SKU-ASM-EXPORT',
            name='装配导出套餐',
            category='主题套餐',
            rental_price=Decimal('188.00'),
            deposit=Decimal('66.00'),
            stock=0,
            is_active=True,
        )
        SKUComponent.objects.create(sku=sku, part=self.part_a, quantity_per_set=1, notes='主体')
        self.client.post(
            reverse('sku_assemble', kwargs={'sku_id': sku.id}),
            {'assembly_quantity': '1', 'assembly_notes': '导出测试'},
            follow=True,
        )
        resp = self.client.get(reverse('assembly_orders_export') + f'?keyword={sku.code}&status=completed')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('装配单号', content)
        self.assertIn(sku.code, content)

    def test_assembly_orders_export_should_support_part_and_creator_keyword(self):
        sku = SKU.objects.create(
            code='SKU-ASM-KEYWORD',
            name='装配关键词套餐',
            category='主题套餐',
            rental_price=Decimal('188.00'),
            deposit=Decimal('66.00'),
            stock=0,
            is_active=True,
        )
        SKUComponent.objects.create(sku=sku, part=self.part_a, quantity_per_set=1, notes='主体')
        self.client.post(
            reverse('sku_assemble', kwargs={'sku_id': sku.id}),
            {'assembly_quantity': '1', 'assembly_notes': '创建人关键词测试'},
            follow=True,
        )
        by_part = self.client.get(reverse('assembly_orders_export') + f'?keyword={self.part_a.name}')
        self.assertEqual(by_part.status_code, 200)
        self.assertIn(sku.code, by_part.content.decode('utf-8-sig'))
        by_creator = self.client.get(reverse('assembly_orders_export') + f'?keyword={self.user.username}')
        self.assertEqual(by_creator.status_code, 200)
        self.assertIn(sku.code, by_creator.content.decode('utf-8-sig'))

    def test_maintenance_work_order_cancel_should_restore_unit_status(self):
        sku = SKU.objects.create(
            code='SKU-MWO-CANCEL',
            name='维修取消套餐',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('50.00'),
            stock=0,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-MWO-CANCEL-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part_a,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        self.client.post(
            reverse('maintenance_work_order_create', kwargs={'unit_id': unit.id}),
            {
                'issue_desc': '取消测试',
                'old_part_id[]': [str(self.part_a.id)],
                'new_part_id[]': [str(self.part_b.id)],
                'replace_quantity[]': ['1'],
                'item_notes[]': ['cancel'],
            },
            follow=True,
        )
        work_order = MaintenanceWorkOrder.objects.get(unit=unit)
        self.assertEqual(work_order.status, 'draft')
        cancel_resp = self.client.post(
            reverse('maintenance_work_order_cancel', kwargs={'work_order_id': work_order.id}),
            {'next': reverse('maintenance_work_orders_list')},
            follow=True,
        )
        self.assertEqual(cancel_resp.status_code, 200)
        work_order.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(work_order.status, 'cancelled')
        self.assertEqual(unit.status, 'in_warehouse')

    def test_maintenance_work_orders_export_should_respect_filters(self):
        sku = SKU.objects.create(
            code='SKU-MWO-EXPORT',
            name='维修导出套餐',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('50.00'),
            stock=0,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-MWO-EXPORT-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part_a,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        self.client.post(
            reverse('maintenance_work_order_create', kwargs={'unit_id': unit.id}),
            {
                'issue_desc': '导出维修',
                'old_part_id[]': [str(self.part_a.id)],
                'new_part_id[]': [str(self.part_b.id)],
                'replace_quantity[]': ['1'],
                'item_notes[]': ['export'],
            },
            follow=True,
        )
        resp = self.client.get(reverse('maintenance_work_orders_export') + '?status=draft&keyword=MWO-EXPORT')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('工单号', content)
        self.assertIn(unit.unit_no, content)

    def test_maintenance_work_orders_export_should_support_part_and_order_keyword(self):
        sku = SKU.objects.create(
            code='SKU-MWO-KEYWORD',
            name='维修关键词套餐',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('50.00'),
            stock=0,
            is_active=True,
        )
        order = Order.objects.create(
            customer_name='维修订单客户',
            customer_phone='13800009999',
            delivery_address='广东省深圳市南山区',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-MWO-KEYWORD-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            current_order=order,
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part_a,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        self.client.post(
            reverse('maintenance_work_order_create', kwargs={'unit_id': unit.id}),
            {
                'issue_desc': '按部件和订单搜索',
                'old_part_id[]': [str(self.part_a.id)],
                'new_part_id[]': [str(self.part_b.id)],
                'replace_quantity[]': ['1'],
                'item_notes[]': ['search'],
            },
            follow=True,
        )
        by_part = self.client.get(reverse('maintenance_work_orders_export') + f'?keyword={self.part_b.name}')
        self.assertEqual(by_part.status_code, 200)
        self.assertIn(unit.unit_no, by_part.content.decode('utf-8-sig'))
        by_order = self.client.get(reverse('maintenance_work_orders_export') + f'?keyword={order.order_no}')
        self.assertEqual(by_order.status_code, 200)
        self.assertIn(unit.unit_no, by_order.content.decode('utf-8-sig'))

    def test_maintenance_work_order_reverse_should_restore_parts_and_stock(self):
        sku = SKU.objects.create(
            code='SKU-MWO-REVERSE',
            name='维修冲销套餐',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('50.00'),
            stock=0,
            is_active=True,
        )
        old_part = Part.objects.create(
            name='旧部件',
            spec='O1',
            category='accessory',
            unit='个',
            current_stock=5,
            safety_stock=1,
            is_active=True,
        )
        new_part = Part.objects.create(
            name='新部件',
            spec='N1',
            category='accessory',
            unit='个',
            current_stock=5,
            safety_stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-MWO-REVERSE-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=old_part,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        self.client.post(
            reverse('maintenance_work_order_create', kwargs={'unit_id': unit.id}),
            {
                'issue_desc': '维修冲销测试',
                'old_part_id[]': [str(old_part.id)],
                'new_part_id[]': [str(new_part.id)],
                'replace_quantity[]': ['1'],
                'item_notes[]': ['replace'],
            },
            follow=True,
        )
        work_order = MaintenanceWorkOrder.objects.get(unit=unit)
        self.client.post(
            reverse('maintenance_work_order_complete', kwargs={'work_order_id': work_order.id}),
            {'next': reverse('maintenance_work_orders_list')},
            follow=True,
        )
        new_part.refresh_from_db()
        self.assertEqual(new_part.current_stock, 4)

        resp = self.client.post(
            reverse('maintenance_work_order_reverse', kwargs={'work_order_id': work_order.id}),
            {'next': reverse('maintenance_work_orders_list')},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        work_order.refresh_from_db()
        unit.refresh_from_db()
        old_part.refresh_from_db()
        new_part.refresh_from_db()
        self.assertEqual(work_order.status, 'reversed')
        self.assertEqual(unit.status, 'in_warehouse')
        self.assertEqual(new_part.current_stock, 5)
        old_row = InventoryUnitPart.objects.get(unit=unit, part=old_part, is_active=True)
        self.assertEqual(old_row.actual_quantity, 1)
        self.assertEqual(old_row.status, 'normal')

    def test_maintenance_work_order_reverse_should_reject_when_unit_has_current_order(self):
        sku = SKU.objects.create(
            code='SKU-MWO-REVERSE-2',
            name='维修冲销套餐2',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('50.00'),
            stock=0,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-MWO-REVERSE-0002',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        work_order = MaintenanceWorkOrder.objects.create(
            unit=unit,
            sku=sku,
            issue_desc='已完成',
            status='completed',
            created_by=self.user,
            completed_by=self.user,
            completed_at=timezone.now(),
        )
        order = Order.objects.create(
            customer_name='占用客户',
            customer_phone='13811112222',
            delivery_address='地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        unit.current_order = order
        unit.save(update_fields=['current_order', 'updated_at'])

        resp = self.client.post(
            reverse('maintenance_work_order_reverse', kwargs={'work_order_id': work_order.id}),
            {'next': reverse('maintenance_work_orders_list')},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, 'completed')

    def test_unit_disposal_disassemble_should_create_pending_recovery_inspections_and_disable_unit(self):
        sku = SKU.objects.create(
            code='SKU-DISASSEMBLE',
            name='拆解套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-DISASSEMBLE-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=2, actual_quantity=2, status='normal', is_active=True)
        InventoryUnitPart.objects.create(unit=unit, part=self.part_b, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.part_a.current_stock = 3
        self.part_a.save(update_fields=['current_stock'])
        self.part_b.current_stock = 4
        self.part_b.save(update_fields=['current_stock'])

        resp = self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'disassemble', 'issue_desc': '拆解回件测试', 'notes': '拆解'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        unit.refresh_from_db()
        sku.refresh_from_db()
        self.part_a.refresh_from_db()
        self.part_b.refresh_from_db()
        order = UnitDisposalOrder.objects.get(unit=unit)
        self.assertEqual(order.action_type, 'disassemble')
        self.assertEqual(order.status, 'completed')
        self.assertFalse(unit.is_active)
        self.assertEqual(unit.status, 'scrapped')
        self.assertEqual(sku.stock, 0)
        self.assertEqual(self.part_a.current_stock, 3)
        self.assertEqual(self.part_b.current_stock, 4)
        inspections = PartRecoveryInspection.objects.filter(disposal_order=order).order_by('part__name')
        self.assertEqual(inspections.count(), 2)
        self.assertEqual(inspections[0].status, 'pending')
        self.assertEqual(inspections[1].status, 'pending')

    def test_part_recovery_inspection_process_returned_should_inbound_stock(self):
        sku = SKU.objects.create(
            code='SKU-RECOVER-RETURN',
            name='回件回库套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-RECOVER-RETURN-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.part_a.current_stock = 2
        self.part_a.save(update_fields=['current_stock'])
        self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'disassemble', 'issue_desc': '拆解待质检', 'notes': '拆解'},
            follow=True,
        )
        inspection = PartRecoveryInspection.objects.get(part=self.part_a)
        resp = self.client.post(
            reverse('part_recovery_inspection_process', kwargs={'inspection_id': inspection.id}),
            {'action_type': 'returned', 'notes': '合格回库'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        inspection.refresh_from_db()
        self.part_a.refresh_from_db()
        self.assertEqual(inspection.status, 'returned')
        self.assertEqual(self.part_a.current_stock, 3)

    def test_part_recovery_inspection_process_repair_should_not_inbound_stock(self):
        sku = SKU.objects.create(
            code='SKU-RECOVER-REPAIR',
            name='回件待修套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-RECOVER-REPAIR-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.part_a.current_stock = 2
        self.part_a.save(update_fields=['current_stock'])
        self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'disassemble', 'issue_desc': '拆解待维修', 'notes': '拆解'},
            follow=True,
        )
        inspection = PartRecoveryInspection.objects.get(part=self.part_a)
        resp = self.client.post(
            reverse('part_recovery_inspection_process', kwargs={'inspection_id': inspection.id}),
            {'action_type': 'repair', 'notes': '转维修池'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        inspection.refresh_from_db()
        self.part_a.refresh_from_db()
        self.assertEqual(inspection.status, 'repair')
        self.assertEqual(self.part_a.current_stock, 2)

    def test_part_recovery_inspection_repair_then_returned_should_inbound_stock(self):
        sku = SKU.objects.create(
            code='SKU-RECOVER-REPAIR-RETURN',
            name='待修后回库套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-RECOVER-REPAIR-RETURN-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.part_a.current_stock = 1
        self.part_a.save(update_fields=['current_stock'])
        self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'disassemble', 'issue_desc': '拆解待修后回库', 'notes': '拆解'},
            follow=True,
        )
        inspection = PartRecoveryInspection.objects.get(part=self.part_a)
        self.client.post(
            reverse('part_recovery_inspection_process', kwargs={'inspection_id': inspection.id}),
            {'action_type': 'repair', 'notes': '先转待维修'},
            follow=True,
        )
        self.part_a.refresh_from_db()
        self.assertEqual(self.part_a.current_stock, 1)
        resp = self.client.post(
            reverse('part_recovery_inspection_process', kwargs={'inspection_id': inspection.id}),
            {'action_type': 'returned', 'notes': '维修后回库'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        inspection.refresh_from_db()
        self.part_a.refresh_from_db()
        self.assertEqual(inspection.status, 'returned')
        self.assertEqual(self.part_a.current_stock, 2)

    def test_part_recovery_inspection_repair_then_scrapped_should_not_inbound_stock(self):
        sku = SKU.objects.create(
            code='SKU-RECOVER-REPAIR-SCRAP',
            name='待修后报废套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-RECOVER-REPAIR-SCRAP-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.part_a.current_stock = 1
        self.part_a.save(update_fields=['current_stock'])
        self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'disassemble', 'issue_desc': '拆解待修后报废', 'notes': '拆解'},
            follow=True,
        )
        inspection = PartRecoveryInspection.objects.get(part=self.part_a)
        self.client.post(
            reverse('part_recovery_inspection_process', kwargs={'inspection_id': inspection.id}),
            {'action_type': 'repair', 'notes': '先转待维修'},
            follow=True,
        )
        resp = self.client.post(
            reverse('part_recovery_inspection_process', kwargs={'inspection_id': inspection.id}),
            {'action_type': 'scrapped', 'notes': '维修失败报废'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        inspection.refresh_from_db()
        self.part_a.refresh_from_db()
        self.assertEqual(inspection.status, 'scrapped')
        self.assertEqual(self.part_a.current_stock, 1)

    def test_part_recovery_inspections_export_should_respect_filters(self):
        sku = SKU.objects.create(
            code='SKU-RECOVER-EXPORT',
            name='回件导出套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-RECOVER-EXPORT-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'disassemble', 'issue_desc': '回件导出', 'notes': '拆解'},
            follow=True,
        )
        resp = self.client.get(reverse('part_recovery_inspections_export') + '?status=pending&keyword=RECOVER-EXPORT')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('来源处置单', content)
        self.assertIn(unit.unit_no, content)

    def test_part_recovery_inspections_export_should_support_note_keyword(self):
        sku = SKU.objects.create(
            code='SKU-RECOVER-KEYWORD',
            name='回件关键词套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-RECOVER-KEYWORD-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'disassemble', 'issue_desc': '回件备注搜索', 'notes': '备注关键词-回库待检'},
            follow=True,
        )
        inspection = PartRecoveryInspection.objects.get(unit=unit, part=self.part_a)
        inspection.notes = '备注关键词-回库待检'
        inspection.save(update_fields=['notes', 'updated_at'])
        by_note = self.client.get(reverse('part_recovery_inspections_export') + '?keyword=备注关键词-回库待检')
        self.assertEqual(by_note.status_code, 200)
        self.assertIn(unit.unit_no, by_note.content.decode('utf-8-sig'))
        by_part = self.client.get(reverse('part_recovery_inspections_export') + f'?keyword={self.part_a.name}')
        self.assertEqual(by_part.status_code, 200)
        self.assertIn(unit.unit_no, by_part.content.decode('utf-8-sig'))

    def test_unit_disposal_scrap_should_disable_unit_without_returning_parts(self):
        sku = SKU.objects.create(
            code='SKU-SCRAP',
            name='报废套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-SCRAP-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.part_a.current_stock = 7
        self.part_a.save(update_fields=['current_stock'])

        resp = self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'scrap', 'issue_desc': '报废测试', 'notes': '损坏严重'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        unit.refresh_from_db()
        sku.refresh_from_db()
        self.part_a.refresh_from_db()
        order = UnitDisposalOrder.objects.get(unit=unit)
        self.assertEqual(order.action_type, 'scrap')
        self.assertEqual(order.status, 'completed')
        self.assertFalse(unit.is_active)
        self.assertEqual(unit.status, 'scrapped')
        self.assertEqual(sku.stock, 0)
        self.assertEqual(self.part_a.current_stock, 7)

    def test_unit_disposal_orders_export_should_respect_filters(self):
        sku = SKU.objects.create(
            code='SKU-DIS-EXPORT',
            name='处置导出套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-DIS-EXPORT-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'scrap', 'issue_desc': '导出处置', 'notes': 'export'},
            follow=True,
        )
        resp = self.client.get(reverse('unit_disposal_orders_export') + '?action_type=scrap&status=completed&keyword=DIS-EXPORT')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('工单号', content)
        self.assertIn(unit.unit_no, content)

    def test_unit_disposal_orders_export_should_support_part_and_issue_keyword(self):
        sku = SKU.objects.create(
            code='SKU-DIS-KEYWORD',
            name='处置关键词套餐',
            category='主题套餐',
            rental_price=Decimal('100.00'),
            deposit=Decimal('30.00'),
            stock=1,
            is_active=True,
        )
        unit = InventoryUnit.objects.create(
            sku=sku,
            unit_no='ZSY-SKU-DIS-KEYWORD-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(unit=unit, part=self.part_a, expected_quantity=1, actual_quantity=1, status='normal', is_active=True)
        self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'scrap', 'issue_desc': '按订单和部件搜索', 'notes': 'dispose-search'},
            follow=True,
        )
        by_part = self.client.get(reverse('unit_disposal_orders_export') + f'?keyword={self.part_a.name}')
        self.assertEqual(by_part.status_code, 200)
        self.assertIn(unit.unit_no, by_part.content.decode('utf-8-sig'))
        by_issue = self.client.get(reverse('unit_disposal_orders_export') + '?keyword=按订单和部件搜索')
        self.assertEqual(by_issue.status_code, 200)
        self.assertIn(unit.unit_no, by_issue.content.decode('utf-8-sig'))


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


class ApprovalFlowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='approval_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.admin2 = User.objects.create_user(
            username='approval_admin2',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.manager = User.objects.create_user(
            username='approval_manager',
            password='test123',
            role='manager',
            is_staff=True,
        )
        self.warehouse_manager = User.objects.create_user(
            username='approval_wh_manager',
            password='test123',
            role='warehouse_manager',
            is_staff=True,
        )
        self.sku = SKU.objects.create(
            code='SKU-APR-001',
            name='审批测试套餐',
            category='主题套餐',
            rental_price=Decimal('88.00'),
            deposit=Decimal('50.00'),
            stock=5,
            is_active=True,
        )
        self.part = Part.objects.create(
            name='审批测试部件',
            spec='APR-1',
            category='accessory',
            unit='个',
            current_stock=10,
            safety_stock=1,
            is_active=True,
        )
        self.source_order = Order.objects.create(
            customer_name='来源客户',
            customer_phone='13900001111',
            delivery_address='广东省广州市天河区1号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.admin,
        )
        self.target_order = Order.objects.create(
            customer_name='目标客户',
            customer_phone='13900002222',
            delivery_address='广东省广州市越秀区2号',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='pending',
            created_by=self.admin,
        )
        OrderItem.objects.create(
            order=self.source_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('88.00'),
        )
        OrderItem.objects.create(
            order=self.target_order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('88.00'),
        )
        self.transfer = Transfer.objects.create(
            order_from=self.source_order,
            order_to=self.target_order,
            sku=self.sku,
            quantity=1,
            gap_days=6,
            status='pending',
            created_by=self.admin,
        )

    def test_manager_cancel_order_should_create_approval_task(self):
        self.client.force_login(self.manager)
        resp = self.client.post(
            reverse('order_cancel', kwargs={'order_id': self.target_order.id}),
            {'reason': '审批测试取消'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.target_order.refresh_from_db()
        self.assertEqual(self.target_order.status, 'pending')
        task = ApprovalTask.objects.filter(
            action_code='order.force_cancel',
            target_type='order',
            target_id=self.target_order.id,
            status='pending',
        ).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.requested_by_id, self.manager.id)

    def test_manager_cancel_order_should_use_action_required_count_map(self):
        SystemSettings.objects.update_or_create(
            key='approval_required_count_map',
            defaults={'value': '{"order.force_cancel": 2, "transfer.cancel_task": 1}'},
        )
        SystemSettings.objects.update_or_create(
            key='approval_required_count_default',
            defaults={'value': '1'},
        )
        self.client.force_login(self.manager)
        resp = self.client.post(
            reverse('order_cancel', kwargs={'order_id': self.target_order.id}),
            {'reason': '审批层级映射测试'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        task = ApprovalTask.objects.filter(
            action_code='order.force_cancel',
            target_type='order',
            target_id=self.target_order.id,
            status='pending',
        ).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.required_review_count, 2)

    def test_admin_approve_order_cancel_should_execute(self):
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.target_order.id,
            target_label=self.target_order.order_no,
            summary='申请取消',
            payload={
                'order_id': self.target_order.id,
                'order_no': self.target_order.order_no,
                'reason': '审批通过取消',
            },
            requested_by=self.manager,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('approval_task_approve', kwargs={'task_id': task.id}),
            {'note': '同意'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.target_order.refresh_from_db()
        self.assertEqual(task.status, 'executed')
        self.assertEqual(self.target_order.status, 'cancelled')

    def test_manager_cancel_transfer_should_create_approval_and_admin_can_execute(self):
        self.client.force_login(self.manager)
        resp = self.client.post(
            reverse('transfer_cancel', kwargs={'transfer_id': self.transfer.id}),
            {'reason': '审批取消转寄'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        task = ApprovalTask.objects.filter(
            action_code='transfer.cancel_task',
            target_type='transfer',
            target_id=self.transfer.id,
            status='pending',
        ).first()
        self.assertIsNotNone(task)

        self.client.force_login(self.admin)
        resp2 = self.client.post(reverse('approval_task_approve', kwargs={'task_id': task.id}), follow=True)
        self.assertEqual(resp2.status_code, 200)
        task.refresh_from_db()
        self.transfer.refresh_from_db()
        self.assertEqual(task.status, 'executed')
        self.assertEqual(self.transfer.status, 'cancelled')

    def test_manager_unit_disposal_should_create_approval_task(self):
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKU-APPROVAL-DISPOSE-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        self.client.force_login(self.manager)
        resp = self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'scrap', 'issue_desc': '审批报废', 'notes': '待审批'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        unit.refresh_from_db()
        self.assertTrue(unit.is_active)
        self.assertFalse(UnitDisposalOrder.objects.filter(unit=unit).exists())
        task = ApprovalTask.objects.filter(
            action_code='unit.dispose',
            target_type='inventory_unit',
            target_id=unit.id,
            status='pending',
        ).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.requested_by_id, self.manager.id)

    def test_manager_unit_disposal_should_use_scrap_specific_required_count(self):
        SystemSettings.objects.update_or_create(
            key='approval_required_count_unit_dispose',
            defaults={'value': '1'},
        )
        SystemSettings.objects.update_or_create(
            key='approval_required_count_unit_scrap',
            defaults={'value': '3'},
        )
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKU-APPROVAL-DISPOSE-SCRAP-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        self.client.force_login(self.manager)
        resp = self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'scrap', 'issue_desc': '审批报废层级', 'notes': 'scrap-level'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        task = ApprovalTask.objects.filter(
            action_code='unit.dispose',
            target_type='inventory_unit',
            target_id=unit.id,
            status='pending',
        ).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.required_review_count, 3)

    def test_manager_unit_disposal_should_allow_map_override_for_disassemble(self):
        SystemSettings.objects.update_or_create(
            key='approval_required_count_unit_disassemble',
            defaults={'value': '1'},
        )
        SystemSettings.objects.update_or_create(
            key='approval_required_count_map',
            defaults={'value': '{"unit.dispose.disassemble": 2, "unit.dispose.scrap": 3}'},
        )
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKU-APPROVAL-DISPOSE-DIS-0001',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        self.client.force_login(self.manager)
        resp = self.client.post(
            reverse('unit_disposal_create', kwargs={'unit_id': unit.id}),
            {'action_type': 'disassemble', 'issue_desc': '审批拆解层级', 'notes': 'disassemble-level'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        task = ApprovalTask.objects.filter(
            action_code='unit.dispose',
            target_type='inventory_unit',
            target_id=unit.id,
            status='pending',
        ).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.required_review_count, 2)

    def test_admin_approve_unit_disposal_should_execute(self):
        unit = InventoryUnit.objects.create(
            sku=self.sku,
            unit_no='ZSY-SKU-APPROVAL-DISPOSE-0002',
            status='in_warehouse',
            current_location_type='warehouse',
            is_active=True,
        )
        InventoryUnitPart.objects.create(
            unit=unit,
            part=self.part,
            expected_quantity=1,
            actual_quantity=1,
            status='normal',
            is_active=True,
        )
        task = ApprovalTask.objects.create(
            action_code='unit.dispose',
            module='在外库存',
            target_type='inventory_unit',
            target_id=unit.id,
            target_label=unit.unit_no,
            summary='审批报废单套',
            payload={
                'unit_id': unit.id,
                'unit_no': unit.unit_no,
                'action_type': 'scrap',
                'issue_desc': '审批执行报废',
                'notes': '审批通过后执行',
            },
            requested_by=self.manager,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('approval_task_approve', kwargs={'task_id': task.id}),
            {'note': '同意报废'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(task.status, 'executed')
        self.assertFalse(unit.is_active)
        self.assertEqual(unit.status, 'scrapped')
        self.assertTrue(UnitDisposalOrder.objects.filter(unit=unit, status='completed').exists())

    def test_admin_remind_pending_approval_should_increment_counter(self):
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.target_order.id,
            target_label=self.target_order.order_no,
            summary='申请取消',
            payload={'order_id': self.target_order.id},
            requested_by=self.manager,
        )
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('approval_task_remind', kwargs={'task_id': task.id}),
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.remind_count, 1)
        self.assertIsNotNone(task.last_reminded_at)

    def test_approval_sla_remind_command_should_increment_overdue_tasks(self):
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.target_order.id,
            target_label=self.target_order.order_no,
            summary='申请取消',
            payload={'order_id': self.target_order.id},
            requested_by=self.manager,
        )
        ApprovalTask.objects.filter(id=task.id).update(created_at=timezone.now() - timedelta(hours=30))
        out = StringIO()
        call_command('approval_sla_remind', '--hours=24', stdout=out)
        task.refresh_from_db()
        self.assertEqual(task.remind_count, 1)
        self.assertIn('reminded=1', out.getvalue())

    def test_two_level_approval_should_execute_only_after_second_reviewer(self):
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.target_order.id,
            target_label=self.target_order.order_no,
            summary='二级审批取消',
            payload={
                'order_id': self.target_order.id,
                'order_no': self.target_order.order_no,
                'reason': '二级审批测试',
            },
            requested_by=self.manager,
            required_review_count=2,
        )
        self.client.force_login(self.admin)
        resp1 = self.client.post(reverse('approval_task_approve', kwargs={'task_id': task.id}), follow=True)
        self.assertEqual(resp1.status_code, 200)
        task.refresh_from_db()
        self.target_order.refresh_from_db()
        self.assertEqual(task.status, 'pending')
        self.assertEqual(task.current_review_count, 1)
        self.assertEqual(self.target_order.status, 'pending')

        # 同一审批人重复审批应被拒绝
        resp_dup = self.client.post(reverse('approval_task_approve', kwargs={'task_id': task.id}), follow=True)
        self.assertEqual(resp_dup.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.current_review_count, 1)

        self.client.force_login(self.admin2)
        resp2 = self.client.post(reverse('approval_task_approve', kwargs={'task_id': task.id}), follow=True)
        self.assertEqual(resp2.status_code, 200)
        task.refresh_from_db()
        self.target_order.refresh_from_db()
        self.assertEqual(task.status, 'executed')
        self.assertEqual(task.current_review_count, 2)
        self.assertEqual(self.target_order.status, 'cancelled')

    def test_approval_sla_remind_dry_run_should_not_change_counter(self):
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.target_order.id,
            target_label=self.target_order.order_no,
            summary='申请取消',
            payload={'order_id': self.target_order.id},
            requested_by=self.manager,
        )
        ApprovalTask.objects.filter(id=task.id).update(created_at=timezone.now() - timedelta(hours=30))
        out = StringIO()
        call_command('approval_sla_remind', '--hours=24', '--dry-run', stdout=out)
        task.refresh_from_db()
        self.assertEqual(task.remind_count, 0)
        self.assertIn('mode=dry_run', out.getvalue())

    def test_approval_remind_overdue_view_should_batch_increment(self):
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.target_order.id,
            target_label=self.target_order.order_no,
            summary='申请取消',
            payload={'order_id': self.target_order.id},
            requested_by=self.manager,
        )
        ApprovalTask.objects.filter(id=task.id).update(created_at=timezone.now() - timedelta(hours=30))
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('approval_remind_overdue'), follow=True)
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.remind_count, 1)

    def test_approvals_list_should_support_mine_only_reviewable_only_and_export(self):
        mine_task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.target_order.id,
            target_label=self.target_order.order_no,
            summary='我发起审批',
            payload={'order_id': self.target_order.id},
            requested_by=self.admin,
        )
        other_task = ApprovalTask.objects.create(
            action_code='transfer.cancel_task',
            module='转寄',
            target_type='transfer',
            target_id=self.transfer.id,
            target_label=f'任务#{self.transfer.id}',
            summary='他人发起审批',
            payload={'transfer_id': self.transfer.id},
            requested_by=self.manager,
        )

        self.client.force_login(self.admin)
        mine_resp = self.client.get(reverse('approvals_list') + '?mine_only=1')
        self.assertEqual(mine_resp.status_code, 200)
        self.assertContains(mine_resp, mine_task.task_no)
        self.assertNotContains(mine_resp, other_task.task_no)

        reviewable_resp = self.client.get(reverse('approvals_list') + '?reviewable_only=1')
        self.assertEqual(reviewable_resp.status_code, 200)
        self.assertContains(reviewable_resp, other_task.task_no)
        self.assertNotContains(reviewable_resp, mine_task.task_no)

        export_resp = self.client.get(reverse('approvals_list') + '?reviewable_only=1&export=1')
        self.assertEqual(export_resp.status_code, 200)
        self.assertIn('text/csv', export_resp['Content-Type'])
        content = export_resp.content.decode('utf-8-sig')
        self.assertIn(other_task.task_no, content)
        self.assertNotIn(mine_task.task_no, content)


class FinanceCenterTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='finance_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.login(username='finance_admin', password='test123')
        self.sku = SKU.objects.create(
            code='SKU-FIN-001',
            name='财务中心套餐',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('80.00'),
            stock=2,
            is_active=True,
        )
        self.order = Order.objects.create(
            customer_name='财务测试客户',
            customer_phone='18888880001',
            delivery_address='广东省深圳市福田区',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='pending',
            total_amount=Decimal('120.00'),
            balance=Decimal('120.00'),
            created_by=self.admin,
        )
        OrderItem.objects.create(
            order=self.order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=Decimal('120.00'),
        )

    def test_order_detail_should_support_manual_finance_add(self):
        resp = self.client.post(
            reverse('order_finance_add', kwargs={'order_id': self.order.id}),
            {
                'transaction_type': 'penalty_charge',
                'amount': '66.50',
                'notes': '损坏扣罚',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        tx = FinanceTransaction.objects.filter(order=self.order).order_by('-id').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.transaction_type, 'penalty_charge')
        self.assertEqual(tx.amount, Decimal('66.50'))

    def test_finance_transactions_list_export_should_work(self):
        FinanceTransaction.objects.create(
            order=self.order,
            transaction_type='deposit_received',
            amount=Decimal('80.00'),
            notes='测试',
            created_by=self.admin,
        )
        resp = self.client.get(reverse('finance_transactions_list') + '?export=1')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/csv; charset=utf-8-sig')
        self.assertIn('finance_transactions.csv', resp['Content-Disposition'])


class TransferRecommendationReplayTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='replay_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.login(username='replay_admin', password='test123')
        for key, value in (
            ('buffer_days', '1'),
            ('max_transfer_gap_days', '15'),
        ):
            SystemSettings.objects.update_or_create(key=key, defaults={'value': value})
        self.sku = SKU.objects.create(
            code='SKU-REPLAY-1',
            name='推荐回放套餐',
            category='主题套餐',
            rental_price=Decimal('168.00'),
            deposit=Decimal('200.00'),
            stock=5,
            is_active=True,
        )
        self.source = Order.objects.create(
            customer_name='来源用户',
            customer_phone='13600000001',
            delivery_address='广东省广州市天河区体育西路1号',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.admin,
            ship_date=date.today() - timedelta(days=1),
            return_date=date.today() + timedelta(days=1),
        )
        self.target = Order.objects.create(
            customer_name='目标用户',
            customer_phone='13600000002',
            delivery_address='广东省广州市越秀区中山一路2号',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='pending',
            created_by=self.admin,
        )
        OrderItem.objects.create(
            order=self.source,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price,
        )
        OrderItem.objects.create(
            order=self.target,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price,
        )
        TransferAllocation.objects.create(
            source_order=self.source,
            target_order=self.target,
            sku=self.sku,
            quantity=1,
            target_event_date=self.target.event_date,
            window_start=self.target.event_date - timedelta(days=5),
            window_end=self.target.event_date + timedelta(days=5),
            distance_score=Decimal('10.0000'),
            status='locked',
            created_by=self.admin,
        )

    def test_transfer_recommend_should_write_replay_log(self):
        resp = self.client.post(
            reverse('transfer_recommend'),
            {'rows[]': [f'{self.target.id}:{self.sku.id}']},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        log = TransferRecommendationLog.objects.filter(order=self.target, sku=self.sku).order_by('-id').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.trigger_type, 'recommend')
        self.assertTrue((log.score_summary or {}).get('weights'))
        self.assertGreaterEqual(int((log.score_summary or {}).get('candidate_count', 0)), 1)
        self.assertTrue(log.candidates and log.candidates[0].get('score_total') is not None)

    def test_transfer_recommendation_logs_page_should_render(self):
        TransferRecommendationLog.objects.create(
            order=self.target,
            sku=self.sku,
            trigger_type='recommend',
            target_event_date=self.target.event_date,
            target_address=self.target.delivery_address,
            before_source_order_ids=[self.source.id],
            selected_source_order_id=self.source.id,
            selected_source_order_no=self.source.order_no,
            warehouse_needed=0,
            candidates=[],
            score_summary={'candidate_count': 1},
            operator=self.admin,
        )
        resp = self.client.get(reverse('transfer_recommendation_logs'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.target.order_no)
        self.assertContains(resp, '查看评分')

    def test_transfer_recommendation_logs_export_should_return_csv(self):
        TransferRecommendationLog.objects.create(
            order=self.target,
            sku=self.sku,
            trigger_type='recommend',
            target_event_date=self.target.event_date,
            target_address=self.target.delivery_address,
            before_source_order_ids=[self.source.id],
            selected_source_order_id=self.source.id,
            selected_source_order_no=self.source.order_no,
            warehouse_needed=0,
            candidates=[],
            score_summary={'candidate_count': 1},
            operator=self.admin,
        )
        resp = self.client.get(reverse('transfer_recommendation_logs') + '?trigger_type=recommend&export=1')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('目标订单号', content)
        self.assertIn('命中排名', content)
        self.assertIn(self.target.order_no, content)
        self.assertIn('转寄中心重推', content)

    def test_transfer_recommendation_logs_page_should_support_decision_filter(self):
        TransferRecommendationLog.objects.create(
            order=self.target,
            sku=self.sku,
            trigger_type='recommend',
            target_event_date=self.target.event_date,
            target_address=self.target.delivery_address,
            before_source_order_ids=[],
            selected_source_order_id=None,
            selected_source_order_no='',
            warehouse_needed=1,
            candidates=[],
            score_summary={'candidate_count': 0},
            operator=self.admin,
        )
        resp = self.client.get(reverse('transfer_recommendation_logs') + '?decision_type=warehouse')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '仓库补量')


class FinanceReconciliationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='recon_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.login(username='recon_admin', password='test123')
        self.sku = SKU.objects.create(
            code='SKU-REC-001',
            name='对账套餐',
            category='主题套餐',
            rental_price=Decimal('200.00'),
            deposit=Decimal('100.00'),
            stock=2,
            is_active=True,
        )
        self.order = Order.objects.create(
            customer_name='对账客户',
            customer_phone='17700000001',
            delivery_address='广东省深圳市南山区',
            event_date=date.today() + timedelta(days=5),
            rental_days=1,
            status='completed',
            total_amount=Decimal('200.00'),
            deposit_paid=Decimal('100.00'),
            balance=Decimal('0.00'),
            created_by=self.admin,
        )
        OrderItem.objects.create(
            order=self.order,
            sku=self.sku,
            quantity=1,
            rental_price=self.sku.rental_price,
            deposit=self.sku.deposit,
            subtotal=self.sku.rental_price,
        )
        FinanceTransaction.objects.create(order=self.order, transaction_type='deposit_received', amount=Decimal('100.00'), created_by=self.admin)
        FinanceTransaction.objects.create(order=self.order, transaction_type='balance_received', amount=Decimal('200.00'), created_by=self.admin)
        FinanceTransaction.objects.create(order=self.order, transaction_type='deposit_refund', amount=Decimal('100.00'), created_by=self.admin)

    def test_finance_reconciliation_page_should_render(self):
        resp = self.client.get(reverse('finance_reconciliation'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.order.order_no)
        self.assertContains(resp, '一致')
        self.assertContains(resp, '最大差异金额')

    def test_finance_reconciliation_mismatch_filter_should_work(self):
        FinanceTransaction.objects.create(order=self.order, transaction_type='deposit_refund', amount=Decimal('10.00'), created_by=self.admin)
        resp = self.client.get(reverse('finance_reconciliation') + '?mismatch_only=1')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.order.order_no)

    def test_finance_reconciliation_should_render_suggestions_for_mismatch(self):
        FinanceTransaction.objects.create(
            order=self.order,
            transaction_type='deposit_refund',
            amount=Decimal('10.00'),
            created_by=self.admin
        )
        resp = self.client.get(reverse('finance_reconciliation'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '退押超额')

    def test_finance_reconciliation_raise_risk_should_create_event(self):
        FinanceTransaction.objects.create(
            order=self.order,
            transaction_type='deposit_refund',
            amount=Decimal('10.00'),
            created_by=self.admin
        )
        before = RiskEvent.objects.count()
        resp = self.client.post(
            reverse('finance_reconciliation_raise_risk', kwargs={'order_id': self.order.id}),
            {'note': '对账异常测试'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(RiskEvent.objects.count(), before + 1)
        event = RiskEvent.objects.order_by('-id').first()
        self.assertIn('财务对账异常', event.title)

    def test_finance_reconciliation_export_should_include_suggestions_and_diff_summary(self):
        FinanceTransaction.objects.create(
            order=self.order,
            transaction_type='deposit_refund',
            amount=Decimal('10.00'),
            created_by=self.admin
        )
        resp = self.client.get(reverse('finance_reconciliation') + '?mismatch_only=1&export=1')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('建议', content)
        self.assertIn('差异摘要', content)
        self.assertIn(self.order.order_no, content)
        self.assertIn('退押超额', content)
        self.assertIn('退押', content)

    def test_finance_reconciliation_should_support_mismatch_field_and_min_amount_filter(self):
        FinanceTransaction.objects.create(
            order=self.order,
            transaction_type='deposit_refund',
            amount=Decimal('10.00'),
            created_by=self.admin
        )
        # 退押差异=110-100=10，命中 refund 且满足最小差异 5
        resp = self.client.get(reverse('finance_reconciliation') + '?mismatch_only=1&mismatch_field=refund&min_diff_amount=5')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.order.order_no)
        # 提高阈值后应过滤掉
        resp2 = self.client.get(reverse('finance_reconciliation') + '?mismatch_only=1&mismatch_field=refund&min_diff_amount=20')
        self.assertEqual(resp2.status_code, 200)
        self.assertNotContains(resp2, self.order.order_no)

    def test_finance_reconciliation_context_should_include_mismatch_stats(self):
        FinanceTransaction.objects.create(
            order=self.order,
            transaction_type='deposit_refund',
            amount=Decimal('10.00'),
            created_by=self.admin
        )
        resp = self.client.get(reverse('finance_reconciliation') + '?mismatch_only=1')
        self.assertEqual(resp.status_code, 200)
        stats = resp.context['mismatch_stats']
        self.assertGreaterEqual(int(stats.get('abnormal', 0)), 1)
        self.assertGreaterEqual(int(stats.get('refund_count', 0)), 1)


class OpsCenterTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='ops_admin',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.login(username='ops_admin', password='test123')
        self.sku = SKU.objects.create(
            code='SKU-OPS-001',
            name='运维测试套餐',
            category='主题套餐',
            rental_price=Decimal('168.00'),
            deposit=Decimal('200.00'),
            stock=3,
            is_active=True,
        )
        self.order_from = Order.objects.create(
            customer_name='来源',
            customer_phone='18800000001',
            delivery_address='广东省深圳市南山区',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.admin,
        )
        self.order_to = Order.objects.create(
            customer_name='目标',
            customer_phone='18800000002',
            delivery_address='广东省深圳市福田区',
            event_date=date.today() + timedelta(days=6),
            rental_days=1,
            status='pending',
            created_by=self.admin,
        )
        self.transfer = Transfer.objects.create(
            order_from=self.order_from,
            order_to=self.order_to,
            sku=self.sku,
            quantity=1,
            gap_days=6,
            status='pending',
            created_by=self.admin,
        )
        Transfer.objects.filter(id=self.transfer.id).update(
            created_at=timezone.now() - timedelta(hours=50)
        )
        self.approval = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.order_to.id,
            target_label=self.order_to.order_no,
            summary='运维超时审批',
            payload={'order_id': self.order_to.id},
            requested_by=self.admin,
            status='pending',
        )
        ApprovalTask.objects.filter(id=self.approval.id).update(
            created_at=timezone.now() - timedelta(hours=50)
        )
        RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='high',
            status='open',
            module='订单',
            title='运维风险事件',
            description='测试',
            order=self.order_to,
            detected_by=self.admin,
        )
        DataConsistencyCheckRun.objects.create(
            source='manual',
            total_issues=2,
            summary={'total': 2},
            issues=[{'type': 'finance_reconciliation_mismatch', 'msg': 'x'}],
            executed_by=self.admin,
        )

    def test_ops_center_should_render_and_contain_alerts(self):
        resp = self.client.get(reverse('ops_center'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '转寄任务超时')
        self.assertContains(resp, '审批任务超时')
        self.assertContains(resp, '待处理风险事件')
        self.assertContains(resp, '一致性巡检存在问题')
        self.assertContains(resp, '财务对账异常')

    def test_ops_center_should_support_filter_and_export(self):
        resp = self.client.get(reverse('ops_center') + '?source=approval&severity=danger')
        self.assertEqual(resp.status_code, 200)
        alerts = resp.context['alerts']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['source'], 'approval')
        self.assertEqual(alerts[0]['severity'], 'danger')
        self.assertEqual(alerts[0]['title'], '审批任务超时')
        export_resp = self.client.get(reverse('ops_center') + '?source=approval&severity=danger&export=1')
        self.assertEqual(export_resp.status_code, 200)
        self.assertEqual(export_resp['Content-Type'], 'text/csv; charset=utf-8-sig')
        self.assertIn('ops_alerts.csv', export_resp['Content-Disposition'])


class ConsistencyRepairCommandTests(TestCase):
    def setUp(self):
        self.sku = SKU.objects.create(
            code='SKU-REPAIR-001',
            name='修复测试套餐',
            category='主题套餐',
            rental_price=Decimal('168.00'),
            deposit=Decimal('200.00'),
            stock=3,
            is_active=True,
        )

    def test_repair_consistency_dry_run_should_not_update_stock(self):
        out = StringIO()
        call_command('repair_consistency', stdout=out)
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.stock, 3)
        self.assertIn('预览模式', out.getvalue())

    def test_check_consistency_should_output_type_counts_in_text_mode(self):
        out = StringIO()
        call_command('check_consistency', stdout=out)
        content = out.getvalue()
        self.assertIn('按类型统计', content)
        self.assertIn('legacy_stock_mismatch', content)

    def test_repair_consistency_apply_should_update_stock(self):
        out = StringIO()
        call_command('repair_consistency', '--apply', stdout=out)
        self.sku.refresh_from_db()
        # 未创建单套实例时，激活单套数=0，库存应被自动修复到0
        self.assertEqual(self.sku.stock, 0)
        self.assertIn('已应用=1', out.getvalue())

    def test_repair_consistency_apply_should_merge_duplicate_locked_when_enabled(self):
        source = Order.objects.create(
            customer_name='source-r',
            customer_phone='18000000001',
            delivery_address='A',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
        )
        target = Order.objects.create(
            customer_name='target-r',
            customer_phone='18000000002',
            delivery_address='B',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='pending',
        )
        a1 = TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=1,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            status='locked',
        )
        a2 = TransferAllocation.objects.create(
            source_order=source,
            target_order=target,
            sku=self.sku,
            quantity=2,
            target_event_date=target.event_date,
            window_start=target.event_date - timedelta(days=5),
            window_end=target.event_date + timedelta(days=5),
            status='locked',
        )
        out = StringIO()
        call_command('repair_consistency', '--apply', '--fix-duplicate-locked', stdout=out)
        rows = list(
            TransferAllocation.objects.filter(
                source_order=source, target_order=target, sku=self.sku
            ).order_by('id')
        )
        self.assertEqual(len(rows), 2)
        locked_rows = [r for r in rows if r.status == 'locked']
        released_rows = [r for r in rows if r.status == 'released']
        self.assertEqual(len(locked_rows), 1)
        self.assertEqual(len(released_rows), 1)
        self.assertEqual(locked_rows[0].quantity, 3)
        self.assertIn('已应用', out.getvalue())


class OpsWatchdogCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='watchdog_user',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.sku = SKU.objects.create(
            code='SKU-WD-001',
            name='巡检套餐',
            category='主题套餐',
            rental_price=Decimal('168.00'),
            deposit=Decimal('200.00'),
            stock=2,
            is_active=True,
        )
        self.order_from = Order.objects.create(
            customer_name='来源',
            customer_phone='18800000011',
            delivery_address='广东省深圳市南山区',
            event_date=date.today(),
            rental_days=1,
            status='delivered',
            created_by=self.user,
        )
        self.order_to = Order.objects.create(
            customer_name='目标',
            customer_phone='18800000012',
            delivery_address='广东省深圳市福田区',
            event_date=date.today() + timedelta(days=7),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )
        transfer = Transfer.objects.create(
            order_from=self.order_from,
            order_to=self.order_to,
            sku=self.sku,
            quantity=1,
            gap_days=7,
            status='pending',
            created_by=self.user,
        )
        Transfer.objects.filter(id=transfer.id).update(created_at=timezone.now() - timedelta(hours=50))
        task = ApprovalTask.objects.create(
            action_code='order.force_cancel',
            module='订单',
            target_type='order',
            target_id=self.order_to.id,
            target_label=self.order_to.order_no,
            summary='待审批',
            payload={'order_id': self.order_to.id},
            requested_by=self.user,
        )
        ApprovalTask.objects.filter(id=task.id).update(created_at=timezone.now() - timedelta(hours=50))
        RiskEvent.objects.create(
            event_type='frequent_cancel',
            level='medium',
            status='open',
            module='订单',
            title='风控事件',
            description='测试',
            detected_by=self.user,
        )
        DataConsistencyCheckRun.objects.create(
            source='manual',
            total_issues=2,
            summary={'total': 2, 'type_counts': {'x': 1, 'finance_reconciliation_mismatch': 1}},
            issues=[{'type': 'x'}, {'type': 'finance_reconciliation_mismatch'}],
            executed_by=self.user,
        )

    def test_ops_watchdog_json_should_output_alerts(self):
        out = StringIO()
        call_command('ops_watchdog', '--json', stdout=out)
        payload = json.loads(out.getvalue())
        self.assertIn('summary', payload)
        self.assertIn('alerts', payload)
        self.assertGreaterEqual(payload['summary']['alert_count'], 1)
        self.assertEqual(payload['summary'].get('finance_mismatch_count'), 1)
        self.assertTrue(any(a.get('source') == 'finance' for a in payload.get('alerts', [])))

    def test_ops_watchdog_save_audit_should_write_log(self):
        before = AuditLog.objects.filter(module='运维中心', target='ops_watchdog').count()
        out = StringIO()
        call_command('ops_watchdog', '--save-audit', stdout=out)
        after = AuditLog.objects.filter(module='运维中心', target='ops_watchdog').count()
        self.assertEqual(after, before + 1)


class SmokeFlowCommandTests(TestCase):
    def test_smoke_flow_should_run_successfully(self):
        out = StringIO()
        call_command('smoke_flow', stdout=out)
        content = out.getvalue()
        self.assertIn('冒烟通过', content)
        self.assertIn('页面/API连通性检查通过', content)
        self.assertIn('已清理冒烟数据', content)

    def test_smoke_flow_skip_http_check_should_run_successfully(self):
        out = StringIO()
        call_command('smoke_flow', '--skip-http-check', stdout=out)
        content = out.getvalue()
        self.assertIn('冒烟通过', content)
        self.assertNotIn('页面/API连通性检查通过', content)


# ============================================================
# 小程序 API 测试
# ============================================================

class MiniProgramModelTestCase(TestCase):
    """小程序相关模型测试"""

    def test_sku_display_stock_independent_of_effective_stock(self):
        """display_stock 不影响 effective_stock"""
        sku = SKU.objects.create(
            code='MP-TEST-001', name='测试套装', category='主题套餐',
            rental_price=Decimal('168.00'), deposit=Decimal('200.00'),
            stock=5, display_stock=99, display_stock_warning=10,
        )
        self.assertEqual(sku.effective_stock, 5)
        self.assertEqual(sku.display_stock, 99)

    def test_sku_mp_visible_default_false(self):
        """mp_visible 默认 False"""
        sku = SKU.objects.create(
            code='MP-TEST-002', name='测试套装2', category='主题套餐',
            rental_price=Decimal('100.00'), deposit=Decimal('100.00'),
            stock=1,
        )
        self.assertFalse(sku.mp_visible)

    def test_wechat_customer_unique_openid(self):
        """openid 唯一约束"""
        WechatCustomer.objects.create(openid='test_openid_001')
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            WechatCustomer.objects.create(openid='test_openid_001')

    def test_reservation_source_default_manual(self):
        """预定单来源默认为客服录入"""
        sku = SKU.objects.create(
            code='MP-TEST-003', name='测试套装3', category='主题套餐',
            rental_price=Decimal('100.00'), deposit=Decimal('100.00'),
            stock=1,
        )
        reservation = Reservation.objects.create(
            customer_wechat='test_wx', sku=sku,
            event_date=date.today() + timedelta(days=30),
        )
        self.assertEqual(reservation.source, 'manual')

    def test_reservation_source_miniprogram(self):
        """预定单可标记来源为小程序"""
        sku = SKU.objects.create(
            code='MP-TEST-004', name='测试套装4', category='主题套餐',
            rental_price=Decimal('100.00'), deposit=Decimal('100.00'),
            stock=1,
        )
        customer = WechatCustomer.objects.create(openid='test_openid_mp')
        reservation = Reservation.objects.create(
            customer_wechat='test_wx', sku=sku,
            event_date=date.today() + timedelta(days=30),
            source='miniprogram', wechat_customer=customer,
        )
        self.assertEqual(reservation.source, 'miniprogram')
        self.assertEqual(reservation.wechat_customer, customer)

    def test_sku_image_model(self):
        """SKUImage 多图模型"""
        sku = SKU.objects.create(
            code='MP-TEST-005', name='测试套装5', category='主题套餐',
            rental_price=Decimal('100.00'), deposit=Decimal('100.00'),
            stock=1,
        )
        img1 = SKUImage.objects.create(sku=sku, sort_order=0, is_cover=True)
        img2 = SKUImage.objects.create(sku=sku, sort_order=1, is_cover=False)
        self.assertEqual(sku.images.count(), 2)
        self.assertEqual(sku.images.filter(is_cover=True).count(), 1)

    def test_order_source_miniprogram(self):
        """订单来源新增小程序枚举"""
        User = get_user_model()
        user = User.objects.create_user(username='mp_test_user', password='test123')
        sku = SKU.objects.create(
            code='MP-TEST-006', name='测试套装6', category='主题套餐',
            rental_price=Decimal('100.00'), deposit=Decimal('100.00'),
            stock=1,
        )
        order = Order.objects.create(
            customer_name='测试客户', customer_phone='13800000000',
            delivery_address='测试地址', event_date=date.today() + timedelta(days=30),
            order_source='miniprogram', created_by=user,
        )
        self.assertEqual(order.order_source, 'miniprogram')


class MiniProgramAPITestCase(TestCase):
    """小程序 API 接口测试"""

    def setUp(self):
        self.sku_visible = SKU.objects.create(
            code='MP-V-001', name='可见套装', category='主题套餐',
            rental_price=Decimal('168.00'), deposit=Decimal('200.00'),
            stock=3, mp_visible=True, display_stock=5,
            display_stock_warning=2, mp_sort_order=1,
            description='这是一个可见的测试套装',
        )
        self.sku_hidden = SKU.objects.create(
            code='MP-H-001', name='隐藏套装', category='主题套餐',
            rental_price=Decimal('100.00'), deposit=Decimal('100.00'),
            stock=2, mp_visible=False,
        )
        self.part = Part.objects.create(
            name='背景板', spec='120x180cm', category='main',
            current_stock=10, safety_stock=2,
        )
        SKUComponent.objects.create(
            sku=self.sku_visible, part=self.part, quantity_per_set=1,
        )
        self.customer = WechatCustomer.objects.create(
            openid='test_openid_api', nickname='测试用户', phone='13800000001',
        )
        self.staff_user = User.objects.create_user(
            username='cs_mobile',
            password='mobile123',
            role='customer_service',
            full_name='移动客服',
            is_active=True,
        )
        self.warehouse_user = User.objects.create_user(
            username='wh_mobile',
            password='mobile123',
            role='warehouse_staff',
            full_name='移动仓库',
            is_active=True,
        )
        self.manager_user = User.objects.create_user(
            username='mgr_mobile',
            password='mobile123',
            role='manager',
            full_name='移动经理',
            is_active=True,
        )
        # 生成有效 token
        from apps.core.services.wechat_auth_service import generate_token
        self.token = generate_token(self.customer.id, self.customer.openid)

    def _auth_header(self):
        return {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    # --- 产品列表 ---
    def test_sku_list_only_visible(self):
        """只返回 mp_visible=True 的产品"""
        resp = self.client.get('/api/mp/skus/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        names = [r['name'] for r in data['results']]
        self.assertIn('可见套装', names)
        self.assertNotIn('隐藏套装', names)

    def test_sku_list_category_filter(self):
        """分类筛选"""
        resp = self.client.get('/api/mp/skus/?category=主题套餐')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(len(data['results']) >= 1)

    def test_sku_list_keyword_search(self):
        """关键词搜索"""
        resp = self.client.get('/api/mp/skus/?keyword=可见')
        data = resp.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['name'], '可见套装')

    def test_sku_list_stock_status_normal(self):
        """库存状态：正常"""
        resp = self.client.get('/api/mp/skus/')
        data = resp.json()
        item = [r for r in data['results'] if r['name'] == '可见套装'][0]
        self.assertEqual(item['stock_status'], 'normal')
        self.assertEqual(item['display_stock'], 5)

    def test_sku_list_stock_status_warning(self):
        """库存状态：即将售罄"""
        self.sku_visible.display_stock = 2
        self.sku_visible.save()
        resp = self.client.get('/api/mp/skus/')
        data = resp.json()
        item = [r for r in data['results'] if r['name'] == '可见套装'][0]
        self.assertEqual(item['stock_status'], 'warning')

    def test_sku_list_stock_status_soldout(self):
        """库存状态：已售罄"""
        self.sku_visible.display_stock = 0
        self.sku_visible.save()
        resp = self.client.get('/api/mp/skus/')
        data = resp.json()
        item = [r for r in data['results'] if r['name'] == '可见套装'][0]
        self.assertEqual(item['stock_status'], 'soldout')

    # --- 产品详情 ---
    def test_sku_detail_with_components(self):
        """产品详情包含部件列表"""
        resp = self.client.get(f'/api/mp/skus/{self.sku_visible.id}/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['name'], '可见套装')
        self.assertEqual(len(data['components']), 1)
        self.assertEqual(data['components'][0]['name'], '背景板')
        self.assertEqual(data['components'][0]['quantity'], 1)

    def test_sku_detail_not_visible_404(self):
        """不可见的产品返回 404"""
        resp = self.client.get(f'/api/mp/skus/{self.sku_hidden.id}/')
        self.assertEqual(resp.status_code, 404)

    @override_settings(R2_PUBLIC_DOMAIN='https://pic.yanli.net.cn')
    def test_sku_list_should_prefer_r2_image_key(self):
        self.sku_visible.image_key = 'sku-images/2026/03/cover.jpg'
        self.sku_visible.save(update_fields=['image_key'])
        resp = self.client.get('/api/mp/skus/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        item = [r for r in data['results'] if r['name'] == '可见套装'][0]
        self.assertEqual(item['cover_image'], 'https://pic.yanli.net.cn/sku-images/2026/03/cover.jpg')

    @override_settings(R2_PUBLIC_DOMAIN='https://pic.yanli.net.cn')
    def test_sku_detail_should_return_r2_gallery_url(self):
        SKUImage.objects.create(
            sku=self.sku_visible,
            image_key='sku-images/2026/03/gallery.jpg',
            sort_order=0,
            is_cover=True,
        )
        resp = self.client.get(f'/api/mp/skus/{self.sku_visible.id}/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['images'][0]['url'], 'https://pic.yanli.net.cn/sku-images/2026/03/gallery.jpg')

    # --- 意向下单 ---
    def test_create_reservation_success(self):
        """正常提交意向订单"""
        resp = self.client.post('/api/mp/reservations/', data=json.dumps({
            'sku_id': self.sku_visible.id,
            'event_date': str(date.today() + timedelta(days=30)),
            'customer_wechat': 'test_wx_user',
            'customer_name': '张三',
            'customer_phone': '13900000000',
            'city': '广州',
            'notes': '希望多加气球',
        }), content_type='application/json', **self._auth_header())
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIn('reservation_no', data)
        # 验证数据库记录
        r = Reservation.objects.get(reservation_no=data['reservation_no'])
        self.assertEqual(r.source, 'miniprogram')
        self.assertEqual(r.wechat_customer, self.customer)
        self.assertEqual(r.customer_wechat, 'test_wx_user')
        self.assertEqual(r.sku, self.sku_visible)

    def test_create_reservation_requires_login(self):
        """未登录不能提交"""
        resp = self.client.post('/api/mp/reservations/', data=json.dumps({
            'sku_id': self.sku_visible.id,
            'event_date': str(date.today() + timedelta(days=30)),
            'customer_wechat': 'test_wx',
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 401)

    def test_create_reservation_missing_wechat(self):
        """缺少微信号校验"""
        resp = self.client.post('/api/mp/reservations/', data=json.dumps({
            'sku_id': self.sku_visible.id,
            'event_date': str(date.today() + timedelta(days=30)),
        }), content_type='application/json', **self._auth_header())
        self.assertEqual(resp.status_code, 400)

    def test_create_reservation_invalid_sku(self):
        """不可见的 SKU 不能下单"""
        resp = self.client.post('/api/mp/reservations/', data=json.dumps({
            'sku_id': self.sku_hidden.id,
            'event_date': str(date.today() + timedelta(days=30)),
            'customer_wechat': 'test_wx',
        }), content_type='application/json', **self._auth_header())
        self.assertEqual(resp.status_code, 400)

    def test_create_reservation_daily_limit(self):
        """每日提交上限 10 个"""
        for i in range(10):
            self.client.post('/api/mp/reservations/', data=json.dumps({
                'sku_id': self.sku_visible.id,
                'event_date': str(date.today() + timedelta(days=30)),
                'customer_wechat': f'wx_{i}',
            }), content_type='application/json', **self._auth_header())
        resp = self.client.post('/api/mp/reservations/', data=json.dumps({
            'sku_id': self.sku_visible.id,
            'event_date': str(date.today() + timedelta(days=30)),
            'customer_wechat': 'wx_overflow',
        }), content_type='application/json', **self._auth_header())
        self.assertEqual(resp.status_code, 429)

    # --- 我的订单 ---
    def test_my_reservations_only_own(self):
        """只能看到自己的意向订单"""
        # 创建一个属于当前客户的预定单
        Reservation.objects.create(
            customer_wechat='my_wx', sku=self.sku_visible,
            event_date=date.today() + timedelta(days=30),
            source='miniprogram', wechat_customer=self.customer,
        )
        # 创建一个不属于当前客户的预定单
        other_customer = WechatCustomer.objects.create(openid='other_openid')
        Reservation.objects.create(
            customer_wechat='other_wx', sku=self.sku_visible,
            event_date=date.today() + timedelta(days=30),
            source='miniprogram', wechat_customer=other_customer,
        )
        resp = self.client.get('/api/mp/my-reservations/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['sku_name'], '可见套装')

    def test_my_reservations_status_label(self):
        """状态文案正确映射"""
        r = Reservation.objects.create(
            customer_wechat='my_wx', sku=self.sku_visible,
            event_date=date.today() + timedelta(days=30),
            source='miniprogram', wechat_customer=self.customer,
            status='pending_info',
        )
        resp = self.client.get('/api/mp/my-reservations/', **self._auth_header())
        data = resp.json()
        self.assertEqual(data['results'][0]['status_label'], '待客服确认')

    def test_my_reservations_should_include_progress_fields(self):
        """我的订单列表返回进度可视化字段"""
        Reservation.objects.create(
            customer_wechat='my_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=7),
            source='miniprogram',
            wechat_customer=self.customer,
            status='pending_info',
        )
        resp = self.client.get('/api/mp/my-reservations/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        item = resp.json()['results'][0]
        self.assertIn('contact_status_code', item)
        self.assertIn('contact_status_label', item)
        self.assertIn('journey_code', item)
        self.assertIn('journey_label', item)
        self.assertIn('status_tip', item)
        self.assertIn('followup_date', item)
        self.assertTrue(item['journey_label'])

    def test_reservation_detail_should_include_converted_fulfillment_fields(self):
        """订单详情返回转正式订单后的履约跟进字段"""
        order = Order.objects.create(
            customer_name='小程序客户',
            customer_phone='13812345678',
            delivery_address='测试地址 1 号',
            event_date=date.today() + timedelta(days=10),
            rental_days=1,
            ship_date=date.today() + timedelta(days=2),
            status='confirmed',
            total_amount=Decimal('168.00'),
            balance=Decimal('68.00'),
        )
        reservation = Reservation.objects.create(
            customer_wechat='my_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=10),
            source='miniprogram',
            wechat_customer=self.customer,
            status='converted',
            converted_order=order,
        )
        resp = self.client.get(f'/api/mp/my-reservations/{reservation.id}/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['converted_order_no'], order.order_no)
        self.assertEqual(data['journey_code'], 'awaiting_shipment')
        self.assertEqual(data['fulfillment_stage_label'], '已转单待发货')
        self.assertEqual(data['shipping_followup_label'], '待发货')
        self.assertEqual(data['balance_followup_label'], '待收尾款 ￥68.00')
        self.assertEqual(len(data['steps']), 4)

    def test_my_reservations_requires_login(self):
        """未登录不能查看"""
        resp = self.client.get('/api/mp/my-reservations/')
        self.assertEqual(resp.status_code, 401)

    def test_staff_bind_should_bind_backend_user(self):
        resp = self.client.post(
            '/api/mp/staff/bind/',
            data=json.dumps({'username': 'cs_mobile', 'password': 'mobile123'}),
            content_type='application/json',
            **self._auth_header(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(WechatStaffBinding.objects.filter(customer=self.customer, user=self.staff_user).exists())

    def test_staff_profile_should_return_bound_user(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.staff_user)
        resp = self.client.get('/api/mp/staff/profile/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['is_staff_bound'])
        self.assertEqual(data['staff']['username'], 'cs_mobile')

    def test_staff_dashboard_should_return_customer_service_counts(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.staff_user)
        Reservation.objects.create(
            customer_wechat='my_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=7),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='pending_info',
        )
        resp = self.client.get('/api/mp/staff/dashboard/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        labels = [item['label'] for item in data['shortcuts']]
        self.assertIn('今日需联系', labels)

    def test_staff_reservations_should_only_return_owned_for_customer_service(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.staff_user)
        Reservation.objects.create(
            customer_wechat='my_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=5),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='ready_to_convert',
        )
        Reservation.objects.create(
            customer_wechat='other_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=5),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.warehouse_user,
            status='ready_to_convert',
        )
        resp = self.client.get('/api/mp/staff/reservations/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['owner_name'], '移动客服')

    def test_staff_should_update_reservation_followup(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.staff_user)
        reservation = Reservation.objects.create(
            customer_wechat='old_wx',
            customer_name='旧客户名',
            customer_phone='',
            delivery_address='',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=6),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='pending_info',
        )
        resp = self.client.post(
            f'/api/mp/staff/reservations/{reservation.id}/followup/',
            data=json.dumps({
                'customer_name': '新客户名',
                'customer_phone': '13888889999',
                'delivery_address': '上海市徐汇区测试路 1 号',
                'notes': '已电话沟通，等补最终地址',
            }),
            content_type='application/json',
            **self._auth_header(),
        )
        self.assertEqual(resp.status_code, 200)
        reservation.refresh_from_db()
        self.assertEqual(reservation.customer_name, '新客户名')
        self.assertEqual(reservation.customer_phone, '13888889999')
        self.assertEqual(reservation.delivery_address, '上海市徐汇区测试路 1 号')
        self.assertEqual(reservation.notes, '已电话沟通，等补最终地址')

    def test_staff_reservation_list_should_expose_quick_status_actions(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.staff_user)
        active_reservation = Reservation.objects.create(
            customer_wechat='quick_status_wx',
            customer_name='快捷推进客户',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=4),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='pending_info',
        )
        closed_reservation = Reservation.objects.create(
            customer_wechat='closed_status_wx',
            customer_name='已关闭客户',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=4),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='converted',
        )

        resp = self.client.get('/api/mp/staff/reservations/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        results = {item['id']: item for item in resp.json()['results']}
        self.assertFalse(results[active_reservation.id]['can_mark_pending_info'])
        self.assertTrue(results[active_reservation.id]['can_mark_ready_to_convert'])
        self.assertFalse(results[closed_reservation.id]['can_mark_pending_info'])
        self.assertFalse(results[closed_reservation.id]['can_mark_ready_to_convert'])

    def test_manager_should_transfer_reservation_owner(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.manager_user)
        reservation = Reservation.objects.create(
            customer_wechat='owner_wx',
            customer_name='待转交客户',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=6),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='pending_info',
        )
        detail_resp = self.client.get(f'/api/mp/staff/reservations/{reservation.id}/', **self._auth_header())
        self.assertEqual(detail_resp.status_code, 200)
        detail_data = detail_resp.json()
        self.assertTrue(detail_data['can_transfer_owner'])
        self.assertTrue(any(item['id'] == self.staff_user.id for item in detail_data['owner_options']))

        transfer_resp = self.client.post(
            f'/api/mp/staff/reservations/{reservation.id}/transfer/',
            data=json.dumps({'owner_id': self.manager_user.id, 'reason': '客服请假转交'}),
            content_type='application/json',
            **self._auth_header(),
        )
        self.assertEqual(transfer_resp.status_code, 200)
        reservation.refresh_from_db()
        self.assertEqual(reservation.owner_id, self.manager_user.id)

    def test_customer_service_should_not_transfer_reservation_owner(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.staff_user)
        reservation = Reservation.objects.create(
            customer_wechat='owner_wx',
            customer_name='待转交客户',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=6),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='pending_info',
        )
        resp = self.client.post(
            f'/api/mp/staff/reservations/{reservation.id}/transfer/',
            data=json.dumps({'owner_id': self.manager_user.id, 'reason': '尝试转交'}),
            content_type='application/json',
            **self._auth_header(),
        )
        self.assertEqual(resp.status_code, 403)
        reservation.refresh_from_db()
        self.assertEqual(reservation.owner_id, self.staff_user.id)

    def test_staff_order_detail_and_delivery_action_for_warehouse(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.warehouse_user)
        order = Order.objects.create(
            customer_name='仓库客户',
            customer_phone='13800000002',
            delivery_address='仓库地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            ship_date=date.today(),
            status='confirmed',
            total_amount=Decimal('168.00'),
            balance=Decimal('68.00'),
            created_by=self.warehouse_user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku_visible,
            quantity=1,
            rental_price=self.sku_visible.rental_price,
            deposit=self.sku_visible.deposit,
            subtotal=Decimal('168.00'),
        )

        resp = self.client.get(f'/api/mp/staff/orders/{order.id}/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['can_mark_delivered'])

        deliver_resp = self.client.post(
            f'/api/mp/staff/orders/{order.id}/deliver/',
            data=json.dumps({'ship_tracking': 'SF123456'}),
            content_type='application/json',
            **self._auth_header(),
        )
        self.assertEqual(deliver_resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertEqual(order.ship_tracking, 'SF123456')

    def test_staff_order_detail_should_expose_balance_and_return_service_actions(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.warehouse_user)
        order = Order.objects.create(
            customer_name='移动执行客户',
            customer_phone='13800000009',
            delivery_address='移动执行地址',
            event_date=date.today() + timedelta(days=4),
            rental_days=1,
            ship_date=date.today() - timedelta(days=1),
            status='delivered',
            total_amount=Decimal('200.00'),
            balance=Decimal('45.00'),
            return_service_type='platform_return_included',
            return_service_fee=Decimal('45.00'),
            return_service_payment_status='paid',
            return_service_payment_channel='wechat',
            return_service_payment_reference='WX-REF-1',
            return_pickup_status='pending_schedule',
            created_by=self.warehouse_user,
        )
        OrderItem.objects.create(
            order=order,
            sku=self.sku_visible,
            quantity=1,
            rental_price=self.sku_visible.rental_price,
            deposit=self.sku_visible.deposit,
            subtotal=Decimal('168.00'),
        )

        resp = self.client.get(f'/api/mp/staff/orders/{order.id}/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['can_record_balance'])
        self.assertTrue(data['can_update_return_service'])
        self.assertEqual(data['return_service_type'], 'platform_return_included')
        self.assertEqual(data['return_pickup_status'], 'pending_schedule')

    def test_staff_should_record_balance_and_update_return_service(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.warehouse_user)
        order = Order.objects.create(
            customer_name='移动财务客户',
            customer_phone='13800000010',
            delivery_address='移动财务地址',
            event_date=date.today() + timedelta(days=4),
            rental_days=1,
            ship_date=date.today() - timedelta(days=1),
            status='delivered',
            total_amount=Decimal('200.00'),
            balance=Decimal('80.00'),
            return_service_type='none',
            return_service_fee=Decimal('0.00'),
            return_service_payment_status='unpaid',
            return_pickup_status='not_required',
            created_by=self.warehouse_user,
        )

        balance_resp = self.client.post(
            f'/api/mp/staff/orders/{order.id}/balance/',
            data=json.dumps({'amount': '30.00', 'notes': '移动端补收尾款'}),
            content_type='application/json',
            **self._auth_header(),
        )
        self.assertEqual(balance_resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.balance, Decimal('50.00'))
        self.assertTrue(FinanceTransaction.objects.filter(order=order, transaction_type='balance_received', amount=Decimal('30.00')).exists())

        return_service_resp = self.client.post(
            f'/api/mp/staff/orders/{order.id}/return-service/',
            data=json.dumps({
                'return_service_type': 'platform_return_included',
                'return_service_fee': '45.00',
                'return_service_payment_status': 'paid',
                'return_service_payment_channel': 'wechat',
                'return_service_payment_reference': 'WX-RETURN-30',
                'return_pickup_status': 'scheduled',
            }),
            content_type='application/json',
            **self._auth_header(),
        )
        self.assertEqual(return_service_resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.return_service_type, 'platform_return_included')
        self.assertEqual(order.return_service_payment_status, 'paid')
        self.assertEqual(order.return_pickup_status, 'scheduled')
        self.assertTrue(FinanceTransaction.objects.filter(order=order, transaction_type='return_service_received', amount=Decimal('45.00')).exists())

    def test_manager_should_filter_staff_reservations_by_source_and_owner(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.manager_user)
        Reservation.objects.create(
            customer_wechat='mp_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=5),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='ready_to_convert',
        )
        Reservation.objects.create(
            customer_wechat='manual_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=5),
            source='manual',
            owner=self.manager_user,
            status='pending_info',
        )
        resp = self.client.get(
            f'/api/mp/staff/reservations/?source=miniprogram&owner_id={self.staff_user.id}',
            **self._auth_header(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['source'], 'miniprogram')
        self.assertTrue(any(item['value'] == 'manual' for item in data['filters']['sources']))
        self.assertTrue(any(item['value'] == self.staff_user.id for item in data['filters']['owners']))

    def test_manager_should_filter_staff_orders_by_source_and_owner(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.manager_user)
        order_mp = Order.objects.create(
            customer_name='来源客户A',
            customer_phone='13800000111',
            delivery_address='地址A',
            event_date=date.today() + timedelta(days=4),
            rental_days=1,
            status='confirmed',
            order_source='miniprogram',
            total_amount=Decimal('168.00'),
            balance=Decimal('0.00'),
            created_by=self.manager_user,
        )
        order_manual = Order.objects.create(
            customer_name='来源客户B',
            customer_phone='13800000222',
            delivery_address='地址B',
            event_date=date.today() + timedelta(days=4),
            rental_days=1,
            status='confirmed',
            order_source='wechat',
            total_amount=Decimal('168.00'),
            balance=Decimal('20.00'),
            created_by=self.manager_user,
        )
        Reservation.objects.create(
            customer_wechat='a_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=4),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='converted',
            converted_order=order_mp,
        )
        Reservation.objects.create(
            customer_wechat='b_wx',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=4),
            source='manual',
            owner=self.manager_user,
            status='converted',
            converted_order=order_manual,
        )
        resp = self.client.get(
            f'/api/mp/staff/orders/?order_source=miniprogram&owner_id={self.staff_user.id}',
            **self._auth_header(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['order_source'], 'miniprogram')
        self.assertEqual(data['results'][0]['owner_name'], '移动客服')
        self.assertTrue(any(item['value'] == 'wechat' for item in data['filters']['sources']))
        self.assertTrue(any(item['value'] == self.staff_user.id for item in data['filters']['owners']))

    def test_staff_order_list_should_expose_quick_action_flags(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.warehouse_user)
        confirmed_order = Order.objects.create(
            customer_name='待发货客户',
            customer_phone='13900000011',
            delivery_address='待发货地址',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            ship_date=date.today(),
            status='confirmed',
            total_amount=Decimal('168.00'),
            balance=Decimal('68.00'),
            created_by=self.warehouse_user,
            return_service_type='platform_return_included',
            return_pickup_status='pending_schedule',
        )
        delivered_order = Order.objects.create(
            customer_name='待归还客户',
            customer_phone='13900000012',
            delivery_address='待归还地址',
            event_date=date.today() + timedelta(days=2),
            rental_days=1,
            ship_date=date.today(),
            status='delivered',
            total_amount=Decimal('168.00'),
            balance=Decimal('0.00'),
            created_by=self.warehouse_user,
        )
        for order in [confirmed_order, delivered_order]:
            OrderItem.objects.create(
                order=order,
                sku=self.sku_visible,
                quantity=1,
                rental_price=self.sku_visible.rental_price,
                deposit=self.sku_visible.deposit,
                subtotal=self.sku_visible.rental_price,
            )

        resp = self.client.get('/api/mp/staff/orders/', **self._auth_header())
        self.assertEqual(resp.status_code, 200)
        results = {item['id']: item for item in resp.json()['results']}
        self.assertTrue(results[confirmed_order.id]['can_mark_delivered'])
        self.assertTrue(results[confirmed_order.id]['can_record_balance'])
        self.assertTrue(results[confirmed_order.id]['can_update_return_service'])
        self.assertFalse(results[confirmed_order.id]['can_mark_returned'])
        self.assertFalse(results[delivered_order.id]['can_mark_delivered'])
        self.assertTrue(results[delivered_order.id]['can_mark_returned'])

    def test_staff_reservations_should_support_keyword_search(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.manager_user)
        matched = Reservation.objects.create(
            customer_wechat='search_wx',
            customer_name='搜索客户',
            customer_phone='13811112222',
            sku=self.sku_visible,
            event_date=date.today() + timedelta(days=5),
            source='miniprogram',
            wechat_customer=self.customer,
            owner=self.staff_user,
            status='pending_info',
        )
        Reservation.objects.create(
            customer_wechat='other_wx',
            customer_name='其他客户',
            customer_phone='13999990000',
            sku=self.sku_hidden,
            event_date=date.today() + timedelta(days=6),
            source='manual',
            owner=self.manager_user,
            status='pending_info',
        )

        resp = self.client.get(
            '/api/mp/staff/reservations/?keyword=搜索客户',
            **self._auth_header(),
        )
        self.assertEqual(resp.status_code, 200)
        results = resp.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], matched.id)

    def test_staff_orders_should_support_keyword_search_by_sku_name(self):
        WechatStaffBinding.objects.create(customer=self.customer, user=self.warehouse_user)
        matched_order = Order.objects.create(
            customer_name='搜索订单客户',
            customer_phone='13855556666',
            delivery_address='搜索地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='confirmed',
            total_amount=Decimal('168.00'),
            balance=Decimal('0.00'),
            created_by=self.warehouse_user,
        )
        other_order = Order.objects.create(
            customer_name='其他订单客户',
            customer_phone='13877778888',
            delivery_address='其他地址',
            event_date=date.today() + timedelta(days=3),
            rental_days=1,
            status='confirmed',
            total_amount=Decimal('168.00'),
            balance=Decimal('0.00'),
            created_by=self.warehouse_user,
        )
        OrderItem.objects.create(
            order=matched_order,
            sku=self.sku_visible,
            quantity=1,
            rental_price=self.sku_visible.rental_price,
            deposit=self.sku_visible.deposit,
            subtotal=self.sku_visible.rental_price,
        )
        OrderItem.objects.create(
            order=other_order,
            sku=self.sku_hidden,
            quantity=1,
            rental_price=self.sku_hidden.rental_price,
            deposit=self.sku_hidden.deposit,
            subtotal=self.sku_hidden.rental_price,
        )

        resp = self.client.get(
            f'/api/mp/staff/orders/?keyword={self.sku_visible.name}',
            **self._auth_header(),
        )
        self.assertEqual(resp.status_code, 200)
        results = resp.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], matched_order.id)

    # --- 认证 ---
    def test_token_expired(self):
        """过期 token 被拒绝"""
        import time
        from apps.core.services.wechat_auth_service import generate_token, verify_token
        import hmac, hashlib
        from django.conf import settings
        # 手动创建一个过期 token
        expire_ts = int(time.time()) - 100
        payload = f"{self.customer.id}.{expire_ts}"
        signature = hmac.new(
            settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()[:32]
        expired_token = f"{payload}.{signature}"
        resp = self.client.get('/api/mp/my-reservations/',
                               HTTP_AUTHORIZATION=f'Bearer {expired_token}')
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_format(self):
        """格式错误的 token 被拒绝"""
        resp = self.client.get('/api/mp/my-reservations/',
                               HTTP_AUTHORIZATION='Bearer invalid.token')
        self.assertEqual(resp.status_code, 401)

    def test_token_verify_roundtrip(self):
        """Token 签发与校验往返测试"""
        from apps.core.services.wechat_auth_service import generate_token, verify_token
        token = generate_token(self.customer.id, self.customer.openid)
        customer_id = verify_token(token)
        self.assertEqual(customer_id, self.customer.id)
